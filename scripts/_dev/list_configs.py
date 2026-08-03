"""查看所有配置 key"""
import sqlite3

DB = "./data/fwsort.db"
c = sqlite3.connect(DB).cursor()

rows = c.execute("SELECT config_key, default_value, config_value, value_type, [group] FROM system_config ORDER BY [group], config_key").fetchall()
for r in rows:
    cur = r[2] if r[2] else "(default)"
    print(f"{r[0]:45s} | type={r[3]:6s} | group={r[4]:15s} | default={str(r[1])[:30]} | current={str(cur)[:30]}")