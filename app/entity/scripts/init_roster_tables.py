# -*- coding: utf-8 -*-
"""创建人员排班和历史分析所需的数据库表"""
import os
import sys
import sqlite3

# --- 路径锚定：无论从哪个目录执行，都能定位项目根与各层模块 ---

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from entity.config import paths               # noqa: E402

db_path = paths.DB_PATH

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 客流模拟结果表（持久化存储每次模拟结果）
cursor.execute('''
CREATE TABLE IF NOT EXISTS T_PassengerSim (
    sim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    airport TEXT NOT NULL,
    predict_date TEXT NOT NULL,
    daily_passengers TEXT,
    peak_hour TEXT,
    max_hour_passengers TEXT,
    recommend_channels TEXT,
    avg_security_speed TEXT,
    hourly_flow TEXT,
    hourly_speed TEXT,
    operator TEXT DEFAULT 'admin'
)
''')

# 2. 人员信息表
cursor.execute('''
CREATE TABLE IF NOT EXISTS T_StaffInfo (
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT DEFAULT '安检员',
    department TEXT DEFAULT '安检科',
    phone TEXT,
    status TEXT DEFAULT '在岗'
)
''')

# 3. 排班结果表
cursor.execute('''
CREATE TABLE IF NOT EXISTS T_RosterResult (
    roster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_id INTEGER,
    roster_date TEXT NOT NULL,
    shift_type TEXT NOT NULL,
    shift_time TEXT,
    staff_count INTEGER,
    staff_names TEXT,
    channels TEXT,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sim_id) REFERENCES T_PassengerSim(sim_id)
)
''')

# 4. 通道调度结果表
cursor.execute('''
CREATE TABLE IF NOT EXISTS T_ScheduleResult (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_id INTEGER,
    schedule_date TEXT NOT NULL,
    time_slot TEXT,
    expected_flow INTEGER,
    open_channels INTEGER,
    channel_ids TEXT,
    wait_time TEXT,
    utilization TEXT,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sim_id) REFERENCES T_PassengerSim(sim_id)
)
''')

# 初始化一些安检员数据
staff_list = [
    ('张伟', '安检员', '安检一科', '13800138001', '在岗'),
    ('李娜', '安检员', '安检一科', '13800138002', '在岗'),
    ('王强', '安检员', '安检一科', '13800138003', '在岗'),
    ('刘洋', '安检员', '安检二科', '13800138004', '在岗'),
    ('陈静', '安检员', '安检二科', '13800138005', '在岗'),
    ('赵磊', '安检员', '安检二科', '13800138006', '在岗'),
    ('孙丽', '安检员', '安检三科', '13800138007', '在岗'),
    ('周杰', '安检员', '安检三科', '13800138008', '在岗'),
    ('吴敏', '班组长', '安检一科', '13800138009', '在岗'),
    ('郑浩', '班组长', '安检二科', '13800138010', '在岗'),
]

# 检查是否已有数据
count = cursor.execute('SELECT COUNT(*) FROM T_StaffInfo').fetchone()[0]
if count == 0:
    cursor.executemany('INSERT INTO T_StaffInfo (name, role, department, phone, status) VALUES (?, ?, ?, ?, ?)', staff_list)
    print(f'已初始化 {len(staff_list)} 名安检员')

conn.commit()
conn.close()
print('数据库表创建完成！')
