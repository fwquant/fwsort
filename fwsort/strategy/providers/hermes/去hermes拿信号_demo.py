#!/usr/bin/env python3
"""
BTC 信号获取客户端
通过 SFTP 从远程读取 cron 预生成的 btc_signal.json（每 15 分钟更新）

依赖: pip install paramiko

认证方式（按优先级）:
  1. 环境变量 HERMES_SSH_PASSWORD（推荐，免交互）
  2. 交互式输入密码
  3. SSH key（~/.ssh/id_rsa）
"""
import json
import os
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

# paramiko 懒加载：仅在实际使用时导入，避免因未安装导致整个模块无法加载
# 延迟到 connect() 或 get_*_via_sftp() 等函数被调用时才真正 import

load_dotenv()


def _ensure_paramiko():
    """懒加载 paramiko，确保可用"""
    try:
        import paramiko
        return paramiko
    except ImportError:
        raise ImportError(
            "paramiko 未安装，请运行: pip install paramiko"
        )


HISTORY_REMOTE_FILE = "/home/khadas/btc_signal_history.jsonl"


class BtcSignalFetcher:
    """BTC 信号获取器 - 一次连接，多次读取"""

    def __init__(
            self,
            host: str = "100.64.0.9",
            username: str = "khadas",
            password: Optional[str] = None,
            remote_file: str = "/home/khadas/btc_signal.json",
            history_file: str = HISTORY_REMOTE_FILE,
    ):
        self.host = host
        self.username = username
        self.password = password or os.getenv("HERMES_SSH_PASSWORD", "")
        self.remote_file = remote_file
        self.history_file = history_file
        self._ssh = None
        self._sftp = None

    def connect(self) -> bool:
        """建立 SSH/SFTP 连接（只做一次）"""
        paramiko = _ensure_paramiko()

        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for attempt in range(2):
            try:

                self._ssh.connect(
                    self.host, username=self.username,
                    password=self.password, look_for_keys=True,
                )

                self._sftp = self._ssh.open_sftp()
                print(f"[{datetime.now()}] 已连接 {self.username}@{self.host}")
                return True
            # 认证异常，尝试交互式输入密码
            except paramiko.ssh_exception.AuthenticationException:
                if attempt == 0:
                    print(f"[{datetime.now()}] 用户 {self.username} 密码错误或未设置")
                    self.password = input(f"请输入 {self.username}@{self.host} 的密码: ")
                    self._ssh = paramiko.SSHClient()
                    self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                else:
                    print(f"[{datetime.now()}] 用户 {self.username} 认证失败")
                    return False
            except paramiko.ssh_exception.NoValidConnectionsError as e:
                print(f"[{datetime.now()}] 无法连接 {self.host}:22 - {e}")
                return False
            except Exception as e:
                print(f"[{datetime.now()}] SSH 错误: {e}")
                return False
        return False

    def get_signal(self) -> Optional[dict]:
        """读取信号文件（毫秒级，不执行远程脚本）"""
        if self._sftp is None:
            if not self.connect():
                return None

        try:
            with self._sftp.open(self.remote_file, "r") as f:
                raw = f.read().decode().strip()
            return json.loads(raw)
        except FileNotFoundError:
            print(f"[{datetime.now()}] 远程信号文件不存在: {self.remote_file}")
            print("  请确保远程 cron 任务正在运行: crontab -l")
            return None
        except json.JSONDecodeError:
            print(f"[{datetime.now()}] 信号文件 JSON 解析失败")
            return None
        except Exception as e:
            print(f"[{datetime.now()}] 读取信号出错: {e}")
            self._sftp = None
            return None

    def get_history(self, limit: int = 100) -> list[dict]:
        """读取历史信号文件（JSONL 格式，每行一条记录）

        Args:
            limit: 最多返回多少条历史记录，默认 100 条

        Returns:
            历史信号列表，按时间倒序排列（最新的在前）
        """
        if self._sftp is None:
            if not self.connect():
                return []

        try:
            with self._sftp.open(self.history_file, "r") as f:
                raw = f.read().decode().strip()
            if not raw:
                return []
            lines = raw.split("\n")
            records = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            records.reverse()
            return records[:limit]
        except FileNotFoundError:
            print(f"[{datetime.now()}] 历史文件不存在: {self.history_file}")
            print("  请确保远程 btc_moa_signal.py 使用了 --readme 参数并已生成历史")
            return []
        except Exception as e:
            print(f"[{datetime.now()}] 读取历史出错: {e}")
            self._sftp = None
            return []

    def close(self):
        """关闭连接"""
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._ssh:
            self._ssh.close()
            self._ssh = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


def process_signal(signal: dict):
    """处理信号 - 在这里添加你的下单逻辑"""
    direction = signal.get("下单方向", "")
    symbol = signal.get("标的代码", "")
    msg = signal.get("msg", {})
    analysis = msg.get("各模型分析结果", {})
    time_range = msg.get("时间范围", "")
    extra = msg.get("其它扩展信息", "")

    print(f"\n{'=' * 50}")
    print(f"[{datetime.now()}] BTC 信号")
    print(f"{'=' * 50}")
    print(f"  标的代码: {symbol}")
    print(f"  下单方向: {direction}")
    print(f"  时间范围: {time_range}")
    print(f"  各模型分析:")
    for model, result in analysis.items():
        print(f"    - {model}: {result}")
    if extra:
        print(f"  扩展信息: {extra}")
    print(f"{'=' * 50}\n")

    if not direction:
        print("无明确方向，跳过")
        return

    # ====== 在这里添加你的下单逻辑 ======
    # if direction == "UP":
    #     await gateway.buy(symbol, amount=100)
    # elif direction == "DOWN":
    #     await gateway.sell(symbol, amount=100)
    # ===================================


# ============================================================
# SFTP 方式 — 读远程 cron 预生成文件（毫秒级，推荐）
# ============================================================

_sftp_fetcher: Optional[BtcSignalFetcher] = None


def get_btc_signal_via_sftp(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
) -> Optional[dict]:
    """
    [SFTP] 一键获取 BTC 信号（每次新建连接，用完即关）

    前提: 远程 cron 已生成 /home/khadas/btc_signal.json
    """
    with BtcSignalFetcher(host=host, username=username, password=password) as f:
        return f.get_signal()


def get_signal_direction_via_sftp(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
) -> str:
    """[SFTP] 只返回下单方向: "UP" / "DOWN" / "" """
    signal = get_btc_signal_via_sftp(host=host, username=username, password=password)
    if signal and signal.get("success"):
        return signal.get("下单方向", "")
    return ""


def connect_sftp(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
) -> bool:
    """[SFTP] 建立全局长连接，后续 get_signal_sftp() 毫秒级返回"""
    global _sftp_fetcher
    _sftp_fetcher = BtcSignalFetcher(host=host, username=username, password=password)
    return _sftp_fetcher.connect()


def get_signal_sftp() -> Optional[dict]:
    """[SFTP] 通过全局连接获取信号（需先 connect_sftp）"""
    global _sftp_fetcher
    if _sftp_fetcher is None:
        print("[get_signal_sftp] 请先调用 connect_sftp()")
        return None
    return _sftp_fetcher.get_signal()


def close_sftp():
    """[SFTP] 关闭全局连接"""
    global _sftp_fetcher
    if _sftp_fetcher:
        _sftp_fetcher.close()
        _sftp_fetcher = None


def get_btc_history_via_sftp(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
        limit: int = 100,
) -> list[dict]:
    """
    [SFTP] 一键获取历史信号列表（每次新建连接，用完即关）

    Args:
        host: 远程主机
        username: 用户名
        password: 密码
        limit: 最多返回条数

    Returns:
        历史信号列表，最新的在前
    """
    with BtcSignalFetcher(host=host, username=username, password=password) as f:
        return f.get_history(limit=limit)


def get_history_sftp(limit: int = 100) -> list[dict]:
    """[SFTP] 通过全局连接获取历史信号（需先 connect_sftp）"""
    global _sftp_fetcher
    if _sftp_fetcher is None:
        print("[get_history_sftp] 请先调用 connect_sftp()")
        return []
    return _sftp_fetcher.get_history(limit=limit)


# ============================================================
# SSH 方式 — 远程执行 btc_moa_signal.py（实时，较慢）
# ============================================================

_ssh_client = None


def _ssh_connect(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
):
    """建立 SSH 连接（内部用）"""
    paramiko = _ensure_paramiko()

    pwd = password or os.getenv("HERMES_SSH_PASSWORD", "")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for attempt in range(2):
        try:

            ssh.connect(host, username=username, password=pwd, look_for_keys=True)

            return ssh
        except paramiko.ssh_exception.AuthenticationException:
            if attempt == 0:
                print(f"[{datetime.now()}] 用户 {username} 密码错误或未设置")
                pwd = input(f"请输入 {username}@{host} 的密码: ")
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                print(f"[{datetime.now()}] 用户 {username} 认证失败")
                return None
        except Exception as e:
            print(f"[{datetime.now()}] SSH 错误: {e}")
            return None
    return None


def get_btc_signal_via_ssh(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
) -> Optional[dict]:
    """
    [SSH] 远程执行 btc_moa_signal.py --readme，实时调用三个模型（约 5-10s）

    不需要 cron，每次都是最新结果，但较慢
    """
    ssh = _ssh_connect(host=host, username=username, password=password)
    if ssh is None:
        return None

    cmd = (
        "source ~/.hermes/.env 2>/dev/null; "
        "source ~/.openagents/env/openclaw.env 2>/dev/null; "
        "source ~/.openagents/env/codex.env 2>/dev/null; "
        "/home/khadas/poly-venv/bin/python /home/khadas/btc_moa_signal.py --readme 2>&1"
    )
    stdin, stdout, stderr = ssh.exec_command(cmd)
    result = stdout.read().decode().strip()
    ssh.close()

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        print(f"[{datetime.now()}] JSON 解析失败: {result[:200]}")
        return None


def get_signal_direction_via_ssh(
        host: str = "100.64.0.9",
        username: str = "khadas",
        password: Optional[str] = None,
) -> str:
    """[SSH] 只返回下单方向: "UP" / "DOWN" / "" """
    signal = get_btc_signal_via_ssh(host=host, username=username, password=password)
    if signal and signal.get("success"):
        return signal.get("下单方向", "")
    return ""


# ============================================================
# 向后兼容别名
# ============================================================

get_btc_signal = get_btc_signal_via_sftp
get_signal_direction = get_signal_direction_via_sftp
connect_global = connect_sftp
get_global_signal = get_signal_sftp
close_global = close_sftp

# ============================================================
# 调试菜单
# ============================================================

if __name__ == "__main__":
    while True:
        print("\n" + "=" * 45)
        print("  BTC 信号调试菜单")
        print("=" * 45)
        print("  --- SFTP（读文件，毫秒级，需 cron）---")
        print("  1. SFTP 一键获取信号（完整 JSON）")
        print("  2. SFTP 只拿下单方向")
        print("  3. SFTP 全局连接 + 循环读取")
        print("  4. SFTP 获取历史信号")
        print("  --- SSH（远程执行，实时，约 5-10s）---")
        print("  5. SSH 实时获取信号（完整 JSON）")
        print("  6. SSH 只拿下单方向")
        print("  ---")
        print("  0. 退出")
        print("=" * 45)
        choice = input("  请选择: ").strip()

        if choice == "1":
            oldtime = time.time()
            print(f"获取信号（可能 需要10秒~90秒）...oldtime={oldtime}")
            signal = get_btc_signal_via_sftp()
            print(f"【get_btc_signal_via_sftp】用时：[{time.time() - oldtime}]，返回值：[{signal}]")

        elif choice == "2":
            oldtime = time.time()
            print(f"获取信号（可能 需要10秒~90秒）...oldtime={oldtime}")
            direction = get_signal_direction_via_sftp()
            print(f"【get_signal_direction_via_sftp】用时：[{time.time() - oldtime}]，返回值：[{direction}]")

        elif choice == "3":
            if not connect_sftp():
                print("连接失败")
                continue
            interval = input("  间隔秒数 (默认300，即5分钟): ").strip()
            interval = int(interval) if interval else 300
            print(f"\n  开始定时读取，每 {interval} 秒获取一次，按 Ctrl+C 停止\n")
            try:
                seq = 0
                while True:
                    seq += 1
                    sig = get_signal_sftp()
                    if sig:
                        direction = sig.get("下单方向", "")
                        symbol = sig.get("标的代码", "")
                        print(f"  [{seq}] [{datetime.now().strftime('%H:%M:%S')}] {direction} | {symbol}")
                    else:
                        print(f"  [{seq}] [{datetime.now().strftime('%H:%M:%S')}] 获取失败")
                    time.sleep(interval)
            except KeyboardInterrupt:
                print("\n  已停止定时读取")
            finally:
                close_sftp()

        elif choice == "4":
            limit = input("  获取最近 N 条历史 (默认100): ").strip()
            limit = int(limit) if limit else 100
            oldtime = time.time()
            history = get_btc_history_via_sftp(limit=limit)
            print(f"【get_btc_history_via_sftp】用时：[{time.time() - oldtime}]，共 [{len(history)}] 条记录")
            for i, h in enumerate(history):
                direction = h.get("下单方向", "")
                symbol = h.get("标的代码", "")
                ts = h.get("msg", {}).get("时间范围", "")
                print(f"  [{i+1}] {direction} | {symbol} | {ts}")

        elif choice == "5":
            oldtime = time.time()
            print(f"获取信号（可能 需要10秒~90秒）...oldtime={oldtime}")
            signal = get_btc_signal_via_ssh()
            print(f"【get_btc_signal_via_ssh】用时：[{time.time() - oldtime}]，返回值：[{signal}]")

        elif choice == "6":
            oldtime = time.time()
            print(f"获取信号（可能 需要10秒~90秒）...oldtime={oldtime}")
            direction = get_signal_direction_via_ssh()
            print(f"【get_signal_direction_via_ssh】用时：[{time.time() - oldtime}]，返回值：[{direction}]")

        elif choice == "0":
            print("退出")
            break
        else:
            print("无效选项")
        input(f"按任意键继续...")