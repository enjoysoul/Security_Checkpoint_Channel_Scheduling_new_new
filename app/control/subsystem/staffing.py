# -*- coding: utf-8 -*-
"""安检人员定员引擎（对应需求规格 UC04：人员智能排班的「定员」环节）。

解决的问题：给定一个预测日的 24 小时客流分布，**确定性**地推算出
早班 / 中班 / 晚班 各自每天需要多少人，再交给遗传算法排班引擎做「谁上哪班」的分配。

设计原则（与 /simulate 一致，符合需求报告「数值与大模型解耦」的约束）：
    全部数值由确定性算法计算，不调用任何大模型接口。

算法链路：
    1. 班次窗口切分：把 24 小时客流切到三个班次窗口（晚班跨零点，需环绕处理）。
    2. 代表性到达率：窗口内各小时到达率按「高峰加权」合成一个代表值，
       兼顾高峰压力与低谷闲置（默认 0.7×峰值 + 0.3×均值）。
    3. 通道数求解：以该到达率为输入，用排队论（Erlang-C）求满足
       平均等待时间阈值所需的最少通道数（复用 queuing.best_channel）。
    4. 定员换算：通道数 × 每通道定员，再叠加备勤系数与周末系数，最后限幅。
    5. 编制校验：按「每人每周最多出勤 max_work_days 天」反推需要的总编制人数，
       用于判断在岗人员是否够用。

与旧实现的差异：
    旧代码把「每班人数」交给豆包大模型生成（联网失败即整条链路不可用），
    失败回退时用 `flow / (60 * avg_speed)` 的粗略吞吐法，完全没有考虑排队波动，
    高峰时段会系统性地低估人手。本模块用 Erlang-C 显式建模排队等待，
    并保留完整的可解释中间量（通道数、到达率、等待时间）。
"""
import math

from . import queuing

# 班次窗口（起始小时, 结束小时），结束小时不取（半开区间），晚班跨越零点
SHIFT_WINDOWS = {
    '早班': (6, 14),
    '中班': (14, 22),
    '晚班': (22, 6),
}

# 班次时间文案（与页面展示保持一致，页面不改故此处仅作说明/复用）
SHIFT_TIMES = {
    '早班': '06:00-14:00',
    '中班': '14:00-22:00',
    '晚班': '22:00-06:00',
}

SHIFT_ORDER = ('早班', '中班', '晚班')


def shift_hours(start, end):
    """返回班次窗口覆盖的小时下标列表，支持跨零点环绕。

    例：shift_hours(22, 6) -> [22, 23, 0, 1, 2, 3, 4, 5]
    """
    hours = []
    h = start
    while h != end:
        hours.append(h % 24)
        h = (h + 1) % 24
    return hours


def window_flow(hourly_flow, hours):
    """统计窗口内小时客流：合计、峰值、均值。

    :param hourly_flow: 24 小时客流（人次/小时）
    :param hours: 窗口覆盖的小时下标
    :return: dict {total, peak, mean, n}
    """
    vals = []
    for h in hours:
        if h < len(hourly_flow):
            try:
                vals.append(float(hourly_flow[h]))
            except (TypeError, ValueError):
                vals.append(0.0)
        else:
            vals.append(0.0)
    if not vals:
        return {'total': 0.0, 'peak': 0.0, 'mean': 0.0, 'n': 0}
    return {
        'total': sum(vals),
        'peak': max(vals),
        'mean': sum(vals) / len(vals),
        'n': len(vals),
    }


def representative_arrival_per_min(hourly_flow, hours, mode='peak_weighted', alpha=0.7):
    """把窗口内多个小时的客流合成为一个「代表性到达率」（人/分钟）。

    :param mode: 'peak_weighted' 高峰加权（默认，兼顾高峰压力与低谷闲置）
                 'peak'        纯峰值（最保守，人手最充裕）
                 'mean'        纯均值（最激进，人手最省）
    :param alpha: peak_weighted 模式下峰值所占权重，其余给均值
    :return: 到达率 λ（人/分钟）
    """
    stat = window_flow(hourly_flow, hours)
    if stat['n'] == 0:
        return 0.0
    if mode == 'peak':
        hourly = stat['peak']
    elif mode == 'mean':
        hourly = stat['mean']
    else:  # peak_weighted
        a = min(1.0, max(0.0, alpha))
        hourly = a * stat['peak'] + (1.0 - a) * stat['mean']
    return hourly / 60.0


def required_channels(lam, mu, threshold=15.0, c_max=20, rho_max=0.85):
    """给定到达率，求满足等待阈值的最小通道数（复用排队论引擎）。

    :return: dict，至少含 channels / wq / rho / feasible
    """
    if mu <= 0:
        mu = 3.5
    return queuing.best_channel(lam, mu, threshold, c_max, rho_max)


def staff_for_channels(channels, staff_per_channel, reserve_factor=0.0):
    """通道数 -> 人数：严格等于「开放通道数 × 每通道配置人员数」。

    reserve_factor 保留参数位以兼容旧调用，默认 0（不再额外上浮）。
    """
    base = float(channels) * float(staff_per_channel)
    return int(math.ceil(base * (1.0 + max(0.0, reserve_factor))))


def day_factor(day_idx, weekend_factor=1.15, holiday_days=None, holiday_factor=1.25):
    """返回某天（0=周一）的定员放大系数：周末/节假日客流更高，需加人。"""
    if holiday_days and day_idx in holiday_days:
        return holiday_factor
    if day_idx >= 5:  # 周六、周日
        return weekend_factor
    return 1.0


def clamp_staff(n, min_staff=2, max_staff=100000):
    """人数限幅，保证既不低于最低值守要求，也不超过单班人力上限。"""
    return int(max(min_staff, min(max_staff, n)))


def to_security_flow(hourly_flow, security_ratio=0.5):
    """把「旅客吞吐量客流」折算为「需要通过安检的客流」。

    安检通道只服务出港旅客（到达旅客不经过安检），因此定员/通道配置应以
    出港客流为输入；若直接拿总吞吐量计算，会把需求放大约一倍。

    :param security_ratio: 出港旅客占比（默认 0.5，即进出港各半）
    :return: 折算后的 24 小时安检客流（人次/小时）
    """
    try:
        r = float(security_ratio)
    except (TypeError, ValueError):
        r = 0.5
    if r <= 0 or r > 1.0:
        r = 0.5
    return [float(v or 0) * r for v in (hourly_flow or [])]


def compute_shift_requirements(hourly_flow, mu=3.5, staff_per_channel=2,
                               threshold=15.0, c_max=20, weekend_factor=1.15,
                               reserve_factor=0.0, min_staff=2, max_staff=100000,
                               arrival_mode='peak_weighted', holiday_days=None,
                               security_ratio=None):
    """计算一周（7 天）三个班次各自的定员人数矩阵。

    :param hourly_flow: 24 小时客流（人次/小时）
    :param security_ratio: 若给出，则先把客流折算为安检客流（出港占比）再计算
    :param mu: 单人安检服务率（人/分钟）
    :param staff_per_channel: 每条通道配置人数
    :param threshold: 平均等待时间阈值（分钟）
    :param c_max: 最大可开放通道数
    :param weekend_factor: 周末放大系数
    :param reserve_factor: 备勤系数（覆盖请假/培训/突发）
    :param min_staff / max_staff: 单班人数上下限
    :param arrival_mode: 代表性到达率合成方式
    :return: (counts, detail)
             counts: {'早班': [7 个整数], '中班': [...], '晚班': [...]}
             detail: 每个班次的中间量（到达率/通道数/等待时间/利用率），便于可解释性展示
    """
    if isinstance(hourly_flow, str):
        import json
        hourly_flow = json.loads(hourly_flow)
    hourly_flow = list(hourly_flow or [])

    # 可选：折算为安检客流（出港旅客），避免用总吞吐量高估需求
    if security_ratio is not None:
        hourly_flow = to_security_flow(hourly_flow, security_ratio)

    counts = {}
    detail = {}

    for shift in SHIFT_ORDER:
        start, end = SHIFT_WINDOWS[shift]
        hours = shift_hours(start, end)
        lam = representative_arrival_per_min(hourly_flow, hours, arrival_mode)
        bc = required_channels(lam, mu, threshold, c_max)
        base = staff_for_channels(bc['channels'], staff_per_channel, reserve_factor)

        per_day = []
        for d in range(7):
            n = base * day_factor(d, weekend_factor, holiday_days)
            per_day.append(clamp_staff(int(round(n)), min_staff, max_staff))
        counts[shift] = per_day

        detail[shift] = {
            'window': SHIFT_TIMES[shift],
            'hours': hours,
            'arrival_per_min': round(lam, 3),
            'channels': bc['channels'],
            'feasible': bc['feasible'],
            'avg_wait_min': (round(bc['wq'], 2) if math.isfinite(bc['wq']) else None),
            'utilization': round(bc['rho'] * 100, 1),
            'base_staff': base,
            'per_day': list(per_day),
        }

    return counts, detail


def headcount_requirement(counts, max_work_days=5):
    """由定员矩阵反推「需要的总编制人数」。

    每人每周最多出勤 max_work_days 天，因此某班次一周总需求工时（人·天）
    需要 ceil(total / max_work_days) 个人来承担。三个班次互不串岗，
    故取三班所需人数之和作为总编制下限。
    """
    total_people = 0
    per_shift = {}
    for shift in SHIFT_ORDER:
        week_demand = sum(counts.get(shift, []) or [])
        need = int(math.ceil(week_demand / float(max_work_days))) if max_work_days > 0 else week_demand
        per_shift[shift] = {'week_demand': week_demand, 'headcount': need}
        total_people += need
    return {'per_shift': per_shift, 'total': total_people, 'max_work_days': max_work_days}


def headcount_plan(hourly_flow, mu=3.5, staff_per_channel=2, threshold=15.0,
                   c_max=20, weekend_factor=1.15, reserve_factor=0.0,
                   min_staff=2, max_staff=100000, arrival_mode='peak_weighted',
                   security_ratio=None, max_work_days=5):
    """由 24 小时客流（其源头是机场年吞吐量）直接给出完整的「编制方案」。

    完整链路：
        年吞吐量 -> 24 小时客流 -> 各班次所需通道数 -> 各班次每班人数 -> 需求总编制人数

    这是「根据所选机场年客流量反算应配总人数」的统一入口。关键在于：
    **人数是算出来的结果，而不是给定的输入**——先用排队论按客流定员，
    再按「每人每周最多出勤 max_work_days 天」把一周班次数折算为需要的**人数**，
    这样排班才不会脱离机场的实际规模（否则大型枢纽也只能排出 10 个人的班表）。

    :return: dict {counts: 各班次每天人数, shifts: 各班次中间量, headcount: 编制需求}
    """
    counts, detail = compute_shift_requirements(
        hourly_flow,
        mu=mu,
        staff_per_channel=staff_per_channel,
        threshold=threshold,
        c_max=c_max,
        weekend_factor=weekend_factor,
        reserve_factor=reserve_factor,
        min_staff=min_staff,
        max_staff=max_staff,
        arrival_mode=arrival_mode,
        security_ratio=security_ratio,
    )
    return {
        'counts': counts,
        'shifts': detail,
        'headcount': headcount_requirement(counts, max_work_days),
    }


def coverage_check(counts, available_staff, max_work_days=5):
    """校验在岗人员是否能够满足定员需求。

    :param available_staff: 在岗人数（int）或人员列表
    :return: dict {required, available, gap, enough, message}
    """
    if isinstance(available_staff, (list, tuple)):
        available = len(available_staff)
    else:
        available = int(available_staff or 0)
    req = headcount_requirement(counts, max_work_days)
    required = req['total']
    gap = max(0, required - available)
    return {
        'required': required,
        'available': available,
        'gap': gap,
        'enough': gap == 0,
        'per_shift': req['per_shift'],
        'message': ('在岗人员充足' if gap == 0
                    else f'编制缺口 {gap} 人（需 {required} 人，在岗 {available} 人）'),
    }


def assign_names(counts, staff_names):
    """根据各(班次,星期)所需人数生成排班：轮转分配、避免同一天重复。
    counts: {'早班':[7],'中班':[7],'晚班':[7]}（整数人数）。
    返回同结构，每项 {'count':int,'names':[...]}。"""
    result = {'早班': [], '中班': [], '晚班': []}
    if not staff_names:
        for shift in result:
            result[shift] = [{'count': c, 'names': []} for c in counts.get(shift, [0] * 7)]
        return result
    n = len(staff_names)
    ptr = 0
    used = [set() for _ in range(7)]  # 每天已用人员
    for shift in ['早班', '中班', '晚班']:
        day_counts = counts.get(shift, [0] * 7)
        for day in range(7):
            cnt = int(day_counts[day]) if day < len(day_counts) else 0
            cnt = max(0, cnt)
            names = []
            guard = 0
            while len(names) < cnt and guard < cnt * 4 + 20:
                name = staff_names[ptr % n]
                ptr += 1
                if name in used[day]:
                    guard += 1
                    continue
                names.append(name)
                used[day].add(name)
                guard = 0
            # 人员不足时允许同日复用，保证人数凑齐
            while len(names) < cnt:
                names.append(staff_names[ptr % n])
                ptr += 1
            result[shift].append({'count': cnt, 'names': names})
    return result
