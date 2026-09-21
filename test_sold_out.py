import unittest
from web_store import connect,catalogue,save_product,price_cart,Problem
class SoldOutTests(unittest.TestCase):
 def test_unavailable_visible_but_rejected_then_restored(self):
  db=connect(':memory:')
  try:
   p=next(p for p in catalogue(db,True) if p['id']=='n0')
   save_product(db,{**p,'active':False})
   self.assertFalse(next(p for p in catalogue(db) if p['id']=='n0')['active'])
   with self.assertRaises(Problem): price_cart(db,[{'product_id':'n0','size':'small'}])
   p=next(p for p in catalogue(db,True) if p['id']=='n0')
   save_product(db,{**p,'active':True})
   self.assertGreater(price_cart(db,[{'product_id':'n0','size':'small'}])['total'],0)
  finally: db.close()
if __name__=='__main__':unittest.main()
