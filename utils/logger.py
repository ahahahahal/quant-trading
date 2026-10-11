"""
统一日志模块
替代所有 print 输出，支持文件和控制台双输出
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str = "quant", level: int = logging.INFO) -> logging.Logger:
    """获取命名日志器"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件
    fh = logging.FileHandler(LOG_DIR / "quant.log", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# 预置常用日志器
log = get_logger("quant")
