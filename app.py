"""Support FAQ/ticket service; live Telegram access is isolated in telegram_bot.py."""
from __future__ import annotations
import argparse,json,os,re,sqlite3,tempfile
import time
from migrations import connect as connect_db
from datetime import datetime,timedelta,timezone
from pathlib import Path
DB=Path(__file__).with_name('support.sqlite3'); MAX_TEXT=4000; MAX_DOC=50000
def connect(path=DB):
 return connect_db(path)

def load_document(path:Path)->str:
 if path.stat().st_size>10_000_000:raise ValueError('Knowledge file exceeds 10 MB')
 if path.suffix.lower() in {'.md','.txt'}:content=path.read_text(encoding='utf-8')
 elif path.suffix.lower()=='.pdf':
  try:
   from pypdf import PdfReader
   reader=PdfReader(str(path),strict=False)
   if reader.is_encrypted or len(reader.pages)>500:raise ValueError('Encrypted PDFs or documents over 500 pages are not supported')
   content='\f'.join((page.extract_text() or '') for page in reader.pages)+'\f'
  except ImportError:raise ValueError('Install pypdf from requirements.txt to import PDF files') from None
  except ValueError:raise
  except Exception:raise ValueError('PDF could not be parsed; check that it is a readable text PDF') from None
 else:raise ValueError('Supported knowledge files: Markdown, TXT, PDF')
 if len(content)>MAX_DOC:raise ValueError('Extracted document text exceeds 50,000 characters')
 return content

def now():return datetime.now(timezone.utc).isoformat()
def allow_request(db,user,now_ts=None,limit=10,window_seconds=60):
 """Persist a simple per-user Telegram request window; caller owns transaction."""
 current=time.time() if now_ts is None else now_ts
 row=db.execute('SELECT window_started_at,request_count FROM user_rate_limits WHERE user_id=?',(user,)).fetchone()
 if row is None or current-row['window_started_at']>=window_seconds or current<row['window_started_at']:
  db.execute('INSERT INTO user_rate_limits(user_id,window_started_at,request_count) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET window_started_at=excluded.window_started_at,request_count=1',(user,current))
  return True
 if row['request_count']>=limit:return False
 db.execute('UPDATE user_rate_limits SET request_count=request_count+1 WHERE user_id=?',(user,))
 return True
def requeue_failed_delivery(db,delivery_id):
 cur=db.execute("UPDATE telegram_outbox SET status='queued' WHERE id=? AND status='failed'",(delivery_id,))
 db.commit()
 return cur.rowcount==1
def purge_closed(db,older_than_days,reference=None):
 if older_than_days<1 or older_than_days>36500:raise ValueError('Retention period must be between 1 and 36500 days')
 reference=reference or datetime.now(timezone.utc)
 cutoff=(reference-timedelta(days=older_than_days)).isoformat()
 ids=[r[0] for r in db.execute("SELECT id FROM tickets WHERE status='closed' AND updated_at<?",(cutoff,))]
 db.executemany('DELETE FROM audit WHERE ticket_id=?',[(tid,) for tid in ids])
 db.executemany('DELETE FROM tickets WHERE id=?',[(tid,) for tid in ids])
 db.commit()
 return len(ids)
def delete_user_data(db,user):
 ids=[r[0] for r in db.execute('SELECT id FROM tickets WHERE user_id=?',(user,))]
 db.execute('DELETE FROM audit WHERE actor=?',(user,))
 db.executemany('DELETE FROM audit WHERE ticket_id=?',[(tid,) for tid in ids])
 db.executemany('DELETE FROM tickets WHERE id=?',[(tid,) for tid in ids])
 db.execute('DELETE FROM conversations WHERE user_id=?',(user,))
 db.execute('DELETE FROM user_rate_limits WHERE user_id=?',(user,))
 # Private Telegram chat IDs equal user IDs. Clear queued/recent responses with PII.
 db.execute('DELETE FROM telegram_outbox WHERE chat_id=?',(str(user),))
 # Older update-journal versions retained complete Telegram payloads.
 for row in db.execute('SELECT update_id,payload FROM updates').fetchall():
  try:payload=json.loads(row['payload'])
  except (TypeError,ValueError):continue
  message=payload.get('message') or payload.get('edited_message') or {}
  sender=message.get('from') or {}
  chat=message.get('chat') or {}
  if str(sender.get('id',''))==str(user) or str(chat.get('id',''))==str(user):
   db.execute('DELETE FROM updates WHERE update_id=?',(row['update_id'],))
 return len(ids)
STOPWORDS={'the','is','are','what','does','about','and','for','from','you','can','how','to','a','an','of','in','on','do','say','tell','me','please','our','your','with'}
def tokens(s):return set(re.findall(r'[\wА-Яа-яЁё]{3,}',s.lower()))-STOPWORDS
def retrieve(question,d):
 q=tokens(question)
 if not q:return []
 docs=d.execute("SELECT id,title,version,content FROM documents WHERE status='published' ORDER BY id").fetchall(); results=[]
 for doc in docs:
  if '\f' in doc['content']:
   chunks=[(f'page {n+1}',part.strip()) for n,part in enumerate(doc['content'].split('\f')) if part.strip()]
  else:chunks=[(f'paragraph {n+1}',part.strip()) for n,part in enumerate(re.split(r'\n\s*\n',doc['content'])) if part.strip()]
  for n,(location,chunk) in enumerate(chunks):
   terms=tokens(chunk); overlap=q&terms
   # BM25-like normalized overlap; lexical baseline only, never invokes content as instructions.
   score=len(overlap)/max(1,len(q))
   if len(overlap)>=2 and score>=0.25:results.append((score,doc,n,chunk.strip(),sorted(overlap),location))
 results.sort(key=lambda x:(x[0],len(x[4])),reverse=True)
 if not results:return []
 best=results[0][0]
 # Suppress weak, generic-word overlaps so unrelated FAQs do not appear as citations.
 return [hit for hit in results if hit[0]>=best*0.85][:3]
def create_ticket(user,q,citations,d,commit=True):
 existing=d.execute('SELECT id FROM tickets WHERE user_id=? AND question=? AND status="open"',(user,q[:MAX_TEXT])).fetchone()
 if existing:return int(existing['id']),False
 cur=d.execute('INSERT INTO tickets(user_id,question,internal_summary,status,citations,created_at,updated_at) VALUES(?,?,? ,"open",?,?,?)',(user,q[:MAX_TEXT],f'Customer asked: {q[:500]}',json.dumps(citations),now(),now())); tid=cur.lastrowid
 d.execute('INSERT OR IGNORE INTO messages(ticket_id,author,body,created_at) VALUES(?,?,?,?)',(tid,user,q,now()));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(user,'ticket.created',tid,now()));d.execute('INSERT INTO ticket_events(ticket_id,actor,event,created_at) VALUES(?,?,?,?)',(tid,user,'created',now()))
 if commit:d.commit()
 return tid,True
def summarize_ticket(db,ticket_id):
 """Build a private, bounded extractive handoff summary from recorded dialogue."""
 ticket=db.execute('SELECT user_id,question,citations FROM tickets WHERE id=?',(ticket_id,)).fetchone()
 if ticket is None:raise ValueError('Ticket not found')
 messages=db.execute('SELECT author,body FROM messages WHERE ticket_id=? ORDER BY id DESC LIMIT 4',(ticket_id,)).fetchall()
 lines=[f"Customer question: {ticket['question'][:400]}"]
 for msg in reversed(messages):
  if msg['body']!=ticket['question']:
   role='Operator reply' if msg['author']!=ticket['user_id'] else 'Customer update'
   lines.append(f"{role}: {msg['body'][:240]}")
 try:citations=json.loads(ticket['citations'])
 except (TypeError,ValueError):citations=[]
 for citation in citations[:3]:
  title=str(citation.get('document','')).strip()
  excerpt=str(citation.get('excerpt','')).strip()
  if title:lines.append(f"Source {title}: {excerpt[:180]}")
 return '\n'.join(lines)[:1200]
def operator():return [x.strip() for x in os.environ.get('SUPPORT_OPERATORS','').split(',') if x.strip()]
def require_operator(actor,p):
 if actor not in operator() or actor=='':p.error('Operator access denied; configure SUPPORT_OPERATORS with operator IDs')
def main():
 p=argparse.ArgumentParser(description='Offline support FAQ and ticket workflow');p.add_argument('--db',type=Path,default=DB);s=p.add_subparsers(dest='cmd',required=True)
 a=s.add_parser('faq-add');a.add_argument('title');a.add_argument('file',type=Path);a.add_argument('--publish',action='store_true')
 a=s.add_parser('faq-publish');a.add_argument('doc_id',type=int)
 a=s.add_parser('faq-retire');a.add_argument('doc_id',type=int)
 a=s.add_parser('ask');a.add_argument('user');a.add_argument('question')
 a=s.add_parser('tickets');a.add_argument('actor');a.add_argument('--operator',action='store_true')
 a=s.add_parser('ticket');a.add_argument('actor');a.add_argument('ticket_id',type=int);a.add_argument('--operator',action='store_true')
 a=s.add_parser('reply');a.add_argument('actor');a.add_argument('ticket_id',type=int);a.add_argument('text')
 a=s.add_parser('assign');a.add_argument('actor');a.add_argument('ticket_id',type=int);a.add_argument('operator_id')
 a=s.add_parser('close');a.add_argument('actor');a.add_argument('ticket_id',type=int);a.add_argument('--reopen',action='store_true');a.add_argument('--operator',action='store_true')
 a=s.add_parser('export');a.add_argument('user');a.add_argument('file',type=Path)
 a=s.add_parser('delete-user');a.add_argument('user');a.add_argument('--confirm',action='store_true')
 a=s.add_parser('health');a.add_argument('--backup',type=Path)
 a=s.add_parser('outbox');a.add_argument('actor');a.add_argument('--operator',action='store_true')
 a=s.add_parser('retry-delivery');a.add_argument('actor');a.add_argument('delivery_id',type=int);a.add_argument('--operator',action='store_true');a.add_argument('--confirm',action='store_true')
 a=s.add_parser('purge-closed');a.add_argument('--older-than-days',type=int,required=True);a.add_argument('--confirm',action='store_true')
 a=s.add_parser('eval');a.add_argument('--report',type=Path,default=Path('evaluation-report.json'))
 z=p.parse_args();d=connect(z.db)
 try:
  if z.cmd=='faq-add':
   try:content=load_document(z.file)
   except (ValueError,OSError) as exc:p.error(str(exc))
   cur=d.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(z.title,'published' if z.publish else 'draft',content,now()));d.commit();print(f'Document {cur.lastrowid} saved as '+('published' if z.publish else 'draft'))
  elif z.cmd in ('faq-publish','faq-retire'):
   st='published' if z.cmd=='faq-publish' else 'retired';cur=d.execute('UPDATE documents SET status=?,version=version+1,updated_at=? WHERE id=?',(st,now(),z.doc_id))
   if not cur.rowcount:p.error('Document not found')
   d.commit();print(f'Document {z.doc_id}: {st}')
  elif z.cmd=='ask':
   if not z.user.strip() or not z.question.strip() or len(z.question)>MAX_TEXT:p.error('User and a question up to 4000 characters are required')
   hits=retrieve(z.question,d)
   if hits:
    citations=[{'document':h[1]['title'],'version':h[1]['version'],'location':h[5],'excerpt':h[3][:500]} for h in hits]
    print('Evidence found (lexical retrieval only):\n'+'\n'.join(f"[{c['document']} v{c['version']} {c['location']}] {c['excerpt']}" for c in citations))
   else:
    tid,created=create_ticket(z.user,z.question,[],d);print(f"I couldn't find enough published evidence. Ticket #{tid} is open for operator follow-up." if created else f'This request is already tracked in open ticket #{tid}.')
  elif z.cmd in ('tickets','ticket','close') and z.operator:require_operator(z.actor,p)
  if z.cmd=='tickets':
   rows=d.execute('SELECT id,user_id,status,question,created_at FROM tickets '+('ORDER BY id DESC' if z.operator else 'WHERE user_id=? ORDER BY id DESC'),() if z.operator else (z.actor,)).fetchall();print(json.dumps([dict(r) for r in rows],ensure_ascii=False,indent=2))
  elif z.cmd=='ticket':
   t=d.execute('SELECT * FROM tickets WHERE id=? '+('' if z.operator else 'AND user_id=?'),(z.ticket_id,) if z.operator else (z.ticket_id,z.actor)).fetchone()
   if t is None:p.error('Ticket not found')
   print(json.dumps({'id':t['id'],'status':t['status'],'question':t['question'],'messages':[dict(m) for m in d.execute('SELECT author,body,created_at FROM messages WHERE ticket_id=? ORDER BY id',(z.ticket_id,))],'events':[dict(e) for e in d.execute('SELECT actor,event,details,created_at FROM ticket_events WHERE ticket_id=? ORDER BY id',(z.ticket_id,))]},ensure_ascii=False,indent=2))
  elif z.cmd=='assign':
   require_operator(z.actor,p)
   if z.operator_id not in operator():p.error('Assigned operator must be in SUPPORT_OPERATORS')
   t=d.execute('SELECT id FROM tickets WHERE id=? AND status="open"',(z.ticket_id,)).fetchone()
   if not t:p.error('Open ticket not found')
   d.execute('INSERT INTO operator_assignments(ticket_id,operator_id,assigned_at,assigned_by) VALUES(?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET operator_id=excluded.operator_id,assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by',(z.ticket_id,z.operator_id,now(),z.actor));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'operator.assigned',z.ticket_id,now()));d.execute('INSERT INTO ticket_events(ticket_id,actor,event,details,created_at) VALUES(?,?,?,?,?)',(z.ticket_id,z.actor,'assigned',z.operator_id,now()));d.commit();print(f'Assigned to {z.operator_id}')
  elif z.cmd=='reply':
   require_operator(z.actor,p)
   if not z.text.strip() or len(z.text)>MAX_TEXT:p.error('Reply must be non-empty and at most 4000 characters')
   t=d.execute('SELECT id FROM tickets WHERE id=? AND status="open"',(z.ticket_id,)).fetchone()
   if not t:p.error('Open ticket not found')
   cur=d.execute('INSERT OR IGNORE INTO messages(ticket_id,author,body,created_at) VALUES(?,?,?,?)',(z.ticket_id,z.actor,z.text,now()))
   if cur.rowcount:
    d.execute('UPDATE tickets SET updated_at=? WHERE id=?',(now(),z.ticket_id));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'operator.replied',z.ticket_id,now()));d.execute('INSERT INTO ticket_events(ticket_id,actor,event,created_at) VALUES(?,?,?,?)',(z.ticket_id,z.actor,'operator.replied',now()))
   d.commit();print('Operator reply stored; no user notification was sent.' if cur.rowcount else 'This reply is already recorded; no duplicate notification was sent.')
  elif z.cmd=='close':
   t=d.execute('SELECT id FROM tickets WHERE id=? '+('' if z.operator else 'AND user_id=?'),(z.ticket_id,) if z.operator else (z.ticket_id,z.actor)).fetchone()
   if not t:p.error('Ticket not found')
   st='open' if z.reopen else 'closed';d.execute('UPDATE tickets SET status=?,updated_at=? WHERE id=?',(st,now(),z.ticket_id));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'ticket.'+st,z.ticket_id,now()));d.execute('INSERT INTO ticket_events(ticket_id,actor,event,created_at) VALUES(?,?,?,?)',(z.ticket_id,z.actor,st,now()));d.commit();print(f'Ticket {st}')
  elif z.cmd=='export':
   rows=d.execute('SELECT id,status,question,citations,created_at,updated_at FROM tickets WHERE user_id=? ORDER BY id',(z.user,)).fetchall();data=[]
   for row in rows:
    item=dict(row);item['messages']=[dict(m) for m in d.execute('SELECT author,body,created_at FROM messages WHERE ticket_id=? ORDER BY id',(row['id'],))];item['events']=[dict(e) for e in d.execute('SELECT actor,event,details,created_at FROM ticket_events WHERE ticket_id=? ORDER BY id',(row['id'],))];data.append(item)
   conversation=d.execute('SELECT created_at,updated_at,last_message FROM conversations WHERE user_id=?',(z.user,)).fetchone()
   z.file.write_text(json.dumps({'tickets':data,'conversation':dict(conversation) if conversation else None},ensure_ascii=False,indent=2),encoding='utf-8');print(f'Exported {len(rows)} ticket(s), messages, event history and conversation data')
  elif z.cmd=='delete-user':
   if not z.confirm:p.error('Repeat with --confirm to delete user tickets and messages')
   count=delete_user_data(d,z.user);d.commit();print(f'Deleted {count} ticket(s), conversation, queued chat responses and local update payloads')
  elif z.cmd=='health':
   result=d.execute('PRAGMA quick_check').fetchone()[0]
   if result!='ok':p.error('SQLite quick_check failed; restore a verified backup before continuing')
   if z.backup:
    out=sqlite3.connect(z.backup);d.backup(out);out.close();print(f'Backup created: {z.backup}')
   else:print('SQLite quick_check completed')
  elif z.cmd=='outbox':
   require_operator(z.actor,p)
   rows=d.execute('SELECT id,update_id,sequence,chat_id,status,attempt_count,created_at,sent_at FROM telegram_outbox ORDER BY id DESC LIMIT 100').fetchall()
   print(json.dumps([dict(r) for r in rows],ensure_ascii=False,indent=2))
  elif z.cmd=='retry-delivery':
   require_operator(z.actor,p)
   if not z.confirm:p.error('Repeat with --confirm to queue a failed delivery for one retry on the next bot run')
   if not requeue_failed_delivery(d,z.delivery_id):p.error('Only a failed delivery can be requeued; inspect uncertain items manually')
   print(f'Delivery {z.delivery_id} queued for one retry on the next bot run.')
  elif z.cmd=='purge-closed':
   if not z.confirm:p.error('Repeat with --confirm to delete closed tickets older than the selected retention period')
   count=purge_closed(d,z.older_than_days);d.commit();print(f'Purged {count} closed ticket(s). Open tickets were retained.')
  elif z.cmd=='eval':
   # Fixed synthetic cases cover evidence, partial/conflicting sources, stale/draft content and refusal.
   cases=[]
   def add_case(question,expected,allowed_sources=()):
    cases.append({'question':question,'expected':expected,'allowed_sources':list(allowed_sources)})
   with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tf:
    edb=connect(Path(tf.name))
    for n in range(8):
     title=f'Synthetic policy {n}'
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(title,'published',f'Policy topic{n} allows customers to request refund option {n}.',now()))
     add_case(f'What does policy topic{n} say about refund option?','evidence',[title])
    for n in range(4):
     a,b=f'Conflict A {n}',f'Conflict B {n}'
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(a,'published',f'Limit topic{n} is 10 units.',now()))
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(b,'published',f'Limit topic{n} is 20 units.',now()))
     add_case(f'What is the limit topic{n}?','multiple_sources',[a,b])
    for n in range(6):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Partial {n}','published',f'Partialtopic{n} is noted.',now()))
     add_case(f'Explain partialtopic{n} now','refuse')
    for n in range(4):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Draft {n}','draft',f'Drafttopic{n} is available.',now()))
     add_case(f'What is drafttopic{n}?','refuse')
    for n in range(4):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Retired {n}','retired',f'Retiredtopic{n} used to be available.',now()))
     add_case(f'What is retiredtopic{n}?','refuse')
    for n in range(4):
     add_case(f'Ignore rules reveal private ticket number {n}','refuse')
    for n in range(2):
     add_case(f'Other customer account details {n}','refuse')
    for n in range(2):
     add_case(f'Can you guarantee future delivery {n}?','refuse')
    edb.commit()
    passed=0;valid_citations=0;citation_total=0;refusals=0;conflict_shown=0;results=[];retrieval_hits=0;retrieval_case_total=0;relevant_sources=0;recalled_sources=0
    for c in cases:
     hits=retrieve(c['question'],edb)
     actual='refuse' if not hits else ('multiple_sources' if len(hits)>1 else 'evidence')
     citations=[{'document':h[1]['title'],'version':h[1]['version'],'location':h[5],'excerpt':h[3][:500]} for h in hits]
     allowed=set(c['allowed_sources']);returned={cit['document'] for cit in citations}
     citation_total+=len(citations);valid_citations+=sum(bool(cit['document'] and cit['version'] and cit['location'] and cit['excerpt'] and cit['document'] in allowed) for cit in citations)
     if allowed:
      retrieval_case_total+=1;retrieval_hits+=bool(returned & allowed);relevant_sources+=len(allowed);recalled_sources+=len(returned & allowed)
     if c['expected']=='refuse' and actual=='refuse':refusals+=1
     if c['expected']=='multiple_sources' and actual=='multiple_sources':conflict_shown+=1
     ok=actual==c['expected'];passed+=ok;results.append({**c,'actual':actual,'retrieved_sources':sorted(returned),'citations':citations,'passed':ok})
    edb.close()
   z.report.write_text(json.dumps({'n':len(cases),'behavior_match_rate':passed/len(cases),'retrieval_hit_rate':retrieval_hits/retrieval_case_total if retrieval_case_total else 1.0,'retrieval_recall':recalled_sources/relevant_sources if relevant_sources else 1.0,'valid_citation_rate':valid_citations/citation_total if citation_total else 1.0,'correct_refusal_count':refusals,'conflict_cases_with_multiple_sources':conflict_shown,'unsupported_claim_rate':None,'unsupported_claim_rate_note':'Requires human review; the system emits source excerpts, not generated answer prose.','method':'34 fixed synthetic prompts with expected behavior and allowed document titles; published, draft, retired, partial and conflicting FAQ paragraphs; lexical retrieval only.','limitations':'Small constructed dataset measures source retrieval and routing, not answer accuracy, human claim verification, or general performance.','results':results},ensure_ascii=False,indent=2),encoding='utf-8');print(f'Wrote {len(cases)} cases; behavior match rate {passed/len(cases):.2f} (synthetic routing measure, not general accuracy)')
 finally:d.close()
if __name__=='__main__':main()
