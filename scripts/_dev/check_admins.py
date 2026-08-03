# -*- coding: utf-8 -*-
"""检查 user 表中的管理员"""
import sqlite3

db_path = "./data/fwsort.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()

# 先看表结构
cols = c.execute("PRAGMA table_info(user)").fetchall()
print("user table columns:")
for col in cols:
    print(f"  {col[1]} ({col[2]})")

# 查所有用户（只取存在的列）
col_names = [col[1] for col in cols]
select_cols = ", ".join(col_names)
rows = c.execute(f"SELECT {select_cols} FROM user ORDER BY id").fetchall()
print(f"\ntotal users: {len(rows)}")
for r in rows:
    print(f"  {dict(zip(col_names, r))}")

# 查管理员
if "role" in col_names:
    admins = c.execute(f"SELECT {select_cols} FROM user WHERE role>=3").fetchall()
    print(f"\nadmins (role>=3): {len(admins)}")
    for a in admins:
        print(f"  {dict(zip(col_names, a))}")

# 验证密码
try:
    import bcrypt
    print("\n=== 密码验证 ===")
    admins = c.execute("SELECT id, email, password_hash, role, status FROM user WHERE role>=3").fetchall()
    for a in admins:
        uid, email, ph, role, status = a
        match = bcrypt.checkpw(b"admin123456", ph.encode("utf-8")) if ph else False
        print(f"  id={uid} email={email} role={role} status={status}")
        print(f"    pwd_match('admin123456')={match}")
        print(f"    hash_preview={ph[:40] if ph else None}...")
except Exception as e:
    print(f"密码验证失败: {e}")

conn.close()
