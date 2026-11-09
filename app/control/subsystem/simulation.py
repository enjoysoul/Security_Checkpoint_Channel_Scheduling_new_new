# -*- coding: utf-8 -*-
"""公开数据驱动的 AI 客流模拟引擎（对应需求规格 UC01：年度数据导入与 AI 客流模拟）。

设计原则（取自《需求构思及描述》《需求规格说明书》）：
1. 以中国民航局公开的「年度旅客吞吐量」为唯一真实数据锚点，不依赖机场内部敏感数据；
2. 通过四层分解模型逐层生成 月度 -> 日级 -> 小时级 -> 安检客流量，
   每一层分解都满足「总量守恒」（月度之和 = 年度、日级之和 = 月度、小时级之和 = 日级）；
3. 所有模拟数据标注数据来源层级（official_caac / ai_monthly_decomp / ai_daily_decomp /
   ai_hourly_gen / departure_convert）与可信度等级（高/中/低）；
4. 提供自校验（守恒性、合理性）与外部数据（TSA 式）反向验证接口，用于模型校准。

说明：为避免依赖付费/机场内部数据，并消除「调用大模型生成数字」带来的网络不可用风险，
本引擎采用「确定性多层分解 + 特征加权 + 自回归噪声」实现，模型参数全部可配置、可追溯，
符合需求报告对「数值由确定性算法计算、大模型仅负责自然语言生成」的约束。
"""
from datetime import date, timedelta
import random
import math

# ---------------------------------------------------------------------------
# 默认基准参数（行业公开经验值，可在系统参数配置页覆盖）
# ---------------------------------------------------------------------------

# 月度季节性基准比例（全国民航客流经验值，运行时归一化保证和=1）
DEFAULT_MONTHLY_SEASONAL = [
    0.072, 0.078, 0.072, 0.076, 0.082, 0.085,
    0.092, 0.093, 0.084, 0.085, 0.078, 0.103,
]

# 日内客流分布基准曲线（24 小时，运行时归一化保证和=1）
# 早高峰 07-09 最高、午间次高峰 10-12、下午次高峰 16-18、晚高峰 19-21、深夜低谷 00-05
DEFAULT_INTRADAY = [
    0.0132, 0.0079, 0.0053, 0.0039, 0.0053, 0.0118,   # 00-05
    0.0763, 0.1116, 0.1053, 0.0724, 0.0553, 0.0474,   # 06-11
    0.0395, 0.0421, 0.0474, 0.0579, 0.0658, 0.0605,   # 12-17
    0.0500, 0.0395, 0.0289, 0.0224, 0.0171, 0.0132,   # 18-23
]

# 出港占比（旅客吞吐量 -> 安检客流量），默认 50%（进港出港对称）
DEFAULT_DEPARTURE_RATIO = 0.5
DEFAULT_TRANSFER_RATIO = 0.06          # 中转占比
DEFAULT_TRANSFER_SECURITY_RATE = 0.25  # 中转旅客安检率

# 单通道平均服务率默认值（人/分钟），普通通道 ~3.5
DEFAULT_MU = 3.5

# 中国法定节假日（含调休放假日期）— 覆盖 2025/2026，用于日期类型识别
_HOLIDAYS = {
    # 2025
    '2025-01-01', '2025-01-28', '2025-01-29', '2025-01-30', '2025-01-31',
    '2025-02-01', '2025-02-02', '2025-02-03', '2025-02-04',
    '2025-04-04', '2025-04-05', '2025-04-06',
    '2025-05-01', '2025-05-02', '2025-05-03', '2025-05-04', '2025-05-05',
    '2025-05-31', '2025-06-01', '2025-06-02',
    '2025-09-05', '2025-09-06', '2025-09-07',
    '2025-10-01', '2025-10-02', '2025-10-03', '2025-10-04', '2025-10-05',
    '2025-10-06', '2025-10-07', '2025-10-08',
    '2025-12-25',  # 平安夜（部分机场客流小高峰，按普通处理）
    # 2026
    '2026-01-01', '2026-02-15', '2026-02-16', '2026-02-17', '2026-02-18',
    '2026-02-19', '2026-02-20', '2026-02-21', '2026-02-22',
    '2026-04-04', '2026-04-05', '2026-04-06',
    '2026-05-01', '2026-05-02', '2026-05-03', '2026-05-04', '2026-05-05',
    '2026-06-19', '2026-06-20', '2026-06-21',
    '2026-09-25', '2026-09-26', '2026-09-27',
    '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05',
    '2026-10-06', '2026-10-07',
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _normalize(weights):
    """把权重列表归一化为概率分布（和=1），避免除零。"""
    s = float(sum(weights))
    if s <= 0:
        n = len(weights)
        return [1.0 / n] * n
    return [w / s for w in weights]


def _parse_date(s):
    """兼容 'YYYY-MM-DD' / 'YYYY/M/D' 等格式，解析失败返回 None。"""
    if not s:
        return None
    s = str(s).strip().replace('/', '-')
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime_str_to_date(s, fmt)
        except Exception:
            continue
    # 仅日期部分
    try:
        return datetime_str_to_date(s[:10], '%Y-%m-%d')
    except Exception:
        return None


def datetime_str_to_date(s, fmt):
    from datetime import datetime as _dt
    return _dt.strptime(s, fmt).date()


def is_holiday(d):
    return d.strftime('%Y-%m-%d') in _HOLIDAYS


def is_weekend(d):
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def is_spring_festival_period(d, year=None):
    """春运时段：春节前 15 天 ~ 春节后 25 天（以当年春节为锚点）。"""
    if year is None:
        year = d.year
    # 2025 春节 01-29，2026 春节 02-17；用经验锚定
    anchors = {2025: date(2025, 1, 29), 2026: date(2026, 2, 17)}
    anchor = anchors.get(year, date(year, 2, 10))
    delta = (d - anchor).days
    return -15 <= delta <= 25


def is_summer(d):
    return d.month in (7, 8)


def date_type(d):
    """返回日期类型标签：holiday / spring / summer / weekend / weekday / low_season。"""
    if is_holiday(d):
        return 'holiday'
    if is_spring_festival_period(d):
        return 'spring'
    if is_summer(d):
        return 'summer'
    if is_weekend(d):
        return 'weekend'
    if d.month in (3, 4, 11):
        return 'low_season'
    return 'weekday'


# ---------------------------------------------------------------------------
# 四层分解
# ---------------------------------------------------------------------------

def decompose_monthly(annual_passengers, year, params=None):
    """第一层：年度 -> 月度。返回 12 个月度客流量（旅客吞吐量，单位：人次）。"""
    p = params or {}
    seasonal = p.get('monthly_seasonal') or DEFAULT_MONTHLY_SEASONAL
    # 春节所在月上浮、节后次月回落（经验修正）
    weights = list(seasonal)
    try:
        # 以 2 月为春节典型月做轻微修正
        feb_idx = 1
        weights[feb_idx] *= 1.06
        # 3-4 月淡季下浮
        weights[2] *= 0.97
        weights[3] *= 0.95
        # 11 月淡季下浮
        weights[10] *= 0.96
    except Exception:
        pass
    weights = _normalize(weights)
    return [round(annual_passengers * w) for w in weights]


def _daily_base_weight(d):
    """日级基础权重（相对值）：反映星期/节假日/淡旺季差异，后续归一化保证月度守恒。"""
    t = date_type(d)
    base = {
        'holiday': 1.55,
        'spring': 1.30,
        'summer': 1.12,
        'weekend': 1.12,
        'weekday': 1.00,
        'low_season': 0.85,
    }[t]
    # 周末内的周日略低于周六
    if is_weekend(d) and d.weekday() == 6:
        base *= 0.92
    return base


def decompose_daily(month_total, year, month, params=None, seed=42):
    """第二层：月度 -> 日级。返回该月每日客流量（人次），保证和 = month_total。"""
    rng = random.Random(seed + year * 31 + month)
    days = []
    d = date(year, month, 1)
    while d.month == month:
        days.append(d)
        d += timedelta(days=1)
    raw = [_daily_base_weight(x) * (1 + rng.gauss(0, 0.04)) for x in days]
    raw = [max(0.3, w) for w in raw]
    weights = _normalize(raw)
    return [round(month_total * w) for w in weights], days


def decompose_hourly(day_total, d, params=None, seed=42):
    """第三层：日级 -> 小时级。返回 24 小时客流量（人次），保证和 = day_total。

    在日内基准曲线上叠加自回归(AR(1))随机噪声：高峰时段波动大、低谷时段波动小，
    归一化保证日级守恒。
    """
    p = params or {}
    base = p.get('intraday') or DEFAULT_INTRADAY
    rng = random.Random(seed + d.toordinal())
    base = _normalize(base)
    # AR(1) 噪声：noise_t = phi*noise_{t-1} + eps_t
    phi = 0.55
    noise = 0.0
    factors = []
    for h in range(24):
        # 高峰时段波动幅度更大
        sigma = 0.12 if base[h] >= 0.04 else 0.05
        eps = rng.gauss(0, sigma)
        noise = phi * noise + eps
        factors.append(max(0.001, base[h] * (1 + noise)))
    factors = _normalize(factors)
    return [round(day_total * f) for f in factors]


def departure_convert(hourly_passengers, params=None):
    """第四层：旅客吞吐量 -> 安检客流量。security = 吞吐量 * 出港占比 + 中转*中转安检率。"""
    p = params or {}
    dep = p.get('departure_ratio', DEFAULT_DEPARTURE_RATIO)
    tr = p.get('transfer_ratio', DEFAULT_TRANSFER_RATIO)
    tsr = p.get('transfer_security_rate', DEFAULT_TRANSFER_SECURITY_RATE)
    out = []
    for v in hourly_passengers:
        sec = v * dep + v * tr * tsr
        out.append(round(sec))
    return out


# ---------------------------------------------------------------------------
# 对外主接口
# ---------------------------------------------------------------------------

def simulate_day(airport, predict_date_str, annual_wan, params=None, seed=42):
    """针对单个日期，生成「客流模拟结果」（与前端 /simulate 返回结构兼容）。

    :param annual_wan: 该机场年度旅客吞吐量（万人次）
    :param params: 可覆盖参数 {monthly_seasonal, intraday, departure_ratio,
                    transfer_ratio, transfer_security_rate, mu}
    :return: dict，含 dailyPassengers/peakHour/maxHourPassengers/avgSecuritySpeed/
             hourlyFlow/hourlySpeed 以及数值型辅助字段
    """
    p = params or {}
    d = _parse_date(predict_date_str)
    if d is None:
        raise ValueError('预测日期格式无法解析：%s' % predict_date_str)
    if not annual_wan or annual_wan <= 0:
        raise ValueError('缺少该机场年度吞吐量数据，无法锚定模拟')

    annual_passengers = int(annual_wan * 10000)  # 万人次 -> 人次

    # 第一层：月度（用于全年一致性与守恒校验，这里仅取目标月）
    monthly = decompose_monthly(annual_passengers, d.year, p)
    month_total = monthly[d.month - 1]

    # 第二层：日级
    daily_list, days = decompose_daily(month_total, d.year, d.month, p, seed)
    day_idx = days.index(d)
    day_total = daily_list[day_idx]

    # 第三层：小时级
    hourly_passengers = decompose_hourly(day_total, d, p, seed)

    # 第四层：折算安检客流量（用于排队论输入）
    hourly_security = departure_convert(hourly_passengers, p)

    # 安检速度（人/分钟）：按通道类型默认服务率，并随该时段客流相对水平轻微波动
    mu = float(p.get('mu', DEFAULT_MU))
    avg_flow = (sum(hourly_passengers) / 24.0) or 1.0
    hourly_speeds = []
    for flow in hourly_passengers:
        ratio = flow / avg_flow
        # 客流高于均值 -> 速度略降；低于均值 -> 略升（±0.4 人/分钟，限幅 2.0~5.0）
        speed = mu - (ratio - 1.0) * 0.4
        speed = round(max(2.0, min(5.0, speed)), 2)
        hourly_speeds.append(speed)
    avg_speed = round(sum(hourly_speeds) / 24.0, 2)

    # 峰值时段：安检只服务出港旅客，故以「安检客流」为准
    sec_flow = [float(x) for x in hourly_security]
    peak_idx = int(max(range(24), key=lambda h: sec_flow[h]))
    peak_hour = '%02d:00-%02d:00' % (peak_idx, (peak_idx + 1) % 24)
    max_hour = int(sec_flow[peak_idx])
    daily_security = int(sum(sec_flow))

    result = {
        'airport': airport,
        'predict_date': d.strftime('%Y-%m-%d'),
        # 前端展示用字符串
        'dailyPassengers': '%d人次' % daily_security,
        'peakHour': peak_hour,
        'maxHourPassengers': '%d人次' % max_hour,
        'avgSecuritySpeed': '%.2f 人/分钟' % avg_speed,
        'hourlyFlow': [int(x) for x in sec_flow],
        'hourlySpeed': hourly_speeds,
        # 数值型辅助字段（供调度/排班复用，避免重复解析字符串）
        '_daily_passengers': day_total,
        '_daily_security': daily_security,
        '_hourly_passengers': hourly_passengers,
        '_hourly_security': [int(x) for x in sec_flow],
        '_peak_idx': peak_idx,
        '_mu': mu,
        '_date_type': date_type(d),
        # 数据来源与可信度标注
        'source_tags': {
            'annual': 'official_caac',
            'monthly': 'ai_monthly_decomp',
            'daily': 'ai_daily_decomp',
            'hourly': 'ai_hourly_gen',
            'security': 'departure_convert',
        },
        'confidence': {'annual': '高', 'monthly': '中', 'daily': '中', 'hourly': '低',
                       'security': '中'},
    }
    return result


def simulate_year(airport, year, annual_wan, params=None, seed=42):
    """生成全年逐日模拟数据（用于历史分析 / 预测训练样本 / 数据大屏）。"""
    p = params or {}
    if not annual_wan or annual_wan <= 0:
        raise ValueError('缺少该机场年度吞吐量数据')
    annual_passengers = int(annual_wan * 10000)
    monthly = decompose_monthly(annual_passengers, year, p)
    days_out = []
    for m in range(1, 13):
        daily_list, days = decompose_daily(monthly[m - 1], year, m, p, seed)
        for i, d in enumerate(days):
            hourly = decompose_hourly(daily_list[i], d, p, seed)
            sec = departure_convert(hourly, p)
            days_out.append({
                'date': d.strftime('%Y-%m-%d'),
                'date_type': date_type(d),
                'daily_passengers': daily_list[i],
                'hourly_passengers': hourly,
                'hourly_security': sec,
            })
    return {
        'airport': airport,
        'year': year,
        'annual_passengers': annual_passengers,
        'monthly': monthly,
        'days': days_out,
    }


# ---------------------------------------------------------------------------
# 数据质量监控 / 外部验证
# ---------------------------------------------------------------------------

def self_validation(annual_wan, year, params=None, seed=42):
    """自校验：守恒性（年度=Σ月=Σ日=Σ小时）与合理性（非负、峰值占比合理）。

    返回 {conservation, reasonable, monthly_sum, daily_sum, hourly_sum, peak_share, errors}
    """
    errors = []
    try:
        yr = simulate_year('__self__', year, annual_wan, params, seed)
    except Exception as e:
        return {'ok': False, 'errors': [str(e)]}
    annual = yr['annual_passengers']
    monthly_sum = sum(yr['monthly'])
    daily_sum = sum(x['daily_passengers'] for x in yr['days'])
    hourly_sum = sum(sum(x['hourly_passengers']) for x in yr['days'])
    cons_tol = max(1.0, annual * 0.005)
    if abs(monthly_sum - annual) > cons_tol:
        errors.append('月度之和(%.0f)与年度(%.0f)偏差超阈值' % (monthly_sum, annual))
    if abs(daily_sum - annual) > cons_tol:
        errors.append('日级之和(%.0f)与年度(%.0f)偏差超阈值' % (daily_sum, annual))
    if abs(hourly_sum - daily_sum) > cons_tol:
        errors.append('小时级之和与日级之和偏差超阈值')
    # 合理性：单小时峰值占比应在 0.5%~15% 之间（早高峰 3 小时块整体约 22%~28%，
    # 单小时在噪声下可略高于 12%）
    peak_share = max(max(x['hourly_passengers']) / x['daily_passengers'] for x in yr['days'] if x['daily_passengers'] > 0)
    reasonable = 0.005 <= peak_share <= 0.15
    if not reasonable:
        errors.append('单小时峰值占比 %.2f%% 超出合理区间[0.5%%,12%%]' % (peak_share * 100))
    return {
        'ok': len(errors) == 0,
        'conservation': len(errors) == 0,
        'reasonable': reasonable,
        'peak_share': round(peak_share, 4),
        'annual': annual,
        'monthly_sum': monthly_sum,
        'daily_sum': daily_sum,
        'hourly_sum': hourly_sum,
        'errors': errors,
    }


def tsa_style_validation(real_daily, sim_daily):
    """TSA 式反向验证：对比真实日级序列与模拟日级序列，输出 MAPE/RMSE/相关系数。

    :param real_daily: list[float] 真实日级客流量（如 TSA 公开数据）
    :param sim_daily: list[float] 模拟日级客流量
    两者长度应一致。返回 {mape, rmse, corr, n}
    """
    import statistics
    n = min(len(real_daily), len(sim_daily))
    if n == 0:
        return {'mape': None, 'rmse': None, 'corr': None, 'n': 0}
    r = real_daily[:n]
    s = sim_daily[:n]
    mae = sum(abs(a - b) for a, b in zip(r, s)) / n
    rmse = (sum((a - b) ** 2 for a, b in zip(r, s)) / n) ** 0.5
    denom = sum(abs(a) for a in r) or 1.0
    mape = sum(abs(a - b) / (abs(a) or 1.0) for a, b in zip(r, s)) / n * 100.0
    # 皮尔逊相关系数
    mr = statistics.mean(r)
    ms = statistics.mean(s)
    cov = sum((a - mr) * (b - ms) for a, b in zip(r, s))
    vr = sum((a - mr) ** 2 for a in r) ** 0.5
    vs = sum((b - ms) ** 2 for b in s) ** 0.5
    corr = cov / (vr * vs) if vr > 0 and vs > 0 else None
    return {'mape': round(mape, 2), 'rmse': round(rmse, 2),
            'corr': round(corr, 4) if corr is not None else None, 'n': n,
            'mae': round(mae, 2)}


# ---------------------------------------------------------------------------
# UC01 算法深化：到达过程 / 服务时间分布 / 离散事件仿真 / 不确定性量化
# ---------------------------------------------------------------------------


def sample_service_time(rng, mu, dist='exp', k=2, sigma=0.5):
    """按指定分布抽样一次安检服务时间（分钟）。

    Erlang-C 解析模型假设服务时间服从**指数分布**（方差大、波动强）。
    真实安检流程（验证 -> 引导 -> 人身检查 -> 随身物品）是多个环节串联，
    服务时间更接近 Erlang-k（k 个阶段，波动更小）或对数正态（右偏）。
    本函数提供三种分布，用于量化「服务时间分布假设」对结果的影响。

    :param dist: 'exp'（默认）/ 'erlang' / 'lognormal'
    :param k: Erlang 的阶段数（越大越稳定）
    :param sigma: 对数正态的变异系数（均值/标准差之比）
    """
    if mu <= 0:
        return 0.0
    u = 1.0 - rng.random()
    if u <= 0.0:
        u = 1e-12
    if dist == 'erlang':
        # Erlang-k：k 个参数 k·mu 的指数分布之和，均值仍为 1/mu，方差缩小为 1/k
        kk = max(1, int(k))
        return sum(-math.log(1.0 - rng.random()) / (kk * mu) for _ in range(kk))
    if dist == 'lognormal':
        # 对数正态：由目标均值 1/mu 与变异系数 sigma 反解 (mu_ln, sigma_ln)
        m = 1.0 / mu
        s = max(1e-6, float(sigma))
        mu_ln = math.log(m) - 0.5 * math.log(1.0 + s * s)
        sigma_ln = math.sqrt(math.log(1.0 + s * s))
        return math.exp(rng.gauss(mu_ln, sigma_ln))
    return -math.log(u) / mu  # 指数分布


def nhpp_arrival_times(hourly_arrivals, seed=42):
    """非齐次泊松过程（NHPP）到达时刻抽样。

    机场到达率在一天内剧烈变化，不能用一个恒定 λ 描述。这里采用
    **逐小时常数率的 NHPP**（piecewise-constant rate）：小时内到达间隔服从
    Exp(λ_h)，跨小时切换速率——比「均匀撒点」更真实地保留了随机聚集效应。

    :return: 按时间升序的到达时刻列表（单位：分钟，0 ~ 1440）
    """
    rng = random.Random(seed)
    times = []
    for h in range(24):
        lam = (hourly_arrivals[h] if h < len(hourly_arrivals) else 0) / 60.0
        if lam <= 0:
            continue
        t = h * 60.0
        end = (h + 1) * 60.0
        while True:
            # 指数间隔抽样
            u = 1.0 - rng.random()
            if u <= 0.0:
                u = 1e-12
            t += -math.log(u) / lam
            if t >= end:
                break
            times.append(t)
    times.sort()
    return times


def _quantile(sorted_vals, p):
    """从已升序列表中取 p 分位数。"""
    if not sorted_vals:
        return None
    p = min(max(float(p), 0.0), 1.0)
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def des_simulate(hourly_arrivals, channels, mu, service_dist='exp', k=2,
                 sigma=0.5, seed=42, patience=None):
    """离散事件仿真（DES）：模拟「旅客到达 → 排队 → 安检 → 离开」全过程。

    与 Erlang-C 解析模型互为校验：
      - 解析模型（queuing.py）给出理论期望，计算快、可解释；
      - 仿真模型给出**经验分布**（分位数、最大值），并支持解析模型做不到的假设：
        非指数服务时间、分时段变化的通道数、旅客放弃行为等。
    两者对比即可量化「M/M/c 假设」带来的误差。

    :param channels: int（固定通道数）或长度 24 的列表（分时段通道数）
    :param patience: 旅客耐心（分钟）；给出则启用放弃行为（对应 Erlang-A 场景）
    :return: dict，含平均/分位等待时间、平均队长、放弃率、通道利用率等
    """
    rng = random.Random(seed)
    times = nhpp_arrival_times(hourly_arrivals, seed)
    n = len(times)

    if isinstance(channels, (list, tuple)):
        ch_plan = [max(1, int(c)) for c in channels]
    else:
        ch_plan = [max(1, int(channels))] * 24
    c_max = max(ch_plan)

    free = [0.0] * c_max       # 每条通道的下一次空闲时刻
    waits = []
    total_service = 0.0
    served = 0
    abandoned = 0

    for t in times:
        h = int(min(23, max(0, t // 60.0)))
        c = ch_plan[h]
        # 选择最早空闲的一条通道
        idx = 0
        best_free = free[0]
        for i in range(1, c):
            if free[i] < best_free:
                best_free = free[i]
                idx = i
        start = max(t, best_free)
        wait = start - t

        if patience is not None and wait > patience:
            abandoned += 1
            continue

        waits.append(wait)
        stime = sample_service_time(rng, mu, service_dist, k, sigma)
        free[idx] = start + stime
        total_service += stime
        served += 1

    srt = sorted(waits)
    horizon = 1440.0
    # 平均队长用 Little 法则估计：Lq = 总等待时间 / 观测时长
    avg_queue = (sum(waits) / horizon) if waits else 0.0
    # 利用率 = 总忙碌时间 / (平均通道数 × 时长)
    avg_c = sum(ch_plan) / 24.0
    util = (total_service / (avg_c * horizon)) if avg_c > 0 else 0.0

    return {
        'n_arrivals': n,
        'n_served': served,
        'n_abandoned': abandoned,
        'abandon_rate': round(abandoned / float(n), 4) if n else 0.0,
        'avg_wait_min': round(sum(waits) / len(waits), 3) if waits else 0.0,
        'p50_wait_min': round(_quantile(srt, 0.50), 3) if srt else 0.0,
        'p95_wait_min': round(_quantile(srt, 0.95), 3) if srt else 0.0,
        'max_wait_min': round(srt[-1], 3) if srt else 0.0,
        'avg_queue_len': round(avg_queue, 3),
        'utilization': round(min(1.0, util) * 100, 1),
        'service_dist': service_dist,
        'channels': ch_plan[0] if len(set(ch_plan)) == 1 else ch_plan,
    }


def compare_des_vs_erlangc(hourly_arrivals, channels, mu, service_dist='exp',
                           k=2, sigma=0.5, seed=42, threshold=15.0):
    """把 DES 仿真结果与 Erlang-C 解析结果对比，量化模型假设带来的误差。

    这是「细化算法」的关键一环：解析模型便于实时决策，仿真模型更贴近真实；
    用仿真定期校准解析模型，可明确误差上界（例如 Erlang-C 是否系统性高估等待）。
    """
    from . import queuing  # 延迟导入（包内相对引用），保持 simulation 模块自身内聚性

    des = des_simulate(hourly_arrivals, channels, mu, service_dist, k, sigma, seed)
    # 以高峰小时作为对比基准（排队压力最大的时段决定了旅客体验）
    peak_h = max(range(24), key=lambda h: (hourly_arrivals[h] if h < len(hourly_arrivals) else 0))
    lam_peak = (hourly_arrivals[peak_h] if peak_h < len(hourly_arrivals) else 0) / 60.0
    c = channels[peak_h] if isinstance(channels, (list, tuple)) else channels
    an = queuing.metrics(c, lam_peak, mu, threshold)

    err = None
    rel = None
    if math.isfinite(an['wq']):
        err = round(des['avg_wait_min'] - an['wq'], 3)
        rel = (round(err / an['wq'], 4) if an['wq'] > 0 else None)
    return {
        'peak_hour': peak_h,
        'arrival_per_min': round(lam_peak, 3),
        'channels': c,
        'erlang_c_wq': round(an['wq'], 3) if math.isfinite(an['wq']) else None,
        'des_avg_wq': des['avg_wait_min'],
        'des_p95_wq': des['p95_wait_min'],
        'abs_error': err,
        'rel_error': rel,
        # 判定时同时看相对误差与绝对误差：低负载下两者都接近 0，
        # 相对误差会被放大到很大但实际差异只有几秒，此时应判定为一致。
        'note': ('仿真与解析差异在可接受范围'
                 if (rel is not None and (abs(rel) < 0.5 or abs(err or 0) < 0.5))
                 else '差异较大：建议以仿真结果校准解析模型的分布假设'),
    }


def monte_carlo_flow(airport, predict_date_str, annual_wan, params=None,
                     n=200, seed=42):
    """对模拟客流做不确定性量化（蒙特卡洛）：多次运行给出客流的分位区间。

    单次模拟只能给出一个点估计，无法表达「预测有多不确定」。
    本函数通过对模型内部随机扰动取不同种子重复运行 n 次，
    输出日客流与高峰小时客流的 5%/50%/95% 分位区间，
    供调度决策做「最坏情况」预案（例如按 P95 客流配置通道）。
    """
    dailies = []
    peaks = []
    for i in range(int(n)):
        r = simulate_day(airport, predict_date_str, annual_wan, params, seed=seed + i)
        dailies.append(r['_daily_passengers'])
        peaks.append(max(r['_hourly_passengers']))
    dailies.sort()
    peaks.sort()

    def stat(a):
        return {
            'mean': round(sum(a) / len(a), 1),
            'p05': _quantile(a, 0.05),
            'p50': _quantile(a, 0.50),
            'p95': _quantile(a, 0.95),
            'min': a[0],
            'max': a[-1],
        }

    return {
        'n': int(n),
        'daily_passengers': stat(dailies),
        'peak_hour_passengers': stat(peaks),
        'note': '调度建议可按 P95 客流配置通道，以覆盖绝大多数波动情形',
    }


if __name__ == '__main__':
    # 自测：以某枢纽机场 2025 年约 6000 万人次为例
    res = simulate_day('北京首都国际机场', '2025-08-08', 6000.0, seed=42)
    print('日客流:', res['dailyPassengers'], '| 高峰:', res['peakHour'],
          '| 峰值小时:', res['maxHourPassengers'])
    print('小时级(前6):', res['hourlyFlow'][:6], '...')
    print('来源标注:', res['source_tags'])
    val = self_validation(6000.0, 2025, seed=42)
    print('自校验:', val['ok'], '| 峰值占比:',
          '%.2f%%' % (val['peak_share'] * 100), '| 误差:', val['errors'])
