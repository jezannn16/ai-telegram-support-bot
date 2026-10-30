import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import connect
from telegram_bot import handle_update
from telegram_runtime import process_update

class FakeAPI:
 def __init__(self):self.sent=[]
 def send_message(self,chat,text):self.sent.append((str(chat),text))
class FailingAPI:
 def send_message(self,chat,text):
  from telegram_api import TelegramAPIError
  raise TelegramAPIError('synthetic failure')
def update(uid,chat,text):return {'update_id':uid,'message':{'from':{'id':chat},'chat':{'id':chat,'type':'private'},'text':text}}

class SupportTelegramTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=connect(Path(self.tmp.name)/'support.sqlite');self.api=FakeAPI()
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_faq_citation_no_evidence_handoff_and_cross_user_denial(self):
  self.db.execute('insert into documents(title,status,content,updated_at) values(?,?,?,?)',('Returns','published','Unopened products can be returned within thirty days.','now'))
  out=handle_update(update(1,101,'How can I return unopened products?'),self.db);self.assertIn('[Returns v1',out[0][1])
  out=handle_update(update(2,101,'Can you guarantee delivery tomorrow?'),self.db);self.assertIn('Ticket #1',out[0][1])
  out=handle_update(update(3,202,'/ticket 1'),self.db);self.assertIn('Ticket not found',out[0][1])
  self.assertNotIn('Internal summary',out[0][1])
 def test_operator_acl_assignment_and_reply_notification_dedupe(self):
  with patch.dict(os.environ,{'SUPPORT_OPERATORS':'900,901'}):
   process_update(self.db,self.api,handle_update,update(1,101,'question without answer'))
   self.assertIn('Operator access denied',handle_update(update(2,101,'/tickets'),self.db)[0][1])
   self.db.commit()
   process_update(self.db,self.api,handle_update,update(3,900,'/assign 1 900'))
   reply=update(4,900,'/reply 1 Please send the order number.')
   self.assertTrue(process_update(self.db,self.api,handle_update,reply));self.assertFalse(process_update(self.db,self.api,handle_update,reply))
   self.assertTrue(any(chat=='101' and 'Please send' in text for chat,text in self.api.sent))
   public=handle_update(update(5,101,'/ticket 1'),self.db)[0][1]
   self.assertNotIn('Internal summary',public);self.assertIn('Please send',public)
   denied=handle_update(update(6,901,'/reply 1 duplicate attempt'),self.db)[0][1]
   self.assertIn('assigned to another operator',denied)
 def test_migrations_and_delete_scope(self):
  self.assertEqual([r[0] for r in self.db.execute('select version from schema_migrations order by version')],[1,2,3,4,5])
  process_update(self.db,self.api,handle_update,update(1,101,'unknown'))
  self.db.execute('INSERT INTO updates(update_id,created_at,payload,processed_at) VALUES(?,?,?,?)',('legacy-101','now','{"message":{"from":{"id":101},"chat":{"id":101}}}','now'))
  self.db.execute('INSERT INTO updates(update_id,created_at,payload,processed_at) VALUES(?,?,?,?)',('legacy-202','now','{"message":{"from":{"id":202},"chat":{"id":202}}}','now'));self.db.commit()
  out=handle_update(update(2,101,'/delete_me CONFIRM'),self.db);self.assertIn('deleted',out[0][1])
  self.assertEqual(self.db.execute('select count(*) from tickets').fetchone()[0],0)
  self.assertEqual(self.db.execute('select count(*) from user_rate_limits where user_id="101"').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telegram_outbox WHERE chat_id="101"').fetchone()[0],0)
  self.assertIsNone(self.db.execute('SELECT update_id FROM updates WHERE update_id="legacy-101"').fetchone())
  self.assertIsNotNone(self.db.execute('SELECT update_id FROM updates WHERE update_id="legacy-202"').fetchone())
 def test_delivery_failure_is_visible_and_only_failed_can_be_requeued(self):
  process_update(self.db,FailingAPI(),handle_update,update(70,101,'synthetic question without FAQ'))
  row=self.db.execute('SELECT id,status FROM telegram_outbox ORDER BY id LIMIT 1').fetchone()
  self.assertEqual(row['status'],'failed')
  self.assertEqual(self.db.execute('SELECT attempt_count FROM telegram_outbox WHERE id=?',(row['id'],)).fetchone()[0],1)
  from app import requeue_failed_delivery
  from telegram_runtime import flush_outbox
  self.assertTrue(requeue_failed_delivery(self.db,row['id']))
  self.assertEqual(self.db.execute('SELECT status FROM telegram_outbox WHERE id=?',(row['id'],)).fetchone()[0],'queued')
  flush_outbox(self.db,self.api)
  self.assertEqual(self.db.execute('SELECT status,attempt_count FROM telegram_outbox WHERE id=?',(row['id'],)).fetchone()['status'],'sent')
  self.assertEqual(self.db.execute('SELECT attempt_count FROM telegram_outbox WHERE id=?',(row['id'],)).fetchone()[0],2)
  self.db.execute("UPDATE telegram_outbox SET status='uncertain' WHERE id=?",(row['id'],));self.db.commit()
  self.assertFalse(requeue_failed_delivery(self.db,row['id']))
  self.assertEqual(self.db.execute('SELECT status FROM telegram_outbox WHERE id=?',(row['id'],)).fetchone()[0],'uncertain')
 def test_outbox_claim_lost_to_another_worker_does_not_send_twice(self):
  from telegram_runtime import flush_outbox
  self.db.execute("INSERT INTO telegram_outbox(update_id,sequence,chat_id,text,created_at,status) VALUES(?,?,?,?,?,'queued')",(91,0,'101','synthetic response','now'));self.db.commit()
  class RacingDB:
   raced=False
   def commit(inner):self.db.commit()
   def execute(inner,sql,args=()):
    result=self.db.execute(sql,args)
    if sql.startswith('SELECT * FROM telegram_outbox') and not inner.raced:
     inner.raced=True;self.db.execute("UPDATE telegram_outbox SET status='sent' WHERE update_id=91");self.db.commit()
    return result
  flush_outbox(RacingDB(),self.api)
  self.assertEqual(self.api.sent,[])
  self.assertEqual(self.db.execute('SELECT attempt_count FROM telegram_outbox WHERE update_id=91').fetchone()[0],0)
 def test_user_erasure_remains_atomic_with_update_processing(self):
  from app import delete_user_data,create_ticket
  create_ticket('101','synthetic retained if transaction rolls back',[],self.db)
  def failing_handler(update,db):
   delete_user_data(db,'101')
   raise RuntimeError('synthetic post-erasure failure')
  with self.assertRaises(RuntimeError):process_update(self.db,self.api,failing_handler,update(99,101,'/delete_me CONFIRM'))
  self.assertIsNotNone(self.db.execute('SELECT id FROM tickets WHERE user_id="101"').fetchone())
if __name__=='__main__':unittest.main()
