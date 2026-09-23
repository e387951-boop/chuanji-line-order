import unittest,json
from datetime import datetime
from web_store import connect,price_cart,place_order,order_cards,settings,TZ
class CardTests(unittest.TestCase):
 def setUp(self): self.db=connect(':memory:')
 def tearDown(self): self.db.close()
 def order(self,count):
  items=[{'product_id':'n0','size':'small','omit':['香菜'],'spicy':'微辣','extras':{'a0':1},'basil':True} for _ in range(count)]
  data={'idempotency_key':'test-cards-123456789','payment_method':'cash','name':'測試','phone':'0912345678','pickup':'2026-09-20 12:00','note':'請分袋','items':items,'expected_total':price_cart(self.db,items)['total']}
  self.db.execute('INSERT INTO notification_owners VALUES (?,?)',('U'+'1'*32,'test'))
  return place_order(self.db,'U'+'2'*32,data,now=datetime(2026,9,20,10,tzinfo=TZ))
 def test_roles_and_queue(self):
  o=self.order(1);rows=self.db.execute('SELECT payload FROM web_outbox').fetchall();self.assertEqual(len(rows),2)
  payloads=[json.loads(r[0]) for r in rows]
  self.assertTrue(all(p['messages'][0]['type']=='flex' for p in payloads))
  self.assertIn('訂單已送出',payloads[0]['messages'][0]['altText']);self.assertIn('店家新訂單',payloads[1]['messages'][0]['altText'])
  rendered=json.dumps(payloads,ensure_ascii=False)
  for value in ['微辣','香菜換九層塔','請分袋',o['pickup_number'],str(o['total'])]: self.assertIn(value,rendered)
 def test_forty_bowls_preserved_and_within_limits(self):
  o=self.order(40);messages=order_cards(o,settings(self.db));self.assertLessEqual(len(messages),5)
  bubbles=[]
  for m in messages:
   c=m['contents'];self.assertLess(len(json.dumps(c,ensure_ascii=False).encode()),50000)
   bubbles.extend(c['contents'] if c['type']=='carousel' else [c])
  self.assertEqual(len(bubbles),8)
  for b in bubbles: self.assertLess(len(json.dumps(b,ensure_ascii=False).encode()),30000)
  raw=json.dumps(bubbles,ensure_ascii=False)
  for n in range(1,41): self.assertIn(str(n)+'. 四喜麵線糊',raw)
if __name__=='__main__': unittest.main()
