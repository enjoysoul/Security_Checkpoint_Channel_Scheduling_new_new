# -*- coding: utf-8 -*-
"""排队论通道调度引擎（queuing.py / UC02）单元测试。

覆盖：Erlang-B/C 边界、metrics 稳定性判定、best_channel 最小可行通道搜索、
分时段调度计划与 what-if 情景分析。
"""
import math

from control.subsystem import queuing
def test_erlang_b_edges():
    # a=0（无呼叫请求）-> 损失概率 0
    assert queuing.erlang_b(0, 5) == 0.0
    assert queuing.erlang_b(0, 10) == 0.0
    # c=0 且 a=0 -> 边界返回 1.0
    assert queuing.erlang_b(0, 0) == 1.0
    assert 0.0 <= queuing.erlang_b(5, 3) <= 1.0


def test_erlang_c_pwait_unstable():
    # rho >= 1 时必然排队，等待概率恒为 1
    assert queuing.erlang_c_pwait(2, 5.0) == 1.0
    assert queuing.erlang_c_pwait(0, 1.0) == 1.0


def test_metrics_stable_vs_unstable():
    m = queuing.metrics(10, 5.0, 3.5)
    assert m['unstable'] is False
    assert m['wq'] >= 0 and math.isfinite(m['wq'])
    assert abs(m['rho'] - (5.0 / (10 * 3.5))) < 1e-9
    mu = queuing.metrics(2, 100.0, 3.5)
    assert mu['unstable'] is True
    assert mu['wq'] == float('inf')


def test_best_channel_finds_feasible():
    bc = queuing.best_channel(20.0, 3.5, threshold=15.0, c_max=20)
    assert bc['feasible'] is True
    assert 1 <= bc['channels'] <= 20
    assert math.isfinite(bc['wq']) and bc['wq'] <= 15.0


def test_best_channel_over_capacity():
    bc = queuing.best_channel(500.0, 3.5, threshold=15.0, c_max=10)
    assert bc['feasible'] is False


def test_schedule_plan_and_what_if():
    hourly = [0] * 6 + [8000] * 3 + [12000] * 2 + [5000] * 5 + [2000] * 8
    plan = queuing.schedule_plan(hourly, 3.5, threshold=15.0, c_max=20)
    assert len(plan) == 24
    for it in plan:
        assert 'recommend_channels' in it and 'avg_wait_min' in it
    wi = queuing.what_if(hourly, 3.5, 10, factor=1.5, threshold=15.0)
    assert len(wi) == 24


def test_avg_security_time():
    assert queuing.avg_security_time(3.5) == round((1.0 / 3.5) * 60.0, 1)


# ---------------- 扩展模型（UC02 深化）----------------

def test_sipp_plan_length_and_fields():
    hourly = [0] * 6 + [3000] * 2 + [4500] * 2 + [2000] * 8 + [500] * 6
    plan = queuing.sipp_plan(hourly, 3.5, threshold=15.0, c_max=20)
    assert len(plan) == 24
    for it in plan:
        assert 'recommend_channels' in it and 'carryover_people' in it
        assert it['recommend_channels'] >= 1


def test_erlang_a_reduces_wait_vs_erlang_c():
    """顾客会因等待过久而放弃，因此 Erlang-A 的有效到达率与等待应不高于 Erlang-C。"""
    lam, mu, c = 20.0, 3.5, 8
    base = queuing.metrics(c, lam, mu)
    a = queuing.erlang_a_metrics(c, lam, mu, theta=0.05)
    assert a['eff_arrival'] <= lam
    assert a['abandon_prob'] >= 0.0
    assert a['wq'] <= base['wq'] + 1e-9       # 放弃使队列变短
    assert queuing.erlang_a_metrics(c, lam, mu, theta=0.0)['abandon_prob'] == 0.0


def test_mmck_blocking_and_wait():
    """有限候检区会产生阻塞，等待时间应低于无限队列的 Erlang-C。"""
    lam, mu, c = 20.0, 3.5, 8
    k = queuing.mmck_metrics(c, lam, mu, K=12)
    base = queuing.metrics(c, lam, mu)
    assert 0.0 <= k['blocking_prob'] <= 1.0
    assert k['blocking_prob'] > 0.0
    assert k['eff_arrival'] < lam
    assert k['wq'] <= base['wq'] + 1e-9


def test_service_level_and_quantile():
    lam, mu, c = 20.0, 3.5, 8
    sl = queuing.service_level(c, lam, mu, 10.0)
    assert 0.0 <= sl <= 1.0
    # 达标概率越高，对应分位数越大（单调性）
    q50 = queuing.wait_quantile(c, lam, mu, 0.50)
    q95 = queuing.wait_quantile(c, lam, mu, 0.95)
    assert q50 <= q95
    assert queuing.prob_wait_exceeds(c, lam, mu, 0.0) <= 1.0


def test_cost_optimal_channel_returns_curve():
    res = queuing.cost_optimal_channel(20.0, 3.5, staff_per_channel=4,
                                       threshold=15.0, c_max=20)
    assert res['channels'] >= 1
    assert len(res['cost_curve']) == 20
    finite = [x for x in res['cost_curve'] if x.get('total_cost') != float('inf')]
    assert finite, '至少应存在一个稳定（非过载）的通道配置'


def test_monte_carlo_and_robust_channel():
    mc = queuing.monte_carlo_wait(20.0, 3.5, 8, n=200, cv=0.15, threshold=15.0)
    assert 0.0 <= mc['service_prob'] <= 1.0
    assert mc['n'] == 200
    rc = queuing.robust_channel(20.0, 3.5, threshold=15.0, c_max=20,
                                cv=0.15, target_service=0.9, n=200)
    assert rc['channels'] >= 1
    assert 'service_prob' in rc
