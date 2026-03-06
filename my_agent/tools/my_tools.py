"""Outils custom pour le système de révision."""

import re
import json
from google.adk.tools import ToolContext


def calculer_score(reponses_utilisateur: str, tool_context: ToolContext) -> dict:
    """
    Compare les réponses utilisateur aux bonnes réponses stockées dans le state.

    Args:
        reponses_utilisateur: Réponses au format "Q1=A, Q2=B, Q3=C, Q4=D, Q5=A".

    Returns:
        dict: Score, détail par question, message d'encouragement.
    """
    correct_answers = tool_context.state.get("correct_answers", {})
    if not correct_answers:
        return {"status": "error", "message": "Bonnes réponses introuvables dans le state."}

    matches = re.findall(r"Q(\d+)\s*=\s*([A-Da-d])", reponses_utilisateur, re.IGNORECASE)
    if not matches:
        return {"status": "error", "message": "Format invalide. Utilisez Q1=A, Q2=B..."}

    user_answers = {f"Q{n}": l.upper() for n, l in matches}
    score = 0
    detail = {}

    for q, user_ans in user_answers.items():
        correct = correct_answers.get(q, "?")
        is_correct = user_ans == correct
        if is_correct:
            score += 1
        detail[q] = {"user": user_ans, "correct": correct, "ok": is_correct}

    total = len(user_answers)
    tool_context.state["score"] = score

    if score == total:
        msg = " Parfait ! Score maximum !"
    elif score >= total * 0.8:
        msg = " Excellent, presque parfait !"
    elif score >= total * 0.6:
        msg = " Bien joué, encore un effort !"
    else:
        msg = " Relis la fiche et réessaie !"

    print(f"[TOOL calculer_score] {score}/{total} — {detail}")
    return {"status": "ok", "score": score, "total": total, "detail": detail, "message": msg}


def enregistrer_reponses(user_answers: str, tool_context: ToolContext) -> dict:
    """
    Parse et enregistre les réponses utilisateur dans le state.

    Args:
        user_answers: Réponses au format "Q1=A, Q2=B, Q3=C, Q4=D, Q5=A".

    Returns:
        dict: Réponses parsées et nombre total.
    """
    matches = re.findall(r"Q(\d+)\s*=\s*([A-Da-d])", user_answers, re.IGNORECASE)
    if not matches:
        return {"status": "error", "message": "Format invalide. Utilisez Q1=A, Q2=B..."}

    parsed = {f"Q{n}": l.upper() for n, l in matches}
    tool_context.state["user_answers"] = parsed
    print(f"[TOOL enregistrer_reponses] {parsed}")
    return {"status": "ok", "reponses": parsed, "total": len(parsed)}


def sauvegarder_reponses_correctes(reponses_json: str, tool_context: ToolContext) -> dict:
    """
    Sauvegarde les bonnes réponses du quiz dans le state partagé.

    Args:
        reponses_json: JSON string des bonnes réponses. Ex: '{"Q1":"C","Q2":"A","Q3":"B","Q4":"D","Q5":"A"}'

    Returns:
        dict: Confirmation de la sauvegarde.
    """
    try:
        reponses = json.loads(reponses_json)
        if not isinstance(reponses, dict):
            return {"status": "error", "message": "Format JSON invalide, attendu un objet."}
        tool_context.state["correct_answers"] = reponses
        print(f"[TOOL sauvegarder_reponses_correctes] {reponses}")
        return {"status": "ok", "sauvegarde": reponses}
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON invalide : {str(e)}"}


def obtenir_progression(tool_context: ToolContext) -> dict:
    """
    Récupère l'historique complet des scores depuis le state.

    Returns:
        dict: Historique des sessions, score moyen, tendance.
    """
    historique = tool_context.state.get("historique_scores", [])
    if not historique:
        return {"status": "ok", "message": "Aucune session enregistrée.", "historique": []}

    scores = [s["score"] for s in historique]
    moyenne = sum(scores) / len(scores)

    if len(scores) > 1:
        if scores[-1] > scores[-2]:
            tendance = "progression"
        elif scores[-1] < scores[-2]:
            tendance = "régression"
        else:
            tendance = "stable"
    else:
        tendance = "première session"

    print(f"[TOOL obtenir_progression] {historique}")
    return {
        "status": "ok",
        "nb_sessions": len(historique),
        "historique": historique,
        "moyenne": round(moyenne, 1),
        "tendance": tendance,
    }