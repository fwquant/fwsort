"""快速诊断脚本：检查生产库与演示库的管理员状态"""
import sqlite3
import bcrypt
import os

DB_PROD = "./data/fwsort.db"
DB_DEMO = "./data/fwsort_demo.db"


def inspect(db_path: str, label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  [{label}] {db_path}")
    print(f"  exists={os.path.exists(db_path)}")
    if not os.path.exists(db_path):
        return
    c = sqlite3.connect(db_path).cursor()

    # 表清单
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f"  tables={tables}")

    if "user" not in tables:
        print("  ⚠️ user 表不存在")
        return

    # user 表结构
    cols = [r[1] for r in c.execute("PRAGMA table_info(user)").fetchall()]
    print(f"  user.columns={cols}")

    # 所有 role>=3 的用户
    print("\n  --- 管理员账户 (role>=3) ---")
    rows = c.execute(
        "SELECT id, email, nickname, role, status, substr(password_hash, 1, 30) FROM user WHERE role>=3"
    ).fetchall()
    if not rows:
        print("  ❌ 没有管理员账户")
    for r in rows:
        uid, email, nick, role, status, ph_preview = r
        print(f"    id={uid} email={email} nickname={nick} role={role} status={status}")
        print(f"      hash_preview={ph_preview}...")

    # 验证默认密码
    print("\n  --- 密码验证 ---")
    admins = c.execute("SELECT id, email, password_hash FROM user WHERE role>=3").fetchall()
    if not admins:
        print("  无管理员可验证")
    for a in admins:
        uid, email, ph = a
        if not ph:
            print(f"    id={uid} email={email} → password_hash 为空！")
            continue
        try:
            match_admin = bcrypt.checkpw(b"admin123456", ph.encode("utf-8"))
            match_demo = bcrypt.checkpw(b"demo123456", ph.encode("utf-8"))
            print(f"    id={uid} email={email}")
            print(f"      admin123456 match = {match_admin}")
            print(f"      demo123456  match = {match_demo}")
        except Exception as e:
            print(f"    id={uid} email={email} 验证失败: {e}")

    # 统计
    print("\n  --- 数据统计 ---")
    for tbl in ("user", "execution_account", "vote_decision", "agent_prediction", "strategy_performance"):
        if tbl in tables:
            cnt = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"    {tbl}: {cnt} 条")


if __name__ == "__main__":
    inspect(DB_PROD, "生产库")
    inspect(DB_DEMO, "演示库")
