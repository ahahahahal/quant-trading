"""
ML交易策略 - 基于机器学习模型生成交易信号
- MLStrategy: 继承BaseStrategy，使用ML模型预测结果生成买卖信号
- 支持 XGBoost 和 LightGBM 两种模型
- 注册到策略工厂（STRATEGY_REGISTRY）
"""
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.engine import BaseStrategy, Signal, STRATEGY_REGISTRY
from ml.feature_engine import FeatureEngine
from ml.models import XGBoostModel, LightGBMModel, create_model, MODEL_DIR

log = logging.getLogger("quant.ml.strategy")


class MLStrategy(BaseStrategy):
    """
    基于ML模型的交易策略
    
    工作原理:
    1. 使用FeatureEngine将原始数据转换为ML特征
    2. 用训练好的模型对每天的数据做预测
    3. 预测概率 > 买入阈值 → BUY
    4. 预测概率 < 卖出阈值 → SELL
    5. 其余 → HOLD
    """

    def __init__(self, model_type: str = "xgboost",
                 buy_threshold: float = 0.6,
                 sell_threshold: float = 0.4,
                 model_path: str = None,
                 task: str = "classification",
                 target_days: int = 5):
        """
        Args:
            model_type: 'xgboost' 或 'lightgbm'
            buy_threshold: 买入概率阈值（>此值买入）
            sell_threshold: 卖出概率阈值（<此值卖出，仅分类任务）
            model_path: 预训练模型路径，为None时使用默认路径
            task: 'classification' 或 'regression'
            target_days: 预测天数
        """
        strategy_name = f"ml_{model_type}"
        super().__init__(
            name=strategy_name,
            params={
                "model_type": model_type,
                "buy_threshold": buy_threshold,
                "sell_threshold": sell_threshold,
                "task": task,
                "target_days": target_days,
            }
        )
        self.model_type = model_type
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.task = task
        self.target_days = target_days

        # 创建特征引擎
        self.feature_engine = FeatureEngine(
            target_days=target_days,
            task=task,
        )

        # 创建/加载模型
        self.model: Optional[XGBoostModel, LightGBMModel] = None
        if model_path:
            self.model = create_model(model_type, task)
            self.model.load(model_path)
        else:
            # 尝试加载默认路径的模型
            default_path = str(MODEL_DIR / f"{model_type}_model.pkl")
            self.model = create_model(model_type, task)
            if not self.model.load(default_path):
                log.warning("未找到预训练模型 %s, 策略将在无模型状态下运行（默认HOLD）", model_type)
                self.model = None

        # 预测缓存（避免重复计算）
        self._prediction_cache = {}

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        """
        在第idx行生成交易信号
        
        如果模型未加载，返回HOLD
        如果缓存中有预测结果，直接使用
        """
        if self.model is None or not self.model.is_fitted:
            return Signal.HOLD

        # 使用缓存（一次run中只计算一次）
        cache_key = id(df)
        if cache_key not in self._prediction_cache:
            self._build_prediction_cache(df, cache_key)

        predictions = self._prediction_cache.get(cache_key, {})
        proba = predictions.get(idx)

        if proba is None:
            return Signal.HOLD

        if self.task == "classification":
            if proba >= self.buy_threshold:
                return Signal.BUY
            elif proba <= self.sell_threshold:
                return Signal.SELL
            return Signal.HOLD
        else:
            # 回归任务：预测收益率
            if proba > 0.02:  # 预期收益 > 2%
                return Signal.BUY
            elif proba < -0.02:  # 预期亏损 > 2%
                return Signal.SELL
            return Signal.HOLD

    def _build_prediction_cache(self, df: pd.DataFrame, cache_key: int):
        """一次性对整个DataFrame做预测，缓存结果"""
        try:
            X, _, _ = self.feature_engine.transform(df)
            if len(X) == 0:
                self._prediction_cache[cache_key] = {}
                return

            proba = self.model.predict_proba(X)

            # 对齐索引（特征引擎可能会丢弃最后N行）
            predictions = {}
            # X的长度可能小于df（因为丢弃了NaN行）
            start_idx = len(df) - len(X)
            for i in range(len(X)):
                df_idx = start_idx + i
                if 0 <= df_idx < len(df):
                    predictions[df_idx] = float(proba[i])

            self._prediction_cache[cache_key] = predictions
            log.debug("预测缓存已构建: %d 个预测值", len(predictions))
        except Exception as e:
            log.error("构建预测缓存失败: %s", e)
            self._prediction_cache[cache_key] = {}

    def run(self, df: pd.DataFrame):
        """重写run方法，在开始前清空缓存"""
        self._prediction_cache.clear()
        return super().run(df)

    def _calc_score(self, df: pd.DataFrame, idx: int, sig: Signal) -> float:
        """根据模型预测概率计算信号强度"""
        cache_key = id(df)
        predictions = self._prediction_cache.get(cache_key, {})
        proba = predictions.get(idx, 0.5)

        if sig == Signal.BUY:
            # 买入信号：概率越高分数越高
            return min(float(proba), 1.0)
        elif sig == Signal.SELL:
            # 卖出信号：概率越低分数越高
            return min(float(1 - proba), 1.0)
        return 0.5

    def _explain(self, df: pd.DataFrame, idx: int, sig: Signal) -> str:
        """生成信号说明"""
        cache_key = id(df)
        predictions = self._prediction_cache.get(cache_key, {})
        proba = predictions.get(idx, 0.5)

        if sig == Signal.BUY:
            if self.task == "classification":
                return f"ML({self.model_type})看涨概率={proba:.3f}"
            else:
                return f"ML({self.model_type})预期收益率={proba:.4f}"
        elif sig == Signal.SELL:
            if self.task == "classification":
                return f"ML({self.model_type})看跌概率={1-proba:.3f}"
            else:
                return f"ML({self.model_type})预期亏损={proba:.4f}"
        return ""


def train_and_register(model_type: str = "xgboost",
                       symbol: str = "000001",
                       start: str = "20230101",
                       end: str = "20260601",
                       target_days: int = 5,
                       task: str = "classification") -> dict:
    """
    训练ML模型并注册策略
    
    Args:
        model_type: 'xgboost' 或 'lightgbm'
        symbol: 股票代码
        start: 开始日期
        end: 结束日期
        target_days: 预测天数
        task: 'classification' 或 'regression'
    
    Returns:
        训练结果（含评估指标）
    """
    from data.provider import get_data

    log.info("开始训练ML模型: %s, %s, %s~%s, target=%d", model_type, symbol, start, end, target_days)

    # 获取数据
    df = get_data(symbol, start, end)
    if df.empty:
        raise ValueError(f"获取 {symbol} 数据失败")

    # 特征工程
    engine = FeatureEngine(target_days=target_days, task=task)
    X, y, feature_names = engine.fit_transform(df)

    if len(X) < 100:
        raise ValueError(f"样本数不足: {len(X)}, 需要至少100条")

    # 按时间序列划分
    X_train, X_test, y_train, y_test = engine.split_by_time(X, y, train_ratio=0.8)

    # 创建并训练模型
    model = create_model(model_type, task=task)
    model.fit(X_train, y_train, feature_names=feature_names,
              X_val=X_test, y_val=y_test)

    # 评估
    train_metrics = model.evaluate(X_train, y_train)
    test_metrics = model.evaluate(X_test, y_test)

    # 保存模型
    model_path = model.save()

    # 特征重要性
    importance = model.get_feature_importance(top_n=20)

    log.info("训练完成: 训练集指标=%s, 测试集指标=%s", train_metrics, test_metrics)

    return {
        "model_type": model_type,
        "symbol": symbol,
        "task": task,
        "target_days": target_days,
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "total_features": len(feature_names),
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "feature_importance": importance,
        "model_path": model_path,
    }


# ======================== 注册ML策略到工厂 ========================

def _register_ml_strategies():
    """将ML策略注册到策略工厂"""
    # 延迟注册，避免循环导入
    if "ml_xgboost" not in STRATEGY_REGISTRY:
        # 用lambda包装，支持带参数创建
        STRATEGY_REGISTRY["ml_xgboost"] = lambda **kwargs: MLStrategy(
            model_type="xgboost", **kwargs
        )
    if "ml_lightgbm" not in STRATEGY_REGISTRY:
        STRATEGY_REGISTRY["ml_lightgbm"] = lambda **kwargs: MLStrategy(
            model_type="lightgbm", **kwargs
        )
    log.info("ML策略已注册: ml_xgboost, ml_lightgbm")


# 模块导入时自动注册
_register_ml_strategies()
