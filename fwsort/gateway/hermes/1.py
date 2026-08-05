#!/usr/bin/env python3
"""
BTC 信号获取客户端
通过 SSH 直接调用远程 Hermes 的 btc_moa_signal.py 获取实时信号

依赖: pip install paramiko
"""
import json
import paramiko
from datetime import datetime
from typing import Optional


def get_btc_signal(
    host: str = "100.64.0.9",
    username: str = "khadas",
    password: Optional[str] = None,
) -> Optional[dict]:
    """
    通过 SSH 直接调用远程 btc_moa_signal.py --readme 获取实时信号

    返回 readme.md 标准格式:
    {
        "success": true,
        "code": 0,
        "msg": { "各模型分析结果": {...}, "时间范围": "...", "其它扩展信息": "..." },
        "标的代码": "btc-updown-15m-xxx",
        "下单方向": "UP|DOWN|"
    }
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(host, username=username, password=password)
    except paramiko.ssh_exception.NoValidConnectionsError as e:
        print(f"[{datetime.now()}] 连接失败: {e}")
        return None
    except Exception as e:
        print(f"[{datetime.now()}] SSH 错误: {e}")
        return None

    # 远程执行脚本（自动加载环境变量）
    cmd = (
        "source ~/.hermes/.env 2>/dev/null; "
        "source ~/.openagents/env/openclaw.env 2>/dev/null; "
        "source ~/.openagents/env/codex.env 2>/dev/null; "
        "/home/khadas/poly-venv/bin/python /home/khadas/btc_moa_signal.py --readme 2>&1"
    )
    stdin, stdout, stderr = ssh.exec_command(cmd)
    result = stdout.read().decode().strip()
    err = stderr.read().decode().strip()

    ssh.close()

    if err:
        print(f"[{datetime.now()}] stderr: {err[:200]}")

    try:
        signal = json.loads(result)
        return signal
    except json.JSONDecodeError:
        print(f"[{datetime.now()}] JSON 解析失败，原始输出: {result[:300]}")
        return None


def process_signal(signal: dict):
    """处理信号 - 在这里添加你的下单逻辑"""
    direction = signal.get("下单方向", "")
    symbol = signal.get("标的代码", "")
    msg = signal.get("msg", {})
    analysis = msg.get("各模型分析结果", {})
    time_range = msg.get("时间范围", "")
    extra = msg.get("其它扩展信息", "")

    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] BTC 信号")
    print(f"{'='*50}")
    print(f"  标的代码: {symbol}")
    print(f"  下单方向: {direction}")
    print(f"  时间范围: {time_range}")
    print(f"  各模型分析:")
    for model, result in analysis.items():
        print(f"    - {model}: {result}")
    if extra:
        print(f"  扩展信息: {extra}")
    print(f"{'='*50}\n")

    if not direction:
        print("无明确方向，跳过")
        return

    # ====== 在这里添加你的下单逻辑 ======
    # 示例:
    # if direction == "UP":
    #     await gateway.buy(symbol, amount=100)
    # elif direction == "DOWN":
    #     await gateway.sell(symbol, amount=100)
    # ===================================


if __name__ == "__main__":
    signal = get_btc_signal(host="100.64.0.9", username="khadas")

    if signal and signal.get("success"):
        process_signal(signal)

        # 直接访问字段
        print(f"下单方向: {signal['下单方向']}")
        print(f"标的代码: {signal['标的代码']}")
        print(f"各模型分析: {signal['msg']['各模型分析结果']}")
    else:
        print("获取信号失败")