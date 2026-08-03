"""信号提供者配置服务层：CRUD + 热加载 + 文件生成 + IDE 打开

职责：
    - 信号提供者配置的增删改查
    - 新建信号源时自动在 providers/ 下生成 .py 文件
    - 热加载（重新扫描 + 重新注册 + 同步数据库）
    - 用默认 IDE 打开信号源 .py 文件
    - 系统启动时将 auto-discovered providers 同步到数据库
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from loguru import logger

from fwsort.database import get_sync_db
from fwsort.models import AutoTask, SignalProviderConfig
from fwsort.signals.base import SignalCategory
from fwsort.signals.manager import (
    _PROVIDERS,
    _PROVIDER_CATEGORIES,
    get_provider,
    get_provider_file_path,
    list_providers,
    register_provider,
    reload_providers,
    reset_provider_instance,
)

# providers 目录路径
_PROVIDERS_DIR = os.path.join(os.path.dirname(__file__), "providers")


def sync_builtin_providers() -> int:
    """将自动发现的内置信号源同步到数据库（幂等）

    Returns:
        int: 新增数量
    """
    count = 0
    with get_sync_db() as db:
        for name, cls in _PROVIDERS.items():
            category = _PROVIDER_CATEGORIES.get(name, SignalCategory.INTERNAL.value)
            existing = db.query(SignalProviderConfig).filter(
                SignalProviderConfig.provider_name == name
            ).first()

            if existing:
                # 更新已有的记录（仅更新类名和模块路径，不改变 is_builtin 状态）
                existing.class_name = cls.__name__
                existing.module_path = cls.__module__
                existing.category = category
                # 仅当原本就是内置时才标记为内置（自定义信号源保持 is_builtin=False）
                if existing.is_builtin:
                    existing.is_builtin = True
                if not existing.display_name:
                    existing.display_name = name
            else:
                # 新增
                config = SignalProviderConfig(
                    provider_name=name,
                    category=category,
                    class_name=cls.__name__,
                    module_path=cls.__module__,
                    display_name=name,
                    is_builtin=True,
                    is_active=True,
                )
                db.add(config)
                count += 1
        db.commit()

    if count > 0:
        logger.info("[SignalConfig] synced {} builtin providers to DB", count)
    return count


def list_signal_providers(
    category: str | None = None,
    include_inactive: bool = False,
) -> list[dict]:
    """查询信号提供者列表"""
    with get_sync_db() as db:
        query = db.query(SignalProviderConfig)
        if category:
            query = query.filter(SignalProviderConfig.category == category)
        if not include_inactive:
            query = query.filter(SignalProviderConfig.is_active == True)
        configs = query.order_by(SignalProviderConfig.id.asc()).all()

        result = []
        for c in configs:
            d = _config_to_dict(c)
            d["available_in_runtime"] = c.provider_name in _PROVIDERS
            # 补充文件路径信息
            d["file_path"] = get_provider_file_path(c.provider_name)
            result.append(d)
        return result


def get_signal_provider(provider_id: int) -> dict | None:
    """查询单个信号提供者"""
    with get_sync_db() as db:
        c = db.query(SignalProviderConfig).filter(SignalProviderConfig.id == provider_id).first()
        if not c:
            return None
        d = _config_to_dict(c)
        d["available_in_runtime"] = c.provider_name in _PROVIDERS
        d["file_path"] = get_provider_file_path(c.provider_name)
        return d


def list_active_signal_providers() -> list[dict]:
    """获取所有启用的信号源（供任务创建时选择信号来源）"""
    with get_sync_db() as db:
        configs = (
            db.query(SignalProviderConfig)
            .filter(SignalProviderConfig.is_active == True)
            .order_by(SignalProviderConfig.id.asc())
            .all()
        )
        result = []
        for c in configs:
            d = _config_to_dict(c)
            d["available_in_runtime"] = c.provider_name in _PROVIDERS
            result.append(d)
        return result


def _auto_generate_name(existing_names: set[str] | None = None) -> str:
    """自动生成信号源名称: signal_YYYYMMDDhhmm

    若生成的名称已存在（同一分钟内多次调用），自动向后顺延 1 分钟，
    保证 provider_name 在数据库中唯一。
    """
    from datetime import timedelta
    dt = datetime.now()
    base = dt.strftime("signal_%Y%m%d%H%M")
    if not existing_names or base not in existing_names:
        return base
    # 若已存在，逐分钟向后顺延
    for i in range(1, 120):
        candidate = (dt + timedelta(minutes=i)).strftime("signal_%Y%m%d%H%M")
        if candidate not in existing_names:
            return candidate
    # 兜底（极端情况）：用秒级时间戳
    return dt.strftime("signal_%Y%m%d%H%M%S")


def create_signal_provider(data: dict) -> dict:
    """创建信号提供者配置

    流程：
        1. 自动生成名称/类名（如未指定）
        2. 根据 source_type 生成对应的 .py 文件
        3. 写入数据库
        4. 热加载注册到系统

    Args:
        data: dict 包含:
            - provider_name: 信号源名称（可选，自动生成）
            - class_name: Python 类名（可选，自动生成）
            - source_type: "python" | "http_url"，默认 "python"
            - http_url: 当 source_type=http_url 时必填
            - category: 类别，默认 custom
            - display_name: 显示名称（可选）
            - description: 描述（可选）
            - config_json: 初始化参数 JSON（可选）
    """
    source_type = data.get("source_type", "python")
    category = data.get("category", SignalCategory.CUSTOM.value)

    # 自动生成名称（如未指定）
    provider_name = data.get("provider_name", "").strip()
    if not provider_name:
        # 先收集已有的 provider_name 集合，避免同分钟内重名
        with get_sync_db() as _db0:
            _existing = {r[0] for r in _db0.query(SignalProviderConfig.provider_name).all()}
        provider_name = _auto_generate_name(_existing)
    elif source_type == "python" and not provider_name.startswith("signal_"):
        # 规范化名称
        provider_name = re.sub(r'[^a-zA-Z0-9_]', '_', provider_name.lower())

    class_name = data.get("class_name", "").strip()
    if not class_name:
        # 根据 provider_name 生成类名
        parts = provider_name.split("_")
        class_name = "".join(p.capitalize() for p in parts) + "Provider"

    with get_sync_db() as db:
        existing = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.provider_name == provider_name
        ).first()
        if existing:
            raise ValueError(f"信号源名称已存在: {provider_name}")

        # 1. 生成 .py 文件
        if source_type == "http_url":
            http_url = data.get("http_url", "").strip()
            if not http_url:
                raise ValueError("HTTP URL 信号源必须配置 http_url")
            module_path = _generate_http_provider_file(
                provider_name=provider_name,
                class_name=class_name,
                http_url=http_url,
                category=category,
            )
            config_data = {"http_url": http_url}
        else:
            module_path = _generate_provider_file(
                provider_name=provider_name,
                class_name=class_name,
                category=category,
                config_template=data.get("config_json", {}),
            )
            raw_cfg = data.get("config_json", {})
            if isinstance(raw_cfg, str):
                try:
                    config_data = json.loads(raw_cfg)
                except (json.JSONDecodeError, TypeError):
                    config_data = {}
            elif isinstance(raw_cfg, dict):
                config_data = raw_cfg
            else:
                config_data = {}

        # 2. 写入数据库
        config = SignalProviderConfig(
            provider_name=provider_name,
            category=category,
            class_name=class_name,
            module_path=module_path,
            display_name=data.get("display_name", provider_name),
            description=data.get("description", ""),
            config_json=json.dumps(config_data),
            is_active=data.get("is_active", True),
            is_builtin=False,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

        # 3. 热加载注册（在 session 内完成，避免 DetachedInstanceError）
        _try_register_from_config(config)

        logger.info(f"[SignalConfig] created provider: {provider_name} (file: {module_path})")
        result = _config_to_dict(config)

    result["file_path"] = get_provider_file_path(provider_name)
    return result


def update_signal_provider(provider_id: int, data: dict) -> dict | None:
    """更新信号提供者配置

    注意：当修改 http_url 时，会同时更新 config_json 和重新生成 .py 文件。
    """
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return None

        source_type = data.get("source_type", "python")
        http_url = data.get("http_url", "").strip()

        # 处理 config_json 更新
        existing_config = json.loads(config.config_json or "{}")
        if "http_url" in data:
            if data.get("http_url"):
                existing_config["http_url"] = data["http_url"]
            elif "http_url" in existing_config:
                del existing_config["http_url"]

        if "config_json" in data and data["config_json"] is not None:
            raw_cfg = data["config_json"]
            try:
                if isinstance(raw_cfg, str):
                    new_cfg = json.loads(raw_cfg)
                elif isinstance(raw_cfg, dict):
                    new_cfg = raw_cfg
                else:
                    new_cfg = {}
                existing_config.update(new_cfg)
            except (json.JSONDecodeError, TypeError):
                pass

        config.config_json = json.dumps(existing_config)

        # 处理可更新字段
        updatable = ["display_name", "description"]
        for field in updatable:
            if field in data:
                setattr(config, field, data[field])

        if "is_active" in data:
            config.is_active = data["is_active"]

        # 如果 HTTP URL 变更，重新生成文件并注册
        if http_url and source_type == "http_url" and config.category == SignalCategory.CUSTOM.value:
            class_name = config.class_name
            module_path = _generate_http_provider_file(
                provider_name=config.provider_name,
                class_name=class_name,
                http_url=http_url,
                category=config.category,
            )
            config.module_path = module_path

        db.commit()
        db.refresh(config)

        reset_provider_instance(config.provider_name)

        # 如果文件重新生成，重新注册
        if http_url and source_type == "http_url":
            _try_register_from_config(config)

        logger.info(f"[SignalConfig] updated provider: {config.provider_name}")
        result = _config_to_dict(config)
        result["file_path"] = get_provider_file_path(config.provider_name)
        return result


def check_provider_references(provider_id: int) -> list[dict]:
    """检查信号源被哪些任务引用

    Returns:
        list[dict]: 引用该信号源的任务列表 [{task_id, task_name}]
    """
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return []
        provider_name = config.provider_name

        tasks = db.query(AutoTask).filter(
            AutoTask.signal_source == provider_name,
            AutoTask.deleted_at.is_(None),
        ).all()
        return [{"task_id": t.id, "task_name": t.task_name, "is_active": t.is_active} for t in tasks]


def delete_signal_provider(provider_id: int) -> bool:
    """删除信号提供者配置

    注意：仅 custom 类别的信号源可删除，internal/external 不可删除。
    删除前会检查是否有任务引用该信号源。
    """
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return False
        if config.is_builtin:
            raise ValueError("系统内置信号源不可删除")
        if config.category != SignalCategory.CUSTOM.value:
            raise ValueError(f"{SignalCategory.display_names().get(config.category, config.category)}信号源不可删除，仅自定义信号源可删除")

        # 检查是否有任务引用
        ref_tasks = db.query(AutoTask).filter(
            AutoTask.signal_source == config.provider_name,
            AutoTask.deleted_at.is_(None),
        ).all()
        if ref_tasks:
            task_info = ", ".join([f'"{t.task_name}"(ID:{t.id})' for t in ref_tasks])
            raise ValueError(f"该信号源正在被 {len(ref_tasks)} 个任务使用: {task_info}。请先删除相关任务后再删除信号源")

        # 同时删除 .py 文件
        file_path = get_provider_file_path(config.provider_name)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info("[SignalConfig] deleted file: {}", file_path)
            except Exception as e:
                logger.warning("[SignalConfig] failed to delete file: {}", e)

        provider_name = config.provider_name
        db.delete(config)
        db.commit()

        reset_provider_instance(provider_name)

        logger.info("[SignalConfig] deleted provider: {}", provider_name)
        return True


def run_health_check(provider_id: int) -> dict:
    """执行信号源健康检查"""
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return {"error": "not found"}

        try:
            provider = get_provider(config.provider_name)
            health = provider.health_check()
            config.health_status = "ok" if health.get("ready", False) else "fail"
            config.health_message = json.dumps(health, ensure_ascii=False)
            config.last_health_check_at = datetime.utcnow()
            db.commit()
            return {"status": config.health_status, "health": health}
        except Exception as e:
            config.health_status = "fail"
            config.health_message = str(e)
            config.last_health_check_at = datetime.utcnow()
            db.commit()
            return {"status": "fail", "error": str(e)}


def test_signal_provider(provider_id: int) -> dict:
    """测试信号生成"""
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return {"error": "not found"}

        try:
            cfg = json.loads(config.config_json or "{}")
            # 传递 config_json 参数（包含 http_url 等配置）
            provider = get_provider(config.provider_name, config_json=cfg)
            signal = provider.get_signal()
            return {"success": True, "signal": signal.to_dict()}
        except Exception as e:
            return {"success": False, "error": str(e)}


def hot_reload() -> dict:
    """热加载：重新扫描 providers/ 目录，同步数据库

    用于：新建/编辑/删除 .py 文件后，点击"刷新"按钮触发热加载。
    """
    # 1. 重新扫描 providers/ 目录
    reload_result = reload_providers()

    # 2. 同步到数据库（新增的入库，已删除的清理）
    with get_sync_db() as db:
        for name, cls in _PROVIDERS.items():
            category = _PROVIDER_CATEGORIES.get(name, SignalCategory.CUSTOM.value)
            existing = db.query(SignalProviderConfig).filter(
                SignalProviderConfig.provider_name == name
            ).first()
            if not existing:
                config = SignalProviderConfig(
                    provider_name=name,
                    category=category,
                    class_name=cls.__name__,
                    module_path=cls.__module__,
                    display_name=name,
                    is_builtin=False,
                    is_active=True,
                )
                db.add(config)
            elif existing.is_builtin:
                # 更新内置信号源的类信息（保持 is_builtin=True）
                existing.class_name = cls.__name__
                existing.module_path = cls.__module__
                existing.category = category

        # 处理已删除的（runtime 中不存在的）
        all_configs = db.query(SignalProviderConfig).all()
        removed = []
        for c in all_configs:
            if c.provider_name not in _PROVIDERS and not c.is_builtin:
                # 文件被删除了，同时删除数据库记录
                db.delete(c)
                removed.append(c.provider_name)
                logger.info("[SignalConfig] removed stale provider from DB: {}", c.provider_name)

        db.commit()

        if removed:
            logger.info("[SignalConfig] hot reload removed {} stale providers", len(removed))

    logger.info("[SignalConfig] hot reload: {}", reload_result)
    return reload_result


def open_provider_file(provider_id: int) -> dict:
    """用默认 IDE 打开信号源的 .py 文件进行编辑"""
    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            return {"success": False, "error": "信号源不存在"}

        file_path = get_provider_file_path(config.provider_name)
        if not file_path or not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"未找到文件: {file_path}",
                "provider_name": config.provider_name,
                "module_path": config.module_path,
            }

    try:
        if sys.platform == "win32":
            os.startfile(file_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])

        logger.info(f"[SignalConfig] opened file in IDE: {file_path}")
        return {"success": True, "file_path": file_path}
    except Exception as e:
        return {"success": False, "error": str(e), "file_path": file_path}


def _generate_http_provider_file(
    provider_name: str,
    class_name: str,
    http_url: str,
    category: str = "custom",
) -> str:
    """生成 HTTP URL 类型的信号源 .py 文件

    该信号源会请求配置的 HTTP URL，获取 JSON 并返回 Signal 对象。
    """
    file_stem = re.sub(r'[^a-zA-Z0-9_]', '_', provider_name.lower())
    file_name = f"{file_stem}.py"
    file_path = os.path.join(_PROVIDERS_DIR, file_name)

    category_cn_map = SignalCategory.display_names()
    category_cn = category_cn_map.get(category, category)

    code = f'''"""HTTP URL 信号源: {provider_name}

类别: {category_cn} ({category})
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
HTTP URL: {http_url}

使用说明：
    1. 该信号源会请求配置的 HTTP URL
    2. URL 返回 JSON，需包含: symbol, direction, amount, timestamp
    3. 如果返回格式不符，会使用默认值
    4. 如需自定义解析逻辑，可编辑此文件
"""
from __future__ import annotations

import json
import time
from urllib.request import urlopen, Request

from fwsort.signals.base import Direction, Signal, SignalProvider


class {class_name}(SignalProvider):
    """HTTP URL 信号提供者

    请求 HTTP URL 获取 JSON 信号数据。
    """

    name: str = "{provider_name}"
    category: str = "{category}"  # {category_cn}

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {{}}
        self.http_url = self.config.get("http_url", "{http_url}")
        self.timeout = self.config.get("timeout", 10)

    def get_signal(self) -> Signal:
        """从 HTTP URL 获取信号

        请求配置的 URL，解析 JSON 返回 Signal 对象。
        JSON 格式: {{"symbol": "...", "direction": "UP/DOWN", "amount": 1.0, "timestamp": 123456}}
        """
        try:
            req = Request(
                self.http_url,
                headers={{"User-Agent": "fwsort-signal/1.0"}}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            symbol = data.get("symbol", f"btc-updown-4h-{{int(time.time())}}")
            direction = data.get("direction", "UP")
            if direction not in ("UP", "DOWN"):
                direction = "UP"
            amount = float(data.get("amount", 1.0))
            timestamp = int(data.get("timestamp", int(time.time())))

            return Signal(
                symbol=symbol,
                amount=amount,
                direction=direction,
                source=self.name,
                timestamp=timestamp,
            )
        except Exception as e:
            from loguru import logger
            logger.warning(f"[{{self.name}}] HTTP request failed: {{e}}, returning default signal")
            return Signal(
                symbol=f"btc-updown-4h-{{int(time.time())}}",
                amount=1.0,
                direction="UP",
                source=self.name,
            )

    def health_check(self) -> dict:
        """健康检查 - 测试 URL 是否可达"""
        try:
            req = Request(
                self.http_url,
                headers={{"User-Agent": "fwsort-health-check/1.0"}}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                return {{
                    "provider": self.name,
                    "category": self.category,
                    "ready": True,
                    "http_url": self.http_url,
                    "status_code": resp.status,
                }}
        except Exception as e:
            return {{
                "provider": self.name,
                "category": self.category,
                "ready": False,
                "http_url": self.http_url,
                "error": str(e),
            }}
'''

    os.makedirs(_PROVIDERS_DIR, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    logger.info(f"[SignalConfig] generated HTTP provider file: {file_path}")
    return f"fwsort.signals.providers.{file_stem}"


def _generate_provider_file(
    provider_name: str,
    class_name: str,
    category: str = "custom",
    config_template: dict | None = None,
) -> str:
    """在 providers/ 目录下生成一个继承 SignalProvider 的 .py 文件

    Returns:
        str: 模块路径（如 fwsort.signals.providers.my_provider）
    """
    # 生成文件名：provider_name 转为 snake_case
    file_stem = re.sub(r'[^a-zA-Z0-9_]', '_', provider_name.lower())
    file_name = f"{file_stem}.py"
    file_path = os.path.join(_PROVIDERS_DIR, file_name)

    # 检查是否已存在
    if os.path.exists(file_path):
        logger.warning("[SignalConfig] file already exists: {}, will overwrite", file_path)

    # 生成模板代码
    category_cn_map = SignalCategory.display_names()
    category_cn = category_cn_map.get(category, category)
    config_default = json.dumps(config_template or {}, ensure_ascii=False, indent=4)

    code = f'''"""自定义信号源: {provider_name}

类别: {category_cn} ({category})
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

使用说明：
    1. 实现 get_signal() 方法返回 Signal 对象
    2. 可选覆盖 health_check() 方法
    3. 修改后在 Admin 面板点击"🔄 刷新"热加载

标的代码格式: btc-updown-4h-{{epoch}}
下单金额: 固定 1 USDC
下单方向: UP / DOWN
"""
from __future__ import annotations

import random
import time

from fwsort.signals.base import Direction, Signal, SignalProvider


class {class_name}(SignalProvider):
    """{provider_name} 信号提供者

    继承 SignalProvider，实现自定义信号逻辑。
    """

    name: str = "{provider_name}"
    category: str = "{category}"  # {category_cn}

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {config_default}

    def get_signal(self) -> Signal:
        """获取信号

        在这里实现你的信号生成逻辑。
        示例：随机方向 + 基于时间的标的代码
        """
        epoch = str(((int(time.time()) // (4 * 60 * 60)) * (4 * 60 * 60)))
        direction: Direction = random.choice(["UP", "DOWN"])
        symbol = f"btc-updown-4h-{{epoch}}"

        return Signal(
            symbol=symbol,
            amount=1.0,
            direction=direction,
            source=self.name,
            timestamp=int(epoch),
        )

    def health_check(self) -> dict:
        """健康检查（可选覆盖）"""
        return {{
            "provider": self.name,
            "category": self.category,
            "ready": True,
        }}
'''

    # 写入文件
    os.makedirs(_PROVIDERS_DIR, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    logger.info(f"[SignalConfig] generated provider file: {file_path}")
    return f"fwsort.signals.providers.{file_stem}"


def _try_register_from_config(config: SignalProviderConfig) -> None:
    """尝试根据数据库配置注册信号源到系统"""
    if not config.module_path or not config.class_name:
        return
    try:
        module = importlib.import_module(config.module_path)
        cls = getattr(module, config.class_name)
        from fwsort.signals.base import SignalProvider
        if isinstance(cls, type) and issubclass(cls, SignalProvider):
            register_provider(config.provider_name, cls, category=config.category)
            logger.info(f"[SignalConfig] registered external provider: {config.provider_name}")
    except Exception as e:
        logger.warning(f"[SignalConfig] failed to register {config.provider_name}: {e}")


def _config_to_dict(c: SignalProviderConfig) -> dict:
    result = {
        "id": c.id,
        "provider_name": c.provider_name,
        "category": c.category,
        "class_name": c.class_name,
        "module_path": c.module_path,
        "display_name": c.display_name,
        "description": c.description,
        "config_json": json.loads(c.config_json or "{}"),
        "is_active": c.is_active,
        "is_builtin": c.is_builtin,
        "health_status": c.health_status,
        "health_message": c.health_message,
        "last_health_check_at": c.last_health_check_at.isoformat() if c.last_health_check_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    # 补充中文类别名
    result["category_display"] = SignalCategory.display_names().get(c.category, c.category)
    # 是否可删除：custom 且非 builtin
    result["can_delete"] = (c.category == SignalCategory.CUSTOM.value) and (not c.is_builtin)
    return result


def get_available_categories() -> list[dict]:
    """获取所有可用的信号源类别（供前端下拉选择）"""
    result = []
    for cat in SignalCategory:
        result.append({
            "value": cat.value,
            "display": SignalCategory.display_names().get(cat.value, cat.value),
        })
    return result