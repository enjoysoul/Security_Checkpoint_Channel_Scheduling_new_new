# -*- coding: utf-8 -*-
"""客流量预测模块单元测试（纯 numpy，无外部依赖）。"""
from control.subsystem import forecasting as fc
import numpy as np


def test_error_metrics_basic():
    a = [1.0, 2.0, 3.0]
    b = [1.0, 2.0, 4.0]
    assert abs(fc.mae(a, b) - 1 / 3) < 1e-9
    # RMSE：残差 [0,0,1]，均方根 = sqrt(1/3)
    assert abs(fc.rmse(a, b) - (1 / 3) ** 0.5) < 1e-9
    # MAPE：单点 [100]->[110] = 10%
    assert abs(fc.mape([100.0], [110.0]) - 10.0) < 1e-9


def test_error_metrics_zero():
    x = [5, 5, 5]
    assert fc.mae(x, x) == 0.0
    assert fc.rmse(x, x) == 0.0


def test_build_history_reproducible():
    h1 = fc.build_history(annual_throughput_wan=8000, n_days=60, seed=42)
    h2 = fc.build_history(annual_throughput_wan=8000, n_days=60, seed=42)
    assert len(h1) == 60
    assert np.allclose(h1, h2)


def test_build_history_scale():
    h = fc.build_history(annual_throughput_wan=8000, n_days=30, seed=1)
    # 年吞吐量 8000 万 => 日均约 8000*1e4/365 ≈ 219178，允许季节波动
    assert 1e5 < np.mean(h) < 3e5


def test_forecast_daily_keys():
    res = fc.forecast_daily(8000, h=20, method='linear', n_days=120)
    for k in ('history', 'forecast', 'metrics', 'method'):
        assert k in res
    assert len(res['forecast']) == 20
    assert 'mape' in res['metrics']


def test_forecast_methods_consistency():
    for m in ('linear', 'holt_winters', 'naive', 'seasonal_naive', 'drift', 'moving_average'):
        res = fc.forecast_daily(8000, h=15, method=m, n_days=120)
        assert res['forecast'] is not None
        assert len(res['forecast']) == 15


def test_forecast_from_records():
    recs = [{'daily_passengers': '218000人次'}, {'daily_passengers': '14.2万人次'},
            {'daily_passengers': '61000人次'}] * 5
    res = fc.forecast_from_records(recs, 10, 'linear')
    assert res.get('ok') is True
    assert len(res['forecast']) == 10


# ---------------- 扩展能力（UC05 深化）----------------

def test_theta_method_shape_and_nonneg():
    hist = fc.build_history(1500, n_days=120, seed=7)
    out = fc.theta_method(hist, 14)
    assert len(out) == 14
    assert all(x >= 0 for x in out)


def test_ensemble_forecast_weights_sum_to_one():
    hist = fc.build_history(1500, n_days=120, seed=7)
    en = fc.ensemble_forecast(hist, 10, season_length=7)
    assert len(en['forecast']) == 10
    # 权重对外保留 4 位小数，故用 1e-3 容差
    assert abs(sum(en['weights'].values()) - 1.0) < 1e-3
    assert set(en['methods']) == set(en['weights'].keys())


def test_rolling_origin_eval_folds():
    hist = fc.build_history(1500, n_days=180, seed=7)
    ro = fc.rolling_origin_eval(hist, 'holt_winters', season_length=7,
                                n_splits=3, test_size=7)
    assert ro['ok'] is True
    assert ro['n_folds'] >= 1
    assert ro['mae_mean'] >= 0.0


def test_grid_search_returns_best():
    hist = fc.build_history(1500, n_days=120, seed=7)
    gs = fc.grid_search(hist, season_lengths=(7, 14), test_size=14)
    assert gs['ok'] is True
    assert gs['best']['method'] in fc.DEFAULT_ENSEMBLE_METHODS
    assert gs['n_configs'] > 1


def test_prediction_interval_bounds():
    hist = fc.build_history(1500, n_days=120, seed=7)
    pi = fc.prediction_interval(hist, 7, 'holt_winters', level=0.95)
    assert len(pi['point']) == len(pi['lower']) == len(pi['upper']) == 7
    for lo, pt, up in zip(pi['lower'], pi['point'], pi['upper']):
        assert lo <= pt <= up


def test_detect_and_clean_outliers():
    hist = fc.build_history(1500, n_days=120, seed=7)
    dirty = list(hist)
    dirty[60] = dirty[60] * 5          # 注入极端异常点
    det = fc.detect_outliers(dirty, season_length=7, sigma=3.0)
    assert det['n'] >= 1
    cleaned = fc.clean_outliers(dirty, season_length=7, sigma=3.0)
    assert len(cleaned['series']) == len(dirty)
    assert cleaned['replaced'] >= 1
    assert cleaned['series'][60] < dirty[60]
