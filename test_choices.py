import unittest
from web_store import connect, price_cart, catalogue, Problem

class ChoiceTests(unittest.TestCase):
 def setUp(self): self.db=connect(':memory:')
 def tearDown(self): self.db.close()
 def quote(self,**kwargs): return price_cart(self.db,[{'product_id':'n0','size':'small',**kwargs}])
 def test_two_distinct_addons_and_item_note(self):
  result=self.quote(extras={'a0':1,'a1':1},note='分開裝',omit=['蒜泥'])
  self.assertEqual(len(result['items'][0]['extras']),2)
  self.assertEqual(result['items'][0]['note'],'分開裝')
 def test_reject_invalid_customization(self):
  for data in [{'extras':{'a0':2}},{'extras':{'a0':1,'a1':1,'a2':1}},{'extras':{'a5':-1}},{'extras':{'a5':1.5}},{'omit':['大腸']},{'omit':['麵線糊']},{'note':'字'*121}]:
   with self.subTest(data=data),self.assertRaises(Problem): self.quote(**data)
 def test_catalog_has_six_in_bowl_addons_and_youtiao(self):
  self.assertEqual({p['name'] for p in catalogue(self.db) if p['category']=='addon'},{'大腸','蚵仔','蝦仁','發魷魚','虱目魚漿','貢丸','油條'})
 def test_youtiao_separate_from_bowl_limit(self):
  result=self.quote(extras={'a0':1,'a1':1,'a5':10})
  self.assertEqual(result['total'],95+20+20+15*10)
  self.assertEqual(result['items'][0]['extras'][-1]['name'],'油條（另外包裝）')
  self.assertEqual(result['items'][0]['extras'][-1]['qty'],10)
  with self.assertRaises(Problem): self.quote(extras={'a0':1,'a1':1,'a2':1,'a5':10})

if __name__=='__main__': unittest.main()
