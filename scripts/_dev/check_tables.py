import sqlite3
conn = sqlite3.connect('fwsort.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('tables in fwsort.db:', tables)
conn.close()
