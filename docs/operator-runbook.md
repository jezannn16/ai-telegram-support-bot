# Operator runbook

## Start safely

1. Install the project in a dedicated environment and set `TELEGRAM_BOT_TOKEN` and `SUPPORT_OPERATORS` (numeric Telegram IDs) in the process environment. Never put tokens in this repository, tickets, or support messages.
2. Confirm the operator IDs and that SQLite is on a backed-up local volume. Start with `python3 telegram_bot.py --db support.sqlite3`.
3. Use `/tickets`, `/ticket <id>`, `/assign <id> <operator-id>`, and `/reply <id> <text>`. Every operator command checks the current allowlist.

## Customer data

Customer actions are scoped to the Telegram numeric user ID. `/export` writes the caller's tickets and messages to local JSON; `/delete_me CONFIRM` removes that caller's tickets and cascaded messages. Keep exports private and remove them when no longer needed. Deletion cannot remove messages already delivered through Telegram or copies in external backups.

## Delivery and recovery

Incoming update IDs and outgoing messages are stored in SQLite. Delivery follows at-most-once handling. A `failed` or `uncertain` outbox item is not resent automatically because a retry could duplicate a customer message. Check outbox state and ticket messages before manually resolving an uncertain delivery. Use `health --backup <path>` for a SQLite-consistent backup. Restore drills and automated retention are not implemented.

## Knowledge quality

Publish only reviewed, current documents. FAQ retrieval returns source excerpts when lexical overlap is strong enough; otherwise it opens or reuses a ticket. Retrieval does not assess policy meaning. Check citations and wording before relying on excerpts. Scanned PDF OCR is unsupported.
