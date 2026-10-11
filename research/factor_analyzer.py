"""
因子分析引擎 - 提供IC分析、分层回测、因子相关性、因子衰减分析
参考 qlib 的因子研究框架
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from scipy import stats
import logging

log = logging.getLogger("quant.research")


class FactorAnalyzer:
    """
    因子分析引擎
    
    功能:
    - IC分析（信息系数）: 计算每个因子与未来收益的rank相关系数
    - 分层回测（Quintile Analysis）: 按因子值分组计算收益
    - 因子相关性矩阵: 计算所有因子之间的相关系数
    - 因子衰减分析: 计算不同持有期的IC
    """

    def __init__(self):
        """初始化因子分析器"""
        self.ic_results = {}
        self.quintile_results = {}

    def compute_ic(self, df: pd.DataFrame, factors: List[str],
                   forward_days: int = 1) -> Dict:
        """
        计算因子IC（信息系数）分析
        
        Args:
            df: 包含因子和价格数据的DataFrame
            factors: 要分析的因子列表
            forward_days: 未来收益的天数（默认1天）
            
        Returns:
            dict: {
                'ic_mean': 各因子IC均值,
                'ic_std': 各因子IC标准差,
                'icir': IC均值/IC标准差,
                'ic_series': IC时间序列（用于绘图）
            }
        """
        # 计算未来收益
        df = df.copy()
        df['forward_return'] = df['close'].pct_change(forward_days).shift(-forward_days)
        
        # 移除无效数据
        df = df.dropna(subset=['forward_return'])
        
        ic_results = {
            'factors': [],
            'ic_mean': [],
            'ic_std': [],
            'icir': [],
            'ic_series': {}
        }
        
        for factor in factors:
            if factor not in df.columns:
                log.warning("因子 %s 不存在，跳过", factor)
                continue
            
            # 计算每日IC（Rank相关系数）
            ic_series = []
            dates = []
            
            # 按日期计算截面IC
            for date in df['date'].unique():
                daily_data = df[df['date'] == date]
                if len(daily_data) < 5:  # 样本太少跳过
                    continue
                
                factor_values = daily_data[factor].dropna()
                returns = daily_data['forward_return'].dropna()
                
                # 取交集
                common_idx = factor_values.index.intersection(returns.index)
                if len(common_idx) < 5:
                    continue
                
                # 计算Rank相关系数（Spearman）
                ic, _ = stats.spearmanr(
                    factor_values.loc[common_idx],
                    returns.loc[common_idx]
                )
                
                if np.isfinite(ic):
                    ic_series.append(ic)
                    dates.append(str(date)[:10])
            
            if len(ic_series) < 2:
                continue
            
            ic_array = np.array(ic_series)
            ic_mean = np.mean(ic_array)
            ic_std = np.std(ic_array)
            icir = ic_mean / (ic_std + 1e-12)
            
            ic_results['factors'].append(factor)
            ic_results['ic_mean'].append(round(float(ic_mean), 4))
            ic_results['ic_std'].append(round(float(ic_std), 4))
            ic_results['icir'].append(round(float(icir), 4))
            ic_results['ic_series'][factor] = {
                'dates': dates,
                'values': [round(float(v), 4) for v in ic_series]
            }
        
        self.ic_results = ic_results
        return ic_results

    def compute_quintile(self, df: pd.DataFrame, factor: str,
                         forward_days: int = 1, n_groups: int = 5) -> Dict:
        """
        分层回测（Quintile Analysis）
        
        按因子值将股票分为N组，计算每组的平均收益和累计收益
        
        Args:
            df: 包含因子和价格数据的DataFrame
            factor: 要分析的因子
            forward_days: 未来收益的天数
            n_groups: 分组数（默认5组）
            
        Returns:
            dict: {
                'dates': 日期列表,
                'groups': {Q1: 累计收益, Q2: 累计收益, ...},
                'long_short': 多空收益（Q1 - Q5）,
                'group_returns': 各组平均收益
            }
        """
        df = df.copy()
        df['forward_return'] = df['close'].pct_change(forward_days).shift(-forward_days)
        df = df.dropna(subset=[factor, 'forward_return'])
        
        # 按因子值分组
        df['group'] = pd.qcut(df[factor], n_groups, labels=False, duplicates='drop') + 1
        # 重新映射：因子值最大为Q1（最高组）
        df['group'] = n_groups + 1 - df['group']
        
        # 按日期和分组计算平均收益
        quintile_data = {}
        dates = sorted(df['date'].unique())
        
        for g in range(1, n_groups + 1):
            quintile_data[f'Q{g}'] = []
        
        long_short = []
        
        for date in dates:
            daily = df[df['date'] == date]
            
            group_returns = {}
            for g in range(1, n_groups + 1):
                group_data = daily[daily['group'] == g]
                if len(group_data) > 0:
                    avg_ret = group_data['forward_return'].mean()
                else:
                    avg_ret = 0
                group_returns[g] = avg_ret
                quintile_data[f'Q{g}'].append(round(float(avg_ret), 6))
            
            # 多空收益 = Q1 - Q5
            ls = group_returns.get(1, 0) - group_returns.get(n_groups, 0)
            long_short.append(round(float(ls), 6))
        
        # 计算累计收益
        cumulative = {}
        for g_name, returns in quintile_data.items():
            cum = np.cumprod(1 + np.array(returns)) - 1
            cumulative[g_name] = [round(float(v) * 100, 2) for v in cum]
        
        long_short_cum = np.cumprod(1 + np.array(long_short)) - 1
        long_short_cum = [round(float(v) * 100, 2) for v in long_short_cum]
        
        # 计算各组平均收益
        group_returns = {}
        for g_name, returns in quintile_data.items():
            group_returns[g_name] = round(float(np.mean(returns)) * 100, 4)
        
        result = {
            'dates': [str(d)[:10] for d in dates],
            'groups': cumulative,
            'long_short': long_short_cum,
            'group_returns': group_returns,
            'factor': factor
        }
        
        self.quintile_results = result
        return result

    def compute_correlation(self, df: pd.DataFrame, factors: List[str]) -> Dict:
        """
        计算因子相关性矩阵
        
        Args:
            df: 包含因子数据的DataFrame
            factors: 要分析的因子列表
            
        Returns:
            dict: {
                'factors': 因子名称列表,
                'matrix': 相关系数矩阵（二维数组）
            }
        """
        # 筛选存在的因子
        available_factors = [f for f in factors if f in df.columns]
        
        if len(available_factors) < 2:
            return {'factors': [], 'matrix': []}
        
        # 计算相关系数矩阵
        corr_matrix = df[available_factors].corr(method='spearman')
        
        # 转换为前端可用的格式
        matrix_data = []
        for i, f1 in enumerate(available_factors):
            for j, f2 in enumerate(available_factors):
                val = corr_matrix.loc[f1, f2]
                if np.isfinite(val):
                    matrix_data.append([i, j, round(float(val), 3)])
        
        return {
            'factors': available_factors,
            'matrix': matrix_data
        }

    def compute_decay(self, df: pd.DataFrame, factors: List[str],
                      holding_periods: List[int] = None) -> Dict:
        """
        因子衰减分析 - 计算不同持有期的IC
        
        Args:
            df: 包含因子和价格数据的DataFrame
            factors: 要分析的因子列表
            holding_periods: 持有期列表（默认[1, 5, 10, 20]天）
            
        Returns:
            dict: {
                'factors': 因子名称列表,
                'holding_periods': 持有期列表,
                'decay_data': {因子名: [各持有期IC均值]}
            }
        """
        if holding_periods is None:
            holding_periods = [1, 5, 10, 20]
        
        df = df.copy()
        
        # 计算不同持有期的未来收益
        for days in holding_periods:
            df[f'fwd_ret_{days}d'] = df['close'].pct_change(days).shift(-days)
        
        decay_data = {}
        
        for factor in factors:
            if factor not in df.columns:
                continue
            
            factor_ics = []
            
            for days in holding_periods:
                ret_col = f'fwd_ret_{days}d'
                temp_df = df.dropna(subset=[factor, ret_col])
                
                if len(temp_df) < 10:
                    factor_ics.append(0)
                    continue
                
                # 计算Rank相关系数
                ic, _ = stats.spearmanr(
                    temp_df[factor],
                    temp_df[ret_col]
                )
                
                factor_ics.append(round(float(ic), 4) if np.isfinite(ic) else 0)
            
            decay_data[factor] = factor_ics
        
        return {
            'factors': list(decay_data.keys()),
            'holding_periods': holding_periods,
            'decay_data': decay_data
        }

    @staticmethod
    def get_alpha158_factor_names() -> List[str]:
        """
        获取Alpha158因子库中所有因子名称
        
        Returns:
            list: 因子名称列表
        """
        return [
            # 价格因子
            'RET5', 'RET10', 'RET20', 'RET30', 'RET60',
            'MA5', 'MA10', 'MA20', 'MA30', 'MA60',
            'EMA5', 'EMA10', 'EMA20', 'EMA30', 'EMA60',
            'MAX5', 'MAX10', 'MAX20', 'MAX30', 'MAX60',
            'MIN5', 'MIN10', 'MIN20', 'MIN30', 'MIN60',
            'QTLU5', 'QTLU10', 'QTLU20', 'QTLU30', 'QTLU60',
            'QTLD5', 'QTLD10', 'QTLD20', 'QTLD30', 'QTLD60',
            'MA5_BIAS', 'MA10_BIAS', 'MA20_BIAS', 'MA30_BIAS', 'MA60_BIAS',
            'MA5_SLOPE', 'MA10_SLOPE', 'MA20_SLOPE', 'MA30_SLOPE', 'MA60_SLOPE',
            'MA_BULL', 'MA_BEAR',
            'PRICE_ACCEL', 'PRICE_JERK',
            
            # 量能因子
            'VMA5', 'VMA10', 'VMA20', 'VMA30', 'VMA60',
            'VSTD5', 'VSTD10', 'VSTD20', 'VSTD30', 'VSTD60',
            'VRATIO5', 'VRATIO10', 'VRATIO20', 'VRATIO30', 'VRATIO60',
            'PV_CORR20', 'PV_CORR60',
            'OBV', 'OBV_MA20', 'OBV_SLOPE',
            'VOL_TREND', 'MONEY_FLOW', 'MF_MA20',
            
            # 技术指标因子
            'RSI6', 'RSI12', 'RSI14', 'RSI24',
            'MACD_DIF', 'MACD_DEA', 'MACD_HIST', 'MACD_CROSS',
            'KDJ_K', 'KDJ_D', 'KDJ_J',
            'BOLL_MID', 'BOLL_UPPER', 'BOLL_LOWER', 'BOLL_WIDTH', 'BOLL_POS',
            'ATR14', 'ATR14_RATIO', 'ATR20', 'ATR20_RATIO',
            'CCI14', 'CCI20',
            'WILLR14', 'WILLR20',
            'ADX', 'PLUS_DI', 'MINUS_DI',
            
            # 形态因子
            'BODY', 'BODY_RATIO', 'UPPER_SHADOW', 'LOWER_SHADOW',
            'IS_LIMIT_UP', 'IS_LIMIT_DOWN',
            'HIGH_20D', 'LOW_20D', 'NEAR_HIGH_20D', 'NEAR_LOW_20D',
            'HIGH_60D', 'LOW_60D', 'NEAR_HIGH_60D', 'NEAR_LOW_60D',
            'HIGH_120D', 'LOW_120D', 'NEAR_HIGH_120D', 'NEAR_LOW_120D',
            'CONSEC_UP', 'CONSEC_DOWN',
            'GAP_UP', 'GAP_DOWN',
            
            # 统计因子
            'VOL5D', 'VOL10D', 'VOL20D', 'VOL60D',
            'SKEW5', 'SKEW10', 'SKEW20', 'SKEW60',
            'KURT5', 'KURT10', 'KURT20', 'KURT60',
            'DOWN_VOL5', 'DOWN_VOL10', 'DOWN_VOL20', 'DOWN_VOL60',
            'MAX_DD5', 'MAX_DD10', 'MAX_DD20', 'MAX_DD60',
            'INFO_RATIO20',
        ]
