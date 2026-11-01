import unittest,json,time
from datetime import datetime
from core import TZ
from web_store import connect,place_order,price_cart
import receipt_delivery as r
class ReceiptFallback(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:');self.user='U'+'a'*32;self.owner='U'+'b'*32
  items=[{'product_id':'n0','size':'small'}]
  data={'idempotency_key':'fallback-test-123456','items':items,'name':'測試','phone':'0900000000','payment_method':'cash','pickup_mode':'asap','expected_total':price_cart(self.db,items)['total']}
  self.o=place_order(self.db,self.user,data,now=datetime(2026,11,1,12,tzinfo=TZ))
 def tearDown(self):self.db.close()
 def jobs(self):return self.db.execute('SELECT * FROM web_outbox').fetchall()
 def event(self,user=None):return {'type':'message','source':{'type':'user','userId':user or self.user},'message':{'type':'text','text':'訂單查詢碼：'+self.o['id']}}
 def test_timeout_deduplicates(self):
  r.enroll(self.db,self.o,True,100);r.schedule(self.db,self.owner,159);self.assertEqual(len(self.jobs()),0)
  r.schedule(self.db,self.owner,160);r.schedule(self.db,self.owner,200);self.assertEqual(len(self.jobs()),1)
  payload=json.loads(self.jobs()[0]['payload']);self.assertEqual(payload['to'],self.owner);self.assertEqual(payload['messages'][1]['type'],'flex')
 def test_webhook_owner_and_late_confirmation(self):
  r.enroll(self.db,self.o,True,100);r.schedule(self.db,self.owner,160)
  self.assertFalse(r.acknowledge(self.db,self.event(self.owner)));self.assertFalse(r.status(self.db,self.o['id'])['confirmed'])
  self.assertTrue(r.acknowledge(self.db,self.event()));self.assertTrue(r.acknowledge(self.db,self.event()))
  self.assertEqual(self.jobs()[0]['state'],'cancelled');self.assertTrue(r.status(self.db,self.o['id'])['confirmed'])
 def test_sent_waits_for_webhook(self):
  r.enroll(self.db,self.o,True,100);r.report(self.db,self.o['id'],'sent',{'reason':'sent'})
  self.assertFalse(r.status(self.db,self.o['id'])['confirmed']);r.schedule(self.db,self.owner,160);self.assertEqual(len(self.jobs()),1)
 def test_client_mode_late_failure(self):
  r.enroll(self.db,self.o,False,100);r.report(self.db,self.o['id'],'sent',{'reason':'sent'});r.report(self.db,self.o['id'],'failed',{'reason':'api_error'})
  r.schedule(self.db,self.owner,160);self.assertEqual(len(self.jobs()),0);self.assertEqual(r.status(self.db,self.o['id'])['chat'],'sent')
 def test_missing_owner_recovers(self):
  r.enroll(self.db,self.o,True,100);r.schedule(self.db,'',160);self.assertEqual(r.status(self.db,self.o['id'])['fallback'],'no_recipient')
  self.db.execute('INSERT INTO notification_owners VALUES (?,?)',(self.owner,'now'));r.schedule(self.db,self.owner,221);self.assertEqual(len(self.jobs()),1)
 def test_old_and_demo_orders(self):
  r.schedule(self.db,self.owner,time.time()+1000);self.assertEqual(len(self.jobs()),0)
  r.enroll(self.db,{**self.o,'demo':True},True,100);r.schedule(self.db,self.owner,160);self.assertEqual(len(self.jobs()),0)
 def test_safe_diagnostics(self):
  r.report(self.db,self.o['id'],'failed',{'reason':'token-secret','context':'private-id','error_code':'Bearer secret!'});d=dict(self.db.execute('SELECT * FROM chat_diagnostics').fetchone())
  self.assertNotIn('secret',json.dumps(d));self.assertEqual(d['reason'],'check_failed')
 def test_restart_keeps_watch(self):
  r.enroll(self.db,self.o,True,100);r.install(self.db);r.schedule(self.db,self.owner,160);self.assertEqual(len(self.jobs()),1)
if __name__=='__main__':unittest.main()
