# Operator runbook

## Start safely

1. Install the project in a dedicated environment and set `TELEGRAM_BOT_TOKEN` and `SUPPORT_OPERATORS` (numeric Telegram IDs) in the process environment. Never put tokens in this repository, tickets, or support messages.
2. Confirm the operator IDs and that SQLite is on a backed-up local volume. Start with `python3 telegram_bot.py --db support.sqlite3`.
3. Use `/tickets`, `/ticket <id>`, `/assign <id> <operator-id>`, and `/reply <id> <text>`. Every operator command checks the current allowlist.

## Customer data

Customer actions are scoped to the Telegram numeric user ID. The CLI `export USER FILE` writes tickets, messages, event history, and conversation data to JSON; `/delete_me CONFIRM` removes that caller's tickets, conversation, rate-limit record, queued chat responses, legacy raw update payloads, and cascaded messages. Keep exports private and remove them when no longer needed. Deletion cannot remove messages already delivered through Telegram or copies in external backups. Run `purge-closed --older-than-days N --confirm` periodically to delete old closed tickets while retaining open cases.

## Delivery and recovery

Incoming update IDs and outgoing messages are stored in SQLite. Delivery follows at-most-once handling. Inspect delivery state with `outbox OPERATOR_ID --operator`. A definite `failed` item can be queued once per operator action with `retry-delivery OPERATOR_ID ID --operator --confirm`; queued work sends on the next bot run. A `sending` item left by a crash becomes `uncertain` and must not be retried until delivery is checked because it may already have reached Telegram. Attempt counts are stored. Use `health --backup <path>` for a SQLite-consistent backup; the test suite runs a local backup/restore round trip.

## Knowledge quality

Publish only reviewed, current documents. FAQ retrieval returns source excerpts when lexical overlap is strong enough; otherwise it opens or reuses a ticket. Retrieval does not assess policy meaning. Check citations and wording before relying on excerpts. Scanned PDF OCR is unsupported.
