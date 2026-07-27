# 业务异常层级（安全 > 稳定 > 性能 > 功能 > 界面）
class FwsortError(Exception):
    """福纹系统基础异常"""

    code: int = 500
    message: str = "internal error"

    def __init__(self, message: str | None = None, code: int | None = None) -> None:
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


class ParamError(FwsortError):
    """参数错误"""

    code = 400
    message = "param error"


class AuthError(FwsortError):
    """未授权"""

    code = 401
    message = "unauthorized"


class PermissionError_(FwsortError):
    """权限不足（避免与内置 PermissionError 冲突，尾部下划线）"""

    code = 403
    message = "permission denied"


class NotFoundError(FwsortError):
    """资源不存在"""

    code = 404
    message = "not found"


class RiskControlError(FwsortError):
    """风控拦截（单笔过大、日亏上限等）"""

    code = 422
    message = "risk control triggered"
