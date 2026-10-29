# Demo

```sh
python3 app.py --db /tmp/support-demo.sqlite3 faq-add 'Returns policy' fixtures/faq.md --publish
python3 app.py --db /tmp/support-demo.sqlite3 ask customer-1 'How long do I have to return an unopened product?'
python3 app.py --db /tmp/support-demo.sqlite3 ask customer-1 'Can you guarantee delivery tomorrow?'
SUPPORT_OPERATORS=agent-1 python3 app.py --db /tmp/support-demo.sqlite3 tickets agent-1 --operator
SUPPORT_OPERATORS=agent-1 python3 app.py --db /tmp/support-demo.sqlite3 assign agent-1 1 agent-1
SUPPORT_OPERATORS=agent-1 python3 app.py --db /tmp/support-demo.sqlite3 reply agent-1 1 'An operator reviewed this request.'
python3 app.py --db /tmp/support-demo.sqlite3 ticket customer-1 1
```

The FAQ response includes its source excerpt. The unsupported request opens a ticket. Operator replies are saved only; no notification is sent.
