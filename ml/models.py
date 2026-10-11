"""
ML选股模型 - XGBoost / LightGBM
功能:
- 模型训练/预测/评估
- 特征重要性分析
- 模型保存/加载（joblib）
"""
import logging
import numpy as np
import joblib
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("quant.ml.models")

# 模型保存目录
MODEL_DIR = Path(__file__).parent.parent / "models"


class MLModel(ABC):
    """ML模型基类"""

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
        self.model = None
        self.feature_names: List[str] = []
        self.is_fitted = False

    @abstractmethod
    def _create_model(self):
        """创建底层模型实例"""
        pass

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: List[str] = None,
            X_val: np.ndarray = None, y_val: np.ndarray = None):
        """训练模型"""
        pass

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """预测（返回类别或概率）"""
        pass

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """预测概率（分类任务返回正类概率）"""
        pass

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """
        评估模型
        分类: accuracy, precision, recall, f1, auc
        回归: mse, mae, r2, ic (信息系数)
        """
        from sklearn.metrics import (
            accuracy_score, precision_score, recall_score, f1_score, roc_auc_score,
            mean_squared_error, mean_absolute_error, r2_score
        )

        y_pred = self.predict(X)

        if self.params.get("task", "classification") == "classification":
            y_proba = self.predict_proba(X)
            metrics = {
                "accuracy": float(accuracy_score(y, y_pred)),
                "precision": float(precision_score(y, y_pred, zero_division=0)),
                "recall": float(recall_score(y, y_pred, zero_division=0)),
                "f1": float(f1_score(y, y_pred, zero_division=0)),
            }
            try:
                metrics["auc"] = float(roc_auc_score(y, y_proba))
            except ValueError:
                metrics["auc"] = 0.0
        else:
            metrics = {
                "mse": float(mean_squared_error(y, y_pred)),
                "mae": float(mean_absolute_error(y, y_pred)),
                "r2": float(r2_score(y, y_pred)),
            }
            # IC（信息系数 = 预测值与真实值的秩相关系数）
            if len(y_pred) > 10:
                from scipy.stats import spearmanr
                ic, _ = spearmanr(y_pred, y)
                metrics["ic"] = float(ic) if not np.isnan(ic) else 0.0
            else:
                metrics["ic"] = 0.0

        return metrics

    def get_feature_importance(self, top_n: int = 20) -> List[Dict]:
        """获取特征重要性排名"""
        if self.model is None or not self.feature_names:
            return []

        importance = self._get_raw_importance()
        if importance is None:
            return []

        # 排序
        pairs = list(zip(self.feature_names, importance))
        pairs.sort(key=lambda x: x[1], reverse=True)

        result = []
        for name, score in pairs[:top_n]:
            # 去掉 feat_ 前缀以便显示
            display_name = name[5:] if name.startswith("feat_") else name
            result.append({
                "feature": display_name,
                "importance": round(float(score), 6),
            })
        return result

    @abstractmethod
    def _get_raw_importance(self) -> Optional[np.ndarray]:
        """获取原始特征重要性数组"""
        pass

    def save(self, path: str = None):
        """保存模型到文件"""
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if path is None:
            path = str(MODEL_DIR / f"{self.name}_model.pkl")
        save_data = {
            "model": self.model,
            "feature_names": self.feature_names,
            "params": self.params,
            "name": self.name,
            "is_fitted": self.is_fitted,
        }
        joblib.dump(save_data, path)
        log.info("模型已保存: %s", path)
        return path

    def load(self, path: str = None) -> bool:
        """从文件加载模型"""
        if path is None:
            path = str(MODEL_DIR / f"{self.name}_model.pkl")
        p = Path(path)
        if not p.exists():
            log.warning("模型文件不存在: %s", path)
            return False
        try:
            save_data = joblib.load(path)
            self.model = save_data["model"]
            self.feature_names = save_data["feature_names"]
            self.params = save_data.get("params", {})
            self.is_fitted = save_data.get("is_fitted", True)
            log.info("模型已加载: %s", path)
            return True
        except Exception as e:
            log.error("加载模型失败: %s", e)
            return False


class XGBoostModel(MLModel):
    """
    XGBoost选股模型
    支持分类（涨跌预测）和回归（收益率预测）
    """

    def __init__(self, task: str = "classification", params: dict = None):
        default_params = {
            "task": task,
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 5,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "random_state": 42,
        }
        if params:
            default_params.update(params)
        super().__init__(name="xgboost", params=default_params)

    def _create_model(self):
        if self.params["task"] == "classification":
            from xgboost import XGBClassifier
            return XGBClassifier(
                n_estimators=self.params["n_estimators"],
                max_depth=self.params["max_depth"],
                learning_rate=self.params["learning_rate"],
                subsample=self.params["subsample"],
                colsample_bytree=self.params["colsample_bytree"],
                min_child_weight=self.params["min_child_weight"],
                reg_alpha=self.params["reg_alpha"],
                reg_lambda=self.params["reg_lambda"],
                random_state=self.params["random_state"],
                use_label_encoder=False,
                eval_metric="logloss",
                verbosity=0,
            )
        else:
            from xgboost import XGBRegressor
            return XGBRegressor(
                n_estimators=self.params["n_estimators"],
                max_depth=self.params["max_depth"],
                learning_rate=self.params["learning_rate"],
                subsample=self.params["subsample"],
                colsample_bytree=self.params["colsample_bytree"],
                min_child_weight=self.params["min_child_weight"],
                reg_alpha=self.params["reg_alpha"],
                reg_lambda=self.params["reg_lambda"],
                random_state=self.params["random_state"],
                verbosity=0,
            )

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: List[str] = None,
            X_val: np.ndarray = None, y_val: np.ndarray = None):
        """训练XGBoost模型"""
        self.model = self._create_model()
        self.feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))

        self.model.fit(
            X, y,
            eval_set=eval_set,
            verbose=False,
        )
        self.is_fitted = True
        log.info("XGBoost训练完成: %d 样本, %d 特征", X.shape[0], X.shape[1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")
        if self.params["task"] == "classification":
            proba = self.model.predict_proba(X)
            return proba[:, 1]  # 返回正类概率
        else:
            return self.model.predict(X)

    def _get_raw_importance(self) -> Optional[np.ndarray]:
        if self.model is None:
            return None
        return self.model.feature_importances_


class LightGBMModel(MLModel):
    """
    LightGBM选股模型
    支持分类（涨跌预测）和回归（收益率预测）
    """

    def __init__(self, task: str = "classification", params: dict = None):
        default_params = {
            "task": task,
            "n_estimators": 300,
            "max_depth": 7,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_samples": 20,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "num_leaves": 63,
            "random_state": 42,
        }
        if params:
            default_params.update(params)
        super().__init__(name="lightgbm", params=default_params)

    def _create_model(self):
        if self.params["task"] == "classification":
            from lightgbm import LGBMClassifier
            return LGBMClassifier(
                n_estimators=self.params["n_estimators"],
                max_depth=self.params["max_depth"],
                learning_rate=self.params["learning_rate"],
                subsample=self.params["subsample"],
                colsample_bytree=self.params["colsample_bytree"],
                min_child_samples=self.params["min_child_samples"],
                reg_alpha=self.params["reg_alpha"],
                reg_lambda=self.params["reg_lambda"],
                num_leaves=self.params["num_leaves"],
                random_state=self.params["random_state"],
                verbose=-1,
            )
        else:
            from lightgbm import LGBMRegressor
            return LGBMRegressor(
                n_estimators=self.params["n_estimators"],
                max_depth=self.params["max_depth"],
                learning_rate=self.params["learning_rate"],
                subsample=self.params["subsample"],
                colsample_bytree=self.params["colsample_bytree"],
                min_child_samples=self.params["min_child_samples"],
                reg_alpha=self.params["reg_alpha"],
                reg_lambda=self.params["reg_lambda"],
                num_leaves=self.params["num_leaves"],
                random_state=self.params["random_state"],
                verbose=-1,
            )

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: List[str] = None,
            X_val: np.ndarray = None, y_val: np.ndarray = None):
        """训练LightGBM模型"""
        self.model = self._create_model()
        self.feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))

        self.model.fit(
            X, y,
            eval_set=eval_set if X_val is not None else None,
            callbacks=[
                __import__("lightgbm").early_stopping(50, verbose=False),
                __import__("lightgbm").log_evaluation(period=0),
            ] if X_val is not None else None,
        )
        self.is_fitted = True
        log.info("LightGBM训练完成: %d 样本, %d 特征", X.shape[0], X.shape[1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")
        if self.params["task"] == "classification":
            proba = self.model.predict_proba(X)
            return proba[:, 1]  # 返回正类概率
        else:
            return self.model.predict(X)

    def _get_raw_importance(self) -> Optional[np.ndarray]:
        if self.model is None:
            return None
        return self.model.feature_importances_


# ======================== 模型工厂 ========================

MODEL_REGISTRY = {
    "xgboost": XGBoostModel,
    "lightgbm": LightGBMModel,
}


def create_model(model_type: str, task: str = "classification", params: dict = None) -> MLModel:
    """
    模型工厂函数
    
    Args:
        model_type: 'xgboost' 或 'lightgbm'
        task: 'classification' 或 'regression'
        params: 额外参数
    
    Returns:
        MLModel实例
    """
    if model_type not in MODEL_REGISTRY:
        raise ValueError(f"未知模型类型: {model_type}, 可选: {list(MODEL_REGISTRY.keys())}")
    return MODEL_REGISTRY[model_type](task=task, params=params)


def get_saved_models() -> List[Dict]:
    """获取已保存的模型列表"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    models = []
    for p in sorted(MODEL_DIR.glob("*_model.pkl"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            info = joblib.load(str(p))
            models.append({
                "name": info.get("name", p.stem),
                "path": str(p),
                "features": len(info.get("feature_names", [])),
                "params": info.get("params", {}),
                "is_fitted": info.get("is_fitted", False),
                "modified": p.stat().st_mtime,
            })
        except Exception:
            continue
    return models
