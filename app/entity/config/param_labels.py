# -*- coding: utf-8 -*-
"""系统参数中文名与说明 —— 单一数据源

`system_params` 表以英文 `param_key` 作为程序内部标识（各算法按 key 读取，
如 `params_dict.get('avg_security_speed')`），该标识不对外展示。
本模块提供 key -> 中文名 / 中文说明 的权威映射，供三处复用：

1. `init_db.py`  建库时写入 `param_name`、`description` 字段；
2. `app.py`      `/api/params` 对历史数据兜底补全中文名（老库里 param_name 为空时自动补齐）；
3. 前端页面      「系统参数配置」列表统一展示中文名，英文键仅作小号灰色附注。

新增系统参数时，请同步在此登记中文名与说明，界面即可自动以中文展示。
"""

# key -> (中文名, 中文说明)
PARAM_META = {
    'avg_security_speed': (
        '平均安检速度',
        '每名安检员每分钟平均查验的旅客人数，用于计算服务率 μ',
    ),
    'max_channel_capacity': (
        '单通道最大处理能力',
        '单个安检通道每小时可处理的最大旅客人数，超出即触发加开通道建议',
    ),
    'warning_threshold': (
        '平均等待时间预警阈值',
        '旅客平均等待时间超过该值时触发拥堵预警（单位：分钟）',
    ),
    'schedule_period': (
        '排班生成周期',
        '排班表自动生成的周期，可选「每日」或「每周」',
    ),
    'max_open_channels': (
        '最大可开放通道数',
        '同一时刻允许同时开放的安检通道数量上限，受场地与设备限制',
    ),
    'staff_per_channel': (
        '每通道配置人员数',
        '每条开放通道固定配置的安检人员数量（含验证、开机、开箱等岗位）',
    ),
    'departure_ratio': (
        '出港旅客占比',
        '出港（出发）旅客占机场总吞吐量的比例，用于将年吞吐量换算为安检客流',
    ),
    'weekend_staff_factor': (
        '周末客流系数',
        '周末客流相对工作日的放大倍数，用于周末班次的增员计算',
    ),
    'reserve_staff_factor': (
        '机动备勤比例',
        '在基础配置之上追加的备勤人员比例，用于应对临时性客流高峰',
    ),
    'max_shift_staff': (
        '单班最大配置人数',
        '单个班次（早班／中班／晚班）可安排的人数上限',
    ),
    'max_work_days': (
        '每周最多工作天数',
        '每名安检员每周最多可被安排的工作天数（劳动合规约束）',
    ),
}


def display_name(key):
    """返回参数的中文名；未登记的 key 原样返回（避免界面出现空白）。"""
    meta = PARAM_META.get((key or '').strip())
    return meta[0] if meta else (key or '')


def description(key):
    """返回参数的中文说明；未登记的 key 返回空串。"""
    meta = PARAM_META.get((key or '').strip())
    return meta[1] if meta else ''


def label_row(key):
    """返回 (中文名, 中文说明)，供建库 / 迁移时批量写入。"""
    return display_name(key), description(key)
