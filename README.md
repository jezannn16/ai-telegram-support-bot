# AI Telegram Support Bot

A local support workflow with FAQ retrieval, source excerpts and ticket/operator handoff. Text FAQ and ticket behavior runs without credentials. Telegram polling is an optional adapter; no bot is started automatically. LLM calls are not implemented, so document text is treated only as searchable data.

## Install

Requires Python 3.11 or newer. Create an isolated environment and install this repository (including the PDF text parser):

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

Use `.venv/bin/python` for the commands below.

## Local CLI

```sh
.venv/bin/python app.py faq-add 'Returns policy' fixtures/faq.md --publish
.venv/bin/python app.py ask customer-1 'How do I return an unopened product?'
.venv/bin/python app.py ask customer-1 'Can you guarantee delivery tomorrow?'
SUPPORT_OPERATORS=agent-1 .venv/bin/python app.py assign agent-1 1 agent-1
SUPPORT_OPERATORS=agent-1 .venv/bin/python app.py reply agent-1 1 'An operator reviewed this request.'
.venv/bin/python app.py ticket customer-1 1
.venv/bin/python app.py --db support.sqlite3 eval --report evaluation-report.json
.venv/bin/python app.py --db support.sqlite3 health --backup support-backup.sqlite3
```

Markdown/TXT knowledge files work with the Python standard library. PDF text extraction uses `pypdf`; install the project dependencies using the installation step above. Supported files are capped at 10 MB, 500 PDF pages and 50,000 extracted characters. Scanned PDFs without embedded text are not OCR-processed. Citations identify document version and paragraph or PDF page. Draft and retired documents are excluded.

## Telegram bot

Set `TELEGRAM_BOT_TOKEN` and `SUPPORT_OPERATORS` (comma-separated Telegram numeric IDs) in the process environment. Keep the token out of chat, source files and logs. Then run:

```sh
.venv/bin/python telegram_bot.py --db support.sqlite3
```

Customers can ask questions, list/view/close their own tickets, request an operator, export their messages or explicitly delete their history. Operators can list tickets, inspect internal summaries and citations, assign tickets, reply, close or reopen. Replies are sent to the customer only after the operator issues `/reply`. Operator permissions are checked from the allowlist on every command; customer ticket access is checked by Telegram user ID in database queries. The bot accepts private chats only.

Telegram updates are durably deduplicated. Outgoing replies are written to a SQLite outbox before sending. Delivery uses at-most-once handling: failures are retained and not retried automatically; a crash during send can leave an `uncertain` state that needs manual review. No message is sent until the bot is explicitly run with a configured token and an operator issues a reply command.

## Tests and evaluation

```sh
.venv/bin/python -m unittest discover -v
.venv/bin/python app.py --db support.sqlite3 eval --report evaluation-report.json
```

The evaluation uses 34 synthetic cases covering published evidence, partial evidence, conflicting sources, draft/retired content, prompt-injection wording, cross-customer data and out-of-scope requests. It reports behavior matching, citation structure and refusal/conflict counts. It does not claim general model accuracy; unsupported-claim rate is left for human review because no generated prose/LLM is used.

## Implemented and limitations

Implemented: published FAQ retrieval and citations, refusals, ticket state/events, duplicate open-question prevention, customer isolation, operator allowlist/assignment, ticket replies, export/delete, versioned SQLite migrations, health/backup and synthetic evaluation. PDF extraction is text-only.

Planned: OCR, a restore drill, automated retention, rate limiting, webhook mode, richer operator search/assignment dashboards and an LLM/RAG provider. Telegram delivery requires configured credentials and has not been tested against a real Telegram account. There is no public deployment.
