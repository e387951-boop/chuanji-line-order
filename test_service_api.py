import json
import threading
import unittest
from http.client import HTTPConnection
from web_server import Handler, ThreadingHTTPServer
from web_store import connect


class ServiceAPI(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:')
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  self.server.db=self.db; self.server.demo=True
  self.server.liff_id='test'; self.server.owner=''; self.server.token=''
  self.server.admin_password='unused'; self.server.origin='http://127.0.0.1:'+str(self.server.server_port)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
  self.cookie=''; self.csrf=''
  status,data=self.call('/api/session'); self.csrf=data['csrf']
 def tearDown(self):
  self.server.shutdown(); self.server.server_close(); self.thread.join(); self.db.close()
 def call(self,path,data=None,csrf=None):
  conn=HTTPConnection('127.0.0.1',self.server.server_port)
  headers={'Cookie':self.cookie,'Origin':self.server.origin,'X-CSRF-Token':self.csrf if csrf is None else csrf,'Content-Type':'application/json'}
  conn.request('POST' if data is not None else 'GET',path,body=json.dumps(data) if data is not None else None,headers=headers)
  res=conn.getresponse()
  if res.getheader('Set-Cookie'): self.cookie=res.getheader('Set-Cookie').split(';')[0]
  status=res.status; data=json.loads(res.read()); conn.close(); return status,data
 def test_day_off_requires_admin_and_csrf(self):
  self.assertEqual(self.call('/api/admin/day-off',{'enabled':True})[0],401)
  self.assertEqual(self.call('/api/admin/login',{})[0],200)
  self.assertEqual(self.call('/api/admin/day-off',{'enabled':True},csrf='bad')[0],403)
  self.assertEqual(self.call('/api/admin/day-off',{'enabled':'yes'})[0],400)
  status,data=self.call('/api/admin/day-off',{'enabled':True})
  self.assertEqual(status,200); self.assertTrue(data['closed_today'])
  self.assertTrue(self.call('/api/catalog')[1]['settings']['closed_today'])
  self.assertFalse(self.call('/api/admin/day-off',{'enabled':False})[1]['closed_today'])
 def test_closed_notification_queued_once_and_contains_exact_text(self):
  self.call('/api/admin/login',{}); self.call('/api/admin/day-off',{'enabled':True})
  self.server.demo=False  # Queue only; no delivery worker exists in this test.
  for _ in range(2): self.assertEqual(self.call('/api/store-entry',{})[0],200)
  rows=self.db.execute('SELECT payload FROM web_outbox').fetchall()
  self.assertEqual(len(rows),1)
  self.assertEqual(json.loads(rows[0][0])['messages'][0]['text'],'今天休息不好意思🙏🙏')
 def test_chat_state_requires_order_ownership(self):
  self.assertEqual(self.call('/api/chat-result',{'id':'other','state':'sent'})[0],404)
  user=self.db.execute('SELECT user_id FROM web_sessions').fetchone()[0]
  with self.db: self.db.execute('INSERT INTO web_orders VALUES (?,?,?,?,?)',('mine',user,'idem','{}','test'))
  self.assertEqual(self.call('/api/chat-result',{'id':'mine','state':'arbitrary'})[0],400)
  self.assertEqual(self.call('/api/chat-result',{'id':'mine','state':'sent'})[0],200)
  self.assertEqual(self.db.execute('SELECT state FROM chat_deliveries WHERE order_id=?',('mine',)).fetchone()[0],'sent')
 def test_diagnostics_requires_admin(self):
  self.assertEqual(self.call('/api/admin/line-status')[0],401)
  self.call('/api/admin/login',{})
  self.assertEqual(self.call('/api/admin/line-status')[1],{'owners':0,'failed':0})

if __name__=='__main__': unittest.main()
