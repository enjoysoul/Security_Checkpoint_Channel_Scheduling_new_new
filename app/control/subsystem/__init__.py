# -*- coding: utf-8 -*-
"""《subsystem》算法子系统：系统全部业务计算集中在本包。

    forecasting.py     UC05 客流量时间序列预测（Theta/集成/滚动交叉验证/区间）
    queuing.py         UC02 排队论模型（Erlang-C/B/A、M/M/c/K、SI 排队计划）
    simulation.py      UC01 客流多层分解与离散事件仿真（DES、NHPP 到达）
    staffing.py        UC04 定员测算（年度吞吐量 → 各班次人数 → 总人数）与人员分配
    advisor.py         UC03 规则专家系统决策建议（多方案打分 + R1~R9 规则裁决）
    scheduling_opt.py  UC04 排班优化（遗传算法 + Memetic 局部搜索 + 模拟退火）
"""
