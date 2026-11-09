# -*- coding: utf-8 -*-
"""规则专家系统决策建议引擎（advisor.py / UC03）单元测试。

覆盖：候选方案生成、多维打分、规则裁决、夜间/高峰/低谷三类典型场景。
"""
from control.subsystem import advisor
def test_build_scenario_fields():
    s = advisor.build_scenario('测试', 8, 20.0, 3.5)
    assert s['channels'] == 8
    assert s['staff'] == 16
    assert 'wq' in s and 'utilization' in s and 'total_cost' in s
    assert 0.0 <= s['service_level'] <= 1.0


def test_generate_candidates_returns_options():
    cands = advisor.generate_candidates(lam=25.0, mu=3.5, c_current=3, c_available=10)
    assert len(cands) >= 1
    for c in cands:
        assert c['channels'] >= 1
        assert c['name']


def test_advise_peak_recommends_more_channels():
    a = advisor.advise(lam=25.0, mu=3.5, c_current=3, c_available=10, threshold=15)
    assert a['recommendChannels'] > 3
    assert any('R3' in r for r in a['rules'])      # 需求上升规则应触发
    assert '建议再开放' in a['suggestion']


def test_advise_low_demand_recommends_fewer():
    a = advisor.advise(lam=2.0, mu=3.5, c_current=8, c_available=10, threshold=15)
    assert a['recommendChannels'] < 8
    assert any('R4' in r for r in a['rules'])      # 需求回落规则应触发


def test_advise_night_keeps_minimum():
    a = advisor.advise(lam=0.0, mu=3.5, c_current=5, c_available=10, threshold=15)
    assert a['recommendChannels'] == 1
    assert any('R1' in r for r in a['rules'])


def test_advise_returns_scenarios_and_score():
    a = advisor.advise(lam=20.0, mu=3.5, c_current=5, c_available=12, threshold=15)
    assert len(a['scenarios']) >= 1
    assert 0.0 <= a['score'] <= 1.0
    for s in a['scenarios']:
        assert 'score' in s and 'channels' in s


def test_suggestion_includes_channel_and_speed_info():
    a = advisor.advise(lam=25.0, mu=3.5, c_current=3, c_available=10,
                       channel_info='当前共有10个通道。', speed_info='该时段安检速度3.5人/分钟',
                       hour_slot='当前时段（8:00-9:00）')
    assert '当前共有10个通道' in a['suggestion']
    assert '该时段安检速度3.5人/分钟' in a['suggestion']
    assert '当前时段（8:00-9:00）' in a['suggestion']
