# 对标对比路由：compare_router（架构文档 8.2）
import random

from fastapi import APIRouter

from fwsort.response import success

router = APIRouter()


@router.post("", response_model=dict)
async def compare(uids: list[str]) -> dict:
    """2~5 个策略对标对比"""
    if len(uids) < 2 or len(uids) > 5:
        from fwsort.exceptions import ParamError

        raise ParamError("compare requires 2~5 uids")
    data = []
    for uid in uids:
        data.append(
            {
                "uid": uid,
                "annualized_return": round(random.uniform(-0.1, 1.2), 4),
                "max_drawdown": round(random.uniform(0.02, 0.3), 4),
                "sharpe_ratio": round(random.uniform(0.5, 3.0), 2),
                "win_rate": round(random.uniform(0.45, 0.75), 4),
                "trade_count": random.randint(100, 1000),
                "execution_score": round(random.uniform(0.6, 0.95), 4),
            }
        )
    return success(data={"uids": uids, "comparison": data})
