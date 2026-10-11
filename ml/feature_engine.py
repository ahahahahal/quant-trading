"""
特征工程引擎 - 将Alpha158因子转换为ML可用特征
功能:
- Alpha158因子 → 数值特征矩阵
- 标签生成（回归:未来N天收益率 / 分类:涨跌）
- 缺失值处理 + Z-Score标准化
- 按时间序列划分训练集/测试集（不能随机打乱）
"""
import logging
import numpy as np
import pandas as pd
from typing import Tuple, List, Optional

log = logging.getLogger("quant.ml.feature_engine")

# 不参与特征工程的列（原始OHLCV + 元数据）
SKIP_COLUMNS = {
    "date", "open", "high", "low", "close", "volume", "amount",
    "symbol", "pct_change", "change", "turnover", "amplitude",
}


class FeatureEngine:
    """
    特征工程引擎
    将Alpha158计算后的DataFrame转换为ML模型可用的特征矩阵和标签向量
    """

    def __init__(self,
                 target_days: int = 5,
                 task: str = "classification",
                 fillna_method: str = "ffill_zero",
                 standardize: bool = True,
                 clip_sigma: float = 3.0):
        """
        Args:
            target_days: 预测未来N天的收益
            task: 'classification'（涨跌分类）或 'regression'（收益率回归）
            fillna_method: 缺失值处理策略 ('ffill_zero', 'zero', 'drop')
            standardize: 是否做Z-Score标准化
            clip_sigma: 异常值裁剪倍数（标准差倍数）
        """
        self.target_days = target_days
        self.task = task
        self.fillna_method = fillna_method
        self.standardize = standardize
        self.clip_sigma = clip_sigma

        # 训练集统计量（用于对测试集做同样的标准化）
        self._train_mean: Optional[pd.Series] = None
        self._train_std: Optional[pd.Series] = None
        self._feature_cols: List[str] = []

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fit_transform(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """
        拟合并转换训练数据（fit + transform）
        
        Args:
            df: 包含Alpha158因子的DataFrame
        
        Returns:
            (X, y, feature_names) — 特征矩阵、标签向量、特征名列表
        """
        df = df.copy()
        df = self._generate_labels(df)
        df = self._select_features(df)
        df = self._handle_missing(df)
        df = self._clip_outliers(df)

        if self.standardize:
            df, self._train_mean, self._train_std = self._zscore_fit(df)

        self._feature_cols = [c for c in df.columns if c.startswith("feat_")]
        X = df[self._feature_cols].values.astype(np.float32)
        y = df["_label"].values.astype(np.float32)

        # 去掉标签为空的行（最后 target_days 行）
        valid_mask = ~np.isnan(y)
        X, y = X[valid_mask], y[valid_mask]

        log.info("特征工程完成: %d 样本, %d 特征, 任务=%s", X.shape[0], X.shape[1], self.task)
        return X, y, self._feature_cols

    def transform(self, df: pd.DataFrame) -> Tuple[np.ndarray, Optional[np.ndarray], List[str]]:
        """
        用训练集的统计量转换新数据（不做fit）
        
        Returns:
            (X, y_or_None, feature_names)
        """
        df = df.copy()
        df = self._generate_labels(df)
        df = self._select_features(df)
        df = self._handle_missing(df)
        df = self._clip_outliers(df)

        if self.standardize and self._train_mean is not None:
            df = self._zscore_transform(df, self._train_mean, self._train_std)

        X = df[self._feature_cols].values.astype(np.float32)

        # 标签可能不存在（纯预测场景）
        if "_label" in df.columns:
            y = df["_label"].values.astype(np.float32)
            valid_mask = ~np.isnan(y)
            # 对于预测，我们也返回全部行，包括无效标签的
            return X, y, self._feature_cols
        return X, None, self._feature_cols

    def split_by_time(self, X: np.ndarray, y: np.ndarray,
                      train_ratio: float = 0.8) -> Tuple:
        """
        按时间序列划分训练集和测试集（不能随机打乱）
        
        Args:
            X: 特征矩阵
            y: 标签
            train_ratio: 训练集占比
        
        Returns:
            (X_train, X_test, y_train, y_test)
        """
        n = len(X)
        split_idx = int(n * train_ratio)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        log.info("时间序列划分: 训练=%d, 测试=%d", len(y_train), len(y_test))
        return X_train, X_test, y_train, y_test

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _generate_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成标签：未来N天收益率（回归）或涨跌分类（分类）"""
        if "close" not in df.columns:
            raise ValueError("DataFrame中缺少 'close' 列，无法生成标签")

        future_ret = df["close"].shift(-self.target_days) / df["close"] - 1
        if self.task == "classification":
            # 涨 = 1, 跌/平 = 0
            df["_label"] = (future_ret > 0).astype(float)
            # 最后N行无法计算标签，设为NaN
            df.loc[df.index[-self.target_days:], "_label"] = np.nan
        else:
            df["_label"] = future_ret
            df.loc[df.index[-self.target_days:], "_label"] = np.nan

        return df

    def _select_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """从Alpha158因子中筛选数值列作为特征"""
        feature_cols = []
        for col in df.columns:
            if col in SKIP_COLUMNS or col == "_label":
                continue
            # 只选择数值列
            if pd.api.types.is_numeric_dtype(df[col]):
                feature_cols.append(col)

        # 重命名特征列，避免和原始列混淆
        renamed = {}
        for col in feature_cols:
            renamed[col] = f"feat_{col}"
        df = df.rename(columns=renamed)

        self._feature_cols = [f"feat_{c}" for c in feature_cols]
        return df

    def _handle_missing(self, df: pd.DataFrame) -> pd.DataFrame:
        """缺失值处理"""
        feat_cols = [c for c in df.columns if c.startswith("feat_")]

        if self.fillna_method == "ffill_zero":
            # 先前向填充，再用0填充剩余
            df[feat_cols] = df[feat_cols].ffill().fillna(0)
        elif self.fillna_method == "zero":
            df[feat_cols] = df[feat_cols].fillna(0)
        elif self.fillna_method == "drop":
            df = df.dropna(subset=feat_cols)

        # 替换无穷值
        df[feat_cols] = df[feat_cols].replace([np.inf, -np.inf], 0)
        return df

    def _clip_outliers(self, df: pd.DataFrame) -> pd.DataFrame:
        """异常值裁剪（3σ）"""
        feat_cols = [c for c in df.columns if c.startswith("feat_")]
        for col in feat_cols:
            mean = df[col].mean()
            std = df[col].std()
            if std > 1e-12:
                lower = mean - self.clip_sigma * std
                upper = mean + self.clip_sigma * std
                df[col] = df[col].clip(lower, upper)
        return df

    @staticmethod
    def _zscore_fit(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
        """
        Z-Score标准化（拟合训练集统计量）
        返回: (标准化后的df, 均值, 标准差)
        """
        feat_cols = [c for c in df.columns if c.startswith("feat_")]
        means = df[feat_cols].mean()
        stds = df[feat_cols].std()
        # 避免除以0
        stds = stds.replace(0, 1)
        df[feat_cols] = (df[feat_cols] - means) / stds
        return df, means, stds

    @staticmethod
    def _zscore_transform(df: pd.DataFrame, means: pd.Series, stds: pd.Series) -> pd.DataFrame:
        """用训练集统计量标准化测试集"""
        feat_cols = [c for c in df.columns if c.startswith("feat_")]
        # 对齐列名（可能测试集缺少某些列）
        common_cols = [c for c in feat_cols if c in means.index]
        df[common_cols] = (df[common_cols] - means[common_cols]) / stds[common_cols]
        return df
