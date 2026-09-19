import unittest, tempfile, json, hmac, hashlib, base64
from datetime import datetime
from core import *
from server import init_db, process_event, verify, messages

NOW=datetime(2026,9,20,10,0,tzinfo=TZ)
class OrderingTests(unittest.TestCase):
 def act(self,s,c='',v=''):
  return step(s,c,v,s['rev'],now=NOW)
 def food(self,s):
  for c in ['food:2','size:大碗','qty:2','extra:1','spice','spicy:微辣','basil','add']:
   s,v,o=self.act(s,c)
  return s
 def review(self):
  s=self.food(fresh())
  for c,v in [('checkout',''),('','0912345678'),('','2026-09-20 12:30'),('note:none','')]: s,_,_=self.act(s,c,v)
  return s
 def test_pricing_and_separate_flavours(self):
  s=self.food(fresh()); self.assertEqual(amount(s['cart'][0]),210)
  for c in ['sides','side:4','qty:2']: s,_,_=self.act(s,c)
  self.assertEqual(sum(amount(i) for i in s['cart']),240)
  self.assertTrue(s['cart'][0]['basil']); self.assertEqual(s['cart'][0]['omit'],['香菜'])
 def test_schedule(self):
  for date in ['2026-09-26 12:00','2026-09-20 09:00','2026-09-20 23:00','2026-09-19 12:00']:
   with self.assertRaises(ValueError): pickup_value(date,NOW)
  self.assertEqual(pickup_value('明天 12:00',NOW),'2026-09-21 12:00')
  self.assertEqual(pickup_value('2026-09-20T22:59',NOW),'2026-09-20 22:59')
 def test_confirmation_and_stale_actions(self):
  s=self.review(); old=s['rev']; s,v,o=self.act(s,'confirm'); self.assertIsNotNone(o); self.assertEqual(o['payment'],'unpaid')
  s,v,o=step(s,'confirm',revision=old,now=NOW); self.assertIsNone(o); self.assertEqual(s['stage'],'done')
  s,v,o=self.act(s,'confirm'); self.assertIsNone(o)
 def test_validation_keeps_order(self):
  s=self.food(fresh()); s,_,_=self.act(s,'checkout'); s,_,_=self.act(s,v='123'); self.assertEqual(s['stage'],'phone'); self.assertEqual(len(s['cart']),1)
  s,_,_=self.act(s,'food:0'); self.assertEqual(s['stage'],'phone')
 def test_remove_and_clear(self):
  s=self.food(fresh())
  for c in ['remove','remove:0']: s,_,_=self.act(s,c)
  self.assertFalse(s['cart']); s,_,_=self.act(s,'checkout'); self.assertEqual(s['stage'],'cart')
 def test_payloads(self):
  s=fresh()
  for c in ['start','food:0','size:小碗','qty:1','spice','spicy:不辣','omit']:
   s,v,_=self.act(s,c)
   m=messages(v); self.assertLessEqual(len(m),5)
   for msg in m: self.assertLessEqual(len(msg.get('quickReply',{}).get('items',[])),13)
 def test_signature(self):
  raw=b'{"events":[]}'; key='test-secret'; sig=base64.b64encode(hmac.new(key.encode(),raw,hashlib.sha256).digest()).decode()
  self.assertTrue(verify(raw,sig,key)); self.assertFalse(verify(raw+b' ',sig,key)); self.assertFalse(verify(raw,sig,''))
 def test_transaction_and_notification_persistence(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=init_db(tmp+'/db.sqlite3'); s=self.review(); s['pickup']='2099-09-20 12:00'
   # Use an explicitly valid future weekday for real-clock event processing.
   s['pickup']='2099-09-21 12:00'
   with db: db.execute('INSERT INTO sessions VALUES (?,?)',('Ucustomer',json.dumps(s)))
   event={'source':{'type':'user','userId':'Ucustomer'},'type':'postback','webhookEventId':'event1','replyToken':'test','postback':{'data':json.dumps({'c':'confirm','r':s['rev']})}}
   with db: process_event(db,event,'Uowner')
   self.assertEqual(db.execute('SELECT count(*) FROM orders').fetchone()[0],1)
   with db: process_event(db,event,'Uowner')
   self.assertEqual(db.execute('SELECT count(*) FROM orders').fetchone()[0],1)
   self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE kind='push'").fetchone()[0],1)
   db.close(); db=init_db(tmp+'/db.sqlite3'); self.assertEqual(db.execute('SELECT count(*) FROM orders').fetchone()[0],1); db.close()
if __name__=='__main__': unittest.main()

