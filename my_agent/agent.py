"""
Systeme de revision multi-agents - Google ADK
"""

import re
import os
import requests
from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai.types import Content, Part, FunctionCall
from google.adk.tools import agent_tool
from my_agent.tools.my_tools import calculer_score, enregistrer_reponses, sauvegarder_reponses_correctes

MODEL = "groq/llama-3.1-8b-instant"


# CALLBACKS

def before_flashcard_callback(callback_context: CallbackContext, **kwargs) -> None:
    """before_model_callback : initialise le state au debut de chaque session."""
    s = callback_context.state
    s["correct_answers"]    = {}
    s["quiz_pret"]          = False
    s["score_actuel"]       = 0
    s["wrong_questions"]    = []
    s["rapport_progression"] = "Premiere session."
    s["wrong_summary"]      = "aucune"
    s["nb_sessions"]        = len(s.get("historique_scores", []))
    if "historique_scores" not in s:
        s["historique_scores"] = []
    print("\n[CALLBACK] State initialise")


def before_quiz_callback(callback_context: CallbackContext, **kwargs) -> None:
    """
    before_model_callback : appelle le LLM separement pour obtenir les reponses
    AVANT que le quiz soit genere. Les reponses ne seront jamais dans l'output.
    """
    callback_context.state["correct_answers"] = {}
    flashcard = callback_context.state.get("flashcard_content", "")
    if not flashcard:
        print("\n[before_quiz] pas de flashcard")
        return
    print("\n[before_quiz] extraction des reponses avant generation...")
    try:
        api_key = os.environ.get("GROQ_API_KEY", "")
        prompt = (
            "Tu vas creer un QCM de 5 questions sur ce contenu. "
            "Avant de le rediger, indique les bonnes reponses. "
            "Reponds UNIQUEMENT avec ce format exact, rien d autre :\n"
            "Q1=X Q2=X Q3=X Q4=X Q5=X\n\n"
            f"Contenu :\n{flashcard}"
        )
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 30,
                "temperature": 0,
            },
            timeout=10
        )
        text = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"\n[before_quiz] LLM: '{text}'")
        m = re.search(
            r"Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])",
            text, re.IGNORECASE
        )
        if m:
            answers = {f"Q{i+1}": m.group(i+1).upper() for i in range(5)}
            callback_context.state["correct_answers"] = answers
            print(f"\n[before_quiz] Reponses : {answers}")
        else:
            print(f"\n[before_quiz] Pattern non trouve: '{text}'")
    except Exception as e:
        print(f"\n[before_quiz] Erreur : {e}")


def after_quiz_callback(callback_context: CallbackContext) -> None:
    """
    after_agent_callback : extrait les bonnes reponses depuis quiz_raw.
    Essaie plusieurs patterns. Fallback sur un appel LLM dedie si necessaire.
    """
    quiz_raw = callback_context.state.get("quiz_raw", "")
    print(f"\n[after_quiz] {len(quiz_raw)} chars")
    print(f"\n[after_quiz] fin:\n{quiz_raw[-300:]}")

    answers = {}

    # Pattern 1 : REPONSES_CACHEES: Q1=C Q2=B Q3=B Q4=C Q5=B
    p1 = re.search(
        r'REPONSES_CACHEES\s*:\s*Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])',
        quiz_raw, re.IGNORECASE
    )
    if p1:
        answers = {f"Q{i+1}": p1.group(i+1).upper() for i in range(5)}
        print(f"\n[after_quiz] Pattern 1 OK : {answers}")
        clean = re.sub(r'[^\n]*REPONSES_CACHEES[^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
        clean = re.sub(r'[^\n]*Ligne\s+1[^\n]*\n?', '', clean, flags=re.IGNORECASE)
        callback_context.state["quiz_raw"] = clean.strip()

    # Pattern 2 : Q1=C, Q2=B, Q3=B sur une ligne
    if not answers:
        p2 = re.search(
            r'Q1\s*=\s*([A-D])[,\s]+Q2\s*=\s*([A-D])[,\s]+Q3\s*=\s*([A-D])[,\s]+Q4\s*=\s*([A-D])[,\s]+Q5\s*=\s*([A-D])',
            quiz_raw, re.IGNORECASE
        )
        if p2:
            answers = {f"Q{i+1}": p2.group(i+1).upper() for i in range(5)}
            print(f"\n[after_quiz] Pattern 2 OK : {answers}")
            clean = re.sub(r'[^\n]*Q1\s*=\s*[A-D][^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
            callback_context.state["quiz_raw"] = clean.strip()

    # Pattern 3 : "Reponse : X" apres chaque question
    if not answers:
        reps = re.findall(r'R[ée]ponse\s*(?:correcte)?\s*:\s*([A-D])', quiz_raw, re.IGNORECASE)
        if len(reps) == 5:
            answers = {f"Q{i+1}": r.upper() for i, r in enumerate(reps)}
            print(f"\n[after_quiz] Pattern 3 OK : {answers}")
            clean = re.sub(r'[^\n]*R[ée]ponse[^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
            callback_context.state["quiz_raw"] = clean.strip()

    # Fallback : appel LLM Groq dedie avec temperature=0
    if not answers:
        print(f"\n[after_quiz] Aucun pattern — fallback LLM")
        try:
            api_key = os.environ.get("GROQ_API_KEY", "")
            prompt = (
                "Lis ce quiz et identifie la bonne reponse pour chaque question.\n"
                "Reponds UNIQUEMENT avec ce format exact, rien d'autre :\n"
                "Q1=X Q2=X Q3=X Q4=X Q5=X\n\n"
                f"Quiz :\n{quiz_raw}"
            )
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 30,
                    "temperature": 0,
                },
                timeout=10
            )
            text = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"\n[after_quiz] LLM dit: '{text}'")
            m = re.search(
                r'Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])',
                text, re.IGNORECASE
            )
            if m:
                answers = {f"Q{i+1}": m.group(i+1).upper() for i in range(5)}
                print(f"\n[after_quiz] Fallback OK : {answers}")
        except Exception as e:
            print(f"\n[after_quiz] Erreur fallback : {e}")

    if answers:
        callback_context.state["correct_answers"] = answers
    else:
        print(f"\n[after_quiz] ECHEC TOTAL")

    callback_context.state["quiz_pret"] = True


def root_router(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — bypass du LLM root.
    Route en Python pur via FunctionCall natif ADK.
    """
    try:
        events = callback_context._invocation_context.session.events
        msg = ""
        for event in reversed(events):
            if event.author == "user" and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        msg = part.text.strip()
                        break
                if msg:
                    break

        callback_context.state["last_user_message"] = msg
        print(f"\n[ROOT ROUTER] '{msg[:60]}'")

        msg_upper = msg.upper().strip()
        if msg_upper.startswith("CORRECTION:"):
            agent_name = "CorrectionPipeline"
        elif any(x in msg_upper for x in ["B)", "B )", "REFAIRE", "MEME SUJET", "MÊME SUJET"]):
            agent_name = "QuizOnlyPipeline"
        else:
            agent_name = "FlashcardQuizPipeline"
        print(f"\n[ROOT ROUTER] -> {agent_name}")

        return LlmResponse(
            content=Content(parts=[Part(function_call=FunctionCall(
                name="transfer_to_agent",
                args={"agent_name": agent_name}
            ))])
        )
    except Exception as e:
        print(f"\n[ROOT ROUTER] Erreur : {e}")
        return None


def before_correcteur_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — calcule le score en Python pur.
    Le LLM du Correcteur n'est JAMAIS appele.
    """
    correct_answers = callback_context.state.get("correct_answers", {})
    quiz_raw        = callback_context.state.get("quiz_raw", "")
    user_msg        = callback_context.state.get("last_user_message", "")

    print(f"\n[CORRECTEUR] correct={correct_answers} | user='{user_msg[:50]}'")

    if not correct_answers:
        return LlmResponse(content=Content(parts=[Part(text="Aucun quiz en cours. Envoie un sujet pour commencer.")]))

    matches = re.findall(r'Q(\d+)\s*=\s*([A-Da-d])', user_msg, re.IGNORECASE)
    if not matches:
        return LlmResponse(content=Content(parts=[Part(text="Format invalide. Utilise : CORRECTION: Q1=A, Q2=B, Q3=C, Q4=D, Q5=A")]))

    user_answers = {f"Q{n}": l.upper() for n, l in matches}

    # Extraire les intitules de questions
    question_texts = {}
    for num, text in re.findall(r'\*\*Q(\d+)\.\*\*\s*(.+?)(?=\n-|\n\*|\Z)', quiz_raw, re.DOTALL):
        question_texts[f"Q{num}"] = text.strip()

    score = 0
    wrong = []
    lines = []
    for i in range(1, 6):
        q       = f"Q{i}"
        user    = user_answers.get(q, "?")
        correct = correct_answers.get(q, "?")
        ok      = (user == correct)
        if ok:
            score += 1
        else:
            wrong.append(q)
        emoji   = "✅" if ok else "❌"
        verdict = "Bonne reponse !" if ok else f"Mauvaise — bonne reponse : **{correct}**"
        q_text  = question_texts.get(q, f"Question {i}")
        lines.append(f"{emoji} **{q}.** {q_text}\n   Ta reponse : **{user}** — {verdict}")

    if score == 5:
        encouragement = " Parfait ! Score maximum !"
    elif score >= 4:
        encouragement = " Excellent, presque parfait !"
    elif score >= 3:
        encouragement = " Bien joue, encore un effort !"
    else:
        encouragement = " Relis la fiche et reessaie !"

    # Stocker pour ProgressAgent
    historique = callback_context.state.get("historique_scores", [])
    historique.append({"score": score, "total": 5, "wrong": wrong})
    callback_context.state["historique_scores"] = historique
    callback_context.state["score_actuel"]      = score
    callback_context.state["wrong_questions"]   = wrong

    result  = "##  Corrige\n\n"
    result += "\n\n".join(lines)
    result += f"\n\n---\n## 🏆 Score : {score}/5 — {encouragement}"

    print(f"\n[CORRECTEUR] Score : {score}/5 | Ratees : {wrong}")
    return LlmResponse(content=Content(parts=[Part(text=result)]))


def before_progress_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — construit le rapport en Python pur.
    Le LLM du ProgressAgent n'est JAMAIS appele.
    """
    historique   = callback_context.state.get("historique_scores", [])
    score_actuel = callback_context.state.get("score_actuel", 0)
    wrong        = callback_context.state.get("wrong_questions", [])

    print(f"\n[PROGRESS] historique={historique} | wrong={wrong}")

    rapport = "##  Progression\n\n"

    if len(historique) > 1:
        rapport += "**Historique :**\n"
        for i, s in enumerate(historique, 1):
            barre = "█" * s["score"] + "░" * (5 - s["score"])
            rapport += f"- Session {i} : [{barre}] {s['score']}/5\n"
        scores = [s["score"] for s in historique]
        if scores[-1] > scores[-2]:
            rapport += "\n En progression !\n\n"
        elif scores[-1] < scores[-2]:
            rapport += "\n En baisse — relis la fiche !\n\n"
        else:
            rapport += "\n Stable\n\n"
    elif len(historique) == 1:
        rapport += f"**Premiere session** — Score : {score_actuel}/5\n\n"
    else:
        rapport += "**Aucune correction effectuee.**\n\n"

    if wrong:
        rapport += f"**Questions a retravailler : {', '.join(wrong)}**\n\n"
        rapport += "Conseil : relis les points correspondants dans la fiche et refais le quiz.\n\n"
    elif len(historique) > 0:
        rapport += "**Toutes les questions reussies !**\n\n"
        rapport += "Bravo ! Tu maitrises ce sujet.\n\n"

    rapport += "---\n Tu veux : **A)** Une nouvelle fiche + quiz  ou  **B)** Refaire le quiz sur le meme sujet ?"

    print(f"\n[PROGRESS] rapport genere ({len(historique)} sessions)")
    return LlmResponse(content=Content(parts=[Part(text=rapport)]))


# AGENTS

flashcard_agent = LlmAgent(
    name="FlashcardAgent",
    model=MODEL,
    description="Genere une fiche de revision sur le sujet demande.",
    instruction="""
Tu es un expert pedagogique. Genere UNIQUEMENT une fiche de revision sur le sujet donne.
- Titre avec emoji
- 8 a 12 puces : definitions, concepts cles, erreurs
- Section "A retenir absolument" (3 points)
NE genere PAS de quiz.
""",
    output_key="flashcard_content",
    before_model_callback=before_flashcard_callback,
)

quiz_agent = LlmAgent(
    name="QuizAgent",
    model=MODEL,
    description="Genere un QCM de 5 questions.",
    instruction="""
Genere un QCM de 5 questions base sur la fiche precedente.

##  Quiz

**Q1.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q2.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q3.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q4.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q5.** [Question]
- A) ... - B) ... - C) ... - D) ...

---
Reponds : CORRECTION: Q1=?, Q2=?, Q3=?, Q4=?, Q5=?

NE montre PAS les reponses dans le quiz.
""",
    output_key="quiz_raw",
    before_model_callback=before_quiz_callback,
    after_agent_callback=after_quiz_callback,
)

correcteur_agent = LlmAgent(
    name="Correcteur",
    model=MODEL,
    description="Corrige les reponses.",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    instruction="Affiche le corrige.",
    output_key="correction_result",
    before_model_callback=before_correcteur_callback,
)

progress_agent = LlmAgent(
    name="ProgressAgent",
    model=MODEL,
    description="Affiche la progression.",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    instruction="Affiche la progression.",
    output_key="progress_result",
    before_model_callback=before_progress_callback,
)

# ConseilAgent via AgentTool (contrainte 5 du TP)
conseil_agent = LlmAgent(
    name="ConseilAgent",
    model=MODEL,
    description="Donne des conseils de methode de revision.",
    include_contents="none",
    instruction="""
Score : {score_actuel}/5 | Questions ratees : {wrong_summary}
Donne 3 conseils de methode d'apprentissage adaptes en 4 lignes max.
""",
    output_key="conseil_result",
)
conseil_tool = agent_tool.AgentTool(agent=conseil_agent)


# PIPELINES

# Pipeline complet : nouvelle fiche + quiz
flashcard_quiz_pipeline = SequentialAgent(
    name="FlashcardQuizPipeline",
    description="Genere la fiche puis le quiz.",
    sub_agents=[flashcard_agent, quiz_agent],
)

# Deuxieme instance du QuizAgent pour QuizOnlyPipeline
# (un agent ne peut avoir qu'un seul parent dans ADK)
quiz_agent_retry = LlmAgent(
    name="QuizAgentRetry",
    model=MODEL,
    description="Regenere un nouveau quiz sur la meme fiche.",
    instruction="""
Genere un QCM de 5 NOUVELLES questions base sur la fiche precedente.
Les questions doivent etre differentes du quiz precedent.

## Nouveau Quiz

**Q1.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q2.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q3.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q4.** [Question]
- A) ... - B) ... - C) ... - D) ...

**Q5.** [Question]
- A) ... - B) ... - C) ... - D) ...

---
Reponds : CORRECTION: Q1=?, Q2=?, Q3=?, Q4=?, Q5=?

NE montre PAS les reponses dans le quiz.
""",
    output_key="quiz_raw",
    before_model_callback=before_quiz_callback,
    after_agent_callback=after_quiz_callback,
)

# Pipeline quiz seul : refaire le quiz sur le meme sujet (sans regenerer la fiche)
quiz_only_pipeline = SequentialAgent(
    name="QuizOnlyPipeline",
    description="Regenere uniquement le quiz sur la fiche existante.",
    sub_agents=[quiz_agent_retry],
)

correction_pipeline = SequentialAgent(
    name="CorrectionPipeline",
    description="Corrige puis affiche la progression.",
    sub_agents=[correcteur_agent, progress_agent],
)


# ROOT AGENT

root_agent = LlmAgent(
    name="RevisionOrchestrator",
    model=MODEL,
    description="Orchestrateur principal.",
    instruction="Route vers FlashcardQuizPipeline ou CorrectionPipeline.",
    sub_agents=[flashcard_quiz_pipeline, quiz_only_pipeline, correction_pipeline],
    before_model_callback=root_router,
)