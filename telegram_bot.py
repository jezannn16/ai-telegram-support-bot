"""Telegram customer and operator workflows with persisted, scoped ticket access."""
from __future__ import annotations
import json
from telegram_runtime import start
from app import MAX_TEXT,allow_request,create_ticket,delete_user_data,now,operator,retrieve,summarize_ticket

def _event(db,actor,event,ticket_id,details=''):
 db.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(actor,event,ticket_id,now()))
 db.execute('INSERT INTO ticket_events(ticket_id,actor,event,details,created_at) VALUES(?,?,?,?,?)',(ticket_id,actor,event,details[:500],now()))
def _ticket(db,tid,user=None):
 if user is None:return db.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
 return db.execute('SELECT * FROM tickets WHERE id=? AND user_id=?',(tid,user)).fetchone()
def _my_tickets(db,user):return db.execute('SELECT id,status,question,created_at FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 20',(user,)).fetchall()
def _messages(db,tid):return db.execute('SELECT author,body,created_at FROM messages WHERE ticket_id=? ORDER BY id',(tid,)).fetchall()
def _conversation(db,user):
 stamp=now();db.execute('INSERT INTO conversations(user_id,created_at,updated_at,last_message) VALUES(?,?,?,\'\') ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at',(user,stamp,stamp))

def handle_update(update,db):
 message=update.get('message') or {};sender=message.get('from') or {};chat=message.get('chat') or {};user=str(sender.get('id',''));chat_id=chat.get('id');text=str(message.get('text','')).strip()
 if not user or chat.get('type')!='private' or not text:return []
 if len(text)>MAX_TEXT:return [(chat_id,'Please keep the message under 4000 characters.')]
 if not allow_request(db,user):return [(chat_id,'You are sending messages too quickly. Please wait a minute and try again.')]
 if chat_id is None:return []
 _conversation(db,user);db.execute('UPDATE conversations SET last_message=? WHERE user_id=?',(text[:MAX_TEXT],user))
 parts=text.split(maxsplit=1);command=parts[0].split('@',1)[0].lower();arg=parts[1].strip() if len(parts)>1 else ''
 is_operator=user in operator()
 if command in ('/start','/help'):
  return [(chat_id,'Support commands: ask a question, /my_tickets, /ticket ID, /operator [message], /close ID, /reopen ID, /export, /delete_me CONFIRM. Published FAQ answers include their source. For operators, /tickets, /assign ID, /reply ID TEXT, /close ID, /reopen ID.')]
 if command in ('/my_tickets','/tickets'):
  if command=='/tickets' and not is_operator:return [(chat_id,'Operator access denied.')]
  rows=db.execute('SELECT id,user_id,status,question,created_at FROM tickets '+('ORDER BY id DESC LIMIT 50' if command=='/tickets' else 'WHERE user_id=? ORDER BY id DESC LIMIT 20'),() if command=='/tickets' else (user,)).fetchall()
  return [(chat_id,'\n'.join(f"#{r['id']} [{r['status']}] user={r['user_id']} {r['question'][:100]}" for r in rows) or 'No tickets.')]
 if command=='/ticket':
  try:tid=int(arg)
  except ValueError:return [(chat_id,'Usage: /ticket TICKET_ID')]
  ticket=_ticket(db,tid,None if is_operator else user)
  if ticket is None:return [(chat_id,'Ticket not found.')]
  lines=[f"Ticket #{tid} — {ticket['status']}",f"Question: {ticket['question']}"]
  if is_operator:
   lines.append('Internal handoff summary (extractive):\n'+summarize_ticket(db,tid))
   ass=db.execute('SELECT operator_id FROM operator_assignments WHERE ticket_id=?',(tid,)).fetchone()
   lines.append(f"Assigned: {ass['operator_id'] if ass else 'unassigned'}")
   citations=json.loads(ticket['citations']);lines.extend('Source: '+c.get('document','')+' '+c.get('excerpt','')[:300] for c in citations)
  lines.extend(f"{m['author']}: {m['body']}" for m in _messages(db,tid))
  return [(chat_id,'\n'.join(lines)[:3900])]
 if command=='/operator':
  question=arg or db.execute('SELECT last_message FROM conversations WHERE user_id=?',(user,)).fetchone()['last_message']
  if not question:return [(chat_id,'Send /operator MESSAGE to create a handoff ticket.')]
  tid,created=create_ticket(user,question,[],db,commit=False)
  if created:_event(db,user,'handoff.requested',tid)
  return [(chat_id,f'Operator handoff ticket #{tid} is '+('open.' if created else 'already open.'))]
 if command in ('/close','/reopen'):
  try:tid=int(arg)
  except ValueError:return [(chat_id,f'Usage: {command} TICKET_ID')]
  ticket=_ticket(db,tid,None if is_operator else user)
  if ticket is None:return [(chat_id,'Ticket not found.')]
  status='closed' if command=='/close' else 'open'
  if ticket['status']==status:return [(chat_id,f'Ticket is already {status}.')]
  db.execute('UPDATE tickets SET status=?,updated_at=? WHERE id=?',(status,now(),tid));_event(db,user,'ticket.'+status,tid);return [(chat_id,f'Ticket #{tid} {status}.')]
 if command=='/assign':
  if not is_operator:return [(chat_id,'Operator access denied.')]
  bits=arg.split()
  if len(bits)!=2:return [(chat_id,'Usage: /assign TICKET_ID OPERATOR_TELEGRAM_ID')]
  try:tid=int(bits[0])
  except ValueError:return [(chat_id,'Ticket ID is invalid.')]
  if bits[1] not in operator():return [(chat_id,'Assignee must be in SUPPORT_OPERATORS.')]
  ticket=_ticket(db,tid)
  if ticket is None or ticket['status']!='open':return [(chat_id,'Open ticket not found.')]
  db.execute('INSERT INTO operator_assignments(ticket_id,operator_id,assigned_at,assigned_by) VALUES(?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET operator_id=excluded.operator_id,assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by',(tid,bits[1],now(),user));_event(db,user,'operator.assigned',tid,bits[1]);return [(chat_id,f'Ticket #{tid} assigned to {bits[1]}.')]
 if command=='/reply':
  if not is_operator:return [(chat_id,'Operator access denied.')]
  bits=arg.split(maxsplit=1)
  if len(bits)!=2 or not bits[1]:return [(chat_id,'Usage: /reply TICKET_ID TEXT')]
  try:tid=int(bits[0])
  except ValueError:return [(chat_id,'Ticket ID is invalid.')]
  ticket=_ticket(db,tid)
  if ticket is None or ticket['status']!='open':return [(chat_id,'Open ticket not found.')]
  assigned=db.execute('SELECT operator_id FROM operator_assignments WHERE ticket_id=?',(tid,)).fetchone()
  if assigned and assigned['operator_id']!=user:return [(chat_id,'Ticket is assigned to another operator.')]
  cur=db.execute('INSERT OR IGNORE INTO messages(ticket_id,author,body,created_at) VALUES(?,?,?,?)',(tid,user,bits[1],now()))
  if not cur.rowcount:return [(chat_id,'This reply is already recorded; no duplicate notification was sent.')]
  db.execute('UPDATE tickets SET updated_at=? WHERE id=?',(now(),tid));_event(db,user,'operator.replied',tid)
  return [(chat_id,'Reply recorded; delivery is queued and its status is tracked.'),(ticket['user_id'],f'Operator reply on ticket #{tid}: {bits[1]}')]
 if command=='/export':
  tickets=_my_tickets(db,user);data=[]
  for t in tickets:data.append({'ticket':dict(t),'messages':[dict(m) for m in _messages(db,t['id'])]})
  return [(chat_id,json.dumps(data,ensure_ascii=False,indent=2)[:3900] or 'No data.')]
 if command=='/delete_me':
  if arg!='CONFIRM':return [(chat_id,'To delete your local support history, send /delete_me CONFIRM.')]
  delete_user_data(db,user);return [(chat_id,'Your support history was deleted.')]
 if command.startswith('/') and command not in ('/faq',):return [(chat_id,'Unknown command. Send /help.')]
 question=arg if command=='/faq' else text
 if not question:return [(chat_id,'Please include a question after /faq.')]
 hits=retrieve(question,db)
 if hits:
  from llm_adapter import answer_from_hits,configured_transport,render_citations
  transport=configured_transport()
  result=answer_from_hits(question,hits,transport) if transport else None
  if result and result.status=='answered':
   citations=render_citations(result,hits)
   answer_lines=[result.answer]
   answer_lines.extend(f"[{c['document']} v{c['version']} {c['location']}] {c['excerpt'][:500]}" for c in citations)
   return [(chat_id,'\n\n'.join(answer_lines)[:3900])]
  if result and result.status=='handoff':
   citations=[{'document':h[1]['title'],'version':h[1]['version'],'location':h[5],'excerpt':h[3][:500]} for h in hits]
   tid,created=create_ticket(user,question,citations,db,commit=False)
   if created:_event(db,user,'ticket.created',tid)
   return [(chat_id,f"I couldn't safely answer this from the published sources. Ticket #{tid} is open for operator follow-up." if created else f'This request is already tracked in open ticket #{tid}.')]
  lines=['Published knowledge base evidence (review this source excerpt):']
  for h in hits:
   excerpt=h[3][:700];citation=f"[{h[1]['title']} v{h[1]['version']} {h[5]}]"
   lines.append(f'{citation} {excerpt}')
  return [(chat_id,'\n\n'.join(lines)[:3900])]
 tid,created=create_ticket(user,question,[],db,commit=False)
 if created:_event(db,user,'ticket.created',tid)
 return [(chat_id,f"I couldn't find enough published evidence. Ticket #{tid} is open for operator follow-up." if created else f'This request is already tracked in open ticket #{tid}.')]

def start_bot(db_path):start(db_path,handle_update)
def main():
 import argparse
 parser=argparse.ArgumentParser(description='Support bot Telegram polling');parser.add_argument('--db',default='support.sqlite3');start_bot(parser.parse_args().db)
if __name__=='__main__':main()
