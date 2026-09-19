"""Run: python server.py --demo ; LINE mode: python server.py"""
import argparse, base64, hashlib, hmac, json, os, secrets, sqlite3, threading, time, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from core import fresh, step, view, summary

ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()

def init_db(path):
    db=sqlite3.connect(path,check_same_thread=False)
    db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, user_id TEXT, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, event TEXT NOT NULL, done INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT, payload TEXT, retry_key TEXT, attempts INTEGER DEFAULT 0, due REAL DEFAULT 0, sent INTEGER DEFAULT 0);
    ''')
    db.commit(); return db

def dumps(x): return json.dumps(x,ensure_ascii=False)

def transact(db,user,command='',value='',revision=None):
    row=db.execute('SELECT state FROM sessions WHERE id=?',(user,)).fetchone()
    s=json.loads(row['state']) if row else fresh()
    s,v,order=step(s,command,value,revision)
    db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(user,dumps(s)))
    if order: db.execute('INSERT INTO orders VALUES (?,?,?)',(order['id'],user,dumps(order)))
    return v,order

def postback(cmd,rev,label):
    return {'type':'postback','label':label[:20],'data':dumps({'c':cmd,'r':rev}),'displayText':label[:300]}

def messages(v):
    result=[]
    if v['cards']:
        bubbles=[]
        for c in v['cards']:
            bubbles.append({'type':'bubble','size':'kilo','body':{'type':'box','layout':'vertical','spacing':'md','contents':[
                {'type':'text','text':'川記 · 現點現做','size':'xs','color':'#84745C'},
                {'type':'text','text':c['title'],'weight':'bold','size':'xl','wrap':True},
                {'type':'text','text':c['detail'],'size':'sm','color':'#777777','wrap':True},
                {'type':'text','text':c['price'],'weight':'bold','color':'#A14F2B'}]},
                'footer':{'type':'box','layout':'vertical','contents':[{'type':'button','style':'primary','color':'#27634C','action':postback(c['cmd'],v['rev'],'選這碗')}]}})
        result.append({'type':'flex','altText':'川記麵線糊點餐菜單','contents':{'type':'carousel','contents':bubbles}})
    msg={'type':'text','text':v['text'][:4900]}
    if v['options']:
        msg['quickReply']={'items':[{'type':'action','action':postback(o['cmd'],v['rev'],o['label'])} for o in v['options']]}
    result.append(msg); return result

def verify(raw,signature,secret):
    expected=base64.b64encode(hmac.new(secret.encode(),raw,hashlib.sha256).digest()).decode()
    return bool(secret) and hmac.compare_digest(expected,signature)

def queue_out(db,key,kind,payload):
    db.execute('INSERT OR IGNORE INTO outbox(id,kind,payload,retry_key) VALUES (?,?,?,?)',(key,kind,dumps(payload),str(uuid.uuid4())))

def process_event(db,event,owner):
    source=event.get('source',{})
    if source.get('type')!='user': return
    user=source.get('userId')
    if not user: return
    cmd=''; value=''; rev=None
    if event['type']=='follow': cmd='start'
    elif event['type']=='postback':
        try:
            data=json.loads(event['postback']['data']); cmd=data['c']; rev=data['r']
            if not isinstance(cmd,str) or not isinstance(rev,str): return
        except (ValueError,KeyError,TypeError): return
    elif event['type']=='message' and event['message'].get('type')=='text':
        value=event['message']['text'].strip()
        if value in ('開始點餐','點餐','菜單'): cmd='start'; value=''
    else: return
    v,order=transact(db,user,cmd,value,rev)
    eid=event['webhookEventId']
    if event.get('replyToken'): queue_out(db,eid+':reply','reply',{'replyToken':event['replyToken'],'messages':messages(v)})
    if order:
        text='【川記新訂單・尚未付款】\n'+order['id']+'\n\n'+summary(order['detail'])+'\n\n請聯繫客人確認接單；此版本不會自動承諾完成時間。'
        queue_out(db,order['id']+':owner','push',{'to':owner,'messages':[{'type':'text','text':text[:4900]}]})

def send_line(kind,payload,token,retry_key):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if kind=='push': headers['X-Line-Retry-Key']=retry_key
    req=Request('https://api.line.me/v2/bot/message/'+kind,data=payload.encode(),headers=headers,method='POST')
    try:
        with urlopen(req,timeout=12) as r: r.read()
    except HTTPError as e:
        if kind=='push' and e.code==409 and e.headers.get('X-Line-Accepted-Request-Id'): return
        raise

def worker(db,owner,token):
    while True:
        try:
            with LOCK:
                rows=db.execute('SELECT * FROM inbox WHERE done=0 ORDER BY rowid LIMIT 20').fetchall()
                for row in rows:
                    with db:
                        process_event(db,json.loads(row['event']),owner)
                        db.execute('UPDATE inbox SET done=1 WHERE id=?',(row['id'],))
                jobs=db.execute('SELECT * FROM outbox WHERE sent=0 AND due<=? ORDER BY rowid LIMIT 10',(time.time(),)).fetchall()
            for job in jobs:
                try:
                    send_line(job['kind'],job['payload'],token,job['retry_key'])
                    with LOCK,db: db.execute('UPDATE outbox SET sent=1 WHERE id=?',(job['id'],))
                except Exception:
                    attempts=job['attempts']+1
                    # Push retries keep the same idempotency key; never duplicate a paid/order notification.
                    with LOCK,db:
                        db.execute('UPDATE outbox SET attempts=?,due=?,sent=? WHERE id=?',(attempts,time.time()+min(300,2**min(attempts,8)), -1 if attempts>=10 else 0,job['id']))
                    print('LINE delivery failed; kind='+job['kind']+' attempt='+str(attempts),flush=True)
        except Exception as exc:
            # Do not log raw event payloads, phone numbers, access tokens or response bodies.
            print('Worker error: '+type(exc).__name__,flush=True)
        time.sleep(1)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,status,data,ctype='application/json; charset=utf-8',cookie=None):
        body=(dumps(data) if ctype.startswith('application/json') else data).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path=='/health': return self.respond(200,{'ok':True,'mode':'demo' if self.server.demo else 'line'})
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        if self.server.demo and self.path in files:
            filename,ctype=files[self.path]; return self.respond(200,(ROOT/filename).read_text(encoding='utf-8-sig'),ctype)
        self.respond(404,{'error':'Not found'})
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 262144: return self.respond(413,{'error':'Invalid size'})
            raw=self.rfile.read(length)
            if self.path=='/webhook' and not self.server.demo:
                if not verify(raw,self.headers.get('X-Line-Signature',''),self.server.secret): return self.respond(403,{'error':'Invalid signature'})
                payload=json.loads(raw)
                with LOCK,self.server.db:
                    for event in payload.get('events',[]):
                        eid=event.get('webhookEventId')
                        if eid: self.server.db.execute('INSERT OR IGNORE INTO inbox(id,event) VALUES (?,?)',(eid,dumps(event)))
                return self.respond(200,{'ok':True})
            if self.path=='/demo' and self.server.demo:
                # Demo binds loopback only, rejects cross-origin mutation, and uses isolated browser sessions.
                if self.headers.get('Origin') not in (None,'http://'+self.headers.get('Host','')): return self.respond(403,{'error':'Origin rejected'})
                data=json.loads(raw); cookies=SimpleCookie(); cookies.load(self.headers.get('Cookie',''))
                sid=cookies['cj_demo'].value if 'cj_demo' in cookies else secrets.token_hex(24)
                if len(sid)!=48 or any(c not in '0123456789abcdef' for c in sid): sid=secrets.token_hex(24)
                with LOCK,self.server.db:
                    v,order=transact(self.server.db,'demo:'+sid,str(data.get('cmd','')),str(data.get('value',''))[:500],data.get('rev'))
                    rows=self.server.db.execute('SELECT data FROM orders WHERE user_id=? ORDER BY rowid DESC LIMIT 5',('demo:'+sid,)).fetchall()
                    v['orders']=[{'id':o['id'],'summary':summary(o['detail']),'total':o['total']} for o in [json.loads(r['data']) for r in rows]]
                return self.respond(200,v,cookie='cj_demo='+sid+'; HttpOnly; SameSite=Strict; Path=/')
            self.respond(404,{'error':'Not found'})
        except (ValueError,KeyError,TypeError): self.respond(400,{'error':'Invalid request'})
        except Exception: self.respond(500,{'error':'暫時無法處理，請稍後重試。'})

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--demo',action='store_true'); parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8787'))); args=parser.parse_args()
    secret=os.getenv('LINE_CHANNEL_SECRET',''); token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN',''); owner=os.getenv('LINE_OWNER_USER_ID','')
    if not args.demo and not all([secret,token,owner]): parser.error('LINE mode requires LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN and LINE_OWNER_USER_ID. Use --demo for local preview.')
    path=Path(os.getenv('ORDER_DB',str(ROOT/('demo.sqlite3' if args.demo else 'orders.sqlite3')))); path.parent.mkdir(parents=True,exist_ok=True)
    db=init_db(str(path)); srv=ThreadingHTTPServer(('127.0.0.1' if args.demo else '0.0.0.0',args.port),Handler)
    srv.demo=args.demo; srv.db=db; srv.secret=secret
    if not args.demo: threading.Thread(target=worker,args=(db,owner,token),daemon=True).start()
    print(f'Chuanji {"preview" if args.demo else "LINE service"} on port {args.port}',flush=True)
    srv.serve_forever()

if __name__=='__main__': main()
