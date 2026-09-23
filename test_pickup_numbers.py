import unittest, uuid, sqlite3
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from web_store import connect, reserve_pickup_number, place_order, price_cart, Problem, TZ, customer_chat_card, order_cards, settings

class PickupNumbersTests(unittest.TestCase):
 def setUp(self): self.db=connect(':memory:')
 def tearDown(self): self.db.close()
 def test_future_date_reserved_and_midnight_does_not_erase(self):
  with self.db, patch('web_store.secrets.choice',side_effect=lambda available: '8723' if '8723' in available else available[0]):
   self.assertEqual(reserve_pickup_number(self.db,'2026-09-25 12:00','future'),'8723')
   self.assertEqual(reserve_pickup_number(self.db,'2026-09-23 12:00','today'),'8723')
   self.assertEqual(reserve_pickup_number(self.db,'2026-09-24 12:00','tomorrow'),'8723')
   self.assertNotEqual(reserve_pickup_number(self.db,'2026-09-25 13:00','other'),'8723')
 def test_unique_constraint_and_full_date(self):
  with self.db:
   self.db.executemany('INSERT INTO pickup_numbers VALUES (?,?,?)',[('2026-09-25',str(n),str(n)) for n in range(1000,10000)])
   with self.assertRaises(Problem): reserve_pickup_number(self.db,'2026-09-25 12:00','overflow')
   with self.assertRaises(sqlite3.IntegrityError): self.db.execute('INSERT INTO pickup_numbers VALUES (?,?,?)',('2026-09-25','8723','duplicate'))
 def test_order_idempotency_and_large_cards(self):
  items=[{'product_id':'n0','size':'small'}]
  data={'idempotency_key':'returning-test-12345','payment_method':'cash','name':'測試','phone':'0912345678','pickup':'2026-09-25 12:00','items':items,'expected_total':price_cart(self.db,items)['total']}
  with self.db:
   o=place_order(self.db,'customer',data,demo=True,now=datetime(2026,9,23,12,tzinfo=TZ))
   again=place_order(self.db,'customer',data,demo=True)
  self.assertEqual(o,again)
  self.assertEqual(self.db.execute('SELECT count(*) FROM pickup_numbers').fetchone()[0],1)
  self.assertRegex(o['pickup_number'],r'^[1-9][0-9]{3}$')
  for cards in (customer_chat_card(o,'liff-id'),order_cards(o,settings(self.db)),order_cards(o,settings(self.db),owner=True)):
   body=cards[0]['contents']['body']['contents']
   self.assertTrue(any(x.get('text')==o['pickup_number'] and x.get('size')=='48px' for x in body))
 def test_reservation_rollback(self):
  with self.assertRaises(RuntimeError):
   with self.db:
    reserve_pickup_number(self.db,'2026-09-25 12:00','fail')
    raise RuntimeError('failed order')
  self.assertEqual(self.db.execute('SELECT count(*) FROM pickup_numbers').fetchone()[0],0)
 def test_restart_keeps_reservation(self):
  path=Path(__file__).resolve().parent/('test-reservation-'+uuid.uuid4().hex+'.sqlite3')
  db=None
  try:
   db=connect(str(path))
   with db: number=reserve_pickup_number(db,'2026-09-25 12:00','persisted')
   db.close();db=connect(str(path))
   self.assertEqual(db.execute('SELECT number FROM pickup_numbers WHERE order_id=?',('persisted',)).fetchone()[0],number)
  finally:
   if db: db.close()
   for suffix in ('','-wal','-shm'): Path(str(path)+suffix).unlink(missing_ok=True)


if __name__=='__main__': unittest.main()
