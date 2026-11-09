# -*- coding: utf-8 -*-
"""排队论通道调度引擎（对应需求规格 UC02：安检通道智能调度）。

采用 M/M/c 多服务台排队模型，通过 Erlang-C 公式计算：
- Pw  顾客需要等待的概率
- Wq  平均排队等待时间（分钟）
- W   平均逗留时间（分钟）= Wq + 1/μ
- Lq  平均队列长度（人）
- L   系统中平均顾客数（人）
- ρ   通道利用率 = λ / (c·μ)

并以「满足平均等待时间约束(≤阈值)下的最小通道数」为优化目标搜索最佳通道数，
同时兼顾利用率约束(默认 ≤85%)；支持分时段调度计划生成与 what-if 情景分析。

数值全部由确定性算法计算，符合需求报告「数值与大模型解耦」的约束。
"""
import math
import random


def erlang_b(a, c):
    """Erlang-B 损失概率 B(c, a) = (a^c/c!) / Σ_{k=0}^c (a^k/k!)。
    迭代计算避免阶乘溢出。a 为提供给强度(erlang)，c 为服务台数。
    """
    if c <= 0:
        return 1.0
    if a <= 0:
        return 0.0
    term = 1.0
    total = 1.0
    for k in range(1, c + 1):
        term *= a / k
        total += term
    return term / total


def erlang_c_pwait(c, a):
    """Erlang-C 等待概率（顾客需要排队的概率）。ρ = a/c。"""
    if c <= 0 or a <= 0:
        return 1.0
    rho = a / c
    if rho >= 1.0:
        return 1.0  # 系统不稳定，必然排队
    b = erlang_b(a, c)
    denom = b + (1.0 - rho) * (1.0 - b) * (1.0 / (1.0 - rho) - 1.0)  # 等价 (1-rho)·Σ_{0}^{c-1} a^k/k!
    # 更稳妥：直接用标准公式
    # S1 = Σ_{k=0}^{c-1} a^k/k! ; T = a^c/c! ; C = T / (T + (1-rho)·S1)
    s1 = 1.0
    t = 1.0
    for k in range(1, c):
        t *= a / k
        s1 += t
    term_c = t * (a / c)
    c_wait = term_c / (term_c + (1.0 - rho) * s1)
    return c_wait


def metrics(c, lam, mu, threshold=None):
    """给定通道数 c、到达率 λ(人/分钟)、服务率 μ(人/分钟)，返回全套性能指标。

    边界处理：λ=0 时等待时间为 0；ρ≥1 时系统不稳定，标记 unstable。
    """
    if lam <= 0:
        return {
            'channels': c, 'arrival': lam, 'service': mu,
            'rho': 0.0, 'pw': 0.0, 'wq': 0.0, 'w': 1.0 / mu if mu > 0 else 0.0,
            'lq': 0.0, 'l': 0.0, 'feasible': True, 'unstable': False,
        }
    a = lam / mu if mu > 0 else 0.0  # 提供给强度 erlang
    rho = a / c if c > 0 else 1.0
    if rho >= 1.0:
        return {
            'channels': c, 'arrival': lam, 'service': mu,
            'rho': rho, 'pw': 1.0, 'wq': float('inf'), 'w': float('inf'),
            'lq': float('inf'), 'l': float('inf'),
            'feasible': False, 'unstable': True,
        }
    pw = erlang_c_pwait(c, a)
    wq = pw / (c * mu - lam)        # Paix 公式（分钟）
    w = wq + (1.0 / mu if mu > 0 else 0.0)
    lq = lam * wq
    l = lam * w
    feasible = (threshold is None) or (wq <= threshold)
    return {
        'channels': c, 'arrival': lam, 'service': mu,
        'rho': rho, 'pw': pw, 'wq': wq, 'w': w, 'lq': lq, 'l': l,
        'feasible': feasible, 'unstable': False,
    }


def best_channel(lam, mu, threshold=15.0, c_max=20, rho_max=0.85):
    """搜索满足「等待时间 ≤ threshold 且 利用率 ≤ rho_max」的最小通道数。

    :param lam: 到达率（人/分钟）
    :param mu: 单通道服务率（人/分钟）
    :return: dict {channels, wq, rho, feasible, unstable, all_util_under_max}
    """
    if lam <= 0:
        return {'channels': 1, 'wq': 0.0, 'rho': 0.0, 'feasible': True,
                'unstable': False, 'all_util_under_max': True}
    best = None
    unstable_flag = False
    for c in range(1, c_max + 1):
        m = metrics(c, lam, mu, threshold)
        if m['unstable']:
            unstable_flag = True
            continue
        # 同时满足等待阈值与利用率上限
        if m['wq'] <= threshold and m['rho'] <= rho_max:
            best = m
            break
        # 若只差利用率约束，记录当前（用于「即便开满仍偏忙」提示）
        if best is None and m['wq'] <= threshold:
            best = m
    if best is None:
        # 遍历完仍无法满足（到达率过高），返回 c_max 的指标
        best = metrics(c_max, lam, mu, threshold)
        best['feasible'] = False
        best['unstable'] = unstable_flag
    best['all_util_under_max'] = best['rho'] <= rho_max
    return best


def avg_security_time(mu, as_seconds=True):
    """平均安检服务时间 = 1/μ（普通通道默认 15~20 秒/人）。"""
    if mu <= 0:
        return 0.0
    sec = (1.0 / mu) * 60.0
    return round(sec, 1) if as_seconds else round(1.0 / mu, 4)


def schedule_plan(hourly_passengers, mu, threshold=15.0, c_max=20, rho_max=0.85,
                  prep_minutes=10):
    """基于 24 小时客流量（人次/小时），按小时窗口生成分时段调度计划。

    返回 list[dict]，每项含时段、到达率、建议通道数、等待时间、利用率、备注。
    prep_minutes：增开通道需提前的准备时间（用于提示提前量）。
    """
    plan = []
    prev_c = None
    for h in range(24):
        flow = hourly_passengers[h] if h < len(hourly_passengers) else 0
        lam = flow / 60.0  # 人/分钟
        m = best_channel(lam, mu, threshold, c_max, rho_max)
        note = ''
        if prev_c is not None and m['channels'] > prev_c:
            note = '建议提前 %d 分钟增开 %d 条通道' % (prep_minutes, m['channels'] - prev_c)
        elif prev_c is not None and m['channels'] < prev_c:
            note = '可关闭 %d 条通道以节约人力' % (prev_c - m['channels'])
        if m['unstable']:
            note = '客流超过可用通道处理能力，建议增开通道或分流'
        plan.append({
            'hour': h,
            'time_slot': '%02d:00-%02d:00' % (h, (h + 1) % 24),
            'hourly_flow': flow,
            'arrival_per_min': round(lam, 3),
            'recommend_channels': m['channels'],
            'avg_wait_min': (round(m['wq'], 2) if math.isfinite(m['wq']) else None),
            'utilization': round(m['rho'] * 100, 1),
            'feasible': m['feasible'],
            'note': note,
        })
        prev_c = m['channels']
    return plan


def what_if(hourly_passengers, mu, c, factor=1.0, threshold=15.0):
    """情景分析：对给定通道数 c，调整客流（乘以 factor）后返回性能指标。"""
    out = []
    for h in range(24):
        flow = (hourly_passengers[h] if h < len(hourly_passengers) else 0) * factor
        lam = flow / 60.0
        m = metrics(c, lam, mu, threshold)
        out.append({
            'hour': h,
            'time_slot': '%02d:00-%02d:00' % (h, (h + 1) % 24),
            'hourly_flow': round(flow),
            'recommend_channels': m['channels'],
            'avg_wait_min': (round(m['wq'], 2) if math.isfinite(m['wq']) else None),
            'utilization': round(m['rho'] * 100, 1),
            'feasible': m['feasible'],
        })
    return out


# ---------------------------------------------------------------------------
# 扩展模型（UC02 算法深化）
#   1) SIPP 时变排队（含积压延续 lag）
#   2) Erlang-A：顾客不耐烦 / 放弃排队
#   3) M/M/c/K：候检区容量有限
#   4) 等待时间分布与服务水平 SL / 分位数
#   5) 成本最优：人力成本 + 旅客等待成本
#   6) 蒙特卡洛鲁棒性：到达率波动下的通道数
# ---------------------------------------------------------------------------


def sipp_plan(hourly_passengers, mu, threshold=15.0, c_max=20, rho_max=0.85,
              carryover=True, period_minutes=60.0):
    """SIPP（Stationary Independent Period by Period）时变排队调度。

    机场安检到达率在一天内剧烈波动，若用「全天一个平均 λ」求通道数，
    会严重低估高峰压力（高峰需 20 条、平峰只需 5 条，平均值却是 8 条）。
    SIPP 把一天切成若干时段，逐时段用稳态 M/M/c 求解，是机场排班的经典做法。

    本实现在标准 SIPP 之上增加了 **积压延续（carryover / lag）**：
    若上一时段服务能力不足，未消化完的旅客顺延到下一时段继续排队，
    从而体现高峰的「拖尾效应」——纯 SIPP 会低估下一时段压力，而 lag-SIPP 能捕捉。

    :param carryover: 是否启用积压延续；关闭即退化为标准 SIPP
    :return: list[dict]，逐时段的通道数/等待时间/利用率/积压量/备注
    """
    plan = []
    backlog = 0.0
    prev_c = None
    for h in range(24):
        flow = hourly_passengers[h] if h < len(hourly_passengers) else 0
        lam = flow / 60.0
        # 上一时段遗留积压在本时段内需要一并消化
        extra = (backlog / period_minutes) if carryover else 0.0
        m = best_channel(lam + extra, mu, threshold, c_max, rho_max)
        c = m['channels']

        capacity = c * mu * period_minutes            # 本时段可服务人数
        demand = lam * period_minutes + backlog       # 本时段需服务人数
        backlog = max(0.0, demand - capacity) if carryover else 0.0

        note = ''
        if prev_c is not None and c > prev_c:
            note = '建议提前增开 %d 条通道' % (c - prev_c)
        elif prev_c is not None and c < prev_c:
            note = '可关闭 %d 条通道以节约人力' % (prev_c - c)
        if m['unstable'] or backlog > 0:
            note = '本时段积压 %.0f 人，建议增开通道或对旅客分流' % backlog

        plan.append({
            'hour': h,
            'time_slot': '%02d:00-%02d:00' % (h, (h + 1) % 24),
            'hourly_flow': flow,
            'arrival_per_min': round(lam, 3),
            'carryover_people': round(backlog, 1),
            'recommend_channels': c,
            'avg_wait_min': (round(m['wq'], 2) if math.isfinite(m['wq']) else None),
            'utilization': round(m['rho'] * 100, 1),
            'feasible': m['feasible'],
            'note': note,
        })
        prev_c = c
    return plan


def erlang_a_metrics(c, lam, mu, theta, threshold=None, tol=1e-7, max_iter=100):
    """M/M/c+M（Erlang-A）：顾客具有指数耐心、等待过久会放弃的排队模型。

    标准 Erlang-C 假设顾客「无限耐心」，会高估队列长度与等待时间；
    真实安检场景中旅客会因赶飞机而放弃排队或改走急客通道，Erlang-A 更贴近现实。

    实现说明：精确的 Erlang-A 需要非完整 Gamma 函数（通常依赖 scipy）。
    这里采用 **不动点迭代近似**：以「放弃后的有效到达率」重新求解 Erlang-C，
    迭代至收敛。它数值稳定、无第三方依赖，且能正确刻画负反馈：
        越拥挤 → 等待越久 → 放弃越多 → 有效到达率下降 → 队列缩短。

    :param theta: 放弃率（1/分钟），即平均耐心 1/θ 分钟；θ=0 退化为 Erlang-C
    :return: dict，含 eff_arrival / abandon_prob / wq / rho 等
    """
    if lam <= 0:
        return {'channels': c, 'arrival': lam, 'eff_arrival': 0.0,
                'abandon_prob': 0.0, 'wq': 0.0, 'rho': 0.0,
                'feasible': True, 'unstable': False}
    if theta <= 0:
        m = metrics(c, lam, mu, threshold)
        m['eff_arrival'] = m['arrival']
        m['abandon_prob'] = 0.0
        return m

    eff = lam
    ab = 0.0
    for _ in range(max_iter):
        m = metrics(c, eff, mu, threshold)
        if math.isinf(m['wq']):
            # 需求超过服务能力：超出部分必然放弃，有效到达率被服务能力截断
            eff_new = min(lam, c * mu * 0.98)
            ab = max(0.0, 1.0 - eff_new / lam)
        else:
            # 等待 wq 分钟的旅客放弃比例：1 - exp(-θ·wq)
            ab = 1.0 - math.exp(-theta * m['wq'])
            eff_new = lam * (1.0 - ab)
        if abs(eff_new - eff) < tol:
            eff = eff_new
            break
        eff = eff_new

    m = metrics(c, eff, mu, threshold)
    return {
        'channels': c, 'arrival': lam, 'eff_arrival': round(eff, 4),
        'abandon_prob': round(ab, 4), 'wq': m['wq'], 'w': m['w'],
        'lq': m['lq'], 'l': m['l'], 'rho': m['rho'],
        'feasible': m['feasible'], 'unstable': m['unstable'],
    }


def mmck_metrics(c, lam, mu, K, threshold=None):
    """M/M/c/K：系统总容量受限为 K（含正在安检的人），候检区只能站 K-c 人。

    候检区面积有限时，队满后新到旅客会被劝阻或改道，此时「无限队列」的
    Erlang-C 会严重高估等待时间。本函数给出精确稳态解（闭式，无迭代）。

    :param K: 系统总容量（K >= c）；K <= c 时退化为无排队位的损失制
    :return: dict，含 blocking_prob（满员概率）/ eff_arrival / wq / lq
    """
    if c <= 0 or mu <= 0:
        return {'channels': c, 'blocking_prob': 1.0, 'eff_arrival': 0.0,
                'wq': float('inf'), 'lq': float('inf'), 'unstable': True}
    K = max(int(K), c)
    a = lam / mu if lam > 0 else 0.0

    # s1 = Σ_{n=0}^{c} a^n/n! ，循环结束时 t = a^c/c!
    s1 = 1.0
    t = 1.0
    for n in range(1, c + 1):
        t *= a / n
        s1 += t

    # 一次遍历同时累计：归一化分母 tail、平均队长 Lq、以及末项（用于 p_K）
    rho = a / c
    tail = 0.0
    lq = 0.0
    term = t
    for n in range(c + 1, K + 1):
        term *= rho                 # term = (a^c/c!)·ρ^(n-c)
        tail += term
        lq += (n - c) * term
    p0 = 1.0 / (s1 + tail)
    pk = (term if K > c else t) * p0   # p_K：系统满员概率
    lq *= p0

    eff = lam * (1.0 - pk)
    wq = (lq / eff) if eff > 0 else 0.0
    w = wq + (1.0 / mu)
    return {
        'channels': c, 'capacity': K, 'arrival': lam,
        'blocking_prob': round(pk, 6), 'eff_arrival': round(eff, 4),
        'lq': round(lq, 4), 'wq': round(wq, 4), 'w': round(w, 4),
        'rho': round(eff / (c * mu), 4) if c * mu > 0 else 1.0,
        'unstable': False,
        'feasible': (threshold is None) or (wq <= threshold),
    }


def prob_wait_exceeds(c, lam, mu, t):
    """P(Wq > t)：等待时间超过 t 分钟的概率（M/M/c 精确式）。

    条件于「需要排队」，等待时间服从参数 (c·μ - λ) 的指数分布，故
        P(Wq > t) = Pw · exp(-(c·μ - λ)·t)
    """
    if lam <= 0:
        return 0.0
    m = metrics(c, lam, mu)
    if m['unstable']:
        return 1.0
    return m['pw'] * math.exp(-(c * mu - lam) * t)


def service_level(c, lam, mu, t):
    """服务水平 SL = P(Wq <= t)：能在 t 分钟内开始安检的旅客比例。

    机场安检常用「SL: 95% 旅客在 10 分钟内完成安检」这类指标，比平均等待时间更有意义。
    """
    return 1.0 - prob_wait_exceeds(c, lam, mu, t)


def wait_quantile(c, lam, mu, p):
    """等待时间的 p 分位数 q，使 P(Wq <= q) = p（分钟）。

    例：wait_quantile(c, lam, mu, 0.95) 给出「95% 旅客的等待时间上界」。
    """
    if lam <= 0:
        return 0.0
    m = metrics(c, lam, mu)
    if m['unstable']:
        return float('inf')
    p = min(max(float(p), 0.0), 1.0)
    pw = m['pw']
    exceed = 1.0 - p
    if exceed >= pw:
        # 连 P(Wq>0)=pw 都达不到该 exceedance，说明分位数为 0（多数人无需排队）
        return 0.0
    q = -math.log(exceed / pw) / (c * mu - lam)
    return max(0.0, q)


def cost_optimal_channel(lam, mu, staff_per_channel=2, cost_staff_per_min=0.5,
                         cost_wait_per_min=0.3, threshold=15.0, c_max=20,
                         rho_max=0.85, enforce_constraints=True):
    """成本最优通道数：最小化「人力成本 + 旅客等待成本」之和。

    仅取「满足等待阈值的最小通道数」会忽视经济性：多开一条通道能显著缩短等待，
    但也要多付人力。这里显式建模两类成本：
        人力成本 = 通道数 × 每通道定员 × 单人每分钟成本
        等待成本 = 到达率 × 平均等待分钟 × 每分钟等待成本
    取总成本最小的通道数（可选要求同时满足等待阈值与利用率上限）。

    :return: dict {channels, total_cost, feasible, cost_curve}
    """
    curve = []
    best = None
    for c in range(1, c_max + 1):
        m = metrics(c, lam, mu, threshold)
        if m['unstable']:
            curve.append({'channels': c, 'total_cost': float('inf'),
                          'feasible': False, 'unstable': True})
            continue
        staff_cost = c * staff_per_channel * cost_staff_per_min
        wait_cost = lam * m['wq'] * cost_wait_per_min
        total = staff_cost + wait_cost
        feasible = (m['wq'] <= threshold) and (m['rho'] <= rho_max)
        item = {
            'channels': c,
            'staff_cost': round(staff_cost, 2),
            'wait_cost': round(wait_cost, 2),
            'total_cost': round(total, 2),
            'wq': round(m['wq'], 2),
            'utilization': round(m['rho'] * 100, 1),
            'feasible': feasible,
        }
        curve.append(item)
        if enforce_constraints and not feasible:
            continue
        if best is None or total < best['total_cost']:
            best = item

    if best is None:
        finite = [x for x in curve if x.get('total_cost') != float('inf')]
        best = dict(min(finite, key=lambda x: x['total_cost'])) if finite else dict(curve[-1])
        best['feasible'] = False
    return {
        'channels': best['channels'],
        'total_cost': best.get('total_cost'),
        'feasible': best.get('feasible', True),
        'cost_curve': curve,
    }


def monte_carlo_wait(lam_base, mu, c, n=1000, cv=0.15, threshold=15.0, seed=42):
    """到达率不确定时的等待时间分布（蒙特卡洛）。

    客流量预测本身有误差，用单一 λ 求出的通道数在真实到达率偏高时就会失效。
    这里把 λ 视为服从 N(λ_base, cv·λ_base) 的随机变量，重采样 n 次，
    统计等待时间的均值/分位数以及「满足等待阈值」的概率（服务概率）。
    """
    rng = random.Random(seed)
    wqs = []
    for _ in range(int(n)):
        lam = rng.gauss(lam_base, cv * lam_base)
        if lam <= 0:
            lam = max(1e-6, lam_base * 0.01)
        wqs.append(metrics(c, lam, mu)['wq'])

    finite = sorted(w for w in wqs if math.isfinite(w))
    hit = sum(1 for w in finite if w <= threshold)
    idx = int(0.95 * (len(finite) - 1)) if finite else 0
    return {
        'channels': c,
        'n': len(wqs),
        'service_prob': round(hit / float(len(wqs)), 4) if wqs else 0.0,
        'wq_mean': (round(sum(finite) / len(finite), 3) if finite else None),
        'wq_p95': (round(finite[idx], 3) if finite else None),
        'unstable_ratio': round(1.0 - len(finite) / float(len(wqs)), 4) if wqs else 0.0,
    }


def robust_channel(lam_base, mu, threshold=15.0, c_max=20, cv=0.15,
                   target_service=0.9, n=500, seed=42, rho_max=0.85):
    """鲁棒通道数：在到达率波动下，仍有 target_service 概率满足等待阈值的最小通道数。

    这是对 best_channel 的「可靠性升级」——best_channel 只保证名义 λ 下达标，
    robust_channel 保证在 λ 波动的 target_service 比例情形下仍达标。
    """
    for c in range(1, c_max + 1):
        r = monte_carlo_wait(lam_base, mu, c, n=n, cv=cv, threshold=threshold, seed=seed)
        m = metrics(c, lam_base, mu, threshold)
        if r['service_prob'] >= target_service and m['rho'] <= rho_max:
            return {'channels': c, 'service_prob': r['service_prob'],
                    'wq_p95': r['wq_p95'], 'utilization': round(m['rho'] * 100, 1),
                    'feasible': True, 'cv': cv}
    last = monte_carlo_wait(lam_base, mu, c_max, n=n, cv=cv, threshold=threshold, seed=seed)
    return {'channels': c_max, 'service_prob': last['service_prob'],
            'wq_p95': last['wq_p95'], 'utilization': None,
            'feasible': False, 'cv': cv}


if __name__ == '__main__':
    # 自测：高峰小时 17226 人次 -> λ=287.1 人/分钟；μ=3.5
    lam = 17226 / 60.0
    m = best_channel(lam, 3.5, threshold=15, c_max=20)
    print('高峰到达率 %.1f 人/分钟 | 最佳通道数=%d | Wq=%.2f 分钟 | 利用率=%.1f%%' %
          (lam, m['channels'], m['wq'], m['rho'] * 100))
    plan = schedule_plan([0]*7 + [17226]*2 + [8000]*4 + [5000]*6 + [3000]*5, 3.5)
    print('调度计划（节选）:')
    for it in plan:
        if it['hourly_flow'] > 0:
            print('  ', it['time_slot'], '客流', it['hourly_flow'],
                  '建议通道', it['recommend_channels'], '等待', it['avg_wait_min'], '分')
    print('平均安检服务时间(秒):', avg_security_time(3.5))
