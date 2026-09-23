"""Postgres storage for the ordering service; keeps tables in a private schema."""
import os
import re

COLUMNS = {
 'pickup_numbers': 'pickup_date,number,order_id',
 'web_settings': 'id,data',
 'products': 'id,data,version',
 'photos': 'id,mime,data',
 'web_orders': 'id,user_id,idem,data,created',
 'web_outbox': 'id,order_id,recipient,payload,retry_key,attempts,due,state',
 'notification_owners': 'user_id,created',
 'web_sessions': 'id,csrf,user_id,admin,expires',
}

class Row(dict):
 def __getitem__(self,key):
  return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

def row_factory(cursor):
 names=[c.name for c in cursor.description] if cursor.description else []
 return lambda values: Row(zip(names,values))

def translate(sql):
 """Translate only the small SQLite statement subset used by this application."""
 ignore=sql.startswith('INSERT OR IGNORE INTO ')
 sql=sql.replace('INSERT OR IGNORE INTO ','INSERT INTO ')
 for table,columns in COLUMNS.items():
  sql=sql.replace('INSERT INTO '+table+' VALUES','INSERT INTO '+table+' ('+columns+') VALUES')
 sql=sql.replace('?', '%s')
 if ignore: sql+=' ON CONFLICT DO NOTHING'
 return sql

class PostgresStore:
 persistent=True
 def __init__(self):
  import psycopg
  self.driver=psycopg
  self.depth=0
  self.conn=None
  self._connect()

 def _connect(self):
  self.conn=self.driver.connect(
   host=os.environ['PGHOST'], port=int(os.getenv('PGPORT','5432')),
   dbname=os.getenv('PGDATABASE','postgres'), user=os.environ['PGUSER'],
   password=os.environ['PGPASSWORD'], sslmode='require', connect_timeout=15,
   autocommit=True, prepare_threshold=None, row_factory=row_factory,
   options='-c search_path=chuanji,pg_catalog -c statement_timeout=30000')

 def execute(self,sql,params=()):
  if self.conn.closed:
   if self.depth: raise RuntimeError('Database disconnected during transaction')
   self._connect()
  return self.conn.execute(translate(sql),params)

 def executescript(self,script):
  with self:
   self.conn.execute('CREATE SCHEMA IF NOT EXISTS chuanji')
   for statement in script.split(';'):
    statement=statement.strip()
    if not statement or statement.startswith('PRAGMA'): continue
    statement=statement.replace(' BLOB',' BYTEA').replace(' REAL',' DOUBLE PRECISION')
    statement=re.sub(r'(CREATE TABLE IF NOT EXISTS \w+ \()',r'\1rowid BIGINT GENERATED ALWAYS AS IDENTITY, ',statement)
    self.conn.execute(statement)
   self.conn.execute('REVOKE ALL ON SCHEMA chuanji FROM PUBLIC')

 def __enter__(self):
  if self.depth: raise RuntimeError('Nested database transactions are not supported')
  if self.conn.closed: self._connect()
  self.conn.execute('BEGIN')
  # Serialize order writes across overlapping deploys as well as server threads.
  self.conn.execute('SELECT pg_advisory_xact_lock(73429108)')
  self.depth=1
  return self

 def __exit__(self,kind,value,tb):
  try:
   if kind: self.conn.rollback()
   else: self.conn.commit()
  finally: self.depth=0

 def commit(self): self.conn.commit()
 def close(self): self.conn.close()

 def backup(self,target):
  from web_store import connect
  staging=connect(':memory:')
  try:
   with self,staging:
    for table,columns in COLUMNS.items():
     staging.execute('DELETE FROM '+table)
     # Session secrets must never be exported.
     if table=='web_sessions': continue
     for row in self.execute('SELECT '+columns+' FROM '+table+' ORDER BY rowid'):
      staging.execute('INSERT INTO '+table+' ('+columns+') VALUES ('+','.join('?' for _ in columns.split(','))+')',tuple(row.values()))
   staging.backup(target)
  finally: staging.close()
