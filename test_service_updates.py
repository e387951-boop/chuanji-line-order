import io
import json
import unittest
from datetime import datetime, timedelta
from urllib.error import HTTPError
from web_store import (connect, settings, set_day_off, closed_today, earliest_pickup,
                       pickup_check, place_order, price_cart, customer_chat_card, Problem, TZ)
from line_delivery import failure_reason


class ServiceUpdates(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:')
  self.now=datetime(2026,9,30,14,46,tzinfo=TZ)
 def tearDown(self): self.db.close()
 def data(self):
  items=[{'product_id':'n0','size':'small','extras':{'a5':2},'note':'油條另外包'}]
  return {'idempotency_key':'service-update-123456','items':items,
          'name':'測試','phone':'0900000000','payment_method':'cash','pickup_mode':'asap',
          'pickup':'invalid-client-time','expected_total':price_cart(self.db,items)['total']}
 def test_actual_submission_plus_fifteen(self):
  self.assertEqual(earliest_pickup(settings(self.db),self.now),'2026-09-30 15:01')
  self.assertEqual(earliest_pickup(settings(self.db),self.now.replace(second=40)),'2026-09-30 15:02')
  order=place_order(self.db,'test-user',self.data(),True,now=self.now)
  self.assertEqual(order['pickup'],'2026-09-30 15:01')
  self.assertEqual(place_order(self.db,'test-user',self.data(),True,now=self.now+timedelta(minutes=8))['pickup'],order['pickup'])
 def test_manual_minutes_and_business_limits(self):
  config=settings(self.db)
  self.assertEqual(pickup_check('2026-09-30 15:07',config,self.now),'2026-09-30 15:07')
  for value in ['2026-09-30 15:00','2026-09-30 23:01','2026-10-03 12:03']:
   with self.assertRaises(Problem): pickup_check(value,config,self.now)
  for hour,minute in [(22,46),(23,50)]:
   with self.assertRaises(Problem): earliest_pickup(config,self.now.replace(hour=hour,minute=minute))
  self.assertEqual(earliest_pickup(config,self.now.replace(hour=10,minute=0)),'2026-09-30 11:00')
 def test_rest_expires_in_taipei_and_rejects_stale_checkout(self):
  self.assertTrue(set_day_off(self.db,True,self.now)['closed_today'])
  self.assertTrue(closed_today(settings(self.db),self.now.replace(hour=23,minute=59)))
  self.assertFalse(closed_today(settings(self.db),datetime(2026,10,1,0,0,tzinfo=TZ)))
  with self.assertRaisesRegex(Problem,'今天休息'): place_order(self.db,'test-user',self.data(),True,now=self.now)
  self.assertFalse(set_day_off(self.db,False,self.now)['closed_today'])
  self.assertEqual(place_order(self.db,'test-user',self.data(),True,now=self.now)['pickup'],'2026-09-30 15:01')
 def test_rest_does_not_cancel_existing_order(self):
  order=place_order(self.db,'test-user',self.data(),True,now=self.now)
  set_day_off(self.db,True,self.now)
  self.assertEqual(place_order(self.db,'test-user',self.data(),True,now=self.now)['id'],order['id'])
  saved=json.loads(self.db.execute('SELECT data FROM web_orders').fetchone()[0])
  self.assertEqual(saved['status'],'new')
 def test_chat_has_full_details_and_inbound_text(self):
  order=place_order(self.db,'test-user',self.data(),True,now=self.now)
  messages=customer_chat_card(order,'123-test',settings(self.db))
  raw=json.dumps(messages,ensure_ascii=False)
  for value in ['四喜麵線糊','油條另外包',order['pickup_number'],'0900000000']:
   self.assertIn(value,raw)
  self.assertEqual(messages[-1]['type'],'text')
  self.assertIn(order['pickup_number'],messages[-1]['text'])
  order['items']*=40
  self.assertLessEqual(len(customer_chat_card(order,'123-test')),5)
 def test_safe_error_categories(self):
  monthly=HTTPError('https://api.line.me',429,'',{},io.BytesIO(b'{"message":"You have reached your monthly limit."}'))
  self.assertEqual(failure_reason(monthly)[0],'monthly_limit')
  invalid=HTTPError('https://api.line.me',401,'',{},io.BytesIO(b'{"message":"secret-token"}'))
  code,message=failure_reason(invalid)
  self.assertEqual(code,'401'); self.assertNotIn('secret-token',message)
  self.assertEqual(failure_reason(OSError('secret-token'))[0],'network')

if __name__=='__main__': unittest.main()
