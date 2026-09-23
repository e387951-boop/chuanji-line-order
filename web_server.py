"""LINE web ordering + owner console. Python standard library, persistent SQLite."""
import argparse, base64, csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit,parse_qs,urlencode
from urllib.request import Request,urlopen
from web_store import *
from server import send_line
ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()
LOGIN_TRIES={}

def verify_line_id(token,channel):
 if not isinstance(token,str) or not 1<len(token)<12000: raise Problem('LINE 登入資訊無效',401)
 req=Request('https://api.line.me/oauth2/v2.1/verify',data=urlencode({'id_token':token,'client_id':channel}).encode(),method='POST')
 try:
  with urlopen(req,timeout=12) as res: data=json.load(res)
 except Exception: raise Problem('LINE 登入驗證失敗，請從官方帳號重新開啟',401)
 if data.get('aud')!=channel or data.get('iss')!='https://access.line.me' or data.get('exp',0)<=time.time() or not re.fullmatch(r'U[0-9a-f]{32}',data.get('sub','')): raise Problem('LINE 登入資訊無效',401)
 return data['sub']

def deliver(db,token):
 while True:
  try:
   with LOCK: jobs=db.execute("SELECT * FROM web_outbox WHERE state='queued' AND due<=? ORDER BY rowid LIMIT 15",(time.time(),)).fetchall()
   for job in jobs:
    try:
     send_line('push',job['payload'],token,job['retry_key'])
     with LOCK,db: db.execute("UPDATE web_outbox SET state='sent' WHERE id=?",(job['id'],))
    except Exception:
     n=job['attempts']+1
     with LOCK,db: db.execute('UPDATE web_outbox SET attempts=?,due=?,state=? WHERE id=?',(n,time.time()+min(300,2**min(n,8)),'failed' if n>=10 else 'queued',job['id']))
  except Exception as e: print('Notification worker: '+type(e).__name__,flush=True)
  time.sleep(2)

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def respond(self,status,payload,mime='application/json; charset=utf-8',filename=None):
  body=payload if isinstance(payload,bytes) else (js(payload) if mime.startswith('application/json') else payload).encode()
  self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
  if filename: self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://static.line-scdn.net; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' https://*.line.me https://*.line-scdn.net; frame-src https://*.line.me; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
  if getattr(self,'new_cookie',None): self.send_header('Set-Cookie',self.new_cookie)
  self.end_headers(); self.wfile.write(body)
 def session(self):
  cookie=SimpleCookie(); cookie.load(self.headers.get('Cookie','')); sid=cookie['cj_web'].value if 'cj_web' in cookie else ''
  with LOCK,self.server.db:
   row=self.server.db.execute('SELECT * FROM web_sessions WHERE id=? AND expires>?',(hashlib.sha256(sid.encode()).hexdigest(),time.time())).fetchone() if sid else None
   if not row:
    sid=secrets.token_urlsafe(32); hashed=hashlib.sha256(sid.encode()).hexdigest(); csrf=secrets.token_urlsafe(32)
    self.server.db.execute('INSERT INTO web_sessions VALUES (?,?,?,0,?)',(hashed,csrf,'demo:'+secrets.token_hex(16) if self.server.demo else '',time.time()+43200))
    row=self.server.db.execute('SELECT * FROM web_sessions WHERE id=?',(hashed,)).fetchone()
    self.new_cookie='cj_web='+sid+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200'+('' if self.server.demo else '; Secure')
  return dict(row)
 def admin(self,s):
  if not s['admin']: raise Problem('請先登入店家後台',401)
 def customer(self,s):
  if not s['user_id']: raise Problem('請先透過 LINE 登入後下單',401)
 def notifications(self,oid):
  rows=self.server.db.execute('SELECT id,state FROM web_outbox WHERE order_id=?',(oid,)).fetchall()
  return [{'recipient':'客人' if r['id'].endswith(':customer') else '店家','state':r['state']} for r in rows]
 def do_GET(self):
  self.new_cookie=None
  try:
   parsed=urlsplit(self.path); path=parsed.path; q=parse_qs(parsed.query)
   files={'/':'store.html','/admin':'admin.html','/returning.js':'returning.js','/store.js':'store.js','/chat_receipt.js':'chat_receipt.js','/admin.js':'admin.js','/web.css':'web.css'}
   if path in files:
    f=files[path]; mime='text/html' if f.endswith('.html') else 'text/javascript' if f.endswith('.js') else 'text/css'
    return self.respond(200,(ROOT/f).read_text(encoding='utf-8-sig'),mime+'; charset=utf-8')
   if path=='/health':
    with LOCK: self.server.db.execute('SELECT 1').fetchone()
    return self.respond(200,{'ok':True,'mode':'demo' if self.server.demo else 'live','storage':'postgres' if getattr(self.server.db,'persistent',False) else 'sqlite','version':'2026-09-23-returning-pickup-v1'})
   if path=='/staff-entrance.jpg': return self.respond(200,(ROOT/'staff-entrance.jpg').read_bytes(),'image/jpeg')
   if path=='/admin-greeting.jpg': return self.respond(200,(ROOT/'admin-greeting.jpg').read_bytes(),'image/jpeg')
   if path=='/logo.jpg': return self.respond(200,(ROOT/'logo.jpg').read_bytes(),'image/jpeg')
   if path=='/warm.css': return self.respond(200,(ROOT/'warm.css').read_bytes(),'text/css; charset=utf-8')
   if path.startswith('/media/'):
    with LOCK: row=self.server.db.execute('SELECT * FROM photos WHERE id=?',(path.split('/')[-1],)).fetchone()
    if not row: raise Problem('找不到照片',404)
    return self.respond(200,row['data'],row['mime'])
   s=self.session()
   with LOCK:
    db=self.server.db
    if path=='/api/session': return self.respond(200,{'csrf':s['csrf'],'demo':self.server.demo,'logged_in':bool(s['user_id']),'admin':bool(s['admin']),'liff_id':self.server.liff_id})
    if path=='/api/catalog': return self.respond(200,{'products':catalogue(db),'settings':settings(db),'online_payment':False,'demo':self.server.demo})
    if path=='/api/my-orders':
     self.customer(s); return self.respond(200,{'orders':get_orders(db,user=s['user_id'])[:20]})
    if path.startswith('/api/order/'):
     self.customer(s); row=db.execute('SELECT data FROM web_orders WHERE id=? AND user_id=?',(path.split('/')[-1],s['user_id'])).fetchone()
     if not row: raise Problem('找不到訂單',404)
     o=json.loads(row[0]); o['notifications']=self.notifications(o['id']); o['chat_messages']=customer_chat_card(o,self.server.liff_id); return self.respond(200,o)
    if path.startswith('/api/admin/'):
     self.admin(s)
     if path=='/api/admin/backup':
      backup=sqlite3.connect(':memory:')
      try:
       db.backup(backup)
       backup.execute('DELETE FROM web_sessions'); backup.commit()
       raw=backup.serialize()
      finally: backup.close()
      return self.respond(200,raw,'application/octet-stream','chuanji-backup.sqlite3')
     if path=='/api/admin/export':
      output=io.StringIO(); writer=csv.writer(output)
      writer.writerow(['訂單編號','下單時間','取餐時間','姓名','手機','狀態','付款狀態','金額','餐點明細','備註'])
      def safe(value):
       value=str(value)
       return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
      for o in get_orders(db,q.get('start',[''])[0],q.get('end',[''])[0]):
       writer.writerow([safe(v) for v in [o.get('pickup_number',o['id']),o['created_at'],o['pickup'],o['name'],o['phone'],STATUSES[o['status']],'已收款' if o['payment_status']=='paid' else '尚未收款',o['total'],order_text(o,settings(db)),o['note']]])
      return self.respond(200,output.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8','chuanji-orders.csv')
     if path=='/api/admin/catalog': return self.respond(200,{'products':catalogue(db,True),'settings':settings(db)})
     if path=='/api/admin/orders':
      orders=get_orders(db,q.get('start',[''])[0],q.get('end',[''])[0])
      stats_data=stats(orders)
      for o in orders[:300]: o['notifications']=self.notifications(o['id'])
      return self.respond(200,{'orders':orders[:300],'stats':stats_data,'total_records':len(orders)})
   raise Problem('找不到頁面',404)
  except Problem as e: self.respond(e.status,{'error':e.message})
  except Exception as e: print('GET error '+type(e).__name__,flush=True); self.respond(500,{'error':'暫時無法讀取，請重試'})
 def do_POST(self):
  self.new_cookie=None
  try:
   path=urlsplit(self.path).path
   origin=self.headers.get('Origin',''); expected='http://'+self.headers.get('Host','') if self.server.demo else self.server.origin
   if origin!=expected: raise Problem('網頁來源驗證失敗，請重新開啟',403)
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=4300000: raise Problem('資料過大',413)
   data=json.loads(self.rfile.read(length))
   if not isinstance(data,dict): raise Problem('資料格式錯誤')
   s=self.session()
   if not hmac.compare_digest(str(self.headers.get('X-CSRF-Token','')),s['csrf']): raise Problem('操作驗證過期，請重新整理',403)
   db=self.server.db
   if path=='/api/auth/line':
    if self.server.demo: raise Problem('試用版不需要 LINE 登入')
    user=verify_line_id(data.get('id_token'),self.server.channel_id)
    with LOCK,db: db.execute('UPDATE web_sessions SET user_id=? WHERE id=?',(user,s['id']))
    return self.respond(200,{'ok':True,'customer_key':hashlib.sha256(('returning:'+user).encode()).hexdigest()})
   if path=='/api/admin/login':
    ip=self.client_address[0]; stamp=time.time(); attempts=[x for x in LOGIN_TRIES.get(ip,[]) if x>stamp-600]
    if len(attempts)>=5: raise Problem('嘗試次數過多，請10分鐘後再試',429)
    if not self.server.demo and not hmac.compare_digest(str(data.get('password','')).encode(),self.server.admin_password.encode()):
     LOGIN_TRIES[ip]=attempts+[stamp]; raise Problem('管理密碼不正確',401)
    LOGIN_TRIES.pop(ip,None)
    with LOCK,db: db.execute('UPDATE web_sessions SET admin=1 WHERE id=?',(s['id'],))
    return self.respond(200,{'ok':True})
   with LOCK,db:
    if path=='/api/quote': return self.respond(200,price_cart(db,data.get('items')))
    if path=='/api/orders':
     self.customer(s); o=place_order(db,s['user_id'],data,self.server.demo,self.server.owner)
     # Commit before responding before reporting successful order creation (storage persistence depends on deployment).
     db.commit(); o['notifications']=self.notifications(o['id']); o['chat_messages']=customer_chat_card(o,self.server.liff_id); return self.respond(200,o)
    if path.startswith('/api/admin/'):
     self.admin(s)
     if path=='/api/admin/bind-notifications':
      self.customer(s)
      if self.server.demo: raise Problem('請在正式 LINE 點餐頁設定')
      if not re.fullmatch(r'U[0-9a-f]{32}',s['user_id']): raise Problem('請重新從 LINE 登入',401)
      db.execute('INSERT OR IGNORE INTO notification_owners VALUES (?,?)',(s['user_id'],nowstr()))
      enqueue(db,'binding-'+secrets.token_hex(12),s['user_id'],'owner','川記麵線糊｜店家通知已綁定。之後有新訂單，官方帳號會傳訊息通知你。')
      db.commit(); return self.respond(200,{'ok':True})
     if path=='/api/admin/logout': db.execute('UPDATE web_sessions SET admin=0 WHERE id=?',(s['id'],)); db.commit(); return self.respond(200,{'ok':True})
     if path=='/api/admin/product':
      pid=save_product(db,data); db.commit(); return self.respond(200,{'id':pid})
     if path=='/api/admin/settings':
      config=settings(db)
      for key in ('name','address','phone'): config[key]=str(data.get(key,config[key])).strip()[:150]
      if not config['name']: raise Problem('請填寫店名')
      config['lead_minutes']=integer(data.get('lead_minutes',15),5,180,'備餐分鐘'); config['max_days']=integer(data.get('max_days',7),1,60,'預訂天數'); config['accepting']=bool(data.get('accepting',True))
      db.execute('UPDATE web_settings SET data=? WHERE id=1',(js(config),)); db.commit(); return self.respond(200,{'ok':True})
     if path=='/api/admin/order':
      result=update_order(db,data); db.commit(); return self.respond(200,result)
     if path=='/api/admin/retry':
      oid=str(data.get('id','')); db.execute("UPDATE web_outbox SET state='queued',attempts=0,due=0 WHERE order_id=? AND state='failed'",(oid,)); db.commit(); return self.respond(200,{'ok':True})
     if path=='/api/admin/photo':
      try: raw=base64.b64decode(data.get('base64',''),validate=True)
      except Exception: raise Problem('照片格式錯誤')
      if not 20<=len(raw)<=3000000: raise Problem('請上傳3MB以內的照片')
      if raw.startswith(b'\x89PNG\r\n\x1a\n') and b'IEND' in raw[-20:]: mime='image/png'
      elif raw.startswith(b'\xff\xd8\xff') and raw.rstrip().endswith(b'\xff\xd9'): mime='image/jpeg'
      elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP': mime='image/webp'
      else: raise Problem('僅支援 JPG、PNG、WebP 照片')
      photo=secrets.token_hex(16); db.execute('INSERT INTO photos VALUES (?,?,?)',(photo,mime,raw)); db.commit(); return self.respond(200,{'id':photo})
   raise Problem('操作不存在',404)
  except Problem as e: self.respond(e.status,{'error':e.message})
  except (ValueError,TypeError): self.respond(400,{'error':'資料格式錯誤'})
  except Exception as e: print('POST error '+type(e).__name__,flush=True); self.respond(500,{'error':'暫時無法儲存，請重試；請勿重複建立同一筆訂單'})

def main():
 p=argparse.ArgumentParser();p.add_argument('--demo',action='store_true');p.add_argument('--port',type=int,default=int(os.getenv('PORT','8788')));args=p.parse_args()
 config={'liff_id':os.getenv('LIFF_ID',''),'channel_id':os.getenv('LINE_LOGIN_CHANNEL_ID',''),'token':os.getenv('LINE_CHANNEL_ACCESS_TOKEN',''),'owner':os.getenv('LINE_OWNER_USER_ID',''),'origin':os.getenv('PUBLIC_ORIGIN','').rstrip('/'),'admin_password':os.getenv('ADMIN_PASSWORD','')}
 if not args.demo and (not all(config[k] for k in ('liff_id','channel_id','token','origin','admin_password')) or not config['origin'].startswith('https://') or len(config['admin_password'])<12): p.error('Live mode requires LIFF_ID, LINE_LOGIN_CHANNEL_ID, LINE_CHANNEL_ACCESS_TOKEN, HTTPS PUBLIC_ORIGIN and ADMIN_PASSWORD (12+ characters).')
 path=Path(os.getenv('WEB_ORDER_DB',str(ROOT/('web-demo.sqlite3' if args.demo else 'web-orders.sqlite3'))));path.parent.mkdir(parents=True,exist_ok=True)
 # Database migration is opt-in; staged credentials must not block storefront releases.
 use_postgres=not args.demo and os.getenv('ORDER_STORAGE','sqlite').lower()=='postgres'
 if use_postgres and not all(os.getenv(k) for k in ('PGUSER','PGPASSWORD')): p.error('Postgres requires PGUSER and PGPASSWORD')
 db=connect(str(path),postgres=use_postgres);server=ThreadingHTTPServer(('127.0.0.1' if args.demo else '0.0.0.0',args.port),Handler); server.demo=args.demo;server.db=db
 for k,v in config.items():setattr(server,k,v)
 if not args.demo:threading.Thread(target=deliver,args=(db,config['token']),daemon=True).start()
 print('Chuanji storefront and admin on '+str(args.port),flush=True);server.serve_forever()
if __name__=='__main__':main()
