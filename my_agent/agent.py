"""
Systeme de revision multi-agents - Google ADK
(callbacks refactorisés — logique métier déléguée à my_tools.py)
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


# ── CALLBACKS ──────────────────────────────────────────────────────────────────

def before_flashcard_callback(callback_context: CallbackContext, **kwargs) -> None:
    """Initialise le state au début de chaque session."""
    s = callback_context.state
    s["correct_answers"]     = {}
    s["quiz_pret"]           = False
    s["score_actuel"]        = 0
    s["wrong_questions"]     = []
    s["rapport_progression"] = "Premiere session."
    s["wrong_summary"]       = "aucune"
    s["nb_sessions"]         = len(s.get("historique_scores", []))
    if "historique_scores" not in s:
        s["historique_scores"] = []
    print("\n[CALLBACK] State initialisé")


def before_quiz_callback(callback_context: CallbackContext, **kwargs) -> None:
    """
    Appelle le LLM séparément pour obtenir les réponses AVANT que le quiz soit
    généré — elles ne figureront jamais dans l'output visible.
    """
    callback_context.state["correct_answers"] = {}
    flashcard = callback_context.state.get("flashcard_content", "")
    if not flashcard:
        print("\n[before_quiz] pas de flashcard")
        return
    print("\n[before_quiz] extraction des réponses avant génération…")
    try:
        api_key = os.environ.get("GROQ_API_KEY", "")
        prompt = (
            "Tu vas créer un QCM de 5 questions sur ce contenu. "
            "Avant de le rédiger, indique les bonnes réponses. "
            "Réponds UNIQUEMENT avec ce format exact, rien d'autre :\n"
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
            timeout=10,
        )
        text = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"\n[before_quiz] LLM: '{text}'")
        m = re.search(
            r"Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])",
            text, re.IGNORECASE,
        )
        if m:
            answers = {f"Q{i+1}": m.group(i+1).upper() for i in range(5)}
            callback_context.state["correct_answers"] = answers
            print(f"\n[before_quiz] Réponses : {answers}")
        else:
            print(f"\n[before_quiz] Pattern non trouvé: '{text}'")
    except Exception as e:
        print(f"\n[before_quiz] Erreur : {e}")

def before_progress_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — construit le rapport de progression en Python pur
    et appelle ConseilAgent via AgentTool avant de retourner la réponse.
    """
    historique   = callback_context.state.get("historique_scores", [])
    score_actuel = callback_context.state.get("score_actuel", 0)
    wrong        = callback_context.state.get("wrong_questions", [])

    print(f"\n[PROGRESS] historique={historique} | wrong={wrong}")

    rapport = "## Progression\n\n"

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
        rapport += f"**Première session** — Score : {score_actuel}/5\n\n"
    else:
        rapport += "**Aucune correction effectuée.**\n\n"

    if wrong:
        rapport += f"**Questions à retravailler : {', '.join(wrong)}**\n\n"
        rapport += "Conseil : relis les points correspondants dans la fiche et refais le quiz.\n\n"
    elif historique:
        rapport += "**Toutes les questions réussies !**\n\n"
        rapport += "Bravo ! Tu maîtrises ce sujet.\n\n"

    rapport += "---\nTu veux : **A)** Une nouvelle fiche + quiz  ou  **B)** Refaire le quiz sur le même sujet ?"

    # Stocke pour que ConseilAgent puisse utiliser ces valeurs
    callback_context.state["rapport_progression"] = rapport
    callback_context.state["wrong_summary"] = ", ".join(wrong) if wrong else "aucune"

    print(f"\n[PROGRESS] rapport généré ({len(historique)} sessions)")
    return LlmResponse(content=Content(parts=[Part(text=rapport)]))

def after_quiz_callback(callback_context: CallbackContext) -> None:
    """
    Extrait les bonnes réponses depuis quiz_raw via `sauvegarder_reponses_correctes`.
    """
    quiz_raw = callback_context.state.get("quiz_raw", "")
    print(f"\n[after_quiz] {len(quiz_raw)} chars")

    # ── délégation à my_tools ──
    answers, clean = sauvegarder_reponses_correctes(quiz_raw)

    if answers:
        callback_context.state["correct_answers"] = answers
        callback_context.state["quiz_raw"]        = clean
        print(f"\n[after_quiz] Réponses : {answers}")
    else:
        print(f"\n[after_quiz] ÉCHEC TOTAL")

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
                    if hasattr(part, "text") and part.text:
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
                args={"agent_name": agent_name},
            ))])
        )
    except Exception as e:
        print(f"\n[ROOT ROUTER] Erreur : {e}")
        return None


def before_correcteur_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — calcule le score en Python pur via `calculer_score`
    et persiste via `enregistrer_reponses`.
    Le LLM du Correcteur n'est JAMAIS appelé.
    """
    correct_answers = callback_context.state.get("correct_answers", {})
    quiz_raw        = callback_context.state.get("quiz_raw", "")
    user_msg        = callback_context.state.get("last_user_message", "")

    print(f"\n[CORRECTEUR] correct={correct_answers} | user='{user_msg[:50]}'")

    # ── délégation à my_tools ──
    result = calculer_score(user_msg, correct_answers, quiz_raw)

    if not result["valid"]:
        return LlmResponse(content=Content(parts=[Part(text=result["error_message"])]))

    # Persistance dans le state
    enregistrer_reponses(callback_context.state, result["score"], result["wrong"])

    # Construction du message de sortie
    output  = "## Corrigé\n\n"
    output += "\n\n".join(result["lines"])
    output += f"\n\n---\n## 🏆 Score : {result['score']}/5 — {result['encouragement']}"

    print(f"\n[CORRECTEUR] Score : {result['score']}/5 | Ratées : {result['wrong']}")
    return LlmResponse(content=Content(parts=[Part(text=output)]))


def before_progress_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    before_model_callback — construit le rapport + appelle ConseilAgent via AgentTool.
    Le LLM du ProgressAgent n'est JAMAIS appelé.
    """
    historique   = callback_context.state.get("historique_scores", [])
    score_actuel = callback_context.state.get("score_actuel", 0)
    wrong        = callback_context.state.get("wrong_questions", [])

    print(f"\n[PROGRESS] historique={historique} | wrong={wrong}")

    rapport = "## Progression\n\n"

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
        rapport += f"**Première session** — Score : {score_actuel}/5\n\n"
    else:
        rapport += "**Aucune correction effectuée.**\n\n"

    if wrong:
        rapport += f"**Questions à retravailler : {', '.join(wrong)}**\n\n"
        rapport += "Conseil : relis les points correspondants dans la fiche et refais le quiz.\n\n"
    elif historique:
        rapport += "**Toutes les questions réussies !**\n\n"
        rapport += "Bravo ! Tu maîtrises ce sujet.\n\n"

    rapport += "---\nTu veux : **A)** Une nouvelle fiche + quiz  ou  **B)** Refaire le quiz sur le même sujet ?"

    # Stocke pour que ConseilAgent puisse utiliser ces valeurs via {variables}
    callback_context.state["rapport_progression"] = rapport
    callback_context.state["wrong_summary"] = ", ".join(wrong) if wrong else "aucune"

    print(f"\n[PROGRESS] rapport généré ({len(historique)} sessions)")
    return LlmResponse(content=Content(parts=[Part(text=rapport)]))


# ── AGENTS ─────────────────────────────────────────────────────────────────────

flashcard_agent = LlmAgent(
    name="FlashcardAgent",
    model=MODEL,
    description="Génère une fiche de révision sur le sujet demandé.",
    instruction="""
Tu es un expert pédagogique. Génère UNIQUEMENT une fiche de révision sur le sujet donné.
- Titre avec emoji
- 8 à 12 puces : définitions, concepts clés, erreurs
- Section "À retenir absolument" (3 points)
NE génère PAS de quiz.
""",
    output_key="flashcard_content",
    before_model_callback=before_flashcard_callback,
)

quiz_agent = LlmAgent(
    name="QuizAgent",
    model=MODEL,
    description="Génère un QCM de 5 questions.",
    instruction="""
Génère un QCM de 5 questions basé sur la fiche précédente sans afficher les réponses.

## Quiz

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
Réponds : CORRECTION: Q1=?, Q2=?, Q3=?, Q4=?, Q5=?

NE montre PAS les réponses dans le quiz.
""",
    output_key="quiz_raw",
    before_model_callback=before_quiz_callback,
    after_agent_callback=after_quiz_callback,
)

correcteur_agent = LlmAgent(
    name="Correcteur",
    model=MODEL,
    description="Corrige les réponses.",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    instruction="Affiche le corrigé.",
    output_key="correction_result",
    before_model_callback=before_correcteur_callback,
)

# ConseilAgent via AgentTool — défini AVANT progress_agent
conseil_agent = LlmAgent(
    name="ConseilAgent",
    model=MODEL,
    description="Donne des conseils de méthode de révision.",
    include_contents="none",
    instruction="""
Score : {score_actuel}/5 | Questions ratées : {wrong_summary}
Donne 3 conseils de méthode d'apprentissage adaptés en 4 lignes max.
""",
    output_key="conseil_result",
)
conseil_tool = agent_tool.AgentTool(agent=conseil_agent)

progress_agent = LlmAgent(
    name="ProgressAgent",
    model=MODEL,
    description="Affiche la progression et des conseils de révision.",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    instruction="Affiche la progression.",
    output_key="progress_result",
    before_model_callback=before_progress_callback,
    tools=[conseil_tool],
)

# ConseilAgent via AgentTool
conseil_agent = LlmAgent(
    name="ConseilAgent",
    model=MODEL,
    description="Donne des conseils de méthode de révision.",
    include_contents="none",
    instruction="""
Score : {score_actuel}/5 | Questions ratées : {wrong_summary}
Donne 3 conseils de méthode d'apprentissage adaptés en 4 lignes max.
""",
    output_key="conseil_result",
)
conseil_tool = agent_tool.AgentTool(agent=conseil_agent)


# ── PIPELINES ──────────────────────────────────────────────────────────────────

flashcard_quiz_pipeline = SequentialAgent(
    name="FlashcardQuizPipeline",
    description="Génère la fiche puis le quiz.",
    sub_agents=[flashcard_agent, quiz_agent],
)

quiz_agent_retry = LlmAgent(
    name="QuizAgentRetry",
    model=MODEL,
    description="Régénère un nouveau quiz sur la même fiche.",
    instruction="""
Génère un QCM de 5 NOUVELLES questions basé sur la fiche précédente.
Les questions doivent être différentes du quiz précédent et ne pas révéler les réponses.

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
Réponds : CORRECTION: Q1=?, Q2=?, Q3=?, Q4=?, Q5=?

NE montre PAS les réponses dans le quiz.
""",
    output_key="quiz_raw",
    before_model_callback=before_quiz_callback,
    after_agent_callback=after_quiz_callback,
)

quiz_only_pipeline = SequentialAgent(
    name="QuizOnlyPipeline",
    description="Régénère uniquement le quiz sur la fiche existante.",
    sub_agents=[quiz_agent_retry],
)

correction_pipeline = SequentialAgent(
    name="CorrectionPipeline",
    description="Corrige puis affiche la progression.",
    sub_agents=[correcteur_agent, progress_agent],
)


# ── ROOT AGENT ─────────────────────────────────────────────────────────────────

root_agent = LlmAgent(
    name="RevisionOrchestrator",
    model=MODEL,
    description="Orchestrateur principal.",
    instruction="Route vers FlashcardQuizPipeline ou CorrectionPipeline.",
    sub_agents=[flashcard_quiz_pipeline, quiz_only_pipeline, correction_pipeline],
    before_model_callback=root_router,
)