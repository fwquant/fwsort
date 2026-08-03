# -*- coding: utf-8 -*-
"""验证 Polymarket 配置加载"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_ENV", "development")

from dotenv import load_dotenv
load_dotenv()

from fwsort.config import get_settings
s = get_settings()

print("=== Polymarket 配置检查 ===")
print(f"POLYMARKET_CHAIN: {s.POLYMARKET_CHAIN}")
print(f"POLYMARKET_HOST: {s.POLYMARKET_HOST}")
print(f"TRADE_MODE: {s.TRADE_MODE}")
print(f"is_simulator: {s.is_simulator}")
pk = "已配置" if s.POLYMARKET_PRIVATE_KEY else "未配置"
print(f"POLYMARKET_PRIVATE_KEY: {pk}")
print(f"POLYMARKET_WALLET_ADDRESS: {s.POLYMARKET_WALLET_ADDRESS or '未配置'}")
ak = "已配置" if s.POLYMARKET_APIKEY else "未配置"
print(f"POLYMARKET_APIKEY: {ak}")
sk = "已配置" if s.POLYMARKET_SECRET else "未配置"
print(f"POLYMARKET_SECRET: {sk}")
pp = "已配置" if s.POLYMARKET_PASSPHRASE else "未配置"
print(f"POLYMARKET_PASSPHRASE: {pp}")
rk = "已配置" if s.POLYMARKET_RELAYER_API_KEY else "未配置"
print(f"POLYMARKET_RELAYER_API_KEY: {rk}")
print(f"POLYMARKET_RELAYER_API_KEY_ADDRESS: {s.POLYMARKET_RELAYER_API_KEY_ADDRESS or '未配置'}")
print(f"polymarket_missing_keys: {s.polymarket_missing_keys}")

# 测试 _mask_secret 函数
print("\n=== 脱敏函数测试 ===")
from router.polymarket_router import _mask_secret
if s.POLYMARKET_PRIVATE_KEY:
    print(f"PRIVATE_KEY 脱敏: {_mask_secret(s.POLYMARKET_PRIVATE_KEY, 'POLYMARKET_PRIVATE_KEY')}")
else:
    print("PRIVATE_KEY 脱敏: [POLYMARKET_PRIVATE_KEY]")
if s.POLYMARKET_APIKEY:
    print(f"APIKEY 脱敏: {_mask_secret(s.POLYMARKET_APIKEY, 'POLYMARKET_APIKEY')}")
else:
    print("APIKEY 脱敏: [POLYMARKET_APIKEY]")
print(f"空字符串脱敏: {_mask_secret('', 'TEST_VAR')}")
print(f"短字符串脱敏: {_mask_secret('abc', 'SHORT_VAR')}")
