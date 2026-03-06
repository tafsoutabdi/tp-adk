"""
main.py — Runner programmatique (contrainte 7)
Lance le système de révision via Runner + InMemorySessionService.
"""

import asyncio
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from my_agent.agent import root_agent
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

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

    async def envoyer_message(texte: str, pause: int = 15):
        """Envoie un message, affiche la réponse, puis attend `pause` secondes
        pour éviter le rate limit Groq (6 000 TPM sur le plan gratuit)."""
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

        if pause > 0:
            print(f"\n Pause {pause}s (rate limit Groq)…")
            await asyncio.sleep(pause)

    # Scénario de démonstration
    await envoyer_message("Flutter")
    await envoyer_message("CORRECTION: Q1=A, Q2=A, Q3=A, Q4=A, Q5=A")
    await envoyer_message("Python")
    await envoyer_message("CORRECTION: Q1=B, Q2=B, Q3=B, Q4=B, Q5=B", pause=0)


if __name__ == "__main__":
    asyncio.run(main())