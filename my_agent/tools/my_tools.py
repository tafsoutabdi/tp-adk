"""
my_tools.py — Outils métier du système de révision multi-agents.

Trois outils purs (sans dépendance ADK) utilisés par les callbacks :
  - sauvegarder_reponses_correctes : extrait les bonnes réponses depuis quiz_raw
  - calculer_score                 : compare réponses user vs correctes, retourne le détail
  - enregistrer_reponses           : met à jour l'historique des scores dans le state
"""

import re
import os
import requests


# 1. Extraction des bonnes réponses depuis le texte brut du quiz

def sauvegarder_reponses_correctes(quiz_raw: str) -> dict:
    """
    Parcourt quiz_raw avec plusieurs patterns regex pour trouver les 5 bonnes
    réponses. En dernier recours, interroge l'API Groq avec temperature=0.

    Args:
        quiz_raw: texte brut retourné par le QuizAgent.

    Returns:
        dict  {"Q1": "A", "Q2": "C", ...}  (vide si échec total)
        tuple (answers, cleaned_quiz_raw)   — le texte nettoyé retire les
              lignes contenant les réponses pour ne pas les afficher à l'user.
    """
    answers: dict[str, str] = {}
    clean = quiz_raw

    # -- Pattern 1 : REPONSES_CACHEES: Q1=C Q2=B Q3=B Q4=C Q5=B
    p1 = re.search(
        r'REPONSES_CACHEES\s*:\s*Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])',
        quiz_raw, re.IGNORECASE
    )
    if p1:
        answers = {f"Q{i+1}": p1.group(i+1).upper() for i in range(5)}
        clean = re.sub(r'[^\n]*REPONSES_CACHEES[^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
        clean = re.sub(r'[^\n]*Ligne\s+1[^\n]*\n?', '', clean, flags=re.IGNORECASE)
        return answers, clean.strip()

    # -- Pattern 2 : Q1=C, Q2=B, Q3=B sur une même ligne
    if not answers:
        p2 = re.search(
            r'Q1\s*=\s*([A-D])[,\s]+Q2\s*=\s*([A-D])[,\s]+Q3\s*=\s*([A-D])[,\s]+'
            r'Q4\s*=\s*([A-D])[,\s]+Q5\s*=\s*([A-D])',
            quiz_raw, re.IGNORECASE
        )
        if p2:
            answers = {f"Q{i+1}": p2.group(i+1).upper() for i in range(5)}
            clean = re.sub(r'[^\n]*Q1\s*=\s*[A-D][^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
            return answers, clean.strip()

    # -- Pattern 3 : "Réponse : X" ou "Réponse correcte : X" après chaque question
    if not answers:
        reps = re.findall(r'R[ée]ponse\s*(?:correcte)?\s*:\s*([A-D])', quiz_raw, re.IGNORECASE)
        if len(reps) == 5:
            answers = {f"Q{i+1}": r.upper() for i, r in enumerate(reps)}
            clean = re.sub(r'[^\n]*R[ée]ponse[^\n]*\n?', '', quiz_raw, flags=re.IGNORECASE)
            return answers, clean.strip()

    # -- Fallback : appel LLM Groq dédié (temperature=0)
    if not answers:
        print("[my_tools] Aucun pattern trouvé — fallback LLM Groq")
        try:
            api_key = os.environ.get("GROQ_API_KEY", "")
            prompt = (
                "Lis ce quiz et identifie la bonne réponse pour chaque question.\n"
                "Réponds UNIQUEMENT avec ce format exact, rien d'autre :\n"
                "Q1=X Q2=X Q3=X Q4=X Q5=X\n\n"
                f"Quiz :\n{quiz_raw}"
            )
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 30,
                    "temperature": 0,
                },
                timeout=10,
            )
            text = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"[my_tools] Fallback LLM : '{text}'")
            m = re.search(
                r'Q1=([A-D])\s+Q2=([A-D])\s+Q3=([A-D])\s+Q4=([A-D])\s+Q5=([A-D])',
                text, re.IGNORECASE,
            )
            if m:
                answers = {f"Q{i+1}": m.group(i+1).upper() for i in range(5)}
                return answers, quiz_raw  # pas de nettoyage nécessaire ici
        except Exception as e:
            print(f"[my_tools] Erreur fallback : {e}")

    print("[my_tools] ÉCHEC TOTAL — aucune réponse extraite")
    return answers, quiz_raw


# 2. Calcul du score

def calculer_score(
    user_message: str,
    correct_answers: dict,
    quiz_raw: str,
) -> dict:
    """
    Parse les réponses de l'utilisateur, les compare aux bonnes réponses et
    construit le détail ligne par ligne.

    Args:
        user_message:     message brut de l'user (ex. "CORRECTION: Q1=A, Q2=B …")
        correct_answers:  dict {"Q1": "C", …} issu de sauvegarder_reponses_correctes
        quiz_raw:         texte du quiz pour extraire l'intitulé des questions

    Returns:
        {
            "valid":         bool,    # False si format invalide ou pas de quiz en cours
            "error_message": str,     # rempli si valid=False
            "score":         int,
            "wrong":         list[str],
            "lines":         list[str],   # une ligne de feedback par question
            "encouragement": str,
        }
    """
    if not correct_answers:
        return {
            "valid": False,
            "error_message": "Aucun quiz en cours. Envoie un sujet pour commencer.",
        }

    matches = re.findall(r'Q(\d+)\s*=\s*([A-Da-d])', user_message, re.IGNORECASE)
    if not matches:
        return {
            "valid": False,
            "error_message": (
                "Format invalide. Utilise : CORRECTION: Q1=A, Q2=B, Q3=C, Q4=D, Q5=A"
            ),
        }

    user_answers = {f"Q{n}": l.upper() for n, l in matches}

    # Intitulés des questions pour un feedback plus lisible
    question_texts: dict[str, str] = {}
    for num, text in re.findall(
        r'\*\*Q(\d+)\.\*\*\s*(.+?)(?=\n-|\n\*|\Z)', quiz_raw, re.DOTALL
    ):
        question_texts[f"Q{num}"] = text.strip()

    score = 0
    wrong: list[str] = []
    lines: list[str] = []

    for i in range(1, 6):
        q       = f"Q{i}"
        user    = user_answers.get(q, "?")
        correct = correct_answers.get(q, "?")
        ok      = user == correct
        if ok:
            score += 1
        else:
            wrong.append(q)
        emoji   = "✅" if ok else "❌"
        verdict = "Bonne réponse !" if ok else f"Mauvaise — bonne réponse : **{correct}**"
        q_text  = question_texts.get(q, f"Question {i}")
        lines.append(f"{emoji} **{q}.** {q_text}\n   Ta réponse : **{user}** — {verdict}")

    if score == 5:
        encouragement = "Parfait ! Score maximum !"
    elif score >= 4:
        encouragement = "Excellent, presque parfait !"
    elif score >= 3:
        encouragement = "Bien joué, encore un effort !"
    else:
        encouragement = "Relis la fiche et réessaie !"

    return {
        "valid":         True,
        "error_message": "",
        "score":         score,
        "wrong":         wrong,
        "lines":         lines,
        "encouragement": encouragement,
    }


# 3. Mise à jour de l'historique dans le state ADK

def enregistrer_reponses(state: dict, score: int, wrong: list[str]) -> dict:
    """
    Pousse un nouveau résultat dans l'historique et met à jour les clés de
    suivi dans le state ADK.

    Args:
        state: callback_context.state (dict mutable)
        score: score obtenu (int, 0-5)
        wrong: liste des questions ratées (ex. ["Q2", "Q4"])

    Returns:
        Le state mis à jour (la mutation est in-place, le retour est pratique
        pour les tests unitaires).
    """
    historique: list[dict] = state.get("historique_scores", [])
    historique.append({"score": score, "total": 5, "wrong": wrong})

    state["historique_scores"] = historique
    state["score_actuel"]      = score
    state["wrong_questions"]   = wrong

    print(f"[my_tools] Historique mis à jour : {len(historique)} session(s), dernier score {score}/5")
    return state