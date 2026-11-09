# -*- coding: utf-8 -*-
"""遗传算法排班优化单元测试（纯标准库）。"""
from control.subsystem import scheduling_opt as so
def _staff_rows(n=10):
    return [{'staff_id': i + 1, 'name': f'员工{i + 1}'} for i in range(n)]


def test_build_roster_shape():
    staff = _staff_rows(10)
    # 每天每班约 2 人，7 天 3 班 = 42 个需求
    counts = {'早班': [2] * 7, '中班': [2] * 7, '晚班': [2] * 7}
    res = so.build_roster(staff, counts, generations=20, pop_size=30, seed=1)
    assert 'roster' in res and 'metrics' in res
    roster = res['roster']
    assert set(roster.keys()) == {'早班', '中班', '晚班'}
    # 早班应返回 7 天
    assert len(roster['早班']) == 7


def test_build_roster_coverage():
    """每位被排人员应满足当天的人数需求（软约束下允许少量冲突，但不应整体失配）。"""
    staff = _staff_rows(12)
    counts = {'早班': [3] * 7, '中班': [2] * 7, '晚班': [1] * 7}
    res = so.build_roster(staff, counts, generations=40, pop_size=40, seed=7)
    roster = res['roster']
    total_demand = sum(counts['早班']) + sum(counts['中班']) + sum(counts['晚班'])
    assigned = sum(len(d['names']) for sh in roster.values() for d in sh)
    # 总需求应被满足（GA 优先满足硬覆盖约束）
    assert assigned == total_demand


def test_conflicts_report_runs():
    metrics = {'hard_conflicts': 0, 'soft_conflicts': 2, 'score': 88,
               'conflicts': {'consecutive': 1, 'night': 1},
               'max_consecutive': 4, 'max_night_week': 2}
    rep = so.conflicts_report(metrics)
    assert isinstance(rep, list)
    # 有软冲突时应生成可读说明
    assert len(rep) >= 2


def test_build_roster_metrics_shape():
    staff = _staff_rows(10)
    counts = {'早班': [2] * 7, '中班': [2] * 7, '晚班': [2] * 7}
    res = so.build_roster(staff, counts, generations=20, pop_size=30, seed=3)
    m = res['metrics']
    assert 'score' in m and 'hard_conflicts' in m and 'penalty' in m
    assert 'conflicts' in m


def test_deterministic_with_seed():
    staff = _staff_rows(8)
    counts = {'早班': [2] * 7, '中班': [2] * 7, '晚班': [2] * 7}
    r1 = so.build_roster(staff, counts, generations=15, pop_size=20, seed=99)['metrics']
    r2 = so.build_roster(staff, counts, generations=15, pop_size=20, seed=99)['metrics']
    assert r1['score'] == r2['score']


def test_understaffed_allocates_all_shifts():
    """回归：人手不足时三个班次都应分到人，不得出现「只有早班有人」的退化排班。

    历史缺陷：染色体初始化把可分配的人全部塞进第一个班次，修复阶段再按
    「一人一天一班」去重，结果中班/晚班被清空为 0 人。
    """
    staff = _staff_rows(10)
    counts = {'早班': [15] * 7, '中班': [15] * 7, '晚班': [15] * 7}
    res = so.build_roster(staff, counts, generations=30, pop_size=30, seed=7)
    roster = res['roster']
    for sh in ('早班', '中班', '晚班'):
        assert all(d['count'] > 0 for d in roster[sh]), f'{sh} 出现 0 人的班次'

    # 同一天内各班次人员不得重复（一人一天最多一个班次）
    for day in range(7):
        names = []
        for sh in ('早班', '中班', '晚班'):
            names += roster[sh][day]['names']
        assert len(names) == len(set(names))

    # 人手不足应如实上报缺口，而不是伪装成已满足
    assert res['metrics']['objectives']['demand_shortfall'] > 0
