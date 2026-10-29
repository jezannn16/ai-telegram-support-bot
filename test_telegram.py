import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import connect
from telegram_bot import handle_update
from telegram_runtime import process_update

class FakeAPI:
 def __init__(self):self.sent=[]
 def send_message(self,chat,text):self.sent.append((str(chat),text))
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
  self.assertEqual([r[0] for r in self.db.execute('select version from schema_migrations order by version')],[1,2,3])
  process_update(self.db,self.api,handle_update,update(1,101,'unknown'))
  out=handle_update(update(2,101,'/delete_me CONFIRM'),self.db);self.assertIn('deleted',out[0][1])
  self.assertEqual(self.db.execute('select count(*) from tickets').fetchone()[0],0)
if __name__=='__main__':unittest.main()
