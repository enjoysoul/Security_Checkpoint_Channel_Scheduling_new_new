# -*- coding: utf-8 -*-
"""
forecasting.py —— 机场客流量时间序列预测模块

该模块实现不依赖重型第三方库（仅用 numpy）的时序预测算法集合，用于补全
SRS 中要求的「AI 客流量预测」能力。提供：
  - 朴素预测（naive / seasonal-naive / drift）
  - 移动平均（MA）
  - 线性回归趋势（带傅里叶季节项）
  -  Holt-Winters 指数平滑（加法 / 乘法季节）
  - 加法季节分解（trend + seasonal + residual）
  - 拟合质量评估（MAE / RMSE / MAPE）
  - 历史序列合成（基于年吞吐量构造带周/月季节性的日客流，用于演示与回测）

所有对外函数返回普通 list / dict，方便直接 jsonify 给前端。
"""

from __future__ import annotations

import math
import random
from typing import List, Dict, Any, Optional, Tuple

import numpy as np


# ==========================================================================
# 基础工具
# ==========================================================================

def _to_float_list(series) -> List[float]:
    """将任意可迭代对象转为 float 列表，过滤无效值。"""
    out = []
    for x in series:
        try:
            v = float(x)
            if math.isfinite(v):
                out.append(v)
        except (TypeError, ValueError):
            continue
    return out


def mae(a: List[float], b: List[float]) -> float:
    """平均绝对误差。"""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(abs(a[i] - b[i]) for i in range(n)) / n


def rmse(a: List[float], b: List[float]) -> float:
    """均方根误差。"""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)) / n)


def mape(a: List[float], b: List[float]) -> float:
    """平均绝对百分比误差（百分比数值，如 8.3 表示 8.3%）。"""
    if not a or not b:
        return 0.0
    s = 0.0
    n = 0
    for i in range(min(len(a), len(b))):
        if a[i] != 0:
            s += abs(a[i] - b[i]) / abs(a[i])
            n += 1
    return (s / n * 100.0) if n else 0.0


# ==========================================================================
# 朴素 / 经典方法
# ==========================================================================

def naive(series: List[float], h: int) -> List[float]:
    """朴素预测：未来 h 步都等于最后一个观测值。"""
    if not series:
        return [0.0] * max(h, 0)
    last = series[-1]
    return [last] * h


def seasonal_naive(series: List[float], h: int, season_length: int = 7) -> List[float]:
    """季节朴素：用上一个同季节位置的值外推。"""
    if not series or season_length <= 0:
        return naive(series, h)
    out = []
    for i in range(h):
        out.append(series[-season_length + (i % season_length)] if len(series) >= season_length else series[-1])
    return out


def drift(series: List[float], h: int) -> List[float]:
    """带漂移的朴素预测：末值 + 平均逐期增量 * 步数。"""
    if len(series) < 2:
        return naive(series, h)
    total_drift = series[-1] - series[0]
    step = total_drift / (len(series) - 1)
    return [series[-1] + step * (i + 1) for i in range(h)]


def moving_average(series: List[float], h: int, window: int = 7) -> List[float]:
    """移动平均预测：以末尾 window 均值外推。"""
    if not series:
        return [0.0] * h
    w = min(window, len(series))
    avg = sum(series[-w:]) / w
    return [avg] * h


# ==========================================================================
# 线性回归 + 傅里叶季节项（中等复杂度，无状态）
# ==========================================================================

def linear_trend_forecast(series: List[float], h: int,
                          fourier_terms: int = 3, period: int = 7) -> List[float]:
    """
    用线性回归拟合 [趋势 + 傅里叶周期项]，外推 h 步。
    适合同时捕捉长期趋势与周内季节波动。
    """
    n = len(series)
    if n < 4:
        return naive(series, h)
    t = np.arange(n, dtype=float)
    y = np.array(series, dtype=float)
    # 设计矩阵：[1, t, sin(2πkt/P), cos(2πkt/P), ...]
    cols = [np.ones(n), t]
    for k in range(1, fourier_terms + 1):
        cols.append(np.sin(2 * np.pi * k * t / period))
        cols.append(np.cos(2 * np.pi * k * t / period))
    X = np.vstack(cols).T
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return naive(series, h)
    # 预测区间
    tf = np.arange(n, n + h, dtype=float)
    Xf = [np.ones(h), tf]
    for k in range(1, fourier_terms + 1):
        Xf.append(np.sin(2 * np.pi * k * tf / period))
        Xf.append(np.cos(2 * np.pi * k * tf / period))
    Xf = np.vstack(Xf).T
    pred = Xf @ beta
    pred = np.clip(pred, 0, None)
    return pred.tolist()


# ==========================================================================
# Holt-Winters 指数平滑
# ==========================================================================

def _initial_trend(y: np.ndarray, season_length: int) -> float:
    """用前 2*season_length 个观测估计初始趋势。"""
    s = 0.0
    for i in range(season_length):
        s += (y[i + season_length] - y[i]) / season_length
    return s / season_length


def _initial_seasonal(y: np.ndarray, season_length: int, multiplicative: bool) -> List[float]:
    """估计初始季节因子。"""
    n = len(y)
    seasons = [0.0] * season_length
    n_seasons = n // season_length
    if n_seasons < 1:
        return [1.0] * season_length if multiplicative else [0.0] * season_length
    # 每个季节位置的平均去趋势值
    avg = np.mean(y[: n_seasons * season_length]) or 1.0
    for i in range(season_length):
        s = 0.0
        for j in range(n_seasons):
            idx = j * season_length + i
            if multiplicative:
                s += y[idx] / (avg if avg != 0 else 1.0)
            else:
                s += y[idx] - avg
        seasons[i] = s / n_seasons
    if multiplicative:
        m = sum(seasons) / season_length
        seasons = [s / m for s in seasons]
    return seasons


def holt_winters(series: List[float], h: int, season_length: int = 7,
                 alpha: float = 0.3, beta: float = 0.05, gamma: float = 0.3,
                 multiplicative: bool = False) -> List[float]:
    """
    Holt-Winters 指数平滑（加法或乘法季节）。
    返回未来 h 步预测。窗口不足时回退到线性趋势或朴素。
    """
    y = np.array(_to_float_list(series), dtype=float)
    n = len(y)
    if n < 2 * season_length:
        # 数据不足以估计季节，退化为线性趋势
        return linear_trend_forecast(series, h)
    if multiplicative and np.any(y <= 0):
        multiplicative = False  # 乘法模型要求正数

    m = season_length
    if multiplicative:
        level = y[0] / (_initial_seasonal(y, m, True)[0] or 1.0)
    else:
        level = y[0] - _initial_seasonal(y, m, False)[0]
    trend = _initial_trend(y, m)
    seasons = _initial_seasonal(y, m, multiplicative)

    for i in range(1, n):
        idx = i % m
        if multiplicative:
            last_season = seasons[idx]
            season = gamma * (y[i] / (level + trend)) + (1 - gamma) * last_season
            seasons[idx] = season
            prev_level = level
            level = alpha * (y[i] / season) + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
        else:
            last_season = seasons[idx]
            season = gamma * (y[i] - (level + trend)) + (1 - gamma) * last_season
            seasons[idx] = season
            prev_level = level
            level = alpha * (y[i] - season) + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend

    out = []
    for i in range(h):
        idx = (n + i) % m
        if multiplicative:
            out.append((level + (i + 1) * trend) * seasons[idx])
        else:
            out.append(level + (i + 1) * trend + seasons[idx])
    out = [max(0.0, float(v)) for v in out]
    return out


# ==========================================================================
# 加法季节分解
# ==========================================================================

def seasonal_decompose(series: List[float], season_length: int = 7) -> Dict[str, List[float]]:
    """
    加法分解：observed = trend + seasonal + residual。
    使用 centered MA 估计 trend，再平均去趋势得到季节因子。
    """
    y = _to_float_list(series)
    n = len(y)
    trend = [None] * n
    half = season_length // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        if hi - lo >= season_length:
            trend[i] = sum(y[lo:hi]) / (hi - lo)
        else:
            trend[i] = y[i]
    # 季节因子
    seasonal = [0.0] * n
    if n >= season_length:
        buckets = [[] for _ in range(season_length)]
        for i in range(n):
            if trend[i] is not None:
                buckets[i % season_length].append(y[i] - trend[i])
        means = [ (sum(b) / len(b)) if b else 0.0 for b in buckets ]
        avg = sum(means) / season_length
        means = [m - avg for m in means]
        for i in range(n):
            seasonal[i] = means[i % season_length]
    residual = [ (y[i] - (trend[i] if trend[i] is not None else y[i]) - seasonal[i]) for i in range(n) ]
    return {
        'trend': [round(t, 2) if t is not None else 0.0 for t in trend],
        'seasonal': [round(s, 2) for s in seasonal],
        'residual': [round(r, 2) for r in residual],
    }


# ==========================================================================
# 统一入口
# ==========================================================================

def forecast(series: List[float], h: int, method: str = 'holt_winters',
             season_length: int = 7) -> List[float]:
    """根据 method 选择预测器，返回 h 步预测（非负）。"""
    s = _to_float_list(series)
    if not s:
        return [0.0] * h
    method = (method or 'holt_winters').lower()
    if method == 'naive':
        return naive(s, h)
    if method == 'seasonal_naive':
        return seasonal_naive(s, h, season_length)
    if method == 'drift':
        return drift(s, h)
    if method == 'moving_average':
        return moving_average(s, h, season_length)
    if method == 'linear':
        return linear_trend_forecast(s, h, period=season_length)
    if method == 'holt_winters':
        return holt_winters(s, h, season_length, multiplicative=False)
    if method == 'holt_winters_mul':
        return holt_winters(s, h, season_length, multiplicative=True)
    if method == 'theta':
        return theta_method(s, h, season_length)
    # 默认
    return holt_winters(s, h, season_length, multiplicative=False)


def evaluate(series: List[float], method: str = 'holt_winters',
             test_size: int = 14, season_length: int = 7) -> Dict[str, float]:
    """用末尾 test_size 个点做滚动/直截验证，返回评估指标。"""
    s = _to_float_list(series)
    if len(s) < test_size + season_length + 2:
        return {'mae': 0.0, 'rmse': 0.0, 'mape': 0.0, 'train_size': len(s), 'test_size': 0}
    train = s[:-test_size]
    test = s[-test_size:]
    pred = forecast(train, test_size, method, season_length)
    return {
        'mae': round(mae(test, pred), 2),
        'rmse': round(rmse(test, pred), 2),
        'mape': round(mape(test, pred), 2),
        'train_size': len(train),
        'test_size': test_size,
    }


# ==========================================================================
# 历史序列合成（用于演示 / 回测 / 冷启动）
# ==========================================================================

def build_history(annual_throughput_wan: float, n_days: int = 120,
                   seed: int = 42, trend: float = 0.002) -> List[float]:
    """
    基于年吞吐量（万人次）合成一段带真实结构的日客流历史：
      - 基准 = 年吞吐量 / 365
      - 周季节：周末上浮
      - 月季节：春运/暑运上浮，3-4月/11月下浮
      - 长期趋势 + 随机噪声
    返回每日客流量列表。seed 固定保证可复现。
    """
    rng = random.Random(seed)
    base = max(1.0, (annual_throughput_wan * 1e4) / 365.0)
    out = []
    for d in range(n_days):
        # 周季节（0=周一）
        weekday = d % 7
        weekly = {0: 1.00, 1: 0.98, 2: 1.02, 3: 1.05, 4: 1.12, 5: 1.35, 6: 1.28}[weekday]
        # 月季节（近似周期 30.4 天）
        month_phase = 2 * math.pi * (d % 30) / 30.4
        monthly = 1.0 + 0.18 * math.sin(month_phase - math.pi / 2) \
                  + 0.10 * math.sin(2 * math.pi * (d % 365) / 365.0)
        # 长期缓慢增长趋势
        growth = 1.0 + trend * d
        noise = rng.gauss(1.0, 0.05)
        val = base * weekly * monthly * growth * max(0.5, noise)
        out.append(round(val, 1))
    return out


def forecast_daily(annual_throughput_wan: float, h: int = 30,
                   method: str = 'holt_winters', n_days: int = 120,
                   seed: int = 42) -> Dict[str, Any]:
    """
    端到端：合成历史 -> 预测未来 h 天 -> 评估 -> 返回结构化结果。
    适合没有真实历史库时的演示与回测。
    """
    history = build_history(annual_throughput_wan, n_days=n_days, seed=seed)
    pred = forecast(history, h, method)
    metrics = evaluate(history, method, test_size=min(14, n_days // 4))
    return {
        'history': history,
        'forecast': [round(x, 1) for x in pred],
        'metrics': metrics,
        'method': method,
        'annual_throughput_wan': annual_throughput_wan,
    }


# ==========================================================================
# 基于真实历史（T_PassengerSim 的日客流）的预测
# ==========================================================================

def forecast_from_records(records: List[Dict[str, Any]], h: int = 30,
                          method: str = 'holt_winters') -> Dict[str, Any]:
    """
    从数据库记录列表（每条含 daily_passengers 字符串/数字、日期可选）构造序列并预测。
    records 按时间升序。若不足，调用方应回退到 forecast_daily。
    """
    series = []
    for r in records:
        dp = r.get('daily_passengers') if isinstance(r, dict) else r
        try:
            series.append(float(str(dp).replace('人次', '').replace('人', '').strip()))
        except (TypeError, ValueError):
            continue
    series = _to_float_list(series)
    if len(series) < 8:
        return {'ok': False, 'reason': 'history_too_short', 'series': series}
    pred = forecast(series, h, method)
    metrics = evaluate(series, method, test_size=min(14, len(series) // 4))
    return {
        'ok': True,
        'history': [round(x, 1) for x in series],
        'forecast': [round(x, 1) for x in pred],
        'metrics': metrics,
        'method': method,
    }


# ==========================================================================
# UC05 算法深化：
#   Theta 方法 / 多模型加权集成 / 滚动原点回测 / 超参网格搜索 /
#   预测区间 / 残差异常检测与修正
# ==========================================================================

# 参与集成的默认模型集合（覆盖平滑类、回归类、Theta 类与朴素基准）
DEFAULT_ENSEMBLE_METHODS = ('holt_winters', 'holt_winters_mul', 'linear',
                            'theta', 'seasonal_naive', 'drift')


def theta_method(series: List[float], h: int, season_length: int = 7) -> List[float]:
    """Theta 方法预测（M3 预测竞赛的经典强基准，实现简单但精度很高）。

    原理：把序列拆成两条「Theta 线」分别外推后再平均。
      - θ=0 线：对时间索引做最小二乘回归得到的长期趋势线（完全去噪）
      - θ=2 线：2·y - 趋势线（把短期波动放大一倍）
    对 θ=2 线做简单指数平滑（SES）并外推，最终预测 = (θ0 外推 + θ2 外推) / 2。

    SES 的平滑系数 α 通过网格搜索最小化「一步向前预测误差平方和」得到，
    避免手工拍定参数。
    """
    s = _to_float_list(series)
    n = len(s)
    if n == 0:
        return [0.0] * h
    if n < 4:
        return [max(0.0, s[-1])] * h

    # θ=0：对时间索引做最小二乘回归
    t = np.arange(n, dtype=float)
    y = np.array(s, dtype=float)
    X = np.vstack([np.ones(n), t]).T
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    line = beta[0] + beta[1] * t

    # θ=2 线
    theta2 = 2.0 * y - line

    # 网格搜索 SES 的最优 alpha（最小化一步误差平方和）
    best_alpha, best_sse = 0.5, None
    for a in [i / 20.0 for i in range(1, 20)]:
        lvl = theta2[0]
        sse = 0.0
        for v in theta2[1:]:
            err = v - lvl
            sse += float(err * err)
            lvl = lvl + a * err
        if best_sse is None or sse < best_sse:
            best_sse, best_alpha = sse, a

    # 用最优 alpha 平滑并外推（SES 的未来预测恒为最后的 level）
    lvl = theta2[0]
    for v in theta2[1:]:
        lvl = lvl + best_alpha * (v - lvl)
    theta2_fc = np.array([lvl] * h, dtype=float)

    # θ=0 趋势线外推
    tf = np.arange(n, n + h, dtype=float)
    theta0_fc = beta[0] + beta[1] * tf

    out = 0.5 * (theta0_fc + theta2_fc)
    return [max(0.0, float(x)) for x in out]


def ensemble_forecast(series: List[float], h: int, methods=None,
                      season_length: int = 7, test_size: int = 14,
                      weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """多模型加权集成预测：按回测误差定权（误差越小权重越大）。

    单一模型在不同形态的序列上表现差异很大（Holt-Winters 擅长稳定季节、
    线性回归擅长单调趋势、Theta 擅长长期外推）。集成能显著降低选错模型的风险，
    是预测竞赛与工业界的通用做法。

    :param weights: 可选，手工指定权重（给出时不再按回测定权）
    :return: dict，含 forecast / weights / basis / components（各模型分项）
    """
    s = _to_float_list(series)
    methods = list(methods or DEFAULT_ENSEMBLE_METHODS)
    if not s:
        return {'forecast': [0.0] * h, 'weights': {}, 'basis': 'empty',
                'methods': methods, 'components': {}}

    if weights:
        w = {m: max(0.0, float(weights.get(m, 0.0))) for m in methods}
        tot = sum(w.values())
        w = ({m: v / tot for m, v in w.items()} if tot > 0
             else {m: 1.0 / len(methods) for m in methods})
        basis = 'user_weights'
    else:
        # 回测定权：w ∝ 1 / MAE（MAE 为 0 时回退 RMSE，仍为 0 则给极小误差）
        ts = min(max(2, test_size), max(2, len(s) // 4))
        errs = {}
        for m in methods:
            e = evaluate(s, m, test_size=ts, season_length=season_length)
            errs[m] = e['mae'] if e['mae'] > 0 else (e['rmse'] if e['rmse'] > 0 else 1e-9)
        inv = {m: 1.0 / (errs[m] + 1e-9) for m in methods}
        tot = sum(inv.values())
        w = {m: v / tot for m, v in inv.items()}
        basis = 'backtest_error'

    preds = {m: forecast(s, h, m, season_length) for m in methods}
    out = []
    for i in range(h):
        out.append(max(0.0, sum(w[m] * preds[m][i] for m in methods)))

    return {
        'forecast': [round(x, 1) for x in out],
        'weights': {m: round(w[m], 4) for m in methods},
        'errors': {m: round(evaluate(s, m, test_size=min(max(2, test_size), max(2, len(s) // 4)),
                                     season_length=season_length)['mae'], 2) for m in methods},
        'basis': basis,
        'methods': methods,
        'components': {m: [round(x, 1) for x in preds[m]] for m in methods},
    }


def rolling_origin_eval(series: List[float], method: str = 'holt_winters',
                        season_length: int = 7, n_splits: int = 3,
                        test_size: int = 7) -> Dict[str, Any]:
    """滚动原点交叉验证（rolling-origin / time-series CV）。

    单次「末尾切一刀」的验证受测试期偶然性影响很大。滚动原点评估把
    时间序列从后往前切成多个折叠，逐折训练-测试并汇总误差，
    得到更稳健、更可信的模型精度估计。
    """
    s = _to_float_list(series)
    if len(s) < season_length + 2 + test_size + 1:
        return {'ok': False, 'reason': 'series_too_short', 'folds': []}

    folds = []
    maes, mapes = [], []
    for k in range(n_splits):
        end = len(s) - k * test_size
        start_test = end - test_size
        if start_test - season_length - 2 < 2:
            break
        train = s[:start_test]
        test = s[start_test:end]
        pred = forecast(train, test_size, method, season_length)
        f_mae, f_mape = mae(test, pred), mape(test, pred)
        folds.append({
            'fold': k + 1, 'train_size': len(train),
            'mae': round(f_mae, 2), 'rmse': round(rmse(test, pred), 2),
            'mape': round(f_mape, 2),
        })
        maes.append(f_mae)
        mapes.append(f_mape)

    if not folds:
        return {'ok': False, 'reason': 'no_valid_fold', 'folds': []}
    return {
        'ok': True, 'method': method, 'season_length': season_length,
        'n_folds': len(folds), 'folds': folds,
        'mae_mean': round(sum(maes) / len(maes), 2),
        'mape_mean': round(sum(mapes) / len(mapes), 2),
    }


def grid_search(series: List[float], season_lengths=(7, 14), methods=None,
                test_size: int = 14) -> Dict[str, Any]:
    """超参网格搜索：遍历 (模型, 季节周期) 组合，按 MAPE 选出最优配置。

    季节周期（7=周周期、14=双周、30=月周期）对预测精度影响显著，
    不同机场/不同时期的客流季节结构并不同，因此需要按数据自动选择而非写死。
    """
    methods = list(methods or DEFAULT_ENSEMBLE_METHODS)
    results = []
    for m in methods:
        for sl in season_lengths:
            e = evaluate(series, m, test_size=test_size, season_length=sl)
            if e.get('test_size', 0) <= 0:
                continue
            results.append({
                'method': m, 'season_length': sl,
                'mae': e['mae'], 'rmse': e['rmse'], 'mape': e['mape'],
            })
    if not results:
        return {'ok': False, 'best': None, 'results': []}
    best = min(results, key=lambda x: (x['mape'], x['mae']))
    return {'ok': True, 'best': best, 'results': results, 'n_configs': len(results)}


def prediction_interval(series: List[float], h: int, method: str = 'holt_winters',
                        season_length: int = 7, level: float = 0.95,
                        test_size: int = 14) -> Dict[str, Any]:
    """预测区间：在点预测之外给出上下界，表达预测的不确定性。

    做法：用留出集的**残差标准差**估计噪声水平，区间半宽随预测步长
    按 sqrt(1+i) 逐步放大（越远的未来越不确定），这是时序预测区间的常用近似。

    :return: dict {point, lower, upper, resid_sd, level, method}
    """
    s = _to_float_list(series)
    point = forecast(s, h, method, season_length)
    if len(s) < test_size + season_length + 2:
        sd = float(np.std(np.array(s, dtype=float))) if len(s) > 1 else 0.0
    else:
        train, test = s[:-test_size], s[-test_size:]
        pred_test = forecast(train, test_size, method, season_length)
        resid = np.array([a - b for a, b in zip(test, pred_test)], dtype=float)
        sd = float(np.std(resid)) if len(resid) > 1 else 0.0

    z_table = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}
    z = z_table.get(round(float(level), 2), 1.96)

    lower, upper = [], []
    for i, p in enumerate(point):
        half = z * sd * math.sqrt(1.0 + i)
        lower.append(round(max(0.0, p - half), 1))
        upper.append(round(p + half, 1))
    return {
        'point': [round(x, 1) for x in point],
        'lower': lower, 'upper': upper,
        'resid_sd': round(sd, 2), 'level': level, 'method': method,
    }


def detect_outliers(series: List[float], season_length: int = 7,
                    sigma: float = 3.0) -> Dict[str, Any]:
    """基于季节分解残差的异常值检测（3σ 准则）。

    直接对原始序列做 3σ 会把「春运高峰」误判为异常。正确做法是先
    剥离趋势与季节成分，只对**残差**做 3σ 检验，这样才不会把规律性的
    高峰当成脏数据。
    """
    s = _to_float_list(series)
    if len(s) < season_length * 2:
        return {'outliers': [], 'n': 0, 'resid_sd': 0.0, 'sigma': sigma}
    d = seasonal_decompose(s, season_length)
    resid = np.array(d['residual'], dtype=float)
    sd = float(np.std(resid))
    if sd <= 0:
        return {'outliers': [], 'n': 0, 'resid_sd': 0.0, 'sigma': sigma}
    items = []
    for i in range(len(resid)):
        if abs(resid[i]) > sigma * sd:
            items.append({
                'index': i, 'value': s[i],
                'residual': round(float(resid[i]), 2),
                'z': round(float(resid[i] / sd), 2),
            })
    return {'outliers': items, 'n': len(items), 'resid_sd': round(sd, 2), 'sigma': sigma}


def clean_outliers(series: List[float], season_length: int = 7,
                   sigma: float = 3.0) -> Dict[str, Any]:
    """把检测出的异常点替换为「趋势 + 季节」的重构值，返回去噪后的序列。

    修正而非删除，可以保持序列长度与时间戳对齐（这对时序模型很重要）。
    """
    s = _to_float_list(series)
    d = seasonal_decompose(s, season_length)
    trend, seasonal = d['trend'], d['seasonal']
    det = detect_outliers(s, season_length, sigma)
    out = list(s)
    for o in det['outliers']:
        i = o['index']
        out[i] = max(0.0, round(trend[i] + seasonal[i], 1))
    return {'series': out, 'replaced': det['n'], 'outliers': det['outliers']}


if __name__ == '__main__':
    # 简单自测
    demo = build_history(800, n_days=120, seed=7)
    for m in ['naive', 'drift', 'linear', 'holt_winters', 'holt_winters_mul', 'seasonal_naive']:
        e = evaluate(demo, m, test_size=14)
        print(f"{m:16s} MAE={e['mae']:8.1f} RMSE={e['rmse']:8.1f} MAPE={e['mape']:5.1f}%")
