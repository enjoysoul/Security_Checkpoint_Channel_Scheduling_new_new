# -*- coding: utf-8 -*-
"""
scheduling_opt.py —— 人员排班约束优化引擎

在原有「按客流量估算各时段所需人数 + 轮转分配」的基础上，本模块引入
约束规划 + 遗传算法（GA），对一周（7 天 × 早/中/晚三班）的安检排班做
整体优化，目标是在满足覆盖需求的前提下，尽量满足：

  【硬约束】（必须满足，否则记为冲突）
    1. 每个 (天, 班次) 的人数 == 需求人数（覆盖）
    2. 同一人一天最多排一个班次（不重复排班）
    3. 资质约束：特定班次要求具备相应技能（如资深/双语）
    4. 休息约束：前一晚夜班(晚班)后，次日早班不得排（连续作业过长）

  【软约束】（尽量满足，转化为惩罚）
    5. 每周连续工作天数不超过上限（默认 5）
    6. 每周夜班次数不超过上限（默认 3）
    7. 公平性：所有人总排班次数尽量均衡（方差最小）
    8. 周末人力适当上浮（已由需求人数体现）

引擎输出：最优排班（含具体姓名）、约束冲突清单、质量评分(0~100)。

仅依赖标准库 random，可独立运行自测。
"""

from __future__ import annotations

import math
import random
from typing import List, Dict, Any, Tuple, Optional

SHIFTS = ['早班', '中班', '晚班']          # 06-14 / 14-22 / 22-06
WEEK_DAYS = 7

# 各班次起止时刻（小时）。晚班 22:00-06:00 跨零点，结束时刻记为 30（即次日 06:00），
# 这样「相邻两天的班间休息小时数」可以直接用时刻差计算。
SHIFT_START_HOUR = {'早班': 6.0, '中班': 14.0, '晚班': 22.0}
SHIFT_END_HOUR = {'早班': 14.0, '中班': 22.0, '晚班': 30.0}


def _rest_hours(a_si: int, b_si: int) -> Optional[float]:
    """相邻两天两次排班之间的休息小时数（a 在前一天，b 在后一天）。

    用于把原先只判断「夜班后接早班」的特例规则，泛化为按真实作息计算的
    「班间最小休息时长」约束——更符合劳动法与排班实务。
    """
    try:
        start_b = SHIFT_START_HOUR[SHIFTS[b_si]] + 24.0   # 后一天班次开始（相对前一天 0 点）
        end_a = SHIFT_END_HOUR[SHIFTS[a_si]]              # 前一天班次结束
    except (IndexError, KeyError):
        return None
    return start_b - end_a


# ==========================================================================
# 数据模型
# ==========================================================================

def _normalize_staff(staff_rows) -> List[Dict[str, Any]]:
    """把数据库行 / 字典统一为内部 staff 结构。"""
    out = []
    for i, s in enumerate(staff_rows):
        if isinstance(s, dict):
            name = s.get('name') or f'员工{i + 1}'
            skills = s.get('skills') or ['基础']
            max_night = s.get('max_night', 3)
        else:  # sqlite3.Row 或元组
            try:
                name = s['name']
            except Exception:
                name = getattr(s, 'name', f'员工{i + 1}')
            skills = ['基础']
            max_night = 3
        out.append({
            'id': i,
            'name': name,
            'skills': list(skills) if isinstance(skills, (list, tuple)) else [str(skills)],
            'max_night': int(max_night),
        })
    return out


# ==========================================================================
# 染色体与约束评估
# ==========================================================================

class RosterOptimizer:
    """
    染色体表示：chrom[(shift_idx, day)] = 该 (天,班次) 上排的 staff id 列表
    （长度等于需求人数）。全局通过「每日三班互斥」保证一人一天一班。
    """

    def __init__(self, staff: List[Dict[str, Any]], requirements: Dict[str, List[int]],
                 params: Optional[Dict[str, Any]] = None):
        self.staff = staff
        self.n = len(staff)
        self.req = {s: list(requirements.get(s, [0] * WEEK_DAYS)) for s in SHIFTS}
        self.params = params or {}
        self.max_consecutive = int(self.params.get('max_consecutive', 6))
        self.max_night_week = int(self.params.get('max_night_week', 3))
        self.skill_required = self.params.get('skill_required', {})  # {shift: ['技能']}

        # --- 劳动法规 / 排班细则（UC04 深化）---
        # 班间最小休息时长（小时），默认 8 小时；该默认下等价于原「夜班后不得接早班」
        self.min_rest_hours = float(self.params.get('min_rest_hours', 8.0))
        # 每人每周最多班次数（劳动法口径的工时上限）
        self.max_shifts_week = int(self.params.get('max_shifts_week', 6))
        # 是否要求每周至少休息一天
        self.require_day_off = bool(self.params.get('require_day_off', True))

        # --- 遗传算子与自适应参数（UC04 深化）---
        self.crossover_op = self.params.get('crossover_op', 'adaptive')   # day|uniform|multi|adaptive
        self.local_search_iters = int(self.params.get('local_search_iters', 20))
        self.sa_iters = int(self.params.get('sa_iters', 60))

        self.weights = {
            'duplicate': 1000,     # 同日同班次重复（硬）
            'double_book': 1000,   # 同日跨班次重复（硬）
            'skill': 500,          # 资质不符（硬）
            'rest': 200,           # 班间休息不足（硬，按真实作息间隔判定）
            'consecutive': 10,     # 连续工作过长（软）
            'night': 15,           # 夜班过多（软）
            'fairness': 8,         # 不公平（软）
            'overload': 20,        # 周班次数超上限（软）
            'no_day_off': 30,      # 整周无休（软）
        }

    # ---------------- 初始化合法染色体 ----------------
    @staticmethod
    def _proportional_alloc(demands: List[int], capacity: int) -> List[int]:
        """按需求比例把 capacity 个名额分配到各班次（最大余数法）。

        当「当天总需求 > 在岗人数」时，必须公平地把有限人力按需求比例摊到
        各班次，而不是把可分配的人全部塞进第一个班次——否则会出现
        「只有早班有人、中班晚班全为 0 人」的荒谬排班（原实现的缺陷）。
        """
        total = sum(demands)
        if total <= 0 or capacity <= 0:
            return [0] * len(demands)
        if total <= capacity:
            return [int(x) for x in demands]
        quotas = [d / float(total) * capacity for d in demands]
        alloc = [int(q) for q in quotas]
        rem = capacity - sum(alloc)
        order = sorted(range(len(demands)), key=lambda i: quotas[i] - alloc[i], reverse=True)
        for i in order[:max(0, rem)]:
            alloc[i] += 1
        return alloc

    def _rand_chromosome(self, rng: random.Random) -> Dict[Tuple[int, int], List[int]]:
        chrom = {}
        for d in range(WEEK_DAYS):
            demands = [self.req[s][d] for s in SHIFTS]
            total = sum(demands)
            pool = list(range(self.n))
            rng.shuffle(pool)
            # 人手充足 -> 按各班实际需求精确分配（保证覆盖硬约束可满足）；
            # 人手不足 -> 按需求比例分摊可用名额（保证三个班次都有人，公平降级）
            alloc = (demands if total <= self.n
                     else self._proportional_alloc(demands, self.n))
            idx = 0
            for si in range(len(SHIFTS)):
                cnt = alloc[si]
                chrom[(si, d)] = pool[idx:idx + cnt]
                idx += cnt
        return chrom

    # ---------------- 约束惩罚 ----------------
    def _penalty(self, chrom: Dict[Tuple[int, int], List[int]]) -> Tuple[float, Dict[str, int]]:
        p = 0.0
        cnt = {k: 0 for k in self.weights}
        # 每人每周的班次序列（用于连续/夜班统计）
        weekly = {i: [None] * WEEK_DAYS for i in range(self.n)}

        for d in range(WEEK_DAYS):
            day_ids = []
            for si, s in enumerate(SHIFTS):
                ids = chrom[(si, d)]
                # 同班次重复
                if len(ids) != len(set(ids)):
                    cnt['duplicate'] += (len(ids) - len(set(ids)))
                for iid in ids:
                    weekly[iid][d] = si
                    day_ids.append(iid)
            # 同日跨班次重复
            if len(day_ids) != len(set(day_ids)):
                cnt['double_book'] += (len(day_ids) - len(set(day_ids)))
            # 资质
            for si, s in enumerate(SHIFTS):
                need = self.skill_required.get(s)
                if need:
                    for iid in chrom[(si, d)]:
                        if not (set(need) & set(self.staff[iid]['skills'])):
                            cnt['skill'] += 1

        # 每人维度：休息/连续/夜班
        for i in range(self.n):
            seq = weekly[i]
            worked = [d for d in range(WEEK_DAYS) if seq[d] is not None]
            # 班间休息：由「只判夜班后接早班」泛化为按真实起止时刻计算的最小间隔
            nights = 0
            for d in range(WEEK_DAYS):
                if seq[d] == 2:  # 晚班（用于夜班次数上限统计）
                    nights += 1
                a_si, b_si = seq[d], seq[(d + 1) % WEEK_DAYS]
                if a_si is None or b_si is None:
                    continue
                gap = _rest_hours(a_si, b_si)
                if gap is not None and gap < self.min_rest_hours:
                    cnt['rest'] += 1
            if nights > self.max_night_week:
                cnt['night'] += (nights - self.max_night_week)

            # 每周班次数上限 / 每周至少休息一天
            shifts_cnt = len(worked)
            if shifts_cnt > self.max_shifts_week:
                cnt['overload'] += (shifts_cnt - self.max_shifts_week)
            if self.require_day_off and shifts_cnt >= WEEK_DAYS:
                cnt['no_day_off'] += 1
            # 连续工作天数
            run = 0
            max_run = 0
            for d in range(WEEK_DAYS):
                if seq[d] is not None:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0
            if max_run > self.max_consecutive:
                cnt['consecutive'] += (max_run - self.max_consecutive)

        # 公平性：总排班次数方差
        totals = [sum(1 for d in range(WEEK_DAYS) if weekly[i][d] is not None) for i in range(self.n)]
        if totals:
            mean = sum(totals) / len(totals)
            var = sum((t - mean) ** 2 for t in totals) / len(totals)
            cnt['fairness'] = int(var * 10)

        for k, w in self.weights.items():
            p += cnt[k] * w
        return p, cnt

    def fitness(self, chrom) -> float:
        pen, _ = self._penalty(chrom)
        return -pen

    # ---------------- 修复：保证硬约束 ----------------
    def _repair(self, chrom: Dict[Tuple[int, int], List[int]], rng: random.Random) -> Dict[Tuple[int, int], List[int]]:
        for d in range(WEEK_DAYS):
            used = set()
            for si, s in enumerate(SHIFTS):
                ids = chrom[(si, d)]
                cleaned = []
                for iid in ids:
                    if iid in used or iid in cleaned:
                        # 找替补
                        cand = [j for j in range(self.n) if j not in used and j not in cleaned]
                        rng.shuffle(cand)
                        if cand:
                            iid = cand[0]
                        else:
                            continue
                    cleaned.append(iid)
                    used.add(iid)
                chrom[(si, d)] = cleaned
        return chrom

    # ---------------- 遗传操作 ----------------
    def _crossover(self, a, b, rng) -> Dict[Tuple[int, int], List[int]]:
        child = {}
        d = rng.randrange(WEEK_DAYS)
        for dd in range(WEEK_DAYS):
            for si in range(len(SHIFTS)):
                if dd == d:
                    child[(si, dd)] = list(b[(si, dd)])
                else:
                    child[(si, dd)] = list(a[(si, dd)])
        return self._repair(child, rng)

    def _mutate(self, chrom, rng, rate: float = 0.15) -> Dict[Tuple[int, int], List[int]]:
        for d in range(WEEK_DAYS):
            for si in range(len(SHIFTS)):
                if rng.random() < rate:
                    ids = chrom[(si, d)]
                    if not ids:
                        continue
                    pos = rng.randrange(len(ids))
                    # 找当天其它班次未用的人替换
                    used_today = set()
                    for s2 in range(len(SHIFTS)):
                        used_today.update(chrom[(s2, d)])
                    used_today.discard(ids[pos])
                    cand = [j for j in range(self.n) if j not in used_today]
                    if cand:
                        ids[pos] = rng.choice(cand)
        return self._repair(chrom, rng)

    # ---------------- 遗传算子深化（UC04）----------------
    def _crossover_uniform(self, a, b, rng) -> Dict[Tuple[int, int], List[int]]:
        """均匀交叉：每个 (天,班次) 独立地随机取自父代 a 或 b。

        探索能力强，适合种群多样性不足（早熟收敛）时使用。
        """
        child = {}
        for d in range(WEEK_DAYS):
            for si in range(len(SHIFTS)):
                src = b if rng.random() < 0.5 else a
                child[(si, d)] = list(src[(si, d)])
        return self._repair(child, rng)

    def _crossover_multi(self, a, b, rng) -> Dict[Tuple[int, int], List[int]]:
        """多点交叉：随机抽取若干天作为交换片段（介于单点与均匀之间）。"""
        child = {}
        k = max(1, WEEK_DAYS // 3)
        days = set(rng.sample(range(WEEK_DAYS), k))
        for d in range(WEEK_DAYS):
            for si in range(len(SHIFTS)):
                src = b if d in days else a
                child[(si, d)] = list(src[(si, d)])
        return self._repair(child, rng)

    def _crossover_adaptive(self, a, b, rng, diversity: float) -> Dict[Tuple[int, int], List[int]]:
        """自适应选择交叉算子：按种群多样性在三种算子间切换。

        多样性低 -> 均匀交叉（增强探索，跳出早熟收敛）；
        多样性中 -> 多点交叉；
        多样性高 -> 按天交叉（保留完整的日块结构，利于收敛）。
        """
        if diversity < 0.15:
            return self._crossover_uniform(a, b, rng)
        if diversity < 0.35:
            return self._crossover_multi(a, b, rng)
        return self._crossover(a, b, rng)

    def _mutate_swap(self, chrom, rng) -> Dict[Tuple[int, int], List[int]]:
        """交换变异：同一天内把两个班次各一人互换。

        相比「随机换成另一个人」，交换天然保持各 (天,班次) 的人数不变，
        不会破坏覆盖这一硬约束，是更高效的邻域动作。
        """
        d = rng.randrange(WEEK_DAYS)
        si, sj = rng.randrange(len(SHIFTS)), rng.randrange(len(SHIFTS))
        if si == sj:
            return chrom
        a_ids, b_ids = chrom[(si, d)], chrom[(sj, d)]
        if not a_ids or not b_ids:
            return chrom
        i = rng.randrange(len(a_ids))
        j = rng.randrange(len(b_ids))
        a_ids[i], b_ids[j] = b_ids[j], a_ids[i]
        return chrom

    def _local_search(self, chrom, rng, iters: Optional[int] = None) -> Dict[Tuple[int, int], List[int]]:
        """Memetic 局部搜索：对个体做爬山式改进。

        遗传算法全局搜索能力强，但局部精细度不足、收敛慢。Memetic 算法在
        每一代对精英个体做邻域爬山，把「全局探索」与「局部开发」结合，
        通常能以更少的代数得到更好的解。
        """
        iters = self.local_search_iters if iters is None else iters
        cur = {k: list(v) for k, v in chrom.items()}
        cur_fit = self.fitness(cur)
        for _ in range(max(0, int(iters))):
            cand = {k: list(v) for k, v in cur.items()}
            self._mutate_swap(cand, rng)
            self._repair(cand, rng)
            f = self.fitness(cand)
            if f > cur_fit:                # 只接受改进（爬山）
                cur, cur_fit = cand, f
        return cur

    def sa_refine(self, chrom, rng, iters: Optional[int] = None,
                  t0: float = 50.0, t1: float = 1.0) -> Dict[Tuple[int, int], List[int]]:
        """模拟退火精调：以一定概率接受劣解，跳出局部最优。

        爬山法一旦落入局部最优就出不来；模拟退火用 Metropolis 准则
        （以 exp(Δ/T) 的概率接受劣解）配合温度衰减，在「开发」与「探索」间平衡。
        这里用作 GA 结束后的最后一道精加工。
        """
        iters = self.sa_iters if iters is None else iters
        cur = {k: list(v) for k, v in chrom.items()}
        cur_fit = self.fitness(cur)
        best, best_fit = {k: list(v) for k, v in cur.items()}, cur_fit
        n = max(1, int(iters))
        for step in range(n):
            # 指数降温
            t = t0 * ((t1 / t0) ** (step / float(n)))
            cand = {k: list(v) for k, v in cur.items()}
            self._mutate_swap(cand, rng)
            self._repair(cand, rng)
            f = self.fitness(cand)
            delta = f - cur_fit
            if delta >= 0 or rng.random() < math.exp(delta / max(1e-9, t)):
                cur, cur_fit = cand, f
                if f > best_fit:
                    best, best_fit = {k: list(v) for k, v in cand.items()}, f
        return best

    # ---------------- 自适应参数 ----------------
    def _diversity(self, pop) -> float:
        """种群多样性 = 不同基因型数 / 种群规模（0~1）。"""
        if not pop:
            return 0.0
        sigs = set()
        for c in pop:
            sig = tuple(tuple(sorted(c[(si, d)]))
                        for d in range(WEEK_DAYS) for si in range(len(SHIFTS)))
            sigs.add(sig)
        return len(sigs) / float(len(pop))

    def _adapt_rate(self, diversity: float, base: float = 0.15) -> float:
        """自适应变异率：多样性低则提高变异（跳出早熟），高则降低（稳定收敛）。"""
        if diversity < 0.2:
            return min(0.6, base * 2.5)
        if diversity > 0.6:
            return max(0.05, base * 0.6)
        return base

    # ---------------- 主循环 ----------------
    def run(self, generations: int = 80, pop_size: int = 60, seed: int = 123) -> Dict[Tuple[int, int], List[int]]:
        rng = random.Random(seed)
        pop = [self._repair(self._rand_chromosome(rng), rng) for _ in range(pop_size)]
        fitness = [self.fitness(c) for c in pop]
        best = {k: list(v) for k, v in pop[fitness.index(max(fitness))].items()}
        best_fit = max(fitness)
        history = []

        for gen in range(generations):
            div = self._diversity(pop)
            rate = self._adapt_rate(div)

            new_pop = [{k: list(v) for k, v in best.items()}]  # 精英保留
            while len(new_pop) < pop_size:
                # 锦标赛选择（二元锦标赛，取较优者作为父代）
                i1, i2 = rng.randrange(pop_size), rng.randrange(pop_size)
                pa = pop[i1] if fitness[i1] > fitness[i2] else pop[i2]
                j1, j2 = rng.randrange(pop_size), rng.randrange(pop_size)
                pb = pop[j1] if fitness[j1] > fitness[j2] else pop[j2]

                # 自适应交叉算子
                if self.crossover_op == 'adaptive':
                    child = self._crossover_adaptive(pa, pb, rng, div)
                elif self.crossover_op == 'uniform':
                    child = self._crossover_uniform(pa, pb, rng)
                elif self.crossover_op == 'multi':
                    child = self._crossover_multi(pa, pb, rng)
                else:
                    child = self._crossover(pa, pb, rng)

                child = self._mutate(child, rng, rate)
                if rng.random() < rate:          # 以自适应概率追加交换变异
                    child = self._mutate_swap(child, rng)
                new_pop.append(child)

            pop = new_pop
            fitness = [self.fitness(c) for c in pop]
            gbest = max(fitness)
            if gbest > best_fit:
                best_fit = gbest
                best = {k: list(v) for k, v in pop[fitness.index(gbest)].items()}

            # Memetic：定期（及末代）对精英做局部爬山
            if self.local_search_iters > 0 and (gen % 5 == 0 or gen == generations - 1):
                ls = self._local_search(best, rng)
                lf = self.fitness(ls)
                if lf > best_fit:
                    best_fit, best = lf, ls

            history.append({'gen': gen + 1, 'best_fitness': round(best_fit, 2),
                            'diversity': round(div, 3), 'mutate_rate': round(rate, 3)})

        # 模拟退火精调（最后一道精加工）
        if self.sa_iters > 0:
            sa = self.sa_refine(best, rng)
            sf = self.fitness(sa)
            if sf > best_fit:
                best_fit, best = sf, sa

        self.history = history
        self.best_fitness = best_fit
        return best

    # ---------------- 解码为前端结构 ----------------
    def decode(self, chrom) -> Dict[str, Any]:
        roster_data = {s: [] for s in SHIFTS}
        for d in range(WEEK_DAYS):
            for si, s in enumerate(SHIFTS):
                ids = chrom[(si, d)]
                roster_data[s].append({
                    'count': len(ids),
                    'names': [self.staff[i]['name'] for i in ids],
                })
        pen, cnt = self._penalty(chrom)
        totals = [sum(1 for d in range(WEEK_DAYS)
                     for si in range(len(SHIFTS))
                     if chrom[(si, d)] and i in chrom[(si, d)]) for i in range(self.n)]
        max_total = max(totals) if totals else 0
        min_total = min(totals) if totals else 0
        hard = cnt['duplicate'] + cnt['double_book'] + cnt['skill'] + cnt['rest']
        score = max(0, 100 - int(pen / max(1, self.n))) if hard == 0 else max(0, 60 - hard * 5)
        # 需求缺口：人手不足时各班次实际排班人数低于需求的总量（0 表示完全覆盖）
        shortfall = 0
        for d in range(WEEK_DAYS):
            for si, s in enumerate(SHIFTS):
                need = self.req[s][d]
                got = len(chrom[(si, d)])
                if got < need:
                    shortfall += (need - got)

        # 多目标拆分：把单一的惩罚值拆成可解释的若干目标，便于权衡分析
        objectives = {
            'coverage_ok': hard == 0 and shortfall == 0,     # 是否满足覆盖且无硬冲突
            'demand_shortfall': shortfall,                   # 人力缺口（人次·班）
            'fairness_gap': max_total - min_total,           # 工时公平性（极差，越小越公平）
            'avg_shifts': (round(sum(totals) / len(totals), 2) if totals else 0.0),
            'fatigue_penalty': (cnt['consecutive'] * self.weights['consecutive']
                                + cnt['night'] * self.weights['night']),
            'labor_penalty': (cnt['overload'] * self.weights['overload']
                              + cnt['no_day_off'] * self.weights['no_day_off']),
        }
        return {
            'roster': roster_data,
            'metrics': {
                'penalty': int(pen),
                'conflicts': cnt,
                'hard_conflicts': hard,
                'fairness_min': min_total,
                'fairness_max': max_total,
                'score': score,
                'objectives': objectives,
                'history': getattr(self, 'history', []),   # 收敛过程（多样性/变异率/最优值）
            },
        }


# ==========================================================================
# 对外入口
# ==========================================================================

def build_roster(staff_rows, counts: Dict[str, List[int]],
                 params: Optional[Dict[str, Any]] = None,
                 generations: int = 80, pop_size: int = 60, seed: int = 123
                 ) -> Dict[str, Any]:
    """
    根据需求人数 counts（{'早班':[7], '中班':[7], '晚班':[7]}）与人员列表，
    用遗传算法排班，返回 {roster, metrics}。
    """
    staff = _normalize_staff(staff_rows)
    if not staff:
        # 无人时返回空结构
        empty = {s: [{'count': c, 'names': []} for c in counts.get(s, [0] * WEEK_DAYS)] for s in SHIFTS}
        return {'roster': empty, 'metrics': {'penalty': 0, 'conflicts': {}, 'hard_conflicts': 0,
                                             'fairness_min': 0, 'fairness_max': 0, 'score': 0}}
    opt = RosterOptimizer(staff, counts, params)
    best = opt.run(generations=generations, pop_size=pop_size, seed=seed)
    return opt.decode(best)


def conflicts_report(metrics: Dict[str, Any]) -> List[str]:
    """把冲突计数转成可读中文说明。"""
    c = metrics.get('conflicts', {})
    out = []
    if c.get('duplicate'):
        out.append(f"同一班次重复排班 {c['duplicate']} 处")
    if c.get('double_book'):
        out.append(f"同一人一天排多班 {c['double_book']} 处")
    if c.get('skill'):
        out.append(f"资质不符 {c['skill']} 处")
    if c.get('rest'):
        out.append(f"夜班后紧接早班（休息不足）{c['rest']} 处")
    if c.get('consecutive'):
        out.append(f"连续工作超上限 {c['consecutive']} 处")
    if c.get('night'):
        out.append(f"夜班次数超上限 {c['night']} 处")
    if c.get('overload'):
        out.append(f"周班次数超上限 {c['overload']} 处")
    if c.get('no_day_off'):
        out.append(f"整周无休 {c['no_day_off']} 人")
    return out


if __name__ == '__main__':
    # 自测：10 名安检员，需求人数随机
    demo_staff = [{'name': f'员工{i + 1}', 'skills': ['基础'], 'max_night': 3} for i in range(10)]
    demo_counts = {
        '早班': [4, 4, 4, 4, 5, 6, 6],
        '中班': [3, 3, 3, 3, 4, 5, 5],
        '晚班': [2, 2, 2, 2, 3, 3, 3],
    }
    res = build_roster(demo_staff, demo_counts, generations=60, pop_size=50, seed=7)
    m = res['metrics']
    print('质量评分:', m['score'])
    print('硬冲突数:', m['hard_conflicts'])
    print('公平区间:', m['fairness_min'], '~', m['fairness_max'])
    print('冲突说明:', conflicts_report(m) or '无')
    print('早班-周一:', res['roster']['早班'][0])
