from __future__ import annotations

import asyncio

from app.agi_server import start_agi_server


def main() -> None:
    asyncio.run(start_agi_server())


if __name__ == "__main__":
    main()
