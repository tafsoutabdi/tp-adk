"""
main.py — Runner programmatique (contrainte 7)
Lance le système de révision via Runner + InMemorySessionService.
"""

import asyncio
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from my_agent.agent import root_agent

APP_NAME = "revision_agent"
USER_ID = "etudiant_01"


async def main():
    # Instancier le service de session en mémoire
    session_service = InMemorySessionService()

    # Créer une session
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
    )
    session_id = session.id
    print(f"\n Session créée : {session_id}\n")

    # Instancier le Runner
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    async def envoyer_message(texte: str):
        """Envoie un message et affiche la réponse."""
        print(f"\n{'='*60}")
        print(f"Utilisateur : {texte}")
        print(f"{'='*60}")

        contenu = Content(parts=[Part(text=texte)])
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=contenu,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        print(f"\n {event.author} :\n{part.text}")

    # Scénario de démonstration
    await envoyer_message("Flutter")
    await envoyer_message("CORRECTION: Q1=A, Q2=A, Q3=A, Q4=A, Q5=A")
    await envoyer_message("Python")
    await envoyer_message("CORRECTION: Q1=B, Q2=B, Q3=B, Q4=B, Q5=B")


if __name__ == "__main__":
    asyncio.run(main())