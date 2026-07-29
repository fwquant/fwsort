# D1 安全基线验收测试（WP-01 ~ WP-05）
# 覆盖：CORS 收敛 / 启动密钥校验 / 登录限流 / 管理员鉴权 / 软删除
import os
import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock

# 允许在任意目录运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 强制使用 SQLite + FakeRedis，避免依赖外部服务
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_FAKE_REDIS", "true")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32bytes-for-unit-test-pass")
os.environ.setdefault("APP_ENV", "development")


# ========== WP-02 启动密钥校验 ==========
def test_wp02_secret_key_validation_dev_mode_passes():
    """开发模式：默认密钥不抛错（只警告）"""
    from main import _validate_production_secret
    from fwsort.config import settings

    orig_env = settings.APP_ENV
    try:
        object.__setattr__(settings, "APP_ENV", "development")
        _validate_production_secret()  # 不应该抛
    finally:
        object.__setattr__(settings, "APP_ENV", orig_env)


def test_wp02_secret_key_validation_prod_rejects_default():
    """生产模式：默认密钥必须抛 RuntimeError"""
    from main import _validate_production_secret
    from fwsort.config import settings

    orig_env, orig_key = settings.APP_ENV, settings.APP_SECRET_KEY
    try:
        object.__setattr__(settings, "APP_ENV", "production")
        object.__setattr__(settings, "APP_SECRET_KEY", "change-me")
        with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
            _validate_production_secret()
    finally:
        object.__setattr__(settings, "APP_ENV", orig_env)
        object.__setattr__(settings, "APP_SECRET_KEY", orig_key)


def test_wp02_secret_key_validation_prod_rejects_short():
    """生产模式：长度 < 32 抛错"""
    from main import _validate_production_secret
    from fwsort.config import settings

    orig_env, orig_key = settings.APP_ENV, settings.APP_SECRET_KEY
    try:
        object.__setattr__(settings, "APP_ENV", "production")
        object.__setattr__(settings, "APP_SECRET_KEY", "abc123")
        with pytest.raises(RuntimeError, match="长度"):
            _validate_production_secret()
    finally:
        object.__setattr__(settings, "APP_ENV", orig_env)
        object.__setattr__(settings, "APP_SECRET_KEY", orig_key)


def test_wp02_secret_key_validation_prod_accepts_strong():
    """生产模式：≥32 位 → 通过"""
    from main import _validate_production_secret
    from fwsort.config import settings

    orig_env, orig_key = settings.APP_ENV, settings.APP_SECRET_KEY
    try:
        object.__setattr__(settings, "APP_ENV", "production")
        object.__setattr__(settings, "APP_SECRET_KEY", "AbCdEfGh1234567890IjKlMnOpQrStUvWxYz")
        _validate_production_secret()
    finally:
        object.__setattr__(settings, "APP_ENV", orig_env)
        object.__setattr__(settings, "APP_SECRET_KEY", orig_key)


# ========== WP-01 CORS 收敛 ==========
def test_wp01_cors_parse_default_wildcard():
    """默认（空字符串）→ 解析为 ['*']"""
    from main import _parse_cors_origins

    with patch("main.settings") as ms:
        ms.APP_CORS_ORIGINS = ""
        assert _parse_cors_origins() == ["*"]


def test_wp01_cors_parse_explicit_list():
    """显式白名单 → 解析为列表"""
    from main import _parse_cors_origins

    with patch("main.settings") as ms:
        ms.APP_CORS_ORIGINS = "https://app.fwquant.com,https://admin.fwquant.com"
        assert _parse_cors_origins() == ["https://app.fwquant.com", "https://admin.fwquant.com"]


def test_wp01_cors_parse_strips_spaces():
    """多余空格被正确处理"""
    from main import _parse_cors_origins

    with patch("main.settings") as ms:
        ms.APP_CORS_ORIGINS = " https://a.com , https://b.com "
        assert _parse_cors_origins() == ["https://a.com", "https://b.com"]


# ========== WP-03 登录限流（用 asyncio.run 包装）==========
def test_wp03_rate_limit_isolated_by_email():
    """不同邮箱的失败计数互不影响"""
    from fwsort.rate_limit import _record_failure, _clear_failures

    async def _run():
        await _clear_failures("test", "a@x.com")
        await _clear_failures("test", "b@x.com")
        try:
            n1 = await _record_failure("test", "a@x.com", lock_minutes=15)
            n2 = await _record_failure("test", "b@x.com", lock_minutes=15)
            assert n1 == 1
            assert n2 == 1
        finally:
            await _clear_failures("test", "a@x.com")
            await _clear_failures("test", "b@x.com")

    asyncio.run(_run())


def test_wp03_rate_limit_threshold_locks():
    """失败计数超过阈值后被锁定"""
    from fwsort.rate_limit import _record_failure, _clear_failures, _is_locked

    scope = "test_threshold"
    key = "u@x.com"

    async def _run():
        await _clear_failures(scope, key)
        try:
            for _ in range(5):
                await _record_failure(scope, key, lock_minutes=15)
            assert await _is_locked(scope, key) is True
        finally:
            await _clear_failures(scope, key)

    asyncio.run(_run())


# ========== WP-04 管理员鉴权：bootstrap 模式 ==========
def test_wp04_bootstrap_mode_allows_init():
    """首次启动（无 admin）→ init 接口放行"""
    from router.admin_router import _bootstrap_or_admin

    async def _run():
        mock_db = AsyncMock()
        with patch("router.admin_router._has_any_admin", return_value=False), \
             patch("router.admin_router.settings") as ms:
            ms.APP_ALLOW_INIT = True
            await _bootstrap_or_admin(mock_db, user=None)  # 不应抛错

    asyncio.run(_run())


def test_wp04_bootstrap_mode_blocks_without_admin():
    """已有 admin + 无 token → 抛 AuthError"""
    from router.admin_router import _bootstrap_or_admin
    from fwsort.exceptions import AuthError

    async def _run():
        mock_db = AsyncMock()
        with patch("router.admin_router._has_any_admin", return_value=True), \
             patch("router.admin_router.settings") as ms:
            ms.APP_ALLOW_INIT = True
            with pytest.raises(AuthError):
                await _bootstrap_or_admin(mock_db, user=None)

    asyncio.run(_run())


def test_wp04_bootstrap_mode_blocks_with_non_admin():
    """已有 admin + role < 3 → 抛 PermissionError_"""
    from router.admin_router import _bootstrap_or_admin
    from fwsort.exceptions import PermissionError_

    async def _run():
        mock_db = AsyncMock()
        mock_user = AsyncMock()
        mock_user.role = 0
        with patch("router.admin_router._has_any_admin", return_value=True), \
             patch("router.admin_router.settings") as ms:
            ms.APP_ALLOW_INIT = True
            with pytest.raises(PermissionError_):
                await _bootstrap_or_admin(mock_db, user=mock_user)

    asyncio.run(_run())


# ========== WP-05 软删除 ==========
def test_wp05_execution_account_has_deleted_at():
    """ExecutionAccount 模型必须有 deleted_at 字段"""
    from fwsort.models import ExecutionAccount

    col = ExecutionAccount.__table__.columns["deleted_at"]
    assert col.nullable is True
    assert col.index is True


def test_wp05_migration_includes_deleted_at():
    """migrations.py 必须包含 deleted_at 补丁"""
    from fwsort.migrations import _PATCHES

    found = any(t == "execution_account" and c == "deleted_at" for t, c, _, _ in _PATCHES)
    assert found, "migrations._PATCHES must include execution_account.deleted_at"


# ========== WP-03 LoginAttempt 模型 + 迁移 ==========
def test_wp03_login_attempt_model_exists():
    """models.py 必须有 LoginAttempt 表"""
    from fwsort.models import LoginAttempt

    assert LoginAttempt.__tablename__ == "login_attempt"
    cols = {c.name for c in LoginAttempt.__table__.columns}
    for required in ("id", "email", "ip", "success", "user_agent", "reason", "created_at"):
        assert required in cols, f"LoginAttempt missing column: {required}"


def test_wp03_migration_can_create_login_attempt_table():
    """migrations._NEW_TABLES_DDL 必须包含 login_attempt 建表语句"""
    from fwsort.migrations import _NEW_TABLES_DDL
    from fwsort.database import sync_engine
    from sqlalchemy import inspect, text

    insp = inspect(sync_engine)
    if not insp.has_table("login_attempt"):
        assert len(_NEW_TABLES_DDL) >= 1
        ddl = _NEW_TABLES_DDL[0][1] if str(sync_engine.url).startswith("sqlite") else _NEW_TABLES_DDL[1][1]
        with sync_engine.begin() as conn:
            conn.execute(text(ddl))
    assert inspect(sync_engine).has_table("login_attempt")
