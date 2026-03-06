"""
main.py — Script programmatique pour lancer le système de révision.
Instancie Runner + InMemorySessionService (contrainte technique #7).

Usage :
    python main.py
    python main.py --topic "TCP/IP"
"""

import asyncio
import argparse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from my_agent.tools.my_tools import root_agent


APP_NAME = "revision_system"
USER_ID = "etudiant_01"


async def run_revision_session(initial_topic: str | None = None) -> None:
    """
    Lance une session interactive de révision.

    Crée une session ADK avec InMemorySessionService et un Runner,
    puis démarre une boucle de conversation interactive.

    Args:
        initial_topic: Sujet de révision optionnel pour démarrer directement.
    """
    # --- Initialisation du service de session en mémoire ---
    session_service = InMemorySessionService()

    # Créer la session avec un état initial
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state={
            "topic": initial_topic or "",
            "flashcard_content": "",
            "quiz_content": "",
            "user_answers": "",
            "correction_result": "",
            "score": None,
        },
    )

    # --- Instancier le Runner ---
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    print("\n" + "=" * 60)
    print("   🎓  SYSTÈME DE RÉVISION MULTI-AGENTS ADK")
    print("=" * 60)
    print("Commandes disponibles :")
    print("  • Donnez un sujet  → ex: 'révise-moi TCP/IP'")
    print("  • Répondez au quiz → ex: 'Q1=A, Q2=C, Q3=B, Q4=D, Q5=A'")
    print("  • 'quit' pour quitter")
    print("=" * 60 + "\n")

    # Démarrer avec un sujet si fourni en argument
    if initial_topic:
        first_message = f"Je veux réviser : {initial_topic}"
        print(f"[Vous] {first_message}")
        await _send_message(runner, session.id, first_message)

    # Boucle interactive
    while True:
        try:
            user_input = input("\n[Vous] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 À bientôt et bonne révision !")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("\n👋 À bientôt et bonne révision !")
            break

        await _send_message(runner, session.id, user_input)


async def _send_message(runner: Runner, session_id: str, message: str) -> None:
    """
    Envoie un message au runner et affiche la réponse en streaming.

    Args:
        runner: Instance du Runner ADK.
        session_id: Identifiant de la session courante.
        message: Message texte à envoyer à l'agent.
    """
    user_content = Content(
        role="user",
        parts=[Part(text=message)],
    )

    print("\n[Assistant] ", end="", flush=True)

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=user_content,
    ):
        if event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
            print()  # Saut de ligne final


def main():
    parser = argparse.ArgumentParser(
        description="Système de révision multi-agents Google ADK"
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Sujet de révision pour démarrer directement (ex: 'TCP/IP')",
    )
    args = parser.parse_args()

    asyncio.run(run_revision_session(initial_topic=args.topic))


if __name__ == "__main__":
    main()
