# -*- coding: utf-8 -*-
"""按天津滨海国际机场的真实口径重置基础数据（可重复执行，幂等）。

  * 机场字典补 天津滨海国际机场（TSN）
  * 安检通道重建为 T2 航站楼的 23 条（2021 年公开报道口径）
  * 系统参数：删除「单通道最大处理能力」「最大可开放通道数」，
    预警阈值改为天津机场服务承诺上限 10 分钟
  * 用户角色收敛为「系统管理员 / 调度人员」，张家瑞为系统管理员
  * 清空历史模拟/调度/排班结果，避免旧参数下的数据与新口径混用

用法：python entity/scripts/migrate_tianjin.py [db路径]
"""
import os
import sqlite3
import sys

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'security_checkpoint.db')

AIRPORT = ('天津滨海国际机场', 'TSN', '天津')

# 23 条通道：编号、类型、状态
CHANNELS = []
for i in range(1, 24):
    code = '通道%02d' % i
    if i in (2, 3):
        ctype = '晚到/爱心通道'
    elif i == 13:
        ctype = '头等舱/军人优先通道'
    elif i == 14:
        ctype = '综合保障通道'
    else:
        ctype = '普通通道'
    CHANNELS.append((code, ctype, '开放'))

DROP_PARAMS = ('max_channel_capacity', 'max_open_channels')

PARAM_UPDATES = [
    ('warning_threshold', '10', '平均等待时间预警阈值(分钟)'),
    ('avg_security_speed', '3.0', '平均安检速度(人/分钟·单通道)'),
    ('staff_per_channel', '4', '每通道配置人员数'),
    ('schedule_period', '每周', '排班生成周期'),
]

NEW_PARAMS = [
    ('default_airport', '天津滨海国际机场', '默认机场'),
    ('peak_start_hour', '6', '出港高峰起始小时(安检到达峰值)'),
    ('peak_end_hour', '9', '出港高峰结束小时(安检到达峰值)'),
]


def main(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) 机场
    row = cur.execute("SELECT id FROM airports WHERE name = ? OR code = ?",
                      (AIRPORT[0], AIRPORT[1])).fetchone()
    if row:
        cur.execute("UPDATE airports SET name=?, code=?, city=? WHERE id=?",
                    (AIRPORT[0], AIRPORT[1], AIRPORT[2], row['id']))
        print('机场已存在，更新为:', AIRPORT)
    else:
        cur.execute("INSERT INTO airports (name, code, city) VALUES (?,?,?)", AIRPORT)
        print('已新增机场:', AIRPORT)

    # 2) 通道
    cur.execute("DELETE FROM channels")
    for code, ctype, status in CHANNELS:
        cur.execute(
            "INSERT INTO channels (channel_code, channel_type, status, description, "
            "created_at, updated_at) VALUES (?,?,?,?,datetime('now'),datetime('now'))",
            (code, ctype, status, '天津滨海国际机场T2航站楼'))
    print('安检通道已重建:', len(CHANNELS), '条')

    # 3) 参数
    for key in DROP_PARAMS:
        cur.execute("DELETE FROM system_params WHERE param_key = ?", (key,))
    for key, val, name in PARAM_UPDATES:
        exists = cur.execute("SELECT id FROM system_params WHERE param_key=?", (key,)).fetchone()
        if exists:
            cur.execute("UPDATE system_params SET param_value=?, param_name=?, "
                        "updated_at=datetime('now') WHERE param_key=?", (val, name, key))
        else:
            cur.execute("INSERT INTO system_params (param_key, param_name, param_value, "
                        "description, updated_at) VALUES (?,?,?,?,datetime('now'))",
                        (key, name, val, name))
    for key, val, name in NEW_PARAMS:
        exists = cur.execute("SELECT id FROM system_params WHERE param_key=?", (key,)).fetchone()
        if exists:
            cur.execute("UPDATE system_params SET param_value=?, param_name=? WHERE param_key=?",
                        (val, name, key))
        else:
            cur.execute("INSERT INTO system_params (param_key, param_name, param_value, "
                        "description, updated_at) VALUES (?,?,?,?,datetime('now'))",
                        (key, name, val, name))
    print('系统参数已整理，删除:', DROP_PARAMS)

    # 4) 角色
    cur.execute("UPDATE users SET role='调度人员' WHERE username <> '张家瑞'")
    cur.execute("UPDATE users SET role='系统管理员' WHERE username='张家瑞'")
    # users 表若存在 department 列则清空，避免界面上再出现部门概念
    cols = [r[1] for r in cur.execute('PRAGMA table_info(users)')]
    if 'department' in cols:
        cur.execute("UPDATE users SET department=''")
    print('角色已收敛：张家瑞=系统管理员，其余=调度人员')

    # 5) 清空历史计算数据（旧参数下的结果不再成立）
    for t in ('T_PassengerSim', 'T_ScheduleResult', 'T_RosterResult'):
        try:
            cur.execute('DELETE FROM "%s"' % t)
        except sqlite3.Error as e:
            print('  跳过', t, e)
    conn.commit()
    conn.close()
    print('历史模拟/调度/排班数据已清空')


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    main(path)
