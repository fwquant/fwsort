# 标的解析器：把用户输入的交易标 URL 解析为内部 symbol
# 支持：
#   1) Polymarket 二元合约：https://polymarket.com/event/<slug>
#   2) OKX 现货：https://www.okx.com/trade-spot/<inst>  或  okx://<inst>
#   3) 已直接传 symbol（BTC-USDT）则原样返回
from __future__ import annotations

import re
from urllib.parse import urlparse

# 标的统一规范：BTC-USDT / ETH-USDT / <EVENT>-<OUTCOME>
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,16}(-[A-Z0-9]{2,16})?$")


def parse_target_url(url: str | None) -> str | None:
    """把标的 URL 解析为内部 symbol，无法解析返回 None"""
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None

    # 已是 symbol 形式
    candidate = raw.upper().replace("/", "-")
    if _SYMBOL_RE.match(candidate):
        return candidate

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")

    # 1) Polymarket
    if "polymarket.com" in host:
        # /event/<slug>  →  截 slug 末段当 symbol
        m = re.search(r"/event/([^/?#]+)", parsed.path)
        if m:
            slug = m.group(1)
            return "POLY-" + slug[:24].upper()
        return "POLY-UNKNOWN"

    # 2) OKX
    if "okx.com" in host or "okx" in host:
        # /trade-spot/<inst>  或 /trade-swap/<inst>
        m = re.search(r"/trade-(?:spot|swap|margin)/([a-z0-9-]+)", parsed.path, re.IGNORECASE)
        if m:
            inst = m.group(1).upper().replace("_", "-")
            return inst
        # 兜底用 path 末段
        seg = path.split("/")[-1].upper().replace("_", "-")
        return seg or "OKX-UNKNOWN"

    # 3) 未知域名：返回 None，让上层报错
    return None


def is_valid_target_url(url: str | None) -> bool:
    """简单校验：必须 https://，长度 < 512"""
    if not url:
        return True  # 允许为空
    if len(url) > 512:
        return False
    return url.startswith("https://")
