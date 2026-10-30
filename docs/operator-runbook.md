# Operator runbook

## Start safely

1. Install the project in a dedicated environment and set `TELEGRAM_BOT_TOKEN` and `SUPPORT_OPERATORS` (numeric Telegram IDs) in the process environment. Never put tokens in this repository, tickets, or support messages.
2. Confirm the operator IDs and that SQLite is on a backed-up local volume. Start with `python3 telegram_bot.py --db support.sqlite3`.
3. Use `/tickets`, `/ticket <id>`, `/assign <id> <operator-id>`, and `/reply <id> <text>`. Every operator command checks the current allowlist.

## Customer data

Customer actions are scoped to the Telegram numeric user ID. The CLI `export USER FILE` writes tickets, messages, event history, and conversation data to JSON; `/delete_me CONFIRM` removes that caller's tickets, conversation, rate-limit record, queued chat responses, legacy raw update payloads, and cascaded messages. Keep exports private and remove them when no longer needed. Deletion cannot remove messages already delivered through Telegram or copies in external backups. Run `purge-closed --older-than-days N --confirm` periodically to delete old closed tickets while retaining open cases.

## Delivery and recovery

Incoming update IDs and outgoing messages are stored in SQLite. Delivery follows at-most-once handling. Inspect delivery state with `outbox OPERATOR_ID --operator`. A definite `failed` item can be queued once per operator action with `retry-delivery OPERATOR_ID ID --operator --confirm`; queued work sends on the next bot run. A `sending` item left by a crash becomes `uncertain` and must not be retried until delivery is checked because it may already have reached Telegram. Attempt counts are stored.

Create a verified owner-only backup at a new filename with `python3 app.py --db support.sqlite3 health --backup /safe/path/support-backup-2026-10-30.sqlite3`. Existing backup paths and symlinks are refused. To restore, stop polling and every other process using the database, verify the selected backup path, then run `python3 app.py --db support.sqlite3 restore /safe/path/support-backup-2026-10-30.sqlite3 --confirm`. The command checks full SQLite integrity, foreign keys, migration version, and required columns. It rejects symlinked database/sidecar paths and unrelated destination databases; SQLite's backup API handles existing WAL databases. A changed destination aborts before restore, and missing destinations are created atomically without overwriting a path created concurrently. Restore replaces current records; exports/backups may retain deleted customer data, so protect and age them out separately.

## Knowledge quality

Publish only reviewed, current documents. FAQ retrieval returns source excerpts when lexical overlap is strong enough; otherwise it opens or reuses a ticket. Operator ticket view includes a bounded extractive handoff summary with recent dialogue and retrieved source excerpts; it does not infer intent or policy meaning. Check citations and wording before relying on excerpts. Scanned PDF OCR is unsupported.
