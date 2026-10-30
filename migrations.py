"""Versioned local SQLite migrations for knowledge base and ticket state."""
from __future__ import annotations
import sqlite3
from datetime import datetime,timezone

def _v1(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,title TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL CHECK(status IN ('draft','published','retired')),content TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY,user_id TEXT NOT NULL,question TEXT NOT NULL,internal_summary TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('open','closed')),citations TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,author TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(ticket_id,author,body));
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,event TEXT NOT NULL,ticket_id INTEGER,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS updates(update_id TEXT PRIMARY KEY,created_at TEXT NOT NULL);''')
def _v2(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS operator_assignments(ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,operator_id TEXT NOT NULL,assigned_at TEXT NOT NULL,assigned_by TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY,user_id TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS ticket_events(id INTEGER PRIMARY KEY,ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,actor TEXT NOT NULL,event TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
 CREATE UNIQUE INDEX IF NOT EXISTS one_open_question_per_user ON tickets(user_id,question) WHERE status='open';''')
 # Upgrade the original update journal with durable payload/status fields.
 cols={r[1] for r in db.execute('PRAGMA table_info(updates)')}
 if 'payload' not in cols: db.execute('ALTER TABLE updates ADD COLUMN payload TEXT NOT NULL DEFAULT \'{}\'')
 if 'processed_at' not in cols: db.execute('ALTER TABLE updates ADD COLUMN processed_at TEXT')

def _v3(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS bot_updates(update_id INTEGER PRIMARY KEY,processed_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS telegram_outbox(id INTEGER PRIMARY KEY,update_id INTEGER NOT NULL,sequence INTEGER NOT NULL,chat_id TEXT NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',created_at TEXT NOT NULL,sent_at TEXT,UNIQUE(update_id,sequence));''')
 cols={r[1] for r in db.execute('PRAGMA table_info(conversations)')}
 if 'last_message' not in cols:db.execute("ALTER TABLE conversations ADD COLUMN last_message TEXT NOT NULL DEFAULT ''")
def _v4(db):
 db.execute('CREATE TABLE IF NOT EXISTS user_rate_limits(user_id TEXT PRIMARY KEY,window_started_at REAL NOT NULL,request_count INTEGER NOT NULL)')
def _v5(db):
 cols={r[1] for r in db.execute('PRAGMA table_info(telegram_outbox)')}
 if 'attempt_count' not in cols:db.execute('ALTER TABLE telegram_outbox ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0')
def connect(path):
 db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=5000')
 db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
 current=db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0]
 for version,migration in enumerate((_v1,_v2,_v3,_v4,_v5),1):
  if version>current:
   migration(db);db.execute('INSERT INTO schema_migrations VALUES(?,?)',(version,datetime.now(timezone.utc).isoformat()));db.commit()
 return db
