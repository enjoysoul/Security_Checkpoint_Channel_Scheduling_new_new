# -*- coding: utf-8 -*-
"""确定性定员引擎（staffing.py / UC04）单元测试。

覆盖：班次窗口跨零点、窗口客流统计、代表性到达率、通道→人数换算、
定员矩阵形状与限幅、出港折算、编制校验。
"""
from control.subsystem import simulation
from control.subsystem import staffing
# 中等规模机场的 24 小时客流样例（人次/小时），避免结果被限幅饱和
HOURLY = [0] * 6 + [600] * 2 + [900] * 2 + [400] * 8 + [100] * 6


def test_shift_hours_wraps_night():
    assert staffing.shift_hours(6, 14) == [6, 7, 8, 9, 10, 11, 12, 13]
    assert staffing.shift_hours(22, 6) == [22, 23, 0, 1, 2, 3, 4, 5]


def test_window_flow_stats():
    stat = staffing.window_flow(HOURLY, [6, 7, 8, 9])
    assert stat['n'] == 4
    assert stat['peak'] == 900
    assert abs(stat['mean'] - 750.0) < 1e-6


def test_representative_arrival_modes():
    hours = [6, 7, 8, 9]
    peak = staffing.representative_arrival_per_min(HOURLY, hours, mode='peak')
    mean = staffing.representative_arrival_per_min(HOURLY, hours, mode='mean')
    mix = staffing.representative_arrival_per_min(HOURLY, hours, mode='peak_weighted')
    assert peak >= mix >= mean      # 峰值 >= 加权 >= 均值
    assert abs(peak - 900 / 60.0) < 1e-6


def test_staff_for_channels_reserve():
    assert staffing.staff_for_channels(3, 2, reserve_factor=0.0) == 6
    assert staffing.staff_for_channels(3, 2, reserve_factor=0.1) == 7


def test_compute_shift_requirements_shape_and_limits():
    counts, detail = staffing.compute_shift_requirements(
        HOURLY, mu=3.5, staff_per_channel=2, threshold=15, c_max=20,
        security_ratio=0.5)
    assert set(counts.keys()) == {'早班', '中班', '晚班'}
    for shift, vals in counts.items():
        assert len(vals) == 7
        assert all(2 <= v <= 15 for v in vals)
        assert detail[shift]['channels'] >= 1
        assert 'arrival_per_min' in detail[shift]
    # 周末（周六、周日）人数不应少于工作日
    assert counts['早班'][5] >= counts['早班'][0]
    assert counts['早班'][6] >= counts['早班'][0]


def test_security_ratio_reduces_demand():
    """安检只服务出港旅客：折算后到达率应约为原来的一半。"""
    _, d_full = staffing.compute_shift_requirements(HOURLY, mu=3.5, security_ratio=None)
    _, d_half = staffing.compute_shift_requirements(HOURLY, mu=3.5, security_ratio=0.5)
    assert d_half['早班']['arrival_per_min'] < d_full['早班']['arrival_per_min']
    ratio = d_half['早班']['arrival_per_min'] / d_full['早班']['arrival_per_min']
    assert abs(ratio - 0.5) < 0.01


def test_to_security_flow():
    out = staffing.to_security_flow([1000.0] * 24, 0.5)
    assert len(out) == 24
    assert abs(out[0] - 500.0) < 1e-6


def test_headcount_plan_chain():
    """完整链路：年吞吐量 -> 24h 客流 -> 各班次定员 -> 需求总编制人数。"""
    res = simulation.simulate_day('测试机场', '2025-08-10', 1500, seed=42)
    plan = staffing.headcount_plan(res['_hourly_passengers'], mu=3.5,
                                   staff_per_channel=2, security_ratio=0.5)
    assert set(plan.keys()) == {'counts', 'shifts', 'headcount'}
    assert set(plan['counts'].keys()) == {'早班', '中班', '晚班'}
    assert plan['headcount']['total'] > 0
    assert set(plan['headcount']['per_shift'].keys()) == {'早班', '中班', '晚班'}


def test_headcount_rises_as_work_days_shrink():
    """每人每周可出勤天数越少，需要的总人数越多。"""
    res = simulation.simulate_day('测试机场', '2025-08-10', 1500, seed=42)
    hf = res['_hourly_passengers']
    p5 = staffing.headcount_plan(hf, mu=3.5, security_ratio=0.5, max_work_days=5)
    p3 = staffing.headcount_plan(hf, mu=3.5, security_ratio=0.5, max_work_days=3)
    assert p3['headcount']['total'] >= p5['headcount']['total']


def test_headcount_scales_with_throughput():
    """年吞吐量越大，测算出的编制人数越多（人数由客流算出，而非固定值）。"""
    def required(annual_wan):
        r = simulation.simulate_day('测试机场', '2025-08-10', annual_wan, seed=42)
        return staffing.headcount_plan(r['_hourly_passengers'], mu=3.5,
                                       security_ratio=0.5)['headcount']['total']
    small = required(300)
    large = required(1500)
    assert large >= small


def test_headcount_and_coverage():
    counts, _ = staffing.compute_shift_requirements(
        HOURLY, mu=3.5, staff_per_channel=2, security_ratio=0.5)
    req = staffing.headcount_requirement(counts, max_work_days=5)
    assert req['total'] > 0
    assert set(req['per_shift'].keys()) == {'早班', '中班', '晚班'}

    cov = staffing.coverage_check(counts, 100)
    assert cov['enough'] is True
    assert cov['gap'] == 0

    cov2 = staffing.coverage_check(counts, 1)
    assert cov2['enough'] is False
    assert cov2['gap'] > 0
