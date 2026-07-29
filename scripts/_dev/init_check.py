import sys
sys.path.insert(0, 'd:/git/github/fwquant/fwsort')
from fwsort.database import sync_engine, async_engine
from fwsort import models  # 注册模型
from fwsort.database import Base
Base.metadata.create_all(bind=sync_engine)
print('sync engine tables created')

import sqlite3
conn = sqlite3.connect('fwsort.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('tables after sync create_all:', len(tables), tables)
conn.close()
