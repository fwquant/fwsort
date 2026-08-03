"""
配置服务：数据库统一配置管理（单表方案）

架构：
  system_config 单表 → default_value（出厂默认）+ config_value（用户覆盖）

读取逻辑：
  COALESCE(config_value, default_value) → 数据库值
  数据库无记录                           → 用 .env（仅敏感/启动必需项）
  .env 也无                             → 用代码 fallback

所有非敏感配置统一从数据库读取，通过 get(key, default) 访问。
"""

import json
import time
from typing import Any

from sqlalchemy import select
from loguru import logger

from fwsort.database import AsyncSessionLocal
from fwsort.models import SystemConfig


_CONFIG_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 120

_SENSITIVE_PREFIXES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "POSTGRES_PASSWORD", "REDIS_PASSWORD", "REDIS_URL",
    "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
    "POLYMARKET_PRIVATE_KEY", "POLYMARKET_SECRET", "POLYMARKET_PASSPHRASE",
    "POLYMARKET_RELAYER_API_KEY", "POLYMARKET_RELAYER_PRIVATE_KEY",
    "SECRET_KEY", "JWT_SECRET", "ENCRYPTION_KEY",
)


def is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(upper.startswith(p) for p in _SENSITIVE_PREFIXES)


def _convert_value(raw: str, value_type: str) -> Any:
    if value_type == "int":
        return int(raw)
    elif value_type == "float":
        return float(raw)
    elif value_type == "bool":
        return raw.lower() in ("true", "1", "yes", "on")
    elif value_type == "json":
        return json.loads(raw)
    return raw


async def get_config(key: str, default: Any = None) -> Any:
    """获取单个配置值（带缓存），数据库 → env → 代码默认"""
    key_upper = key.upper()
    now = time.time()

    if key_upper in _CONFIG_CACHE:
        value, ts = _CONFIG_CACHE[key_upper]
        if now - ts < _CACHE_TTL:
            return value

    value = await _load_config_from_db(key_upper)
    if value is not None:
        _CONFIG_CACHE[key_upper] = (value, now)
        return value

    return default


async def _load_config_from_db(key: str) -> Any | None:
    """从数据库加载单个配置（COALESCE(config_value, default_value)）"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    SystemConfig.config_value,
                    SystemConfig.default_value,
                    SystemConfig.value_type,
                )
                .where(SystemConfig.config_key == key)
                .limit(1)
            )
            row = result.first()
            if row:
                raw = row[0] if row[0] is not None else row[1]
                if raw is not None:
                    return _convert_value(raw, row[2])
    except Exception as e:
        logger.debug(f"从数据库加载配置 {key} 失败: {e}")
    return None


async def get_all_configs() -> list[dict]:
    """获取所有配置（单表：默认值 + 当前值 + 来源标记）"""
    configs: list[dict] = []

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    SystemConfig.config_key,
                    SystemConfig.default_value,
                    SystemConfig.config_value,
                    SystemConfig.value_type,
                    SystemConfig.group,
                    SystemConfig.description,
                    SystemConfig.updated_by,
                    SystemConfig.updated_at,
                    SystemConfig.readonly,
                ).order_by(SystemConfig.group, SystemConfig.config_key)
            )
            for key, def_val, cur_val, vtype, grp, desc, by, at, ro in result.all():
                is_overridden = cur_val is not None
                configs.append({
                    "config_key": key,
                    "default_value": def_val,
                    "current_value": cur_val,
                    "effective_value": cur_val if is_overridden else def_val,
                    "value_type": vtype,
                    "group": grp,
                    "description": desc,
                    "source": "override" if is_overridden else "default",
                    "is_overridden": is_overridden,
                    "readonly": ro or False,
                    "updated_by": by,
                    "updated_at": at.isoformat() if at else None,
                })
    except Exception as e:
        logger.warning(f"获取所有配置失败: {e}")

    return configs


async def save_config(key: str, value: Any, value_type: str = "str",
                      group: str = "general", description: str = "",
                      updated_by: str = "system") -> None:
    """保存配置当前值到数据库（UPSERT config_value）"""
    key_upper = key.upper()
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(SystemConfig).where(SystemConfig.config_key == key_upper)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.config_value = str(value)
            row.value_type = value_type
            row.group = group
            if description:
                row.description = description
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                config_key=key_upper,
                default_value=str(value),
                config_value=str(value),
                value_type=value_type,
                group=group,
                description=description,
                updated_by=updated_by,
            )
            session.add(row)
        await session.commit()

    _CONFIG_CACHE[key_upper] = (
        _convert_value(str(value), value_type),
        time.time(),
    )


async def reset_config(key: str) -> None:
    """重置配置为默认值（SET config_value = NULL）"""
    key_upper = key.upper()
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            select(SystemConfig).where(SystemConfig.config_key == key_upper)
        )
        obj = row.scalar_one_or_none()
        if obj:
            obj.config_value = None
            obj.updated_by = "system(reset)"
            await session.commit()
    _CONFIG_CACHE.pop(key_upper, None)


async def reset_all_configs() -> int:
    """重置所有非敏感、非只读配置为默认值（批量清空 config_value）"""
    reset_count = 0
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(SystemConfig).where(SystemConfig.readonly == False)
        )
        for obj in rows.scalars().all():
            if is_sensitive_key(obj.config_key):
                continue
            if obj.config_value is not None:
                obj.config_value = None
                obj.updated_by = "system(reset-all)"
                reset_count += 1
        if reset_count > 0:
            await session.commit()
    _CONFIG_CACHE.clear()
    logger.info(f"已批量重置 {reset_count} 条配置为默认值")
    return reset_count


async def seed_defaults(defaults: dict[str, dict]) -> int:
    """种子写入默认值（幂等，新增+补充group/description）
    defaults: {key: {default_value, value_type, group, description}}
    """
    count = 0
    updated = 0
    async with AsyncSessionLocal() as session:
        for key, info in defaults.items():
            key_upper = key.upper()
            existing = await session.execute(
                select(SystemConfig).where(SystemConfig.config_key == key_upper)
            )
            obj = existing.scalar_one_or_none()
            if obj:
                new_group = info.get("group", "general")
                if new_group and new_group != obj.group:
                    obj.group = new_group
                    updated += 1
                new_desc = info.get("description", "")
                if new_desc and (not obj.description or obj.description == ""):
                    obj.description = new_desc
                    updated += 1
                continue
            session.add(SystemConfig(
                config_key=key_upper,
                default_value=str(info.get("default_value", "")),
                config_value=None,
                value_type=info.get("value_type", "str"),
                group=info.get("group", "general"),
                description=info.get("description", ""),
            ))
            count += 1
        await session.commit()
    if count:
        logger.info(f"已种子 {count} 条默认配置到数据库")
    if updated:
        logger.info(f"已更新 {updated} 条配置的分组/描述")
    return count


async def sync_env_to_db(sensitive_keys: set[str]) -> int:
    """将 .env 中有值但数据库 config_value 为空的敏感键同步到数据库

    场景：
        - 用户在 .env 配置了 POLYMARKET_RELAYER_API_KEY_ADDRESS=xxx
        - 数据库对应的 config_value 为空（或仍为 None）
        - 启动时自动把 ENV 值写入 DB，使前端/后端统一可查询
    规则：
        - 仅写入 config_value 为 None/空字符串的记录（不覆盖用户手动修改的值）
        - 仅处理传入的敏感键集合
    返回：
        int: 同步数量
    """
    import os as _os

    count = 0
    try:
        async with AsyncSessionLocal() as session:
            all_rows = await session.execute(
                select(SystemConfig).where(
                    SystemConfig.config_key.in_(sensitive_keys)
                )
            )
            for obj in all_rows.scalars():
                env_val = _os.environ.get(obj.config_key)
                if not env_val:
                    continue
                cur_val = obj.config_value
                if cur_val is None or cur_val == "":
                    obj.config_value = env_val
                    count += 1
                    logger.debug(f"[sync_env_to_db] {obj.config_key}: ENV → DB (len={len(env_val)})")
            if count > 0:
                await session.commit()
    except Exception as e:
        logger.warning(f"sync_env_to_db 失败（非致命）: {e}")
    return count


def clear_cache(key: str | None = None) -> None:
    if key:
        _CONFIG_CACHE.pop(key.upper(), None)
    else:
        _CONFIG_CACHE.clear()


async def load_all_from_db() -> dict[str, Any]:
    """从数据库加载所有有效配置（COALESCE(config_value, default_value)）到 dict"""
    configs: dict[str, Any] = {}
    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(
                    SystemConfig.config_key,
                    SystemConfig.config_value,
                    SystemConfig.default_value,
                    SystemConfig.value_type,
                )
            )
            for key, cur_val, def_val, vtype in rows.all():
                raw = cur_val if cur_val is not None else def_val
                if raw is not None:
                    configs[key] = _convert_value(raw, vtype)
    except Exception as e:
        logger.warning(f"加载所有配置失败: {e}")
    return configs