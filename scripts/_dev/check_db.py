# -*- coding: utf-8 -*-
"""检查数据库状态"""
import sqlite3
import os

db_path = "./data/fwsort.db"
print(f"db path: {db_path}")
print(f"db exists: {os.path.exists(db_path)}")

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t[0] for t in tables]
    print(f"tables: {table_names}")

    if "users" in table_names:
        admins = c.execute("SELECT id, email, role, username FROM users WHERE role>=3").fetchall()
        print(f"admins: {admins}")
        all_users = c.execute("SELECT id, email, role, username FROM users LIMIT 5").fetchall()
        print(f"all users (first 5): {all_users}")
    else:
        print("NO users table")

    conn.close()
else:
    # 也检查一下旧路径
    alt_paths = ["./fwsort.db", "fwsort.db", "./data/fwsort_demo.db"]
    for p in alt_paths:
        if os.path.exists(p):
            print(f"found alt db: {p}")
