"""
Alpha158风格因子库 - 参考微软qlib
扩展技术因子到100+, 增加截面排名、数据清洗管线
"""
import pandas as pd
import numpy as np
from typing import Dict, List


class Alpha158:
    """
    qlib风格的Alpha158因子库
    5类因子: 价格/量能/技术/形态/统计
    """

    WINDOWS = [5, 10, 20, 30, 60]

    @classmethod
    def compute_all(cls, df: pd.DataFrame) -> pd.DataFrame:
        """计算全部因子"""
        df = df.copy()
        df = cls._price_factors(df)
        df = cls._volume_factors(df)
        df = cls._technical_factors(df)
        df = cls._pattern_factors(df)
        df = cls._statistical_factors(df)
        df = cls._cross_sectional_rank(df)
        df = cls._add_compat_aliases(df)
        df = df.copy()  # defragment
        return df

    @classmethod
    def _add_compat_aliases(cls, df: pd.DataFrame) -> pd.DataFrame:
        """添加小写别名, 兼容旧策略"""
        aliases = {
            "MA5": "ma5", "MA10": "ma10", "MA20": "ma20", "MA60": "ma60", "MA120": "ma120",
            "EMA5": "ema5", "EMA10": "ema10", "EMA20": "ema20", "EMA60": "ema60",
            "RSI6": "rsi6", "RSI12": "rsi12", "RSI14": "rsi14", "RSI24": "rsi24",
            "MACD_DIF": "macd_dif", "MACD_DEA": "macd_dea", "MACD_HIST": "macd_hist",
            "KDJ_K": "kdj_k", "KDJ_D": "kdj_d", "KDJ_J": "kdj_j",
            "BOLL_UPPER": "boll_upper", "BOLL_MID": "boll_mid", "BOLL_LOWER": "boll_lower",
            "BOLL_WIDTH": "boll_width",
            "ATR14": "atr14", "ATR20": "atr20",
            "VRATIO5": "vol_ratio", "VOL_TREND": "vol_trend",
            "MA_BULL": "ma_bull", "MA_BEAR": "ma_bear",
            "OBV": "obv", "OBV_MA20": "obv_ma",
        }
        for upper, lower in aliases.items():
            if upper in df.columns and lower not in df.columns:
                df[lower] = df[upper]
        # 兼容旧的 high_20d, low_20d
        for w in [20, 60, 120]:
            if f"HIGH_{w}D" in df.columns and f"high_{w}d" not in df.columns:
                df[f"high_{w}d"] = df[f"HIGH_{w}D"]
            if f"LOW_{w}D" in df.columns and f"low_{w}d" not in df.columns:
                df[f"low_{w}d"] = df[f"LOW_{w}D"]
        return df

    @classmethod
    def _price_factors(cls, df: pd.DataFrame) -> pd.DataFrame:
        """价格因子(批量向量化)"""
        c, h, l = df["close"], df["high"], df["low"]
        new_cols = {}

        for w in cls.WINDOWS:
            roll_max = h.rolling(w).max()
            roll_min = l.rolling(w).min()
            ma = c.rolling(w).mean()
            new_cols[f"RET{w}"] = c.pct_change(w)
            new_cols[f"MA{w}"] = ma
            new_cols[f"EMA{w}"] = c.ewm(span=w, adjust=False).mean()
            new_cols[f"MAX{w}"] = roll_max
            new_cols[f"MIN{w}"] = roll_min
            new_cols[f"QTLU{w}"] = (c - roll_min) / (roll_max - roll_min + 1e-12)
            new_cols[f"QTLD{w}"] = (roll_max - c) / (roll_max - roll_min + 1e-12)
            new_cols[f"MA{w}_BIAS"] = (c - ma) / (ma + 1e-12)
            new_cols[f"MA{w}_SLOPE"] = ma.pct_change(5)

        # 均线多头排列
        if all(f"MA{w}" in [k for k in {**new_cols}] for w in [5, 10, 20, 60]):
            new_cols["MA_BULL"] = ((new_cols["MA5"] > new_cols["MA10"]) &
                                   (new_cols["MA10"] > new_cols["MA20"]) &
                                   (new_cols["MA20"] > new_cols["MA60"])).astype(int)
            new_cols["MA_BEAR"] = ((new_cols["MA5"] < new_cols["MA10"]) &
                                   (new_cols["MA10"] < new_cols["MA20"]) &
                                   (new_cols["MA20"] < new_cols["MA60"])).astype(int)

        accel = c.pct_change().diff()
        new_cols["PRICE_ACCEL"] = accel
        new_cols["PRICE_JERK"] = accel.diff()

        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
        return df

    @classmethod
    def _volume_factors(cls, df: pd.DataFrame) -> pd.DataFrame:
        """量能因子(批量向量化)"""
        v = df["volume"]
        c = df["close"]
        new_cols = {}

        for w in cls.WINDOWS:
            vma = v.rolling(w).mean()
            new_cols[f"VMA{w}"] = vma
            new_cols[f"VSTD{w}"] = v.rolling(w).std()
            new_cols[f"VRATIO{w}"] = v / (vma + 1e-12)
            new_cols[f"VSUMP{w}"] = v.rolling(w).apply(
                lambda x: x[x > 0].sum() if len(x[x > 0]) > 0 else 0, raw=True)
            new_cols[f"VSUMN{w}"] = v.rolling(w).apply(
                lambda x: abs(x[x < 0].sum()) if len(x[x < 0]) > 0 else 0, raw=True)

        new_cols["PV_CORR20"] = c.rolling(20).corr(v)
        new_cols["PV_CORR60"] = c.rolling(60).corr(v)

        obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
        new_cols["OBV"] = obv
        new_cols["OBV_MA20"] = obv.rolling(20).mean()
        new_cols["OBV_SLOPE"] = obv.rolling(10).apply(
            lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) == 10 else 0, raw=True)

        vma5 = new_cols.get("VMA5", v.rolling(5).mean())
        vma20 = new_cols.get("VMA20", v.rolling(20).mean())
        new_cols["VOL_TREND"] = vma5 / (vma20 + 1e-12)

        mf = (c - df["low"]) / (df["high"] - df["low"] + 1e-12) * v
        new_cols["MONEY_FLOW"] = mf
        new_cols["MF_MA20"] = mf.rolling(20).mean()

        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
        return df

    @classmethod
    def _technical_factors(cls, df: pd.DataFrame) -> pd.DataFrame:
        """技术指标因子(批量向量化)"""
        c, h, l = df["close"], df["high"], df["low"]
        new_cols = {}

        # RSI (多周期)
        delta = c.diff()
        for w in [6, 12, 14, 24]:
            gain = delta.where(delta > 0, 0).rolling(w).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
            rs = gain / (loss + 1e-12)
            new_cols[f"RSI{w}"] = 100 - 100 / (1 + rs)

        # MACD
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = 2 * (dif - dea)
        new_cols["MACD_DIF"] = dif
        new_cols["MACD_DEA"] = dea
        new_cols["MACD_HIST"] = hist
        new_cols["MACD_CROSS"] = np.sign(hist) - np.sign(hist.shift(1))

        # KDJ
        low_9 = l.rolling(9).min()
        high_9 = h.rolling(9).max()
        rsv = (c - low_9) / (high_9 - low_9 + 1e-12) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        new_cols["KDJ_K"] = k
        new_cols["KDJ_D"] = d
        new_cols["KDJ_J"] = 3 * k - 2 * d

        # 布林带
        boll_mid = c.rolling(20).mean()
        boll_std = c.rolling(20).std()
        boll_upper = boll_mid + 2 * boll_std
        boll_lower = boll_mid - 2 * boll_std
        new_cols["BOLL_MID"] = boll_mid
        new_cols["BOLL_UPPER"] = boll_upper
        new_cols["BOLL_LOWER"] = boll_lower
        new_cols["BOLL_WIDTH"] = (boll_upper - boll_lower) / (boll_mid + 1e-12)
        new_cols["BOLL_POS"] = (c - boll_lower) / (boll_upper - boll_lower + 1e-12)

        # ATR (一次计算true range)
        tr = pd.concat([
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ], axis=1).max(axis=1)
        for w in [14, 20]:
            atr = tr.rolling(w).mean()
            new_cols[f"ATR{w}"] = atr
            new_cols[f"ATR{w}_RATIO"] = atr / (c + 1e-12)

        # CCI
        tp = (h + l + c) / 3
        for w in [14, 20]:
            ma_tp = tp.rolling(w).mean()
            md_tp = tp.rolling(w).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
            new_cols[f"CCI{w}"] = (tp - ma_tp) / (0.015 * md_tp + 1e-12)

        # Williams %R
        for w in [14, 20]:
            hh = h.rolling(w).max()
            ll = l.rolling(w).min()
            new_cols[f"WILLR{w}"] = (hh - c) / (hh - ll + 1e-12) * (-100)

        # ADX
        plus_dm = h.diff().clip(lower=0)
        minus_dm = (-l.diff()).clip(lower=0)
        tr14 = tr.rolling(14).sum()
        plus_di = plus_dm.rolling(14).sum() / (tr14 + 1e-12) * 100
        minus_di = minus_dm.rolling(14).sum() / (tr14 + 1e-12) * 100
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12) * 100
        new_cols["ADX"] = dx.rolling(14).mean()
        new_cols["PLUS_DI"] = plus_di
        new_cols["MINUS_DI"] = minus_di

        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
        return df

    @classmethod
    def _pattern_factors(cls, df: pd.DataFrame) -> pd.DataFrame:
        """形态因子(批量向量化)"""
        c, h, l, o = df["close"], df["high"], df["low"], df["open"]
        new_cols = {}

        body = (c - o).abs()
        new_cols["BODY"] = body
        new_cols["BODY_RATIO"] = body / (h - l + 1e-12)
        new_cols["UPPER_SHADOW"] = h - pd.concat([c, o], axis=1).max(axis=1)
        new_cols["LOWER_SHADOW"] = pd.concat([c, o], axis=1).min(axis=1) - l

        pct = df["pct_change"] if "pct_change" in df.columns else c.pct_change() * 100
        new_cols["IS_LIMIT_UP"] = (pct >= 9.9).astype(int)
        new_cols["IS_LIMIT_DOWN"] = (pct <= -9.9).astype(int)

        for w in [20, 60, 120]:
            high_w = h.rolling(w).max()
            low_w = l.rolling(w).min()
            new_cols[f"HIGH_{w}D"] = high_w
            new_cols[f"LOW_{w}D"] = low_w
            new_cols[f"NEAR_HIGH_{w}D"] = (c - high_w) / (high_w + 1e-12)
            new_cols[f"NEAR_LOW_{w}D"] = (c - low_w) / (low_w + 1e-12)

        ret = c.pct_change()
        up = (ret > 0).astype(int)
        down = (ret < 0).astype(int)
        new_cols["CONSEC_UP"] = up.groupby((up != up.shift()).cumsum()).cumsum()
        new_cols["CONSEC_DOWN"] = down.groupby((down != down.shift()).cumsum()).cumsum()

        new_cols["GAP_UP"] = (l > h.shift(1)).astype(int)
        new_cols["GAP_DOWN"] = (h < l.shift(1)).astype(int)

        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
        return df

    @classmethod
    def _statistical_factors(cls, df: pd.DataFrame) -> pd.DataFrame:
        """统计因子(批量向量化)"""
        ret = df["close"].pct_change()
        new_cols = {}

        for w in [5, 10, 20, 60]:
            new_cols[f"VOL{w}D"] = ret.rolling(w).std() * np.sqrt(252)
            new_cols[f"SKEW{w}"] = ret.rolling(w).skew()
            new_cols[f"KURT{w}"] = ret.rolling(w).kurt()
            neg_ret = ret.where(ret < 0, 0)
            new_cols[f"DOWN_VOL{w}"] = neg_ret.rolling(w).std() * np.sqrt(252)
            new_cols[f"MAX_DD{w}"] = (1 + ret).rolling(w).apply(
                lambda x: x.cumprod().min() / x.cumprod().max() - 1, raw=True)

        new_cols["INFO_RATIO20"] = ret.rolling(20).mean() / (ret.rolling(20).std() + 1e-12)

        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
        return df

    @classmethod
    def _cross_sectional_rank(cls, df: pd.DataFrame) -> pd.DataFrame:
        """截面排名因子 (当有多只股票时)"""
        if "symbol" not in df.columns or df["symbol"].nunique() <= 1:
            return df

        rank_cols = ["RET5", "RET20", "VOL20D", "RSI14", "MACD_HIST",
                     "BOLL_POS", "PV_CORR20", "VRATIO5"]
        existing = [c for c in rank_cols if c in df.columns]

        for col in existing:
            df[f"RANK_{col}"] = df.groupby("date")[col].rank(pct=True)

        return df


class DataPipeline:
    """
    数据清洗管线 - 参考qlib DataHandler
    缺失值处理 → 异常值裁剪 → 标准化
    """

    @staticmethod
    def clean(df: pd.DataFrame) -> pd.DataFrame:
        """完整清洗管线"""
        df = DataPipeline._handle_missing(df)
        df = DataPipeline._winsorize(df)
        df = DataPipeline._standardize(df)
        return df

    @staticmethod
    def _handle_missing(df: pd.DataFrame) -> pd.DataFrame:
        """缺失值处理"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        # 前向填充
        df[numeric_cols] = df[numeric_cols].ffill()
        # 剩余NaN用0填充
        df[numeric_cols] = df[numeric_cols].fillna(0)
        return df

    @staticmethod
    def _winsorize(df: pd.DataFrame, sigma: float = 3.0) -> pd.DataFrame:
        """异常值裁剪 (3σ)"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        skip = {"date", "open", "high", "low", "close", "volume", "amount",
                "symbol", "pct_change", "change", "turnover", "amplitude"}
        cols = [c for c in numeric_cols if c not in skip]

        for col in cols:
            mean = df[col].mean()
            std = df[col].std()
            lower = mean - sigma * std
            upper = mean + sigma * std
            df[col] = df[col].clip(lower, upper)

        return df

    @staticmethod
    def _standardize(df: pd.DataFrame) -> pd.DataFrame:
        """Z-Score标准化 (仅对因子列)"""
        # 不标准化原始OHLCV
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        skip = {"date", "open", "high", "low", "close", "volume", "amount",
                "symbol", "pct_change", "change", "turnover", "amplitude"}
        factor_cols = [c for c in numeric_cols if c not in skip]

        for col in factor_cols:
            mean = df[col].mean()
            std = df[col].std()
            if std > 1e-12:
                df[f"{col}_Z"] = (df[col] - mean) / std

        return df
