"""Persistent catalogue, per-bowl pricing and orders for the LINE web storefront."""
import json, re, secrets, sqlite3, time, uuid
from datetime import datetime,timedelta
from core import TZ, MENU, SIDES, EXTRAS, SPICY

DEFAULTS={'name':'川記麵線糊','address':'807高雄市三民區達德里熱河一街355號','phone':'','lead_minutes':15,'max_days':7,'accepting':True}
SEASONS=['胡椒粉','烏醋','香油','蒜泥','香菜']
ADDON_NAMES={'大腸','蚵仔','鮮蚵','蝦仁','發魷魚','魷魚','虱目魚漿','貢丸','油條'}
STATUSES={'new':'新訂單','preparing':'製作中','ready':'可取餐','completed':'已完成','cancelled':'已取消'}
TRANSITIONS={'new':['preparing','cancelled'],'preparing':['ready','cancelled'],'ready':['completed','cancelled'],'completed':[],'cancelled':[]}
class Problem(Exception):
 def __init__(self,message,status=400): self.message=message; self.status=status

def js(x): return json.dumps(x,ensure_ascii=False)
def nowstr(): return datetime.now(TZ).isoformat(timespec='seconds')
def integer(x,lo,hi,label):
 if isinstance(x,bool) or not isinstance(x,int) or not lo<=x<=hi: raise Problem(label+'超出可用範圍')
 return x

def connect(path,postgres=False):
 if postgres:
  from postgres_store import PostgresStore
  db=PostgresStore()
 else:
  db=sqlite3.connect(path,check_same_thread=False); db.row_factory=sqlite3.Row
 db.executescript('''PRAGMA journal_mode=WAL;
 CREATE TABLE IF NOT EXISTS web_settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL);
 CREATE TABLE IF NOT EXISTS web_orders (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, idem TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(user_id,idem));
 CREATE TABLE IF NOT EXISTS pickup_numbers (pickup_date TEXT NOT NULL, number TEXT NOT NULL, order_id TEXT NOT NULL UNIQUE, PRIMARY KEY(pickup_date,number));
 CREATE TABLE IF NOT EXISTS web_outbox (id TEXT PRIMARY KEY, order_id TEXT, recipient TEXT, payload TEXT, retry_key TEXT, attempts INTEGER DEFAULT 0, due REAL DEFAULT 0, state TEXT DEFAULT 'queued');
 CREATE TABLE IF NOT EXISTS notification_owners (user_id TEXT PRIMARY KEY, created TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS web_sessions (id TEXT PRIMARY KEY, csrf TEXT, user_id TEXT, admin INTEGER DEFAULT 0, expires REAL);
 ''')
 with db:
  db.execute('INSERT OR IGNORE INTO web_settings VALUES (1,?)',(js(DEFAULTS),))
  if not db.execute('SELECT 1 FROM products LIMIT 1').fetchone():
   for k,m in enumerate(MENU):
    p={'id':'n'+str(k),'name':m[0],'category':'noodle','small':m[2],'large':m[1],'price':0,'ingredients':m[3].split('、'),'description':'熱騰騰現做，依照喜好調整每一碗。','active':True,'photo':''}
    db.execute('INSERT INTO products VALUES (?,?,1)',(p['id'],js(p)))
   for prefix,category,items in [('s','side',SIDES),('a','addon',EXTRAS)]:
    for k,(name,price) in enumerate(items):
     p={'id':prefix+str(k),'name':name,'category':category,'small':0,'large':0,'price':price,'ingredients':[],'description':'','active':True,'photo':''}
     db.execute('INSERT INTO products VALUES (?,?,1)',(p['id'],js(p)))
 return db

def settings(db): return json.loads(db.execute('SELECT data FROM web_settings WHERE id=1').fetchone()[0])
def catalogue(db,all_items=False):
 items=[]
 for r in db.execute('SELECT * FROM products ORDER BY rowid'):
  p=json.loads(r['data']); p['version']=r['version']
  if p['category']=='addon':
   if p['name'] not in ADDON_NAMES and not all_items: continue
   p['name']={'鮮蚵':'蚵仔','魷魚':'發魷魚'}.get(p['name'],p['name'])
  items.append(p)  # Keep unavailable products visible; price_cart rejects them.
 return items

def save_product(db,data):
 pid=data.get('id') or 'p'+secrets.token_hex(6)
 if not re.fullmatch('[a-z0-9]{1,30}',pid): raise Problem('品項編號格式錯誤')
 old=db.execute('SELECT version FROM products WHERE id=?',(pid,)).fetchone()
 if old and data.get('version')!=old['version']: raise Problem('此品項已被更新，請重新開啟後編輯。',409)
 name=str(data.get('name','')).strip(); category=data.get('category')
 if not 1<=len(name)<=40 or category not in ('noodle','side','addon'): raise Problem('請填寫名稱與正確分類')
 p={'id':pid,'name':name,'category':category,'description':str(data.get('description','')).strip()[:180], 'ingredients':data.get('ingredients',[]),'active':bool(data.get('active',True)),'photo':str(data.get('photo',''))}
 if not isinstance(p['ingredients'],list) or len(p['ingredients'])>15 or any(not isinstance(x,str) or not 1<=len(x)<=20 for x in p['ingredients']): raise Problem('原有配料格式錯誤')
 if p['photo'] and not db.execute('SELECT 1 FROM photos WHERE id=?',(p['photo'],)).fetchone(): raise Problem('照片不存在，請重新上傳')
 for key in ('small','large','price'): p[key]=integer(data.get(key,0),0,9999,'價格')
 if (category=='noodle' and min(p['small'],p['large'])<=0) or (category!='noodle' and p['price']<=0): raise Problem('售價必須大於0')
 db.execute('INSERT INTO products VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET data=excluded.data,version=products.version+1',(pid,js(p)))
 return pid

def price_cart(db,items):
 if not isinstance(items,list) or not 1<=len(items)<=40: raise Problem('請加入1至40項餐點')
 products={p['id']:p for p in catalogue(db,True)}; lines=[]
 for item in items:
  if not isinstance(item,dict): raise Problem('餐點格式錯誤')
  p=products.get(item.get('product_id'))
  if not p or not p['active'] or p['category']=='addon': raise Problem('有餐點已下架，請回菜單重新選擇。',409)
  line={'product_id':p['id'],'name':p['name'],'category':p['category'],'photo':p['photo'],'qty':1,'size':'','extras':[],'omit':[],'spicy':'不辣','basil':False}
  line['note']=str(item.get('note','')).strip()
  if len(line['note'])>120: raise Problem('每份餐點備註最多120字')
  if p['category']=='noodle':
   size=item.get('size')
   if size not in ('small','large'): raise Problem('請選大小碗')
   line['size']=size; unit=p[size]; line['ingredients']=p['ingredients']
   spice=item.get('spicy','不辣')
   if spice not in SPICY: raise Problem('辣度選項錯誤')
   line['spicy']=spice; line['basil']=bool(item.get('basil',False))
   omitted=item.get('omit',[])
   if not isinstance(omitted,list) or any(not isinstance(x,str) or x not in SEASONS for x in omitted): raise Problem('固定配料不能取消，請在餐點備註填寫需求。',409)
   line['omit']=list(dict.fromkeys(omitted))
   if line['basil'] and '香菜' not in line['omit']: line['omit'].append('香菜')
   extras=item.get('extras',{})
   if not isinstance(extras,dict) or len(extras)>15: raise Problem('加料格式錯誤')
   count=0; selected_names=set()
   for aid,q in extras.items():
    a=products.get(aid)
    if not a or a['category']!='addon' or not a['active'] or a['name'] not in ADDON_NAMES: raise Problem('加料選項已變更，請重新編輯這碗。',409)
    separate=a['name']=='油條'
    q=integer(q,0,999999 if separate else 1,'油條份數' if separate else '同種加料只能選一份')
    if not q: continue
    if a['name'] in selected_names: raise Problem('加料不可選擇同一種類')
    selected_names.add(a['name'])
    if not separate: count+=q
    unit+=a['price']*q; line['extras'].append({'id':aid,'name':a['name']+('（另外包裝）' if separate else ''),'qty':q,'price':a['price'],'separate':separate})
   if count>2: raise Problem('每碗最多選兩種不同加料')
  else: unit=p['price']; line['qty']=integer(item.get('qty',1),1,20,'滷味份數')
  line['unit_price']=unit; line['subtotal']=unit*line['qty']; lines.append(line)
 return {'items':lines,'total':sum(x['subtotal'] for x in lines)}

def pickup_check(text,config,now=None):
 now=now or datetime.now(TZ)
 try: dt=datetime.strptime(text.replace('T',' '),'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
 except (ValueError,AttributeError): raise Problem('請選取餐日期與時間')
 if dt.weekday()==5: raise Problem('週六公休，請選擇其他日期')
 if not 11<=dt.hour<23: raise Problem('取餐時間為11:00至22:59')
 if dt<now+timedelta(minutes=config['lead_minutes']): raise Problem(f"取餐至少提前{config['lead_minutes']}分鐘")
 if dt.date()>(now+timedelta(days=config['max_days'])).date(): raise Problem(f"最多可預訂{config['max_days']}天內的餐點")
 return dt.strftime('%Y-%m-%d %H:%M')

def order_text(o,config):
 parts=[config['name']+'｜下單完成', '━━━━━━━━━━━━', '訂單編號  '+o.get('pickup_number',o['id']), '取餐時間  '+o['pickup'], '取餐姓名  '+o['name'], '聯絡電話  '+o['phone'], '━━━━━━━━━━━━']
 for n,i in enumerate(o['items'],1):
  title=f"{n}. {i['name']}"+(' '+('大碗' if i['size']=='large' else '小碗') if i['size'] else '')+f" ×{i['qty']}  ${i['subtotal']}"
  if i['category']=='noodle': title+='\n'+i['spicy']+'；不加：'+('、'.join(i['omit']) or '無')+'；加料：'+('、'.join(x['name']+'×'+str(x['qty']) for x in i['extras']) or '無')+('；香菜換九層塔' if i['basil'] else '')
  if i.get('note'): title+='\n這份備註：'+i['note']
  parts.append(title)
 parts+=['━━━━━━━━━━━━','訂單總計  NT$ '+str(o['total']),'付款方式  現場付款・'+('已收款' if o['payment_status']=='paid' else '尚未付款')]
 if o['note']: parts.append('備註：'+o['note'])
 parts.append('餐具：'+('需要' if o.get('utensils',True) else '不需要'))
 if config['address']: parts.append('取餐地址：'+config['address'])
 if config['phone']: parts.append('店家電話：'+config['phone'])
 parts.append('訂單已登記，店家正在確認與安排製作。')
 return '\n'.join(parts)

def customer_chat_card(o,liff_id):
 # The detail endpoint remains restricted to the authenticated order owner.
 def text(value,size='sm',weight='regular',color='#44372D'):
  return {'type':'text','text':str(value),'size':size,'weight':weight,'color':color,'wrap':True}
 body=[text('川記麵線糊','lg','bold'),text('到店自取','xl','bold','#986C43'),text('訂單已送出，待店家確認'),{'type':'separator','margin':'lg'},text('訂單編號'),text(o.get('pickup_number',o['id']),'48px','bold'),text('取餐時間：'+o['pickup']),text('付款方式：現場付款'),text('NT$ '+str(o['total']),'xxl','bold','#B06D38')]
 bubble={'type':'bubble','body':{'type':'box','layout':'vertical','spacing':'md','contents':body},'footer':{'type':'box','layout':'vertical','contents':[{'type':'button','style':'primary','color':'#986C43','action':{'type':'uri','label':'訂單明細','uri':'https://liff.line.me/'+liff_id+'/#order/'+o['id']}}]}}
 return [{'type':'flex','altText':'川記訂單 '+o.get('pickup_number',o['id'])+'｜NT$ '+str(o['total']),'contents':bubble}]

def chat_receipt_messages(o,config):
 # Only call with a stored, authorized order; never trust a browser-provided total.
 text='【網頁訂單紀錄｜請店家確認】\n'+order_text(o,config)
 chunks=[]; current=''; units=0
 for char in text:
  width=2 if ord(char)>0xffff else 1
  if units+width>4500:
   chunks.append(current); current=''; units=0
  current+=char; units+=width
 if current: chunks.append(current)
 # Extremely long customized orders retain the summary, with explicit disclosure.
 if len(chunks)>5:
  text=f"【網頁訂單紀錄】\n訂單編號：{o.get('pickup_number',o['id'])}\n取餐：{o['pickup']}\n姓名：{o['name']}\n電話：{o['phone']}\n總額：NT$ {o['total']}\n餐點明細較長，請店家依訂單編號至後台查看完整明細。"
  chunks=[text]
 return [{'type':'text','text':chunk} for chunk in chunks]

def order_cards(o,config,owner=False):
 def text(value,size='sm',color='#263D35',weight='regular'):
  return {'type':'text','text':str(value) or '—','size':size,'color':color,'weight':weight,'wrap':True}
 def box(contents,**kwargs): return {'type':'box','layout':'vertical','contents':contents,**kwargs}
 def rule(): return {'type':'separator','margin':'lg','color':'#E3E9E3'}
 title='店家新訂單' if owner else '訂單已送出'
 bubbles=[]
 # Paginate rather than dropping bowls or customization on larger orders.
 for start in range(0,len(o['items']),5):
  body=[text('到店自取','sm','#687C71'),text(o['pickup'],'xl',weight='bold'),text('訂單編號','sm','#687C71'),text(o.get('pickup_number',o['id']),'48px',weight='bold'),rule(),text('取餐人  '+o['name']+' · '+o['phone']),rule()]
  for n,i in enumerate(o['items'][start:start+5],start+1):
   size=(' 大碗' if i['size']=='large' else ' 小碗') if i['size'] else ''
   row=box([text(str(n)+'. '+i['name']+size,'md',weight='bold'),text('×'+str(i['qty'])+'　NT$ '+str(i['subtotal']),'sm','#24654F',weight='bold')],spacing='xs',margin='lg')
   if i['category']=='noodle':
    details=[i['spicy']]
    if i['omit']: details.append('不加：'+'、'.join(i['omit']))
    if i['extras']: details.append('加料：'+'、'.join(x['name']+' ×'+str(x['qty']) for x in i['extras']))
    if i['basil']: details.append('香菜換九層塔')
    row['contents'].append(text(' / '.join(details),'xs','#687C71'))
   if i.get('note'): row['contents'].append(text('這份備註：'+i['note'],'xs','#865D22'))
   body.append(row)
  body.extend([rule(),text('訂單總額','sm','#687C71'),text('NT$ '+str(o['total']),'xxl',weight='bold'),box([text('現場付款 · '+('已收款' if o['payment_status']=='paid' else '尚未付款'),'sm','#865D22')],backgroundColor='#FFF2DB',paddingAll='md',cornerRadius='md')])
  body.append(text('餐具：'+('需要' if o.get('utensils',True) else '不需要'),'sm',weight='bold'))
  if o['note']: body.extend([text('訂單備註','sm',weight='bold'),text(o['note'])])
  if config['address']: body.extend([rule(),text('取餐地址','xs','#687C71'),text(config['address'])])
  if config['phone']: body.append(text('店家電話 '+config['phone']))
  body.append(text('請至後台確認訂單並安排製作。' if owner else '訂單已登記，店家正在確認與安排製作。','xs','#687C71'))
  if len(o['items'])>5: body.append(text('餐點明細 '+str(start//5+1)+' / '+str((len(o['items'])+4)//5)+' · 總額為整筆訂單','xs','#687C71'))
  bubbles.append({'type':'bubble','size':'mega','header':box([text(config['name'],'lg','#FFFFFF','bold'),text(title,'sm','#E4D9BE')],backgroundColor='#174B3B',paddingAll='xl',spacing='sm'),'body':box(body,paddingAll='xl',spacing='sm',backgroundColor='#FFFFFF')})
 return [{'type':'flex','altText':(config['name']+'｜'+title+'｜'+o.get('pickup_number',o['id'])+'｜NT$ '+str(o['total']))[:400],'contents':group[0] if len(group)==1 else {'type':'carousel','contents':group}} for group in [bubbles[k:k+4] for k in range(0,len(bubbles),4)]]

def enqueue(db,order_id,recipient,kind,text):
 # Split long orders into LINE's allowed text message sizes, preserving all bowls.
 chunks=[text[i:i+4500] for i in range(0,len(text),4500)] if isinstance(text,str) else []
 payload={'to':recipient,'messages':[{'type':'text','text':c} for c in chunks[:5]] if isinstance(text,str) else text}
 db.execute('INSERT INTO web_outbox(id,order_id,recipient,payload,retry_key) VALUES (?,?,?,?,?)',(order_id+':'+kind,order_id,recipient,js(payload),str(uuid.uuid4())))

def reserve_pickup_number(db,pickup,order_id):
 # A date-scoped reservation lives in the same transaction as the order.
 # Never release cancelled numbers: old receipts must remain unambiguous.
 used={r[0] for r in db.execute('SELECT number FROM pickup_numbers WHERE pickup_date=?',(pickup[:10],))}
 available=[str(n) for n in range(1000,10000) if str(n) not in used]
 if not available: raise Problem('這個取餐日期的訂單已滿，請選擇其他日期。',409)
 number=secrets.choice(available)
 db.execute('INSERT INTO pickup_numbers(pickup_date,number,order_id) VALUES (?,?,?)',(pickup[:10],number,order_id))
 return number

def place_order(db,user,data,demo=False,owner='',now=None):
 idem=data.get('idempotency_key','')
 if not isinstance(idem,str) or not re.fullmatch('[a-zA-Z0-9-]{16,80}',idem): raise Problem('請重新整理結帳頁再試一次')
 existing=db.execute('SELECT data FROM web_orders WHERE user_id=? AND idem=?',(user,idem)).fetchone()
 if existing: return json.loads(existing[0])
 config=settings(db)
 if not config['accepting']: raise Problem('店家暫停接單，請稍後再試',409)
 if data.get('payment_method')!='cash': raise Problem('線上付款尚未開通，請選擇現場付款')
 name=str(data.get('name','')).strip(); phone=re.sub(r'[\s()-]','',str(data.get('phone','')))
 if not 1<=len(name)<=30: raise Problem('請填寫取餐姓名，最多30字')
 if not re.fullmatch(r'09\d{8}',phone): raise Problem('請填寫10碼台灣手機號碼')
 note=str(data.get('note','')).strip()
 if len(note)>200: raise Problem('備註最多200字')
 pickup=pickup_check(data.get('pickup',''),config,now)
 priced=price_cart(db,data.get('items'))
 if type(data.get('expected_total')) is not int or data.get('expected_total')!=priced['total']: raise Problem('菜單價格已更新，請返回購物車重新確認金額。',409)
 created=(now or datetime.now(TZ)).isoformat(timespec='seconds')
 o={**priced,'id':'CJ'+(now or datetime.now(TZ)).strftime('%m%d')+'-'+secrets.token_hex(4).upper(),'name':name,'phone':phone,'pickup':pickup,'note':note,'status':'new','source':'line','external_order_id':None,'print_status':'not_connected','payment_method':'cash','payment_status':'unpaid','created_at':created,'demo':demo}
 if type(data.get('utensils',True)) is not bool: raise Problem('請確認餐具選項')
 o['utensils']=data.get('utensils',True)
 o['pickup_number']=reserve_pickup_number(db,pickup,o['id'])
 db.execute('INSERT INTO web_orders VALUES (?,?,?,?,?)',(o['id'],user,idem,js(o),created))
 if not demo:
  enqueue(db,o['id'],user,'customer',order_cards(o,config))
  recipients={r[0] for r in db.execute('SELECT user_id FROM notification_owners')}
  recipients.update(x.strip() for x in owner.split(',') if re.fullmatch(r'U[0-9a-f]{32}',x.strip()))
  for recipient in sorted(recipients): enqueue(db,o['id'],recipient,'owner-'+recipient,order_cards(o,config,owner=True))
 return o

def get_orders(db,start='',end='',user=None):
 sql='SELECT data FROM web_orders WHERE 1=1'; params=[]
 for date in (start,end):
  if date:
   try: datetime.strptime(date,'%Y-%m-%d')
   except ValueError: raise Problem('日期篩選格式錯誤')
 if start: sql+=' AND substr(created,1,10)>=?'; params.append(start)
 if end: sql+=' AND substr(created,1,10)<=?'; params.append(end)
 if user: sql+=' AND user_id=?'; params.append(user)
 sql+=' ORDER BY created DESC, rowid DESC'
 return [json.loads(r[0]) for r in db.execute(sql,params)]

def update_order(db,data):
 row=db.execute('SELECT data FROM web_orders WHERE id=?',(data.get('id'),)).fetchone()
 if not row: raise Problem('找不到訂單',404)
 o=json.loads(row[0]); action=data.get('action')
 if action=='paid':
  if o['status']=='cancelled': raise Problem('取消訂單不能收款')
  o['payment_status']='paid'
 elif action=='status':
  target=data.get('status')
  if target not in TRANSITIONS[o['status']]: raise Problem('訂單狀態已更新，請重新整理',409)
  if target=='completed' and o['payment_status']!='paid': raise Problem('請先確認已收款，再完成訂單')
  if target=='cancelled' and o['payment_status']=='paid': raise Problem('此單已收款，請先人工處理退款；目前不支援已付款訂單取消')
  o['status']=target
 else: raise Problem('操作不支援')
 db.execute('UPDATE web_orders SET data=? WHERE id=?',(js(o),o['id'])); return o

def stats(orders):
 valid=[o for o in orders if o['status']!='cancelled']; days={}; top={}
 for o in valid:
  day=o['created_at'][:10]; days[day]=days.get(day,0)+o['total']
  for i in o['items']:
   key=i['product_id']; entry=top.setdefault(key,{'name':i['name'],'qty':0,'amount':0}); entry['qty']+=i['qty']; entry['amount']+=i['subtotal']
 return {'count':len(valid),'amount':sum(o['total'] for o in valid),'paid':sum(o['total'] for o in valid if o['payment_status']=='paid'),'pending':sum(o['status'] in ('new','preparing','ready') for o in valid),'cancelled':len(orders)-len(valid),'average':round(sum(o['total'] for o in valid)/len(valid)) if valid else 0,'days':[{'date':k,'amount':v} for k,v in sorted(days.items())],'top':sorted(top.values(),key=lambda x:x['qty'],reverse=True)[:8]}


