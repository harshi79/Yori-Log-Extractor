#!/usr/bin/env python3
"""Start the Yori Log Extractor Telegram bot.

    cp .env.example .env      # then fill in the two values
    python run.py --check     # verify the token and the API key
    python run.py             # start long polling
"""

from __future__ import annotations

from bot.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
