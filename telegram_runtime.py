"""Persistent update journal and at-most-once Telegram response delivery."""
from __future__ import annotations
import logging,time
from datetime import datetime,timezone
from telegram_api import TelegramAPI,TelegramAPIError
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')

def flush_outbox(db,api):
 rows=db.execute("SELECT * FROM telegram_outbox WHERE status='queued' ORDER BY id").fetchall()
 for row in rows:
  db.execute("UPDATE telegram_outbox SET status='sending' WHERE id=? AND status='queued'",(row['id'],));db.commit()
  try:api.send_message(row['chat_id'],row['text'])
  except TelegramAPIError:
   db.execute("UPDATE telegram_outbox SET status='failed' WHERE id=?",(row['id'],));db.commit();logging.warning('Telegram delivery failed; response retained in local outbox')
  else:
   db.execute("UPDATE telegram_outbox SET status='sent',sent_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),row['id']));db.commit()

def process_update(db,api,handler,update):
 uid=int(update.get('update_id',-1))
 if uid<0:return False
 db.execute('BEGIN IMMEDIATE')
 if db.execute('SELECT 1 FROM bot_updates WHERE update_id=?',(uid,)).fetchone():
  db.rollback();return False
 try:responses=handler(update,db)
 except Exception:
  db.rollback();raise
 stamp=datetime.now(timezone.utc).isoformat()
 db.execute('INSERT INTO bot_updates(update_id,processed_at) VALUES(?,?)',(uid,stamp))
 for seq,(chat_id,text) in enumerate(responses):db.execute('INSERT INTO telegram_outbox(update_id,sequence,chat_id,text,created_at) VALUES(?,?,?,?,?)',(uid,seq,str(chat_id),str(text),stamp))
 db.commit();flush_outbox(db,api);return True

def run_polling(db,api,handler):
 db.execute("UPDATE telegram_outbox SET status='uncertain' WHERE status='sending'");db.commit()
 flush_outbox(db,api)
 row=db.execute('SELECT COALESCE(MAX(update_id),0) FROM bot_updates').fetchone();offset=int(row[0])+1
 while True:
  try:updates=api.get_updates(offset)
  except TelegramAPIError:
   logging.warning('Telegram polling failed; retrying with bounded delay');time.sleep(3);continue
  for update in updates:
   uid=int(update.get('update_id',-1))
   if uid<0:continue
   try:process_update(db,api,handler,update)
   except Exception:
    logging.error('Support update handler failed; update will be retried');break
   offset=max(offset,uid+1)

def start(db_path,handler):
 import os
 token=os.environ.get('TELEGRAM_BOT_TOKEN','')
 api=TelegramAPI(token)
 from app import connect
 db=connect(db_path)
 logging.info('Support bot polling started')
 try:run_polling(db,api,handler)
 finally:db.close()
