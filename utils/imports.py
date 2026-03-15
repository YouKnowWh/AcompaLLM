"""
imports.py — 安全的模块导入工具

提供 safe_import() 函数，用于优雅地处理可选依赖的导入失败。
避免使用宽泛的异常捕获，提供详细的日志记录和类型提示。

设计原则：
1. 只捕获 ImportError，不捕获其他异常
2. 记录详细的警告信息便于调试
3. 提供清晰的类型标注
4. 支持自定义降级值
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional, TypeVar, cast

# 设置模块级别日志记录器
logger = logging.getLogger(__name__)

T = TypeVar("T")


def safe_import(
    module_path: str,
    name: str,
    fallback: Optional[T] = None,
    *,
    min_version: Optional[str] = None,
    reason: str = "",
) -> Optional[T]:
    """
    安全导入模块中的特定对象，失败时返回降级值并记录警告。

    Args:
        module_path: 模块路径，例如 'tools_adapter'
        name: 要从模块中导入的对象名，例如 'invoke_tool'
        fallback: 导入失败时返回的值（默认 None）
        min_version: 可选的最低版本要求（格式：'2.0.0'）
        reason: 可选的原因说明，用于日志记录

    Returns:
        导入的对象或降级值

    Raises:
        ImportError: 当指定了 min_version 但版本不满足时
        KeyboardInterrupt, SystemExit: 不会被捕获
    """
    full_name = f"{module_path}.{name}"
    try:
        # 动态导入模块
        module = __import__(module_path, fromlist=[name])
        
        # 检查版本要求（如果提供）
        if min_version and hasattr(module, "__version__"):
            try:
                from packaging import version
                if version.parse(module.__version__) < version.parse(min_version):
                    raise ImportError(
                        f"{module_path} 版本 {module.__version__} 过低，需要 >= {min_version}"
                    )
            except ImportError:
                # packaging 不可用，跳过版本检查
                logger.debug(f"packaging 模块未安装，跳过 {module_path} 的版本检查")
                pass
        
        # 获取目标对象
        obj = getattr(module, name)
        
        if fallback is not None and obj is None:
            # 模块存在但对象为 None，视为导入失败
            raise ImportError(f"{full_name} 存在但为 None")
        
        return cast(Optional[T], obj)
        
    except ImportError as e:
        # 只捕获 ImportError，不捕获其他异常
        warning_msg = f"导入 {full_name} 失败"
        if reason:
            warning_msg += f"（{reason}）"
        warning_msg += f": {e}"
        
        logger.warning(warning_msg)
        
        # 如果是版本不满足，重新抛出
        if "版本" in str(e) and "过低" in str(e):
            raise
        
        return fallback
    except AttributeError as e:
        # 对象在模块中不存在
        warning_msg = f"{full_name} 在模块中不存在"
        if reason:
            warning_msg += f"（{reason}）"
        warning_msg += f": {e}"
        
        logger.warning(warning_msg)
        return fallback
    except Exception as e:
        # 其他异常（如语法错误）不应该被静默捕获
        # 记录错误但重新抛出，因为这不是预期的导入失败
        logger.error(f"导入 {full_name} 时发生意外错误: {e}")
        raise


def setup_logging(level: int = logging.WARNING) -> None:
    """
    设置项目级别的日志记录配置。

    Args:
        level: 日志级别，默认 WARNING
    """
    # 避免重复配置
    if logger.handlers:
        return
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    
    # 创建格式化器
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)
    
    # 添加到根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    
    # 也添加到当前模块的日志记录器
    logger.addHandler(console_handler)
    logger.setLevel(level)


# 自动设置默认日志记录（但不在导入时自动运行，由调用方决定）
__all__ = ["safe_import", "setup_logging"]
