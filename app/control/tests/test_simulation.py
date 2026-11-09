# -*- coding: utf-8 -*-
"""确定性客流模拟引擎（simulation.py / UC01）单元测试。

覆盖：多层分解守恒、确定性可复现、返回结构、数据校验、自校验与外部校验。
"""
import datetime

from control.subsystem import simulation
def test_decompose_monthly_sums_to_annual():
    annual = 100_000_000  # 人次
    monthly = simulation.decompose_monthly(annual, 2025)
    assert len(monthly) == 12
    assert abs(sum(monthly) - annual) < 1000


def test_decompose_daily_and_hourly_structure():
    daily, days = simulation.decompose_daily(1_000_000, 2025, 7, seed=42)
    assert len(daily) == len(days)
    assert abs(sum(daily) - 1_000_000) < 200
    hourly = simulation.decompose_hourly(50_000, datetime.date(2025, 7, 15), seed=42)
    assert len(hourly) == 24
    assert abs(sum(hourly) - 50_000) < 100


def test_simulate_day_deterministic():
    a = simulation.simulate_day('北京首都机场', '2025-07-20', 10000, seed=42)
    b = simulation.simulate_day('北京首都机场', '2025-07-20', 10000, seed=42)
    assert a['_hourly_passengers'] == b['_hourly_passengers']
    assert a['dailyPassengers'] == b['dailyPassengers']


def test_simulate_day_structure_and_conservation():
    res = simulation.simulate_day('上海浦东机场', '2025-08-10', 8000, seed=42)
    assert len(res['hourlyFlow']) == 24
    assert len(res['_hourly_passengers']) == 24
    # 24 小时客流之和约等于日客流（守恒）
    assert abs(sum(res['_hourly_passengers']) - res['_daily_passengers']) < 100
    # 高峰时段格式正确
    assert len(res['peakHour'].split(':')) >= 2
    # 安检速度限幅在 [2.0, 5.0]
    assert all(2.0 <= s <= 5.0 for s in res['hourlySpeed'])
    # 来源与可信度标注齐全
    assert set(['annual', 'monthly', 'daily', 'hourly', 'security']) <= set(res['source_tags'])


def test_simulate_day_date_type_variation():
    spring = simulation.simulate_day('广州白云机场', '2025-02-10', 7000, seed=42)
    summer = simulation.simulate_day('广州白云机场', '2025-07-20', 7000, seed=42)
    assert spring['_daily_passengers'] > 0
    assert summer['_daily_passengers'] > 0
    # 日期类型标签应为合法枚举值（英文标签）
    valid_types = {'holiday', 'spring', 'summer', 'weekend', 'low_season', 'weekday'}
    assert spring['_date_type'] in valid_types
    assert summer['_date_type'] in valid_types
    # 春运（2月）应识别为 spring，暑运（7月）应识别为 summer
    assert spring['_date_type'] == 'spring'
    assert summer['_date_type'] == 'summer'


def test_simulate_day_invalid_inputs():
    import pytest
    with pytest.raises(ValueError):
        simulation.simulate_day('X', 'not-a-date', 1000)
    with pytest.raises(ValueError):
        simulation.simulate_day('X', '2025-07-20', 0)


def test_self_validation_passes():
    v = simulation.self_validation(10000, 2025, seed=42)
    assert v['ok'] is True
    assert 'errors' in v


def test_tsa_style_validation_keys():
    real = [10000 + i * 100 for i in range(30)]
    sim = [10050 + i * 98 for i in range(30)]
    r = simulation.tsa_style_validation(real, sim)
    assert {'mape', 'rmse', 'corr'} <= set(r.keys())


# ---------------- 扩展能力（UC01 深化：NHPP / 服务时间分布 / DES / 蒙特卡洛）----------------

def test_nhpp_arrival_times_sorted_and_bounded():
    hourly = [0] * 6 + [600] * 2 + [900] * 2 + [400] * 8 + [100] * 6
    times = simulation.nhpp_arrival_times(hourly, seed=42)
    assert times == sorted(times)
    assert all(0.0 <= t < 1440.0 for t in times)
    # 夜间 0-6 点无客流，不应产生到达
    assert all(t >= 360.0 for t in times)


def test_sample_service_time_mean_approx():
    import random as _rnd
    rng = _rnd.Random(7)
    n = 4000
    mu = 3.5
    for dist in ('exp', 'erlang'):
        vals = [simulation.sample_service_time(rng, mu, dist=dist, k=2) for _ in range(n)]
        mean = sum(vals) / n
        assert abs(mean - 1.0 / mu) < 0.1      # 均值应接近 1/mu


def test_des_simulate_basic():
    hourly = [0] * 6 + [600] * 2 + [900] * 2 + [400] * 8 + [100] * 6
    res = simulation.des_simulate(hourly, channels=6, mu=3.5, seed=42)
    assert res['n_arrivals'] > 0
    assert res['n_served'] == res['n_arrivals']
    assert res['avg_wait_min'] >= 0.0
    assert res['p50_wait_min'] <= res['p95_wait_min'] <= res['max_wait_min']
    assert 0.0 <= res['utilization'] <= 100.0


def test_des_abandonment():
    """服务能力不足 + 有限耐心时应产生放弃。"""
    hourly = [3000] * 24          # 远超 2 条通道的处理能力
    res = simulation.des_simulate(hourly, channels=2, mu=3.5, seed=42, patience=1.0)
    assert res['n_abandoned'] > 0
    assert res['abandon_rate'] > 0.0


def test_compare_des_vs_erlangc_keys():
    hourly = [0] * 6 + [600] * 2 + [900] * 2 + [400] * 8 + [100] * 6
    c = simulation.compare_des_vs_erlangc(hourly, 6, 3.5, seed=42)
    assert {'erlang_c_wq', 'des_avg_wq', 'note'} <= set(c.keys())
    assert c['peak_hour'] == 8


def test_monte_carlo_flow_intervals():
    mc = simulation.monte_carlo_flow('测试机场', '2025-08-10', 1500, n=20)
    assert mc['n'] == 20
    d = mc['daily_passengers']
    assert d['p05'] <= d['p50'] <= d['p95']
    p = mc['peak_hour_passengers']
    assert p['p05'] <= p['p50'] <= p['p95']
