"""
Système de révision multi-agents - Google ADK
"""

import re
import json
from google.adk.agents import LlmAgent, SequentialAgent, LoopAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai.types import Content, Part, FunctionCall, FunctionResponse
from my_agent.tools.my_tools import calculer_score, enregistrer_reponses, sauvegarder_reponses_correctes

MODEL = "groq/llama-3.1-8b-instant"


# CALLBACKS

def before_flashcard_callback(callback_context: CallbackContext, **kwargs) -> None:
    callback_context.state["correct_answers"] = {}
    callback_context.state["quiz_pret"] = False
    print("\n[CALLBACK] 📚 Génération de la fiche en cours...")


def after_quiz_callback(callback_context: CallbackContext) -> None:
    quiz_raw = callback_context.state.get("quiz_raw", "")
    print(f"\n[CALLBACK after_quiz] {len(quiz_raw)} chars")
    pattern = r'Q1\s*=\s*([A-Da-d]).*?Q2\s*=\s*([A-Da-d]).*?Q3\s*=\s*([A-Da-d]).*?Q4\s*=\s*([A-Da-d]).*?Q5\s*=\s*([A-Da-d])'
    match = re.search(pattern, quiz_raw, re.IGNORECASE | re.DOTALL)
    if match:
        answers = {f"Q{i+1}": match.group(i+1).upper() for i in range(5)}
        callback_context.state["correct_answers"] = answers
        print(f"\n[CALLBACK after_quiz]  {answers}")
    else:
        print(f"\n[CALLBACK after_quiz]  non trouvé:\n{quiz_raw[-200:]}")
    callback_context.state["quiz_pret"] = True


def root_router(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    Bypass complet du LLM pour le routing.
    Retourne directement un LlmResponse avec le transfer_to_agent.
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
        print(f"\n[ROOT ROUTER] '{msg[:50]}'")

        # Router directement via FunctionCall ADK natif
        if msg.upper().startswith("CORRECTION:"):
            agent_name = "Correcteur"
        else:
            agent_name = "FlashcardQuizPipeline"

        print(f"\n[ROOT ROUTER] → {agent_name}")
        return LlmResponse(
            content=Content(parts=[
                Part(function_call=FunctionCall(
                    name="transfer_to_agent",
                    args={"agent_name": agent_name}
                ))
            ])
        )
    except Exception as e:
        print(f"\n[ROOT ROUTER] Erreur : {e}")
        return None


def before_correcteur_callback(callback_context: CallbackContext, **kwargs) -> LlmResponse | None:
    """
    Calcule le score en Python et retourne le corrigé directement.
    Le LLM n'est jamais appelé.
    """
    correct_answers = callback_context.state.get("correct_answers", {})
    quiz_raw = callback_context.state.get("quiz_raw", "")
    user_msg = callback_context.state.get("last_user_message", "")

    print(f"\n[CORRECTEUR] correct={correct_answers}, user={user_msg[:50]}")

    if not correct_answers or not user_msg:
        return None

    matches = re.findall(r'Q(\d+)\s*=\s*([A-Da-d])', user_msg, re.IGNORECASE)
    if not matches:
        return None

    user_answers = {f"Q{n}": l.upper() for n, l in matches}

    # Extraire les questions depuis quiz_raw
    question_texts = {}
    q_pattern = re.findall(r'\*\*Q(\d+)\.\*\*\s*(.+?)(?=\n-|\n\*)', quiz_raw, re.DOTALL)
    for num, text in q_pattern:
        question_texts[f"Q{num}"] = text.strip()

    score = 0
    lines = []
    for i in range(1, 6):
        q = f"Q{i}"
        user = user_answers.get(q, "?")
        correct = correct_answers.get(q, "?")
        ok = user == correct
        if ok:
            score += 1
        verdict = "Bonne réponse !" if ok else f" Mauvaise — Bonne réponse : **{correct}**"
        q_text = question_texts.get(q, f"Question {i}")
        lines.append(f"**{q}.** {q_text}\n- Ta réponse : **{user}** — {verdict}")

    if score == 5:
        encouragement = "Parfait ! Score maximum !"
    elif score >= 4:
        encouragement = "Excellent, presque parfait !"
    elif score >= 3:
        encouragement = "Bien joué, encore un effort !"
    else:
        encouragement = "Relis la fiche et réessaie !"

    result = "## Corrigé\n\n" + "\n\n".join(lines)
    result += f"\n\n---\n## Score : {score}/5\n{encouragement}"

    print(f"\n[CORRECTEUR] Score : {score}/5")
    return LlmResponse(content=Content(parts=[Part(text=result)]))


# AGENTS

flashcard_agent = LlmAgent(
    name="FlashcardAgent",
    model=MODEL,
    description="Génère une fiche de révision sur le sujet demandé.",
    instruction="""
Tu es un expert pédagogique. Génère UNIQUEMENT une fiche de révision sur le sujet donné.

Format :
- Titre avec emoji
- 8 à 12 puces : définitions, concepts clés, mécanismes, erreurs 
- Section " À retenir absolument" (3 points)

NE génère PAS de quiz. NE pose PAS de questions.
""",
    output_key="flashcard_content",
    before_model_callback=before_flashcard_callback,
)

quiz_agent = LlmAgent(
    name="QuizAgent",
    model=MODEL,
    description="Génère un QCM de 5 questions.",
    instruction="""
Tu génères un QCM de 5 questions basé sur la fiche de révision précédente sans donner les réponses.

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
 **Réponds au format : CORRECTION: Q1=B, Q2=C, Q3=A, Q4=D, Q5=B**

IMPORTANT : remplace les lettres par les VRAIES bonnes réponses de TON quiz.
""",
    output_key="quiz_raw",
    after_agent_callback=after_quiz_callback,
)

correcteur_agent = LlmAgent(
    name="Correcteur",
    model=MODEL,
    description="Corrige les réponses de l'utilisateur.",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    instruction="Affiche le corrigé.",
    output_key="correction_result",
    before_model_callback=before_correcteur_callback,
)

flashcard_quiz_pipeline = SequentialAgent(
    name="FlashcardQuizPipeline",
    description="Génère la fiche puis le quiz.",
    sub_agents=[flashcard_agent, quiz_agent],
)

root_agent = LlmAgent(
    name="RevisionOrchestrator",
    model=MODEL,
    description="Assistant de révision scolaire.",
    instruction="Route vers FlashcardQuizPipeline ou Correcteur.",
    sub_agents=[flashcard_quiz_pipeline, correcteur_agent],
    before_model_callback=root_router,
)