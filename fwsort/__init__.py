# fwsort 包：福纹排行榜核心业务层
__version__ = "1.0.0"

# ========== 兼容旧路径（execution/ 与 gateway/ 内拆分文件已合并）==========
# 背景：
#   原 fwsort.execution/ 目录（simulator / es_writer / outbox）已拆分迁移至：
#     - 模拟盘下单  → fwsort.gateway.simulator_gateway
#     - 订单日志 ES → fwsort.order_log.es_writer
#     - Outbox 事件 → fwsort.order_log.outbox
#   原 fwsort.gateway/ 内的 3 个子文件（polymarket_client / okx_client / okx_executor）
#   已分别合并到 1 个网关文件（polymarket_gateway / okx_gateway）。
#   为避免大批量改调用点（router/scheduler/tests 仍用旧 import），通过 sys.modules
#   把旧路径注册为新模块的别名，业务代码无需任何改动。
# 何时可删除：当所有调用点迁移到新路径后，可整体删除本段。
import sys
import types as _types

# 1) 先把 gateway 和 order_log 加载（确保其 __init__.py 已执行完）
# 注：必须显式 import 子模块，否则不会被注册到 sys.modules
from fwsort.order_log import es_writer as _es_writer_mod  # noqa: F401
from fwsort.order_log import outbox as _outbox_mod  # noqa: F401
from fwsort.gateway import simulator_gateway as _sim_mod  # noqa: F401
from fwsort.gateway import polymarket_gateway as _pm_mod  # noqa: F401
from fwsort.gateway import okx_gateway as _okx_mod  # noqa: F401
from fwsort import order_log as _order_log  # noqa: F401

# 2) fwsort.execution.* 兼容（旧 → 新）
_exec_pkg = _types.ModuleType("fwsort.execution")
_exec_pkg.__path__ = []  # 标记为 package
_exec_pkg.es_writer = _es_writer_mod
_exec_pkg.outbox = _outbox_mod
_exec_pkg.simulator = _sim_mod
sys.modules["fwsort.execution"] = _exec_pkg
sys.modules["fwsort.execution.es_writer"] = _es_writer_mod
sys.modules["fwsort.execution.outbox"] = _outbox_mod
sys.modules["fwsort.execution.simulator"] = _sim_mod

# 3) fwsort.gateway.polymarket_client / okx_client / okx_executor 兼容（旧 → 新）
sys.modules["fwsort.gateway.polymarket_client"] = _pm_mod
sys.modules["fwsort.gateway.okx_client"] = _okx_mod
sys.modules["fwsort.gateway.okx_executor"] = _okx_mod
