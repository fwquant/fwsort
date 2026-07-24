# 统一响应封装（架构文档 4.4.2）
from datetime import datetime
from typing import Any


def success(data: Any = None, message: str = "success", code: int = 200) -> dict[str, Any]:
    """成功响应"""
    return {
        "success": True,
        "message": message,
        "data": data,
        "code": code,
        "timestamp": int(datetime.now().timestamp()),
    }


def fail(message: str, code: int = 400, data: Any = None) -> dict[str, Any]:
    """失败响应"""
    return {
        "success": False,
        "message": message,
        "data": data,
        "code": code,
        "timestamp": int(datetime.now().timestamp()),
    }
