"""Text (and optional mic) simulator for the parent-marks call flow."""

from __future__ import annotations

import argparse
import sys

from app.conversation import create_session


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a parent marks phone call in text")
    parser.add_argument("--phone", default="+919876543210", help="Parent phone on the CSV row")
    parser.add_argument("--no-llm", action="store_true", help="Disable Ollama (rules + templates only)")
    args = parser.parse_args()

    session = create_session(args.phone)
    turn = session.greeting()
    print(f"AI: {turn.reply}")
    if turn.hangup:
        return

    while True:
        try:
            uttered = input("Parent: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAI: Goodbye.")
            return
        if not uttered:
            continue
        result = session.handle_text(uttered, use_llm=not args.no_llm)
        print(f"AI: {result.reply}")
        if result.hangup:
            return


if __name__ == "__main__":
    main()
    sys.exit(0)
