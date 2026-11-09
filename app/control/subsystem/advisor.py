# -*- coding: utf-8 -*-
"""智能决策建议引擎（对应需求规格 UC03：大模型智能决策建议）。

设计说明
--------
UC03 原先是「把客流数据丢给大模型，让它吐一句建议」。这种做法有两个问题：
  1. 联网失败即功能不可用（与 /simulate 曾经的「网络错误」同源）；
  2. 建议不可复现、不可解释，无法说明「为什么开 7 条而不是 6 条」。

本模块改为 **规则专家系统（Rule-based Expert System）**：
    「生成候选方案 → 多维度打分 → 规则裁决 → 输出带依据的建议」
全部由确定性算法完成，结果可复现、可解释，且天然离线可用。

打分维度与权重（可在 SCENARIO_WEIGHTS 调整）：
    wait        等待时间（旅客体验，权重最高）
    cost        人力成本 + 等待成本（经济性）
    utilization 通道利用率（过低=闲置浪费，过高=没有缓冲余量）
    robustness  抗波动能力（到达率不确定下仍达标的概率）

规则裁决（R1~R8）用于在打分之外捕获运营常识，并把触发过的规则写入
返回结果，构成可追溯的决策依据链。
"""
import math

from . import queuing

# 打分权重（四项之和为 1）
SCENARIO_WEIGHTS = {
    'wait': 0.45,
    'cost': 0.25,
    'utilization': 0.20,
    'robustness': 0.10,
}

# 利用率的理想区间：低于下限算闲置，高于上限算没有缓冲
UTIL_IDEAL_LOW = 0.60
UTIL_IDEAL_HIGH = 0.85


def build_scenario(name, channels, lam, mu, staff_per_channel=2, threshold=15.0,
                   rho_max=0.85, cost_staff_per_min=0.5, cost_wait_per_min=0.3):
    """构造一个候选调度方案的完整画像（排队指标 + 成本）。

    :param name: 方案名（保守 / 均衡 / 激进）
    :param channels: 该方案开放的通道数
    :return: dict
    """
    channels = max(1, int(channels))
    m = queuing.metrics(channels, lam, mu, threshold)

    wq = m['wq']
    if not math.isfinite(wq):
        # 系统不稳定（需求超过服务能力）时等待时间理论上无界，无法给出真实数值。
        # 这里沿用系统原有口径「阈值的 2 倍」作为占位值，既保证可展示，
        # 又能在打分中把该方案排在可行方案之后。
        wq = threshold * 2.0
    w = m['w']
    if not math.isfinite(w):
        w = wq + (1.0 / mu if mu > 0 else 0.0)

    staff = channels * staff_per_channel
    staff_cost = staff * cost_staff_per_min
    wait_cost = lam * wq * cost_wait_per_min
    sl = queuing.service_level(channels, lam, mu, threshold) if lam > 0 else 1.0

    return {
        'name': name,
        'channels': channels,
        'staff': staff,
        'wq': round(wq, 2),
        'w': round(w, 2),
        # 利用率封顶 99.9%：需求远超能力时 ρ>1（如 17 倍过载会显示 1714%），
        # 这样的数字对用户没有意义，封顶后统一表达为「已饱和」。
        'utilization': round(min(99.9, m['rho'] * 100), 1),
        'feasible': bool(m['feasible'] and m['rho'] <= rho_max),
        'unstable': bool(m['unstable']),
        'service_level': round(sl, 4),
        'staff_cost': round(staff_cost, 2),
        'wait_cost': round(wait_cost, 2),
        'total_cost': round(staff_cost + wait_cost, 2),
    }


def score_scenario(s, all_costs=None, threshold=15.0):
    """对候选方案做多维度打分（各维度归一化到 [0,1]，1 为最优）。"""
    # 1) 等待时间：达标即高分，越接近阈值越扣分；超标快速衰减
    wq = s['wq']
    if wq <= threshold:
        wait = 1.0 - 0.3 * (wq / threshold) if threshold > 0 else 1.0
    else:
        wait = max(0.0, 1.0 - (wq - threshold) / (2.0 * threshold))

    # 2) 成本：在候选集内做相对归一化
    if all_costs:
        cmin, cmax = min(all_costs), max(all_costs)
        cost = 1.0 if cmax <= cmin else 1.0 - (s['total_cost'] - cmin) / (cmax - cmin)
    else:
        cost = 0.5

    # 3) 利用率：理想区间内满分，越偏离越扣分
    u = s['utilization'] / 100.0
    if u < UTIL_IDEAL_LOW:
        util = max(0.0, 1.0 - (UTIL_IDEAL_LOW - u) / UTIL_IDEAL_LOW)
    elif u > UTIL_IDEAL_HIGH:
        util = max(0.0, 1.0 - (u - UTIL_IDEAL_HIGH) / (1.0 - UTIL_IDEAL_HIGH))
    else:
        util = 1.0

    # 4) 鲁棒性：以「等待时间达标的服务水平」作为代理指标
    rob = s.get('service_level', 0.0)

    w = SCENARIO_WEIGHTS
    total = (w['wait'] * wait + w['cost'] * cost
             + w['utilization'] * util + w['robustness'] * rob)
    if not s['feasible']:
        total *= 0.6  # 不可行方案施加惩罚，但不完全排除（便于在全员过载时仍给出次优解）

    return {
        'wait': round(wait, 3), 'cost': round(cost, 3),
        'utilization': round(util, 3), 'robustness': round(rob, 3),
        'total': round(total, 4),
    }


def generate_candidates(lam, mu, c_current, c_available, threshold=15.0,
                        staff_per_channel=2, rho_max=0.85, cv=0.15):
    """生成三个候选方案：保守（维持现状）/ 均衡（排队论最小可行）/ 激进（抗波动）。"""
    c_available = max(1, int(c_available))
    c_current = max(1, min(int(c_current or 1), c_available))

    if lam <= 0:
        # 夜间/无客流：只保留最低值守
        only = build_scenario('最低值守', 1, lam, mu, staff_per_channel, threshold, rho_max)
        return [only]

    balanced = queuing.best_channel(lam, mu, threshold, c_available, rho_max)
    robust = queuing.robust_channel(lam, mu, threshold, c_available, cv=cv,
                                    target_service=0.9, n=200)

    c_bal = max(1, min(balanced['channels'], c_available))
    c_agg = max(c_bal, min(robust['channels'], c_available))
    c_con = c_current

    cand_defs = [
        ('保守（维持现状）', c_con),
        ('均衡（排队论最小可行）', c_bal),
        ('激进（抗客流波动）', c_agg),
    ]
    # 去重：通道数相同的方案只保留一个（保留更靠前的命名）
    seen = set()
    cands = []
    for name, c in cand_defs:
        if c in seen:
            continue
        seen.add(c)
        cands.append(build_scenario(name, c, lam, mu, staff_per_channel, threshold, rho_max))
    return cands


def apply_rules(best, c_current, c_available, lam, mu, threshold=15.0, cv=0.15):
    """规则裁决：在打分之外补充运营常识，并记录触发的规则（可解释性依据链）。"""
    rules = []
    c = best['channels']

    if lam <= 0:
        rules.append('R1 无客流时段：仅保留最低值守通道，避免人力空转')
        return rules

    if best['unstable'] or (not best['feasible'] and c >= c_available):
        rules.append('R2 能力不足：开满 %d 条可用通道仍无法满足 %.0f 分钟等待阈值，'
                     '建议增开通道或对旅客分流' % (c_available, threshold))
    if c > c_current:
        rules.append('R3 需求上升：建议增开 %d 条通道（%d → %d）' % (c - c_current, c_current, c))
    elif c < c_current:
        rules.append('R4 需求回落：建议关闭 %d 条通道以节约人力（%d → %d）'
                     % (c_current - c, c_current, c))
    else:
        rules.append('R5 供需匹配：当前开放 %d 条通道配置合理，无需调整' % c)

    u = best['utilization'] / 100.0
    if u < UTIL_IDEAL_LOW:
        rules.append('R6 利用率偏低（%.0f%%）：可考虑合并通道、集中人力' % best['utilization'])
    if u > UTIL_IDEAL_HIGH:
        rules.append('R7 利用率偏高（%.0f%%）：缺少缓冲余量，建议保留备用通道' % best['utilization'])
    if cv >= 0.15 and best['channels'] > 1:
        rules.append('R8 波动风险（变异系数 %.2f）：已按鲁棒性要求预留余量' % cv)
    if best['wq'] <= threshold * 0.5:
        rules.append('R9 体验富余：平均等待 %.1f 分钟，远优于 %.0f 分钟阈值'
                     % (best['wq'], threshold))

    return rules


def advise(lam, mu, c_current, c_available, threshold=15.0, staff_per_channel=2,
           rho_max=0.85, cv=0.15, hour_slot=None, channel_info=None, speed_info=None):
    """主入口：给出通道开放的决策建议。

    :param lam: 该时段到达率（人/分钟）
    :param mu: 单通道服务率（人/分钟）
    :param c_current: 当前已开放通道数
    :param c_available: 可用通道总数（排除维护中）
    :return: dict，含 recommendChannels / avgWaitTime / utilization / suggestion /
             scenarios（候选方案及打分）/ rules（触发规则）/ best_name
    """
    cands = generate_candidates(lam, mu, c_current, c_available, threshold,
                                staff_per_channel, rho_max, cv)
    costs = [c['total_cost'] for c in cands]
    for c in cands:
        c['score'] = score_scenario(c, costs, threshold)

    best = max(cands, key=lambda x: x['score']['total'])
    rules = apply_rules(best, c_current, c_available, lam, mu, threshold, cv)

    # 组装建议文案（保持与页面原有展示一致的句式与信息要素）
    slot = hour_slot or '当前时段'
    ci = channel_info if channel_info else ''
    tail = ('，' + speed_info) if speed_info else ''
    c = best['channels']
    if best['unstable'] or (not best['feasible'] and c >= c_available):
        action = ('客流量远超可用通道处理能力，即便开放全部 %d 条可用通道仍难以满足 %.0f 分钟'
                  '等待要求，建议增开通道或对旅客分流' % (c_available, threshold))
    elif c > c_current:
        action = '客流量较大，建议再开放 %d 个通道，使开放通道数达到 %d 个' % (c - c_current, c)
    elif c < c_current:
        action = '客流量较小，建议关闭 %d 个通道，使开放通道数降至 %d 个以节约人力' % (c_current - c, c)
    else:
        action = '客流量适中，当前开放 %d 个通道配置合理，无需调整' % c

    suggestion = ('%s%s%s%s。预计平均等待时间 %.1f 分钟，通道利用率 %.1f%%。'
                  '（决策依据：%s）' % (ci, slot, action, tail,
                                  best['wq'], best['utilization'], '；'.join(rules)))

    return {
        'recommendChannels': c,
        'avgWaitTime': round(best['wq'], 1),
        'utilization': best['utilization'],
        'suggestion': suggestion,
        'best_name': best['name'],
        'score': best['score']['total'],
        'rules': rules,
        'scenarios': [
            {'name': c['name'], 'channels': c['channels'], 'staff': c['staff'],
             'wq': c['wq'], 'utilization': c['utilization'],
             'total_cost': c['total_cost'], 'feasible': c['feasible'],
             'score': c['score']['total']}
            for c in cands
        ],
    }
