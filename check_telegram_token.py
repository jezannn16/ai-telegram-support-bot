"""Opt-in, no-send check for a Telegram bot token."""
from __future__ import annotations

import os
import sys

from telegram_api import TelegramAPI, TelegramAPIError


def main() -> int:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        print('Set TELEGRAM_BOT_TOKEN in your local environment first.', file=sys.stderr)
        return 2
    try:
        bot = TelegramAPI(token).get_me()
    except (ValueError, TelegramAPIError):
        print('Telegram token check failed. Verify the token and network, then retry.', file=sys.stderr)
        return 1
    username = bot.get('username')
    print(f"Token is valid for @{username}." if username else 'Token is valid for a Telegram bot.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
