# 系统验证脚本 - 测试整个交易闭环流程
import json
import sys
import time
import traceback
from typing import Optional

import requests

# 配置
BASE_URL = "http://localhost:8000"
TEST_EMAIL = "validate_test@fwquant.com"
TEST_PASSWORD = "Validate@2026"
TEST_NICKNAME = "验证测试用户"


class SystemValidator:
    """系统验证器"""

    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.user_id = None
        self.account_id = None
        self.account_uid = None
        self.vote_id = None
        self.order_id = None

    def log(self, level: str, message: str):
        """日志输出"""
        colors = {
            "INFO": "\033[94m",
            "SUCCESS": "\033[92m",
            "WARNING": "\033[93m",
            "ERROR": "\033[91m",
            "RESET": "\033[0m",
        }
        print(f"{colors[level]}[{level}] {message}{colors['RESET']}")

    def test_register(self) -> bool:
        """测试用户注册"""
        self.log("INFO", "测试用户注册...")
        try:
            resp = self.session.post(
                f"{BASE_URL}/api/auth/register",
                json={
                    "email": TEST_EMAIL,
                    "password": TEST_PASSWORD,
                    "nickname": TEST_NICKNAME,
                },
            )
            data = resp.json()
            if data.get("success"):
                self.user_id = data["data"].get("id")
                self.log("SUCCESS", f"用户注册成功，ID: {self.user_id}")
                return True
            else:
                # 可能已存在，尝试登录
                self.log("WARNING", f"注册失败: {data.get('message')}")
                return True
        except Exception as e:
            self.log("ERROR", f"注册请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_login(self) -> bool:
        """测试用户登录"""
        self.log("INFO", "测试用户登录...")
        try:
            resp = self.session.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
            )
            data = resp.json()
            if data.get("success"):
                self.token = data["data"].get("access_token")
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                self.log("SUCCESS", "登录成功，获取Token")
                return True
            else:
                self.log("ERROR", f"登录失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"登录请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_create_account(self) -> bool:
        """测试创建执行账户"""
        self.log("INFO", "测试创建执行账户...")
        try:
            resp = self.session.post(
                f"{BASE_URL}/api/agent/accounts",
                params={"name": "验证测试账户", "platform": "polymarket", "initial_balance": 1000.0},
            )
            data = resp.json()
            if data.get("success"):
                self.account_id = data["data"].get("id")
                self.account_uid = data["data"].get("uid")
                self.log("SUCCESS", f"创建账户成功，ID: {self.account_id}, UID: {self.account_uid}")
                return True
            else:
                self.log("ERROR", f"创建账户失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"创建账户请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_predict_and_vote(self) -> bool:
        """测试预测+投票+下单闭环"""
        self.log("INFO", "测试预测+投票+下单闭环...")
        try:
            resp = self.session.post(
                f"{BASE_URL}/api/agent/predict-and-vote",
                params={"account_id": self.account_id},
                json={"symbol": "BTCUSDT", "timeframe": "15m"},
            )
            data = resp.json()
            if data.get("success"):
                self.vote_id = data["data"].get("vote_id")
                self.order_id = data["data"].get("order_id")
                direction = data["data"].get("final_direction")
                amount = data["data"].get("order_amount_usd")
                reason = data["data"].get("reason")
                
                self.log("SUCCESS", f"投票决策成功，ID: {self.vote_id}")
                self.log("INFO", f"  - 方向: {'买入' if direction == 1 else '卖出' if direction == 2 else '不交易'}")
                self.log("INFO", f"  - 金额: ${amount}")
                self.log("INFO", f"  - 原因: {reason}")
                
                predictions = data["data"].get("predictions", [])
                self.log("INFO", f"  - 智能体预测 ({len(predictions)}个):")
                for p in predictions:
                    self.log("INFO", f"    * {p['agent_name']}: {'涨' if p['direction'] == 1 else '跌' if p['direction'] == 2 else '平'} (置信度: {p['confidence']})")
                
                if self.order_id:
                    self.log("SUCCESS", f"  - 订单执行成功: {self.order_id}")
                return True
            else:
                self.log("ERROR", f"投票决策失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"投票请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_ranking_list(self) -> bool:
        """测试榜单列表"""
        self.log("INFO", "测试榜单列表接口...")
        try:
            resp = self.session.get(
                f"{BASE_URL}/api/ranking/list",
                params={"rank_type": "realtime", "page": 1, "page_size": 5},
            )
            data = resp.json()
            if data.get("success"):
                items = data["data"].get("items", [])
                total = data["data"].get("total", 0)
                self.log("SUCCESS", f"榜单查询成功，共 {total} 个策略")
                self.log("INFO", "  - TOP 5 策略:")
                for item in items[:5]:
                    self.log("INFO", f"    {item.get('rank')}. {item.get('name', item.get('uid'))} - 综合分: {item.get('composite_score')}")
                return True
            else:
                self.log("ERROR", f"榜单查询失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"榜单请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_ranking_detail(self) -> bool:
        """测试策略详情"""
        self.log("INFO", "测试策略详情接口...")
        try:
            resp = self.session.get(f"{BASE_URL}/api/ranking/detail/{self.account_uid}")
            data = resp.json()
            if data.get("success"):
                detail = data["data"]
                self.log("SUCCESS", f"策略详情查询成功: {detail.get('name')}")
                self.log("INFO", f"  - UID: {detail.get('uid')}")
                self.log("INFO", f"  - 段位: {detail.get('tier')}")
                self.log("INFO", f"  - 综合得分: {detail.get('composite_score')}")
                self.log("INFO", f"  - 年化收益: {detail.get('annualized_return'):.2%}")
                self.log("INFO", f"  - 最大回撤: {detail.get('max_drawdown'):.2%}")
                return True
            else:
                self.log("ERROR", f"策略详情查询失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"策略详情请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_follow_subscribe(self) -> bool:
        """测试跟单订阅"""
        self.log("INFO", "测试跟单订阅接口...")
        try:
            resp = self.session.post(
                f"{BASE_URL}/api/follow/subscribe",
                json={
                    "leader_uid": self.account_uid,
                    "mode": 3,
                    "follow_amount_usd": 50.0,
                },
            )
            data = resp.json()
            if data.get("success"):
                sub_id = data["data"].get("id")
                self.log("SUCCESS", f"跟单订阅成功，ID: {sub_id}")
                return True
            else:
                self.log("WARNING", f"跟单订阅: {data.get('message')}")
                return True  # 可能已订阅，不影响整体验证
        except Exception as e:
            self.log("ERROR", f"跟单订阅请求失败:{e}，traceback: {traceback.format_exc()}")
            return False

    def test_rental_agents(self) -> bool:
        """测试智能体租用列表"""
        self.log("INFO", "测试智能体租用列表接口...")
        try:
            resp = self.session.get(f"{BASE_URL}/api/rental/agents")
            data = resp.json()
            if data.get("success"):
                agents = data["data"].get("agents", [])
                self.log("SUCCESS", f"智能体列表查询成功，共 {len(agents)} 个可租用智能体")
                for agent in agents:
                    self.log("INFO", f"  - {agent.get('name')} ({agent.get('model')}): ${agent.get('price_per_call_usd')}/次")
                return True
            else:
                self.log("ERROR", f"智能体列表查询失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"智能体列表请求失败: {e},traceback={traceback.format_exc()}")
            return False

    def test_execution_logs(self) -> bool:
        """测试执行日志查询"""
        self.log("INFO", "测试执行日志接口...")
        try:
            resp = self.session.get(f"{BASE_URL}/api/agent/execution/{self.account_uid}")
            data = resp.json()
            if data.get("success"):
                logs = data["data"].get("logs", [])
                self.log("SUCCESS", f"执行日志查询成功，共 {len(logs)} 条记录")
                if logs:
                    log = logs[0]
                    self.log("INFO", f"  - 最新订单: {log.get('order_id')}")
                    self.log("INFO", f"    平台: {log.get('platform')}, 方向: {'买' if log.get('side') == 1 else '卖'}")
                    self.log("INFO", f"    金额: ${log.get('amount_usd')}, 状态: {log.get('status')}")
                return True
            else:
                self.log("ERROR", f"执行日志查询失败: {data.get('message')}")
                return False
        except Exception as e:
            self.log("ERROR", f"执行日志请求失败: {e},traceback={traceback.format_exc()}")
            return False

    def run_all_tests(self):
        """运行所有测试"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "福纹排行榜系统 - 完整流程验证")
        self.log("INFO", "=" * 60)
        self.log("INFO", "")

        results = []

        # 测试流程
        tests = [
            ("用户注册", self.test_register),
            ("用户登录", self.test_login),
            ("创建执行账户", self.test_create_account),
            ("预测+投票+下单", self.test_predict_and_vote),
            ("榜单列表", self.test_ranking_list),
            ("策略详情", self.test_ranking_detail),
            ("跟单订阅", self.test_follow_subscribe),
            ("智能体租用列表", self.test_rental_agents),
            ("执行日志", self.test_execution_logs),
        ]

        for name, test_func in tests:
            self.log("INFO", f"--- {name} ---")
            start = time.time()
            success = test_func()
            elapsed = time.time() - start
            results.append((name, success, elapsed))
            self.log("INFO", "")

        # 汇总结果
        self.log("INFO", "=" * 60)
        self.log("INFO", "验证结果汇总")
        self.log("INFO", "=" * 60)
        
        passed = sum(1 for _, s, _ in results if s)
        total = len(results)
        
        for name, success, elapsed in results:
            status = "✅" if success else "❌"
            self.log("INFO", f"{status} {name} ({elapsed:.2f}s)")
        
        self.log("INFO", "")
        self.log("SUCCESS" if passed == total else "WARNING", f"总计: {passed}/{total} 通过")
        
        if passed == total:
            self.log("SUCCESS", "🎉 所有验证通过！系统运行正常")
        else:
            self.log("ERROR", "⚠️ 部分验证未通过，请检查相关模块")
        
        return passed == total


if __name__ == "__main__":
    validator = SystemValidator()
    success = validator.run_all_tests()
    sys.exit(0 if success else 1)