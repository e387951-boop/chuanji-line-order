"""Order receipt watchdog. No historic orders are enrolled implicitly."""
import hashlib
import json
import re
import time
from web_store import enqueue, nowstr, order_cards, settings

REASONS={'ready','sdk_missing','outside_liff','context_missing','wrong_chat','send_missing','permission_denied','check_failed','demo','messages_missing','api_error','timeout','storage_sent','sent'}

def install(db):
 db.executescript('''
 CREATE TABLE IF NOT EXISTS receipt_watch (order_id TEXT PRIMARY KEY, mode TEXT NOT NULL, due REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, fallback TEXT NOT NULL DEFAULT 'waiting');
 CREATE TABLE IF NOT EXISTS chat_diagnostics (order_id TEXT PRIMARY KEY, reason TEXT NOT NULL, context TEXT NOT NULL, error_code TEXT NOT NULL, updated TEXT NOT NULL);
 ''')

def enroll(db,order,webhook=False,stamp=None):
 if order.get('demo'): return
 db.execute('INSERT OR IGNORE INTO receipt_watch(order_id,mode,due) VALUES (?,?,?)',(order['id'],'webhook' if webhook else 'client',(stamp if stamp is not None else time.time())+60))

def confirm(db,oid):
 db.execute("UPDATE receipt_watch SET confirmed=1,fallback=CASE WHEN fallback IN ('waiting','no_recipient') THEN 'not_needed' ELSE fallback END WHERE order_id=?",(oid,))
 db.execute("UPDATE web_outbox SET state='cancelled' WHERE order_id=? AND id LIKE ? AND state='queued'",(oid,oid+':fallback:%'))

def report(db,oid,state,detail):
 old=db.execute('SELECT state FROM chat_deliveries WHERE order_id=?',(oid,)).fetchone()
 # A late timeout/failure must not undo an earlier successful send.
 if old and old[0]=='sent' and state!='sent': return
 reason=detail.get('reason',''); reason=reason if reason in REASONS else 'check_failed'
 context=detail.get('context',''); context=context if context in ('utou','group','room','external','none') else 'none'
 code=str(detail.get('error_code','')); code=code if re.fullmatch(r'[A-Za-z0-9_-]{0,40}',code) else 'UNKNOWN'
 db.execute('INSERT INTO chat_deliveries VALUES (?,?,?) ON CONFLICT(order_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(oid,state,nowstr()))
 db.execute('INSERT INTO chat_diagnostics VALUES (?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET reason=excluded.reason,context=excluded.context,error_code=excluded.error_code,updated=excluded.updated',(oid,reason,context,code,nowstr()))
 watch=db.execute('SELECT mode FROM receipt_watch WHERE order_id=?',(oid,)).fetchone()
 if state=='sent' and watch and watch[0]=='client': confirm(db,oid)
 elif state in ('failed','unavailable'):
  db.execute('UPDATE receipt_watch SET due=? WHERE order_id=? AND confirmed=0',(time.time(),oid))

def acknowledge(db,event):
 # Verify LINE signature at the HTTP boundary and bind the marker to its order owner.
 source=event.get('source',{}); message=event.get('message',{})
 if event.get('type')!='message' or source.get('type')!='user' or message.get('type')!='text': return False
 match=re.search(r'訂單查詢碼：(CJ[0-9]{4}-[0-9A-F]{8})',message.get('text',''))
 if not match: return False
 oid=match[1]
 if not db.execute('SELECT 1 FROM web_orders WHERE id=? AND user_id=?',(oid,source.get('userId',''))).fetchone(): return False
 confirm(db,oid); return True

def recipients(db,configured):
 owners={r[0] for r in db.execute('SELECT user_id FROM notification_owners')}
 owners.update(x.strip() for x in configured.split(','))
 return sorted(x for x in owners if re.fullmatch(r'U[0-9a-f]{32}',x))

def schedule(db,configured,stamp=None):
 stamp=time.time() if stamp is None else stamp
 rows=db.execute("SELECT w.order_id,o.data FROM receipt_watch w JOIN web_orders o ON o.id=w.order_id WHERE w.confirmed=0 AND w.fallback IN ('waiting','no_recipient') AND w.due<=?",(stamp,)).fetchall()
 owners=recipients(db,configured)
 for row in rows:
  oid=row[0]; order=json.loads(row[1])
  if order.get('status') in ('completed','cancelled'):
   db.execute("UPDATE receipt_watch SET fallback='not_needed' WHERE order_id=?",(oid,)); continue
  if not owners:
   db.execute("UPDATE receipt_watch SET fallback='no_recipient',due=? WHERE order_id=?",(stamp+60,oid)); continue
  cards=order_cards(order,settings(db),owner=True)
  for owner in owners:
   kind='fallback:'+hashlib.sha256(owner.encode()).hexdigest()[:20]
   if not db.execute('SELECT 1 FROM web_outbox WHERE id=?',(oid+':'+kind,)).fetchone():
    enqueue(db,oid,owner,kind,[{'type':'text','text':'【新訂單備援通知】\n訂單 '+order.get('pickup_number',oid)+' 已成立，尚未確認客人端聊天室回傳。請至後台確認接單。'}]+cards[:4])
  db.execute("UPDATE receipt_watch SET fallback='queued' WHERE order_id=?",(oid,))

def status(db,oid):
 watch=db.execute('SELECT * FROM receipt_watch WHERE order_id=?',(oid,)).fetchone()
 chat=db.execute('SELECT state FROM chat_deliveries WHERE order_id=?',(oid,)).fetchone()
 result={'chat':chat[0] if chat else 'unknown','confirmed':bool(watch and watch['confirmed']),'confirmation_mode':watch['mode'] if watch else 'legacy','fallback':watch['fallback'] if watch else 'not_enrolled'}
 if watch and watch['fallback']=='queued':
  states=[r[0] for r in db.execute('SELECT state FROM web_outbox WHERE order_id=? AND id LIKE ?',(oid,oid+':fallback:%'))]
  result['fallback']='failed' if 'failed' in states else 'sent' if states and all(s=='sent' for s in states) else 'not_needed' if states and all(s=='cancelled' for s in states) else 'queued'
 return result
