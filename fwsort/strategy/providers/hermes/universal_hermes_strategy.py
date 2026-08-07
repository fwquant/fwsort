"""万能 Hermes 文本信号策略

接收用户前端传入的自然语言文本描述，将其发送给 Hermes 服务端，
等待 Hermes 返回交易信号后，适配为符文排行榜可识别的统一 Signal 格式。

使用场景：
    用户在前端输入一段文本（如 "BTC 4小时内趋势分析"），
    系统将该文本作为 prompt 传给 Hermes 模型，获取预测信号。

依赖：
    - Hermes HTTP 服务端（默认 http://100.64.0.9:8099）
    - pip install requests

参数说明：
    - hermes_url (str): Hermes HTTP 接口地址
    - prompt (str): 前端传入的文本描述（每次 get_signal 时动态设置）
    - amount (float): 下单金额
    - timeout (int): 请求超时秒数（隐藏参数）
    - hermes_api_key (str): Hermes 服务鉴权 Key（隐藏参数）
"""
from __future__ import annotations

import json
import time
import traceback
from typing import Optional

from fwsort.fwlogs import logger
from fwsort.strategy.base import Direction, Signal, StrategyBase


def _ensure_requests():
    try:
        import requests
        return requests
    except ImportError:
        import subprocess
        import sys
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "requests"],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            import requests
            return requests
        except Exception as e:
            raise ImportError(f"requests 自动安装失败: {e}\n请手动运行: pip install requests")


class UniversalHermesStrategy(StrategyBase):
    """万能 Hermes 文本信号策略

    接收前端传入的自然语言文本 → 发送给 Hermes → 获取信号 → 适配为统一格式。

    Hermes 接口约定（POST JSON）：
        请求：{
            "prompt": "BTC 4小时趋势分析",
            "symbol_hint": "btc",       // 可选
            "timeframe": "4h",          // 可选
        }
        响应：{
            "success": true,
            "direction": "UP",           // UP / DOWN / FLAT
            "symbol": "btc-updown-4h-{epoch}",
            "confidence": 0.85,
            "reasoning": "...",
            "raw": {...}                 // Hermes 原始响应
        }

    若 Hermes 响应字段为中文（兼容旧版）：
        - 下单方向 → direction
        - 标的代码 → symbol
    """

    name: str = "hermes_universal"
    category: str = "external"
    description: str = "万能 Hermes 文本信号策略（自然语言→交易信号）"
    author: str = "fwquant"
    version: str = "1.0.0"

    # === 显示参数（Web 界面可编辑） ===
    parameters = ["hermes_url", "amount", "default_prompt"]

    # === 隐藏参数（有默认值但 Web 不显示） ===
    hidden_parameters = ["timeout", "hermes_api_key", "timeframe", "symbol_hint"]

    # === 参数默认值 ===
    hermes_url: str = "http://100.64.0.9:8099"
    amount: float = 1.0
    default_prompt: str = "分析当前市场趋势，给出交易方向"
    timeout: int = 60
    hermes_api_key: str = ""
    timeframe: str = ""
    symbol_hint: str = ""

    固定前缀提示词 = (""
                      ""
                      "返回JSON结构 ：{'标的代码': 'unknown-updown-4h-1786075200','下单方向': '','amount': 1.0,  'source': 'hermes_universal',  'timestamp': 1786080106}"
                      "根据三个模型的预测结果，选择置信度最高的方向作为最终下单方向"
                      ""
                      "")

    def __init__(self, config_json: dict | None = None, **kwargs):
        self.config = config_json or {}

        self.hermes_url = kwargs.get("hermes_url") or self.config.get("hermes_url", self.hermes_url)
        self.amount = kwargs.get("amount") or self.config.get("amount", self.amount)
        self.default_prompt = kwargs.get("default_prompt") or self.config.get("default_prompt", self.default_prompt)
        self.timeout = kwargs.get("timeout") or self.config.get("timeout", self.timeout)
        self.hermes_api_key = kwargs.get("hermes_api_key") or self.config.get("hermes_api_key", self.hermes_api_key)
        self.timeframe = kwargs.get("timeframe") or self.config.get("timeframe", self.timeframe)
        self.symbol_hint = kwargs.get("symbol_hint") or self.config.get("symbol_hint", self.symbol_hint)

        # 运行时状态：最近一次的 Hermes 原始响应
        self._last_raw_response: Optional[dict] = None
        self._last_prompt: str = ""

    # ========== 核心方法：获取信号 ==========
    def get_signal(self, prompt: str | None = None) -> Signal:
        """获取一个信号

        Args:
            prompt: 前端传入的自然语言文本描述。
                    为空时使用 default_prompt。

        Returns:
            Signal: 适配为符文排行榜可识别的统一格式信号对象。
                    direction 为空字符串时表示无有效交易信号。
        """
        text = prompt or self.default_prompt
        text = f"{self.固定前缀提示词} \n {text}"

        self._last_prompt = text

        # 1. 调用 Hermes HTTP 接口
        hermes_result = self._call_hermes(text)

        # 2. 适配为统一 Signal 格式
        signal = self._adapt_to_signal(hermes_result, text)

        logger.info(
            f"[UniversalHermes] prompt='{text[:50]}' → "
            f"direction={signal.direction} symbol={signal.symbol} "
            f"source={signal.source}"
        )
        return signal

    # ========== 公开接口：前端直接调用 ==========
    def get_signal_from_text(self, text: str = "") -> dict:
        """前端友好接口：传入文本 → 返回包含信号和原始响应的完整 dict

        Args:
            text: 用户输入的自然语言文本描述

        Returns:
            {
                "signal": Signal.to_dict(),  # 统一格式信号
                "raw": {...},                # Hermes 原始响应
                "prompt": "...",             # 使用的 prompt
                "success": bool,
                "error": str | None,
            }
        """
        if text == "":
            text = self.default_prompt

        text = f"{self.固定前缀提示词} \n {text} "
        try:
            signal = self.get_signal(prompt=text)
            return {
                "signal": signal.to_dict(),
                "raw": self._last_raw_response or {},
                "prompt": self._last_prompt,
                "success": signal.is_valid,
                "error": None if signal.is_valid else "Hermes 未返回有效方向",
            }
        except Exception as e:
            logger.error(f"[UniversalHermes] get_signal_from_text error: {e},traceback={traceback.format_exc()}")
            return {
                "signal": Signal(
                    symbol="", amount=self.amount, direction="",
                    source=self.name, timestamp=int(time.time()),
                ).to_dict(),
                "raw": {},
                "prompt": text,
                "success": False,
                "error": str(e),
            }

    # ========== Hermes 调用 ==========
    def _call_hermes(self, prompt: str) -> dict:
        """调用 Hermes HTTP 接口获取原始信号

        Args:
            prompt: 自然语言文本

        Returns:
            Hermes 返回的 dict，失败时返回错误信息 dict
        """
        requests = _ensure_requests()

        payload = {
            "prompt": prompt,
        }
        if self.symbol_hint:
            payload["symbol_hint"] = self.symbol_hint
        if self.timeframe:
            payload["timeframe"] = self.timeframe

        headers = {"Content-Type": "application/json"}
        if self.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.hermes_api_key}"

        try:
            resp = requests.post(
                self.hermes_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            self._last_raw_response = data
            return data
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[UniversalHermes] Hermes 连接失败: {e},traceback={traceback.format_exc()}")
            return {"success": False, "error": f"连接失败: {e},traceback={traceback.format_exc()}"}
        except requests.exceptions.Timeout as e:
            logger.error(f"[UniversalHermes] Hermes 请求超时: {e},traceback={traceback.format_exc()}")
            return {"success": False, "error": f"请求超时: {e},traceback={traceback.format_exc()}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"[UniversalHermes] Hermes 请求异常: {e},traceback={traceback.format_exc()}")
            return {"success": False, "error": str(e)}
        except json.JSONDecodeError as e:
            logger.error(f"[UniversalHermes] Hermes 响应解析失败: {e},traceback={traceback.format_exc()}")
            return {"success": False, "error": f"响应解析失败: {e},traceback={traceback.format_exc()}"}

    # ========== 信号适配：Hermes → 统一 Signal ==========

    def _adapt_to_signal(self, hermes_result: dict, prompt: str) -> Signal:
        """将 Hermes 返回结果适配为符文排行榜可识别的 Signal 格式

        适配规则：
            1. direction 字段：接受英文（UP/DOWN/FLAT）和中文（涨/跌/平）
            2. symbol 字段：自动从 Hermes 响应或 prompt 中推断
            3. amount：使用用户配置的默认值
            4. timestamp：使用当前时间戳

        Args:
            hermes_result: Hermes 原始响应 dict
            prompt: 原始 prompt（用于回退 symbol 推断）

        Returns:
            统一 Signal 对象
        """
        direction = self._extract_direction(hermes_result)
        symbol = self._extract_symbol(hermes_result, prompt)

        return Signal(
            symbol=symbol,
            amount=self.amount,
            direction=direction,
            source=self.name,
            timestamp=int(time.time()),
        )

    @staticmethod
    def _extract_direction(result: dict) -> Direction:
        """从 Hermes 响应中提取方向

        支持的字段（按优先级）：
            - direction / 下单方向
            - direction_en / 方向
            - msg.analysis / 分析结果
        """
        # 直接读取 direction 字段
        raw_dir = (
                result.get("direction")
                or result.get("开单方向")
                or result.get("下单方向")
                or result.get("direction_en")
                or result.get("方向")
                or ""
        )

        if not raw_dir:
            # 尝试从嵌套 msg 中提取
            msg = result.get("msg") or {}
            if isinstance(msg, dict):
                raw_dir = (
                        msg.get("direction")
                        or msg.get("开单方向")
                        or msg.get("下单方向")
                        or msg.get("final_direction")
                        or ""
                )

        # 归一化：英文
        direction_map = {
            "UP": "UP", "UPUP": "UP", "CALL": "UP", "BUY": "UP", "LONG": "UP",
            "DOWN": "DOWN", "DOWNDOWN": "DOWN", "PUT": "DOWN", "SELL": "DOWN", "SHORT": "DOWN",
            "FLAT": "", "NEUTRAL": "", "HOLD": "", "平": "",
        }

        if isinstance(raw_dir, str):
            normalized = raw_dir.strip().upper()
            if normalized in direction_map:
                return direction_map[normalized]

            # 中文映射
            cn_map = {"涨": "UP", "跌": "DOWN", "升": "UP", "降": "DOWN", "多": "UP", "空": "DOWN"}
            for cn, en in cn_map.items():
                if cn in raw_dir:
                    return en

            # 英文关键词模糊匹配
            if any(w in normalized for w in ("UP", "CALL", "BUY", "LONG", "涨")):
                return "UP"
            if any(w in normalized for w in ("DOWN", "PUT", "SELL", "SHORT", "跌")):
                return "DOWN"

        # 数值方向：1=涨/UP, 2=跌/DOWN, 0=平
        numeric_dir = result.get("final_direction") or result.get("direction_code")
        if numeric_dir is not None:
            try:
                nd = int(numeric_dir)
                if nd == 1:
                    return "UP"
                if nd == 2:
                    return "DOWN"
            except (ValueError, TypeError):
                pass

        return ""

    @staticmethod
    def _extract_symbol(result: dict, prompt: str) -> str:
        """从 Hermes 响应或 prompt 中推断 symbol

        优先级：
            1. result.symbol / result.标的代码
            2. result.msg.symbol
            3. 从 prompt 中提取关键词生成
        """
        # 直接读取
        symbol = (
                result.get("symbol")
                or result.get("标的代码")
                or result.get("symbol_code")
                or ""
        )

        if not symbol:
            msg = result.get("msg") or {}
            if isinstance(msg, dict):
                symbol = msg.get("symbol") or msg.get("标的代码") or ""

        if symbol:
            return str(symbol)

        # 回退：从 prompt 生成
        import re
        # 尝试从 prompt 提取交易对关键词
        coin_patterns = re.findall(
            r'(BTC|ETH|SOL|BNB|XRP|DOGE|ADA|AVAX|DOT|MATIC|LINK|ATOM|LTC|BCH|UNI)',
            prompt.upper(),
        )
        if coin_patterns:
            coin = coin_patterns[0]
            epoch = int(time.time())
            # 对齐到 4 小时周期
            epoch = (epoch // 14400) * 14400
            return f"{coin.lower()}-updown-4h-{epoch}"

        # 兜底
        epoch = int(time.time())
        epoch = (epoch // 14400) * 14400
        return f"unknown-updown-4h-{epoch}"

    # ========== 健康检查 ==========

    def health_check(self) -> dict:
        requests = _ensure_requests()
        try:
            resp = requests.get(
                self.hermes_url.replace("hermes", "health").replace("8099", "8100"),
                timeout=5,
            )
            return {
                "provider": self.name,
                "category": self.category,
                "ready": resp.status_code == 200,
                "hermes_url": self.hermes_url,
                "last_prompt": self._last_prompt[:100] if self._last_prompt else "",
            }
        except Exception:
            return {
                "provider": self.name,
                "category": self.category,
                "ready": False,
                "hermes_url": self.hermes_url,
                "note": "Hermes 服务不可达（不影响策略注册）",
            }

    # ========== 最近查询 ==========

    def get_last_result(self) -> dict:
        """获取最近一次 Hermes 原始响应"""
        return {
            "prompt": self._last_prompt,
            "raw": self._last_raw_response or {},
            "timestamp": int(time.time()),
        }


# 调试菜单（直接 python universal_hermes_strategy.py 即可）


if __name__ == "__main__":
    import argparse


    def _parse_args():
        parser = argparse.ArgumentParser(description="UniversalHermes 调试工具")
        parser.add_argument("--url", default=None, help="Hermes 服务地址")
        parser.add_argument("--prompt", "-p", default=None, help="一次性 prompt（非交互模式）")
        parser.add_argument("--timeframe", "-t", default="", help="时间周期（如 4h / 1d）")
        parser.add_argument("--symbol", "-s", default="", help="标的代码提示（如 btc）")
        parser.add_argument("--amount", "-a", type=float, default=1.0, help="下单金额")
        parser.add_argument("--api-key", default="", help="Hermes 鉴权 Key")
        parser.add_argument("--timeout", type=int, default=60, help="请求超时秒数")
        parser.add_argument("--loop", action="store_true", help="循环模式")
        parser.add_argument("--interval", type=int, default=300, help="循环间隔秒数（默认300）")
        return parser.parse_args()


    args = _parse_args()

    strategy = UniversalHermesStrategy(
        hermes_url=args.url or UniversalHermesStrategy.hermes_url,
        amount=args.amount,
        default_prompt=args.prompt or UniversalHermesStrategy.default_prompt,
        timeframe=args.timeframe,
        symbol_hint=args.symbol,
        hermes_api_key=args.api_key,
        timeout=args.timeout,
    )

    # 非交互：一次性请求
    if args.prompt:
        print(f"\n[请求] prompt={args.prompt}")
        t0 = time.time()
        result = strategy.get_signal_from_text(args.prompt)
        elapsed = time.time() - t0
        print(f"[用时] {elapsed:.2f}s")
        print(f"[信号] {json.dumps(result['signal'], ensure_ascii=False, indent=2)}")
        print(f"[原始] {json.dumps(result['raw'], ensure_ascii=False, indent=2)}")
        if result.get("error"):
            print(f"[错误] {result['error']}")
        raise SystemExit(0 if result.get("success") else 1)

    # 循环模式
    if args.loop:
        prompt = args.prompt or strategy.default_prompt
        interval = args.interval
        print(f"\n[循环] prompt={prompt}, interval={interval}s, Ctrl+C 停止\n")
        seq = 0
        try:
            while True:
                seq += 1
                t0 = time.time()
                result = strategy.get_signal_from_text(prompt)
                elapsed = time.time() - t0
                sig = result.get("signal", {})
                direction = sig.get("direction", "")
                symbol = sig.get("symbol", "")
                raw_dir = (result.get("raw") or {}).get("direction", "")
                print(
                    f"  [{seq}] {time.strftime('%H:%M:%S')} | cost={elapsed:.1f}s | dir={direction}({raw_dir}) | symbol={symbol}")
                if result.get("error"):
                    print(f"        error: {result['error']}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[循环] 已停止")
        raise SystemExit(0)

    # 交互式菜单
    while True:
        print("\n" + "=" * 50)
        print("  UniversalHermes 调试菜单")
        print("=" * 50)
        print(f"  Hermes URL: {strategy.hermes_url}")
        print(f"  默认 Prompt: {strategy.default_prompt}")
        print(f"  Timeframe: {strategy.timeframe or '(未设置)'}")
        print(f"  Symbol Hint: {strategy.symbol_hint or '(未设置)'}")
        print(f"  API Key: {'***' if strategy.hermes_api_key else '(未设置)'}")
        print("-" * 50)
        print("  1. 获取信号（使用默认 prompt）")
        print("  2. 自定义 prompt 获取信号")
        print("  3. 循环模式（默认间隔300秒）")
        print("  4. 健康检查")
        print("  5. 查看最近一次结果")
        print("  6. 打印当前配置")
        print("  ---")
        print("  0. 退出")
        print("=" * 50)
        choice = input("  请选择: ").strip()

        if choice == "1":
            t0 = time.time()
            result = strategy.get_signal_from_text(text="")
            elapsed = time.time() - t0
            print(f"result={result}")

        elif choice == "2":
            user_prompt = input("  请输入 prompt: ").strip()
            if not user_prompt:
                user_prompt = strategy.default_prompt
            tf = input(f"  Timeframe (当前 '{strategy.timeframe}', 回车保留): ").strip()
            sh = input(f"  Symbol Hint (当前 '{strategy.symbol_hint}', 回车保留): ").strip()
            if tf:
                strategy.timeframe = tf
            if sh:
                strategy.symbol_hint = sh
            t0 = time.time()
            result = strategy.get_signal_from_text(user_prompt)
            elapsed = time.time() - t0
            print(f"\n  [用时] {elapsed:.2f}s")
            print(f"  [信号] {json.dumps(result['signal'], ensure_ascii=False, indent=2)}")
            print(f"  [原始] {json.dumps(result['raw'], ensure_ascii=False, indent=2)}")
            if result.get("error"):
                print(f"  [错误] {result['error']}")

        elif choice == "3":
            interval = input("  间隔秒数 (默认300): ").strip()
            interval = int(interval) if interval else 300
            prompt = input(f"  使用默认 prompt? (Y/n): ").strip().lower()
            prompt_text = strategy.default_prompt if prompt != "n" else input("  请输入 prompt: ").strip()
            if not prompt_text:
                prompt_text = strategy.default_prompt
            print(f"\n  [循环] 每 {interval} 秒获取一次，Ctrl+C 停止\n")
            seq = 0
            try:
                while True:
                    seq += 1
                    t0 = time.time()
                    result = strategy.get_signal_from_text(prompt_text)
                    elapsed = time.time() - t0
                    sig = result.get("signal", {})
                    direction = sig.get("direction", "")
                    symbol = sig.get("symbol", "")
                    raw_dir = (result.get("raw") or {}).get("direction", "")
                    print(
                        f"  [{seq}] {time.strftime('%H:%M:%S')} | cost={elapsed:.1f}s | dir={direction}({raw_dir}) | symbol={symbol}")
                    if result.get("error"):
                        print(f"        error: {result['error']}")
                    time.sleep(interval)
            except KeyboardInterrupt:
                print("\n  已停止循环")

        elif choice == "4":
            print(f"\n  [健康检查] {strategy.hermes_url}")
            t0 = time.time()
            hc = strategy.health_check()
            elapsed = time.time() - t0
            print(f"  [用时] {elapsed:.2f}s")
            print(f"  {json.dumps(hc, ensure_ascii=False, indent=2)}")

        elif choice == "5":
            last = strategy.get_last_result()
            print(f"\n  [最近结果]")
            print(f"  {json.dumps(last, ensure_ascii=False, indent=2)}")

        elif choice == "6":
            print(f"\n  [当前配置]")
            print(f"  hermes_url: {strategy.hermes_url}")
            print(f"  amount: {strategy.amount}")
            print(f"  default_prompt: {strategy.default_prompt}")
            print(f"  timeout: {strategy.timeout}")
            print(f"  timeframe: {strategy.timeframe}")
            print(f"  symbol_hint: {strategy.symbol_hint}")
            print(f"  hermes_api_key: {'***' if strategy.hermes_api_key else '(未设置)'}")
            print(f"  _last_prompt: {strategy._last_prompt}")
            print(
                f"  _last_raw_response: {json.dumps(strategy._last_raw_response or {}, ensure_ascii=False, indent=2)}")

        elif choice == "0":
            print("退出")
            break
        else:
            print("无效选项")
        input("\n按回车继续...")
