"""检查配置表中的 description 字段"""
import sqlite3

DB = "./data/fwsort.db"
c = sqlite3.connect(DB).cursor()

print("=== system_config 表结构 ===")
cols = c.execute("PRAGMA table_info(system_config)").fetchall()
for col in cols:
    print(f"  {col[1]} ({col[2]})")

print("\n=== description 统计 ===")
total = c.execute("SELECT COUNT(*) FROM system_config").fetchone()[0]
with_desc = c.execute("SELECT COUNT(*) FROM system_config WHERE description IS NOT NULL AND description != ''").fetchone()[0]
print(f"  总数: {total}, 有描述: {with_desc}, 无描述: {total - with_desc}")

print("\n=== 有描述的配置（前 10 条）===")
rows = c.execute("SELECT config_key, description FROM system_config WHERE description IS NOT NULL AND description != '' LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1][:60]}")

print("\n=== 无描述的配置（前 10 条）===")
rows = c.execute("SELECT config_key, default_value, [group] FROM system_config WHERE description IS NULL OR description = '' LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0]}: val={r[1]}, group={r[2]}")