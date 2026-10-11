"""
A股量化交易系统 - 全局配置
参考: vnpy / backtrader / qlib 架构设计
支持: YAML配置文件 + 环境变量覆盖
"""
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from datetime import datetime
import json
import os

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"
CACHE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


@dataclass
class DataConfig:
    """数据源配置"""
    source: str = "akshare"           # akshare / tushare
    cache_enabled: bool = True        # 是否启用本地缓存
    cache_dir: str = str(CACHE_DIR)
    default_freq: str = "daily"       # daily / weekly / monthly
    adjust: str = "qfq"               # qfq=前复权, hfq=后复权, ""=不复权


@dataclass
class BacktestConfig:
    """回测配置"""
    initial_capital: float = 1_000_000.0   # 初始资金 100万
    commission_rate: float = 0.0003        # 佣金费率 万三
    stamp_tax_rate: float = 0.001          # 印花税 千一(卖出)
    slippage: float = 0.002                # 滑点 0.2%
    min_commission: float = 5.0            # 最低佣金 5元
    benchmark: str = "000300"              # 基准: 沪深300
    start_date: str = "20250101"
    end_date: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d"))


@dataclass
class RiskConfig:
    """风险管理配置"""
    max_position_pct: float = 0.25      # 单只股票最大仓位 25%
    max_total_position_pct: float = 0.8 # 最大总仓位 80%
    stop_loss_pct: float = 0.08         # 止损线 8%
    take_profit_pct: float = 0.20       # 止盈线 20%
    trailing_stop_pct: float = 0.05     # 追踪止损 5%
    max_drawdown_pct: float = 0.15      # 最大回撤 15% 触发全平
    max_daily_loss_pct: float = 0.03    # 单日最大亏损 3%
    max_holding_days: int = 60          # 最大持仓天数


@dataclass
class SignalConfig:
    """信号配置"""
    min_score: float = 0.6              # 最低信号分
    max_signals_per_day: int = 5        # 每日最多信号数
    signal_weights: dict = field(default_factory=lambda: {
        "trend": 0.3,
        "momentum": 0.25,
        "volume": 0.2,
        "mean_reversion": 0.15,
        "sentiment": 0.1,
    })


@dataclass
class TradingConfig:
    """交易时间配置"""
    market_open: str = "09:30"
    market_close: str = "15:00"
    lunch_start: str = "11:30"
    lunch_end: str = "13:00"


def _apply_env_overrides(cfg: "SystemConfig") -> "SystemConfig":
    """
    用环境变量覆盖配置, 格式: QUANT_<KEY>=<value>
    例如: QUANT_INITIAL_CAPITAL=500000  →  backtest.initial_capital = 500000
    """
    ENV_MAP = {
        "QUANT_INITIAL_CAPITAL": ("backtest", "initial_capital", float),
        "QUANT_COMMISSION_RATE": ("backtest", "commission_rate", float),
        "QUANT_STAMP_TAX_RATE": ("backtest", "stamp_tax_rate", float),
        "QUANT_SLIPPAGE": ("backtest", "slippage", float),
        "QUANT_MIN_COMMISSION": ("backtest", "min_commission", float),
        "QUANT_BENCHMARK": ("backtest", "benchmark", str),
        "QUANT_START_DATE": ("backtest", "start_date", str),
        "QUANT_END_DATE": ("backtest", "end_date", str),
        "QUANT_STOP_LOSS": ("risk", "stop_loss_pct", float),
        "QUANT_TAKE_PROFIT": ("risk", "take_profit_pct", float),
        "QUANT_MAX_POSITION": ("risk", "max_position_pct", float),
        "QUANT_MAX_DRAWDOWN": ("risk", "max_drawdown_pct", float),
        "QUANT_DATA_SOURCE": ("data", "source", str),
        "QUANT_CACHE_ENABLED": ("data", "cache_enabled", lambda x: x.lower() in ("true", "1", "yes")),
    }
    for env_key, (section, attr, caster) in ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                section_obj = getattr(cfg, section)
                setattr(section_obj, attr, caster(val))
            except (ValueError, AttributeError):
                pass
    return cfg


@dataclass
class SystemConfig:
    """系统总配置"""
    data: DataConfig = field(default_factory=DataConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)

    def save(self, path: Optional[str] = None):
        path = path or str(BASE_DIR / "config.json")
        import dataclasses
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "SystemConfig":
        path = path or str(BASE_DIR / "config.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = cls()
        for section_name, section_cls in [
            ("data", DataConfig), ("backtest", BacktestConfig),
            ("risk", RiskConfig), ("signal", SignalConfig),
            ("trading", TradingConfig),
        ]:
            if section_name in data:
                setattr(cfg, section_name, section_cls(**data[section_name]))
        return cfg

    @classmethod
    def load_yaml(cls, path: Optional[str] = None) -> "SystemConfig":
        """从YAML文件加载配置, 并应用环境变量覆盖"""
        path = path or str(BASE_DIR / "config.yaml")
        try:
            import yaml
        except ImportError:
            # yaml不可用时回退到默认配置
            cfg = cls()
            return _apply_env_overrides(cfg)

        p = Path(path)
        if not p.exists():
            cfg = cls()
            return _apply_env_overrides(cfg)

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        cfg = cls()
        section_map = {
            "data": DataConfig, "backtest": BacktestConfig,
            "risk": RiskConfig, "signal": SignalConfig,
            "trading": TradingConfig,
        }
        for section_name, section_cls in section_map.items():
            if section_name in data:
                section_data = data[section_name]
                # 过滤出dataclass接受的字段
                import dataclasses as _dc
                valid_fields = {f.name for f in _dc.fields(section_cls)}
                filtered = {k: v for k, v in section_data.items() if k in valid_fields}
                setattr(cfg, section_name, section_cls(**filtered))

        return _apply_env_overrides(cfg)


# 全局单例: 优先YAML, 回退默认
_yaml_path = BASE_DIR / "config.yaml"
if _yaml_path.exists():
    CONFIG = SystemConfig.load_yaml(str(_yaml_path))
else:
    CONFIG = SystemConfig()
    CONFIG = _apply_env_overrides(CONFIG)
