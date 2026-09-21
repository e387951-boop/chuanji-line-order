import unittest
from web_store import connect, price_cart, settings, chat_receipt_messages

class ChatReceiptTests(unittest.TestCase):
 def setUp(self): self.db=connect(':memory:')
 def tearDown(self): self.db.close()
 def order(self):
  priced=price_cart(self.db,[{'product_id':'n0','size':'small','extras':{'a0':1,'a1':1,'a5':2},'note':'另外包'}])
  return {**priced,'id':'CJ-TEST','pickup':'2026-09-22 12:00','name':'測試','phone':'0900000000','note':'訂單備註','utensils':False,'payment_status':'unpaid'}
 def test_server_price_and_customizations_in_chat(self):
  messages=chat_receipt_messages(self.order(),settings(self.db))
  text=''.join(m['text'] for m in messages)
  for expected in ('CJ-TEST','165','另外包','油條（另外包裝）×2','餐具：不需要','訂單備註'):
   self.assertIn(expected,text)
  self.assertTrue(all(m['type']=='text' for m in messages))
 def test_large_unicode_orders_fit_line_limits(self):
  order=self.order();order['items']=order['items']*40
  for item in order['items']: item['note']='🍜'*120
  messages=chat_receipt_messages(order,settings(self.db))
  self.assertLessEqual(len(messages),5)
  for message in messages:self.assertLessEqual(len(message['text'].encode('utf-16-le'))//2,5000)

if __name__=='__main__': unittest.main()
