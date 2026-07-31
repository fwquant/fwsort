"""
loguru_adapter.py - 完全兼容原 fw_log 的 loguru 实现
保持所有接口、行为、输出格式完全一致，性能更优
新增：自动分离ERROR日志到独立error日志文件
"""
import logging
import sys
import os
import traceback
from datetime import datetime
from pathlib import Path

from fw_config import 日志目录

# 尝试导入 loguru，如果失败则使用标准 logging 模块作为后备
try:
    from loguru import logger as loguru_logger

    _loguru_available = True
except ImportError:
    _loguru_available = False
    loguru_logger = None


# 定义 fw_log 类
class fw_log:
    """loguru 实现版 - 完全兼容原 fw_log 接口，支持 loguru 不可用时的后备机制"""

    # 日志级别定义（兼容标准 logging 模块级别值）
    # DEBUG(10) < INFO(20) < WARNING(30) < ERROR(40)
    LOG_LEVELS = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40
    }
    默认模块名 = "FWLOG"

    def __init__(self, name: str = "fw", level: str = "INFO", save_to_file: bool = True, console_output: bool = True,
                 file_prefix: str = "", modname: str = "fwlog"):
        if modname != "":
            self.默认模块名 = modname

        import re
        self._name = re.sub(r'[\\/:*?"<>|]', '_', name)
        self._modname = modname
        self._file_prefix = file_prefix
        self.save_to_file = save_to_file
        self.console_output = console_output
        self.fuwen主引擎 = None

        # 初始化日志目录
        self.log_dir = 日志目录
        self.log_path = self._get_log_file_path()
        # 新增：ERROR独立日志文件路径
        self.error_log_path = self._get_error_log_file_path()
        self.setlevel(level)

        # 只保留控制台sink，文件写入全部交给自有代码
        if _loguru_available:
            if not hasattr(fw_log, '_loguru_configured'):
                self._configure_loguru()
                fw_log._loguru_configured = True
            else:
                loguru_logger.remove()
                self._configure_loguru()
            self._bound_logger = loguru_logger.bind(modname=self._modname)
        else:
            self._bound_logger = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name: str):
        if name != "":
            import re
            self._name = re.sub(r'[\\/:*?"<>|]', '_', name)
            self._bound_logger = loguru_logger.bind(name=self._name)
            self.log_path = self._get_log_file_path()
            self.error_log_path = self._get_error_log_file_path()
            if hasattr(fw_log, '_loguru_configured') and fw_log._loguru_configured:
                loguru_logger.remove()
                self._configure_loguru()

    def setname(self, name: str = ""):
        if name != "":
            self.name = name

    @property
    def modname(self):
        return self._modname

    @modname.setter
    def modname(self, modname: str):
        if modname != "":
            self._modname = modname
            if _loguru_available:
                self._bound_logger = loguru_logger.bind(modname=self._modname)

    def setmodname(self, modname: str = ""):
        if modname != "":
            self._modname = modname

    @property
    def file_prefix(self):
        return self._file_prefix

    @file_prefix.setter
    def file_prefix(self, prefix: str):
        self._file_prefix = prefix
        self.log_path = self._get_log_file_path()
        self.error_log_path = self._get_error_log_file_path()
        if _loguru_available and hasattr(fw_log, '_loguru_configured') and fw_log._loguru_configured:
            loguru_logger.remove()
            self._configure_loguru()

    def setfileprefix(self, prefix: str = ""):
        if prefix != "":
            self.file_prefix = prefix

    @property
    def level(self):
        return self._level

    @level.setter
    def level(self, level: str | int):
        self.setlevel(level)

    def _configure_loguru(self):
        """只配置控制台输出，文件写入交给自有代码"""
        loguru_logger.remove()

        def format_record(record):
            timestamp = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            level = record["level"].name
            msg = record["message"]
            simple = record["extra"].get("simple", False)

            if simple:
                return f"{msg}\n"

            if level == "ERROR":
                return f"\033[31m[{timestamp}] [{self._modname}] [{level}] {msg}\033[0m\n"
            else:
                level_color = {
                    "DEBUG": "\033[37m",
                    "INFO": "\033[32m",
                    "WARNING": "\033[33m"
                }.get(level, "\033[0m")
                reset_color = "\033[0m"
                return f"[{timestamp}] [{self._modname}]  {level_color}[{level}]{reset_color} {msg}\n"

        if self.console_output:
            loguru_logger.add(
                sys.stdout,
                colorize=False,
                format=format_record,
                level=self.level_name
            )

    def setlevel(self, level: str | int, isprint=False):
        if isinstance(level, str):
            level_upper = level.upper()
            if level_upper in self.LOG_LEVELS:
                self._level = self.LOG_LEVELS[level_upper]
                self.level_name = level_upper
            else:
                print(f"日志级别 '{level}' 不合法，默认使用 INFO 级别")
                self._level = self.LOG_LEVELS["INFO"]
                self.level_name = "INFO"
        elif isinstance(level, int):
            level_name = next((k for k, v in self.LOG_LEVELS.items() if v == level), None)
            if level_name:
                self._level = level
                self.level_name = level_name
            else:
                print(f"日志级别值 {level} 不合法，默认使用 INFO 级别")
                self._level = self.LOG_LEVELS["INFO"]
                self.level_name = "INFO"
        else:
            print(f"日志级别类型 {type(level)} 不支持，默认使用 INFO 级别")
            self._level = self.LOG_LEVELS["INFO"]
            self.level_name = "INFO"

        if _loguru_available and hasattr(fw_log, '_loguru_configured') and fw_log._loguru_configured:
            loguru_logger.remove()
            self._configure_loguru()
        if isprint:
            print(f"日志级别已设置为：{self.level_name}({self.level})（仅打印≥该级别的日志）")

    def 设置主引擎(self, 引擎):
        from fw_core.fuwen_trader.trader.engine import MainEngine
        self.fuwen主引擎: MainEngine = 引擎

    def _should_print(self, level: str) -> bool:
        return self.LOG_LEVELS[level] >= self.level

    def _print_log(self, msg: str, level: str, simple: bool = False, console_output: bool = None,
                   file_output: bool = None):
        if not self._should_print(level):
            return

        timestamp = self._get_timestamp()
        level_upper = level.upper()

        if simple:
            output_msg = f"{msg}"
            file_line = f"{msg}"
        else:
            file_line = f"[{timestamp}] [{self._modname}] [{level_upper}] {msg}"
            if level_upper == "ERROR":
                output_msg = f"\033[31m[{timestamp}] [{self._modname}] [{level_upper}] {msg}\033[0m"
            else:
                level_color = {
                    "DEBUG": "\033[37m",
                    "INFO": "\033[32m",
                    "WARNING": "\033[33m"
                }.get(level_upper, "\033[0m")
                output_msg = f"[{timestamp}] [{self._modname}] {level_color}[{level_upper}]\033[0m {msg}"

        # 控制台输出
        do_console_output = self.console_output if console_output is None else console_output
        if do_console_output:
            print(output_msg)

        # 写入主日志文件
        do_file_output = self.save_to_file if file_output is None else file_output
        if do_file_output:
            try:
                log_dir = os.path.dirname(self.log_path)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(file_line + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                # 关键新增：ERROR 同时写入独立错误日志文件
                if level_upper == "ERROR" and not simple:
                    error_log_dir = os.path.dirname(self.error_log_path)
                    if error_log_dir:
                        os.makedirs(error_log_dir, exist_ok=True)
                    with open(self.error_log_path, "a", encoding="utf-8") as ef:
                        ef.write(file_line + "\n")
                        ef.flush()
                        os.fsync(ef.fileno())
            except (OSError, IOError):
                # 日志文件写入失败时静默忽略，不影响主功能
                pass

    def _get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _打印到引擎(self, msg: str, gateway_name: str = ""):
        引擎 = None
        if gateway_name != "":
            if self.fuwen主引擎 is not None:
                引擎 = self.fuwen主引擎.get_gateway(gateway_name=gateway_name)
            if 引擎 is not None:
                引擎 = self.fuwen主引擎
        else:
            引擎 = self.fuwen主引擎

        if 引擎 is not None:
            引擎.write_log(msg=f"{msg}")

    # ========== 所有对外接口完全保持原样 ==========
    def debug(self, msg: str, gateway_name: str = "", modname: str = "", simple: bool = False,
              console_output: bool = None, file_output: bool = None):
        self.setmodname(modname)
        self._print_log(msg, "DEBUG", simple=simple, console_output=console_output, file_output=file_output)
        self._打印到引擎(msg=msg, gateway_name=gateway_name)

    def info(self, msg: str, gateway_name: str = "", modname: str = "", simple: bool = False,
             console_output: bool = None, file_output: bool = None):
        self.setmodname(modname)
        self._print_log(msg, "INFO", simple=simple, console_output=console_output, file_output=file_output)
        self._打印到引擎(msg=msg, gateway_name=gateway_name)

    def echo(self, msg: str, gateway_name: str = "", modname: str = "", simple: bool = True,
             console_output: bool = None, file_output: bool = None):
        self.info(msg, gateway_name=gateway_name, modname=modname, simple=simple, console_output=console_output,
                  file_output=file_output)

    def print(self, msg: str):
        print(msg)

    def warning(self, msg: str, gateway_name: str = "", modname: str = "", simple: bool = False,
                console_output: bool = None, file_output: bool = None):
        self.setmodname(modname)
        self._print_log(msg, "WARNING", simple=simple, console_output=console_output, file_output=file_output)
        self._打印到引擎(msg=msg, gateway_name=gateway_name)

    def error(self, msg: str, gateway_name: str = "", modname: str = "", simple: bool = False,
              console_output: bool = None, file_output: bool = None):
        self.setmodname(modname)
        self._print_log(msg, "ERROR", simple=simple, console_output=console_output, file_output=file_output)
        self._打印到引擎(msg=msg, gateway_name=gateway_name)

    def write_log(self, msg: str, simple: bool = False, console_output: bool = None, file_output: bool = None):
        self._print_log(msg, "INFO", simple=simple, console_output=console_output, file_output=file_output)
        if self.fuwen主引擎 is not None:
            self.fuwen主引擎.write_log(msg=msg)

    def bind(self, **kwargs):
        return _BoundLogger(self, **kwargs)

    def _get_log_dir(self) -> str:
        return os.path.abspath(self.log_dir)

    def _get_log_file_path(self, date: str = None) -> str:
        import datetime
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        prefix = self._file_prefix if self._file_prefix else self._name if self._name else self._modname if self._modname else "fw"
        file_name = f"{prefix}_{date}.log"
        return os.path.abspath(os.path.join(self.log_dir, file_name))

    # 新增：获取独立ERROR日志路径
    def _get_error_log_file_path(self, date: str = None) -> str:
        import datetime
        if date is None:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        prefix = self._file_prefix if self._file_prefix else self._name if self._name else self._modname if self._modname else "fw"
        file_name = f"{prefix}_error_{date}.log"
        return os.path.abspath(os.path.join(self.log_dir, file_name))


class _BoundLogger:
    """保持原有绑定逻辑不变，完全兼容老代码"""

    def __init__(self, fw_log_instance, **kwargs):
        self._fw_log = fw_log_instance
        self._extra = kwargs

    def log(self, level, msg):
        level = level.upper() if isinstance(level, str) else level
        level_map = {
            10: "DEBUG",
            20: "INFO",
            30: "WARNING",
            40: "ERROR",
            "DEBUG": "DEBUG",
            "INFO": "INFO",
            "WARNING": "WARNING",
            "ERROR": "ERROR"
        }
        level_str = level_map.get(level, "INFO")
        gateway_name = self._extra.get("gateway_name", "")

        if level_str == "DEBUG":
            self._fw_log.debug(msg, gateway_name=gateway_name)
        elif level_str == "INFO":
            self._fw_log.info(msg, gateway_name=gateway_name)
        elif level_str == "WARNING":
            self._fw_log.warning(msg, gateway_name=gateway_name)
        elif level_str == "ERROR":
            self._fw_log.error(msg, gateway_name=gateway_name)


# 读取配置部分完全保留原样
import json


def get_log_level() -> str:
    filepath = os.path.join(os.path.expanduser("~"), ".fwquant", "fw_setting.json")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        result = data.get("log.level", "INFO")
    return result


def get_fw_log_config() -> dict:
    filepath = os.path.join(os.path.expanduser("~"), ".fwquant", "fw_setting.json")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "active": data.get("log.active", True),
                "level": data.get("log.level", "INFO"),
                "file": data.get("log.file", True),
                "console": data.get("log.console", True)
            }
    except Exception:
        return {
            "active": True,
            "level": "INFO",
            "file": True,
            "console": True
        }


_log_config = get_fw_log_config()

if _loguru_available:
    loguru_logger.remove()

# 全局日志记录器 (实例，直接可用）
logger: fw_log | None = None
if _log_config.get("active", True):
    logger = fw_log(
        name="fwlog",
        level=_log_config.get("level", "INFO"),
        save_to_file=_log_config.get("file", True),
        console_output=_log_config.get("console", True)
    )
else:
    logger = None

# 如果 loguru 不可用，使用标准 logging 模块作为后备
if not _loguru_available and logger is None:
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger = logging.getLogger("fw_core")
    logger.warning("使用标准 logging 模块作为后备，loguru 不可用")

if __name__ == "__main__":
    # 测试代码保持原样
    print("\n" + "=" * 50)
    print("测试：正常日志 + ERROR独立日志分离")
    print("=" * 50)
    logger.modname = "fwlog"
    logger.level = logging.DEBUG

    logger.debug("调试信息")
    logger.info(f"打印配置："
                f"\n主日志文件：{logger.log_path}"
                f"\n错误独立日志文件：{logger.error_log_path}")

    logger.info("普通业务信息")
    logger.warning("警告信息")
    logger.error("数据库连接失败【这条会同时写入主日志+error独立文件】")
