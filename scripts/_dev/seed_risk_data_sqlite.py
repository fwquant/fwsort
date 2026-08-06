# -*- coding: utf-8 -*-
"""独立风控数据初始化脚本（避免触发 fwsort 全量导入）"""
import os
import sys
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

# 直接使用 SQLite3 原生接口
import sqlite3

# 找到数据库文件
def find_db_path():
    """尝试找到 SQLite 数据库路径"""
    candidates = [
        Path("data/fwsort.db"),
        Path("fwsort.db"),
        Path("../data/fwsort.db"),
    ]
    # 从配置文件读取
    config_path = Path("fwsort/config.py")
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
            for line in content.splitlines():
                if "APP_SQLITE_PATH" in line or ("SQLITE" in line and "PATH" in line and "=" in line):
                    print(f"Config line: {line}")
    
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    
    # 扫描 data 目录
    data_dir = Path("data")
    if data_dir.exists():
        for f in data_dir.iterdir():
            if f.suffix == ".db":
                return str(f.resolve())
    return None

db_path = find_db_path()
if not db_path:
    print("ERROR: 找不到数据库文件！")
    sys.exit(1)

print(f"使用数据库: {db_path}")
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA foreign_keys = OFF")
cursor = conn.cursor()

# ========== 1. 检查并创建系统默认风控模板 ==========
cursor.execute("SELECT COUNT(*) FROM risk_profile WHERE owner_id IS NULL AND is_default = 1")
count = cursor.fetchone()[0]

if count == 0:
    print("创建系统默认风控模板...")
    cursor.execute("""
        INSERT INTO risk_profile (
            name, owner_id, is_default, description,
            risk_single_ratio, risk_daily_loss_ratio,
            max_daily_amount, max_daily_count, max_consecutive_failures,
            max_drawdown_ratio, max_open_positions,
            stop_loss_ratio, take_profit_ratio,
            is_active, created_at, updated_at
        ) VALUES (?, NULL, 1, ?, ?, ?, NULL, NULL, ?, NULL, NULL, NULL, NULL, 1, datetime('now'), datetime('now'))
    """, ("系统默认", "系统内置风控模板，包含基础风控参数", 0.2000, 0.1500, 5))
    profile_id = cursor.lastrowid
    print(f"  -> 模板 ID: {profile_id}")
else:
    cursor.execute("SELECT id FROM risk_profile WHERE owner_id IS NULL AND is_default = 1 LIMIT 1")
    profile_id = cursor.fetchone()[0]
    print(f"系统默认模板已存在 (ID: {profile_id})")

# ========== 2. 为所有账户补全风控档案 ==========
cursor.execute("SELECT id, uid FROM execution_account")
accounts = cursor.fetchall()
print(f"账户数量: {len(accounts)}")

for acc_id, acc_uid in accounts:
    cursor.execute("SELECT id FROM account_risk_profile WHERE account_id = ?", (acc_id,))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute("""
            INSERT INTO account_risk_profile (
                account_id, risk_profile_id,
                risk_single_ratio, risk_daily_loss_ratio,
                max_daily_amount, max_daily_count, max_consecutive_failures,
                max_drawdown_ratio, max_open_positions,
                stop_loss_ratio, take_profit_ratio,
                consecutive_failures, is_frozen, frozen_reason,
                created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0, '', datetime('now'), datetime('now'))
        """, (acc_id, profile_id))
        print(f"  -> 账户 {acc_uid} 风控档案已创建")

# ========== 3. 为所有策略补全风控档案 ==========
cursor.execute("SELECT id, task_name, max_daily_amount, max_daily_count, max_consecutive_failures, consecutive_failures FROM auto_strategy")
strategies = cursor.fetchall()
print(f"策略数量: {len(strategies)}")

for strat_id, name, max_amt, max_cnt, max_fail, consec_fail in strategies:
    cursor.execute("SELECT id FROM strategy_risk_profile WHERE auto_strategy_id = ?", (strat_id,))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute("""
            INSERT INTO strategy_risk_profile (
                auto_strategy_id, risk_profile_id,
                max_daily_amount, max_daily_count, max_consecutive_failures,
                consecutive_failures,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """, (strat_id, profile_id, 
              max_amt if max_amt else 50.0, 
              max_cnt if max_cnt else 50, 
              max_fail if max_fail else 5,
              consec_fail if consec_fail else 0))
        print(f"  -> 策略 {name} 风控档案已创建")

# ========== 4. 生成 Mock 风控事件日志（如果为空） ==========
cursor.execute("SELECT COUNT(*) FROM risk_event_log")
log_count = cursor.fetchone()[0]

if log_count == 0 and accounts:
    print("生成 Mock 风控事件日志...")
    now = datetime.utcnow()
    
    # 找一个策略 ID 作为示例
    strat_id = strategies[0][0] if strategies else None
    
    reasons = [
        (1, 1, "风控检查通过", "所有风控规则通过"),
        (2, 2, "风控拦截", "DailyAmountLimitRule: 已达每日最大执行金额($1000.00)"),
        (3, 3, "触发风控冻结", "DailyLossRatioRule: 日亏 16.7% ≥ 阈值 15%"),
        (2, 2, "风控拦截", "DailyCountLimitRule: 已达每日最大执行次数(10次)"),
        (3, 3, "触发风控冻结", "ConsecutiveFailureRule: 连续失败 5 次，触发熔断"),
    ]
    
    for i in range(80):
        acc = random.choice(accounts)
        acc_id = acc[0]
        acc_uid = acc[1]
        
        event_type, severity, title, message = random.choice(reasons)
        
        # 生成事件 UID
        date_str = now.strftime("%Y%m%d")
        rand_hex = secrets.token_hex(4).upper()
        event_uid = f"RSK-{date_str}-{rand_hex}"
        
        cursor.execute("""
            INSERT INTO risk_event_log (
                event_uid, account_id, auto_strategy_id, user_id,
                rule_name, event_type, severity, stage,
                title, detail_json, message,
                balance_snapshot, daily_pnl_snapshot, order_amount_snapshot,
                created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_uid,
            acc_id,
            strat_id,
            random.choice(["DailyLossRatioRule", "DailyAmountLimitRule", "DailyCountLimitRule", "ConsecutiveFailureRule", "SingleOrderRatioRule"]),
            event_type,
            severity,
            random.choice(["pre_vote", "pre_order", "post_settle"]),
            title,
            '{"reason": "Mock event generated by seed script"}',
            message,
            round(random.uniform(500, 5000), 2),
            round(random.uniform(-200, 100), 2),
            round(random.uniform(10, 200), 2),
            now - timedelta(hours=random.randint(1, 720)),
        ))
    
    print(f"  -> 80 条 Mock 事件日志已生成")

conn.commit()
conn.close()

print("\n✅ 风控基础数据初始化完成！")
print("现在可以刷新 /risk 页面查看数据了。")
