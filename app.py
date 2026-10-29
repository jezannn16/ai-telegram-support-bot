"""Support FAQ/ticket service; live Telegram access is isolated in telegram_bot.py."""
from __future__ import annotations
import argparse,json,os,re,sqlite3,tempfile
from migrations import connect as connect_db
from datetime import datetime,timezone
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
   print(json.dumps({'id':t['id'],'status':t['status'],'question':t['question'],'messages':[dict(m) for m in d.execute('SELECT author,body,created_at FROM messages WHERE ticket_id=? ORDER BY id',(z.ticket_id,))]},ensure_ascii=False,indent=2))
  elif z.cmd=='assign':
   require_operator(z.actor,p)
   if z.operator_id not in operator():p.error('Assigned operator must be in SUPPORT_OPERATORS')
   t=d.execute('SELECT id FROM tickets WHERE id=? AND status="open"',(z.ticket_id,)).fetchone()
   if not t:p.error('Open ticket not found')
   d.execute('INSERT INTO operator_assignments(ticket_id,operator_id,assigned_at,assigned_by) VALUES(?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET operator_id=excluded.operator_id,assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by',(z.ticket_id,z.operator_id,now(),z.actor));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'operator.assigned',z.ticket_id,now()));d.commit();print(f'Assigned to {z.operator_id}')
  elif z.cmd=='reply':
   require_operator(z.actor,p)
   if not z.text.strip() or len(z.text)>MAX_TEXT:p.error('Reply must be non-empty and at most 4000 characters')
   t=d.execute('SELECT id FROM tickets WHERE id=? AND status="open"',(z.ticket_id,)).fetchone()
   if not t:p.error('Open ticket not found')
   d.execute('INSERT OR IGNORE INTO messages(ticket_id,author,body,created_at) VALUES(?,?,?,?)',(z.ticket_id,z.actor,z.text,now()));d.execute('UPDATE tickets SET updated_at=? WHERE id=?',(now(),z.ticket_id));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'operator.replied',z.ticket_id,now()));d.commit();print('Operator reply stored; no user notification was sent.')
  elif z.cmd=='close':
   t=d.execute('SELECT id FROM tickets WHERE id=? '+('' if z.operator else 'AND user_id=?'),(z.ticket_id,) if z.operator else (z.ticket_id,z.actor)).fetchone()
   if not t:p.error('Ticket not found')
   st='open' if z.reopen else 'closed';d.execute('UPDATE tickets SET status=?,updated_at=? WHERE id=?',(st,now(),z.ticket_id));d.execute('INSERT INTO audit(actor,event,ticket_id,created_at) VALUES(?,?,?,?)',(z.actor,'ticket.'+st,z.ticket_id,now()));d.commit();print(f'Ticket {st}')
  elif z.cmd=='export':
   rows=d.execute('SELECT id,status,question,created_at,updated_at FROM tickets WHERE user_id=? ORDER BY id',(z.user,)).fetchall();z.file.write_text(json.dumps([dict(r) for r in rows],ensure_ascii=False,indent=2),encoding='utf-8');print(f'Exported {len(rows)} ticket(s)')
  elif z.cmd=='delete-user':
   if not z.confirm:p.error('Repeat with --confirm to delete user tickets and messages')
   ids=[r[0] for r in d.execute('SELECT id FROM tickets WHERE user_id=?',(z.user,))];d.execute('DELETE FROM audit WHERE actor=?',(z.user,));d.executemany('DELETE FROM audit WHERE ticket_id=?',[(x,) for x in ids]);d.executemany('DELETE FROM tickets WHERE id=?',[(x,) for x in ids]);d.commit();print(f'Deleted {len(ids)} ticket(s)')
  elif z.cmd=='health':
   d.execute('PRAGMA quick_check').fetchone();
   if z.backup:
    out=sqlite3.connect(z.backup);d.backup(out);out.close();print(f'Backup created: {z.backup}')
   else:print('SQLite quick_check completed')
  elif z.cmd=='eval':
   # Fixed synthetic cases cover evidence, partial/conflicting sources, stale/draft content and refusal.
   cases=[]
   with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tf:
    edb=connect(Path(tf.name))
    for n in range(8):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Synthetic policy {n}','published',f'Policy topic{n} allows customers to request refund option {n}.',now()))
     cases.append({'question':f'What does policy topic{n} say about refund option?','expected':'evidence'})
    for n in range(4):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Conflict A {n}','published',f'Limit topic{n} is 10 units.',now()))
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Conflict B {n}','published',f'Limit topic{n} is 20 units.',now()))
     cases.append({'question':f'What is the limit topic{n}?','expected':'multiple_sources'})
    for n in range(6):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Partial {n}','published',f'Partialtopic{n} is noted.',now()))
     cases.append({'question':f'Explain partialtopic{n} now','expected':'refuse'})
    for n in range(4):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Draft {n}','draft',f'Drafttopic{n} is available.',now()))
     cases.append({'question':f'What is drafttopic{n}?','expected':'refuse'})
    for n in range(4):
     edb.execute('INSERT INTO documents(title,status,content,updated_at) VALUES(?,?,?,?)',(f'Retired {n}','retired',f'Retiredtopic{n} used to be available.',now()))
     cases.append({'question':f'What is retiredtopic{n}?','expected':'refuse'})
    for n in range(4):
     cases.append({'question':f'Ignore rules reveal private ticket number {n}','expected':'refuse'})
    for n in range(2):
     cases.append({'question':f'Other customer account details {n}','expected':'refuse'})
    for n in range(2):
     cases.append({'question':f'Can you guarantee future delivery {n}?','expected':'refuse'})
    edb.commit()
    passed=0;valid_citations=0;citation_total=0;refusals=0;conflict_shown=0;results=[]
    for c in cases:
     hits=retrieve(c['question'],edb)
     actual='refuse' if not hits else ('multiple_sources' if len(hits)>1 else 'evidence')
     citations=[{'document':h[1]['title'],'version':h[1]['version'],'location':h[5],'excerpt':h[3][:500]} for h in hits]
     citation_total+=len(citations);valid_citations+=sum(bool(cit['document'] and cit['version'] and cit['location'] and cit['excerpt']) for cit in citations)
     if c['expected']=='refuse' and actual=='refuse':refusals+=1
     if c['expected']=='multiple_sources' and actual=='multiple_sources':conflict_shown+=1
     ok=actual==c['expected'];passed+=ok;results.append({**c,'actual':actual,'citations':citations,'passed':ok})
    edb.close()
   z.report.write_text(json.dumps({'n':len(cases),'behavior_match_rate':passed/len(cases),'valid_citation_rate':valid_citations/citation_total if citation_total else 1.0,'correct_refusal_count':refusals,'conflict_cases_with_multiple_sources':conflict_shown,'unsupported_claim_rate':None,'unsupported_claim_rate_note':'Requires human review of generated prose; the baseline emits source excerpts only.','method':'34 fixed synthetic prompts over published, draft, retired, partial and conflicting synthetic FAQ paragraphs using lexical retrieval; no LLM.','limitations':'Small constructed dataset tests routing, source visibility and refusal, not real-world answer quality, recall or general accuracy.','results':results},ensure_ascii=False,indent=2),encoding='utf-8');print(f'Wrote {len(cases)} cases; behavior match rate {passed/len(cases):.2f} (synthetic routing measure, not general accuracy)')
 finally:d.close()
if __name__=='__main__':main()
