# -*- coding: utf-8 -*-
"""安检通道调度系统 - 统一数据库初始化脚本（可重复执行，幂等）

创建全部 8 张表并写入基础种子数据：
  users / airports / channels / system_params / T_StaffInfo
  T_PassengerSim / T_RosterResult / T_ScheduleResult
所有路径由 config.paths 统一提供（锚定项目根），不依赖执行时的工作目录；删库后
重跑本脚本即可完整重建，解决旧环境手工建表、换机器跑不起来的问题。
"""
import os
import sys
import sqlite3

# --- 路径锚定：无论从哪个目录执行，都能定位项目根与各层模块 ---

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from entity.config import paths               # noqa: E402

from entity.config.param_labels import PARAM_META as _PARAM_META

try:
    import pandas as pd
except ImportError:
    pd = None

db_path = paths.DB_PATH
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def create_if_not(name, ddl):
    cur.execute(f"CREATE TABLE IF NOT EXISTS {name} ({ddl})")


# ---------- 1. 建表 ----------
create_if_not('airports', """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT,
    city TEXT
""")

create_if_not('users', """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    real_name TEXT NOT NULL,
    role TEXT NOT NULL,
    student_id TEXT,
    department TEXT
""")

create_if_not('channels', """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_code TEXT,
    channel_type TEXT,
    status TEXT,
    description TEXT,
    created_at TEXT,
    updated_at TEXT
""")

create_if_not('system_params', """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    param_key TEXT,
    param_name TEXT,
    param_value TEXT,
    description TEXT,
    updated_at TEXT
""")

create_if_not('T_StaffInfo', """
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT DEFAULT '安检员',
    department TEXT DEFAULT '安检科',
    phone TEXT,
    status TEXT DEFAULT '在岗'
""")

create_if_not('T_PassengerSim', """
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
""")

create_if_not('T_RosterResult', """
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
""")

create_if_not('T_ScheduleResult', """
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_id INTEGER,
    schedule_date TEXT NOT NULL,
    time_slot TEXT,
    expected_flow INTEGER,
    open_channels INTEGER,
    channel_ids TEXT,
    wait_time TEXT,
    utilization TEXT,
    channel_details TEXT,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sim_id) REFERENCES T_PassengerSim(sim_id)
""")


# ---------- 1.1 字段迁移（幂等，兼容旧库） ----------


def ensure_column(table, column, ddl):
    """旧库缺字段时补上，已存在则跳过；保证脚本可重复执行。"""
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"已为表 {table} 补充字段：{column}")


# system_params.param_name：参数中文名（老库无此列，界面曾直接显示英文 param_key）
ensure_column('system_params', 'param_name', 'TEXT')

# 用统一的中文名映射回填 / 刷新已有参数的中文名与说明
for _key, (_name, _desc) in _PARAM_META.items():
    cur.execute(
        "UPDATE system_params SET param_name=?, description=? WHERE param_key=?",
        (_name, _desc, _key),
    )
# 未登记在映射表中的自定义参数：至少保证中文名为非空（退回原 key，避免界面空白）
cur.execute(
    "UPDATE system_params SET param_name=param_key "
    "WHERE param_name IS NULL OR TRIM(param_name)=''"
)


# ---------- 2. 种子数据 ----------
# 2.1 用户（写入最终角色，确保 RBAC 直接可用）
users = [
    ('张家瑞', '240340089', '张家瑞', '系统管理员', '240340089', '系统管理员'),
    ('张智涛', '240340097', '张智涛', '值班调度员', '240340097', '调度中心'),
    ('石政',   '240340094', '石政',   '值班调度员', '240340094', '调度中心'),
    ('胡幸乃', '240340091', '胡幸乃', '排班人员',   '240340091', '人事部'),
    ('黄伟煊', '240340294', '黄伟煊', '排班人员',   '240340294', '人事部'),
]
for u in users:
    cur.execute(
        "INSERT OR IGNORE INTO users (username,password,real_name,role,student_id,department) VALUES (?,?,?,?,?,?)",
        u,
    )
    # 覆盖旧数据中残留的 admin/user 角色，保证为最终角色
    cur.execute(
        "UPDATE users SET role=?, department=? WHERE username=?",
        (u[3], u[5], u[0]),
    )

# 2.2 通道（与已有数据保持一致）
if cur.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0:
    channels = [
        ('通道01', '普通通道', '开放', '1号安检通道'),
        ('通道02', '普通通道', '开放', '2号安检通道'),
        ('通道03', '普通通道', '开放', '3号安检通道'),
        ('通道04', '普通通道', '关闭', '4号安检通道'),
        ('通道05', '普通通道', '开放', '5号安检通道'),
        ('通道06', '普通通道', '维护中', '6号安检通道'),
        ('通道07', '普通通道', '开放', '7号安检通道'),
    ]
    cur.executemany(
        "INSERT INTO channels (channel_code,channel_type,status,description,created_at,updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        channels,
    )

# 2.3 系统参数（中文名与说明统一取自 param_labels.PARAM_META）
if cur.execute("SELECT COUNT(*) FROM system_params").fetchone()[0] == 0:
    params = [
        (key, _PARAM_META[key][0], val, _PARAM_META[key][1])
        for key, val in (
            ('avg_security_speed', '2.5'),
            ('warning_threshold', '10'),
            ('departure_ratio', '0.5'),
            ('schedule_period', '每周'),
            ('staff_per_channel', '5'),
        ('default_airport', '天津滨海国际机场'),
        ('peak_start_hour', '6'),
        ('peak_end_hour', '9'),
        )
    ]
    cur.executemany(
        "INSERT INTO system_params (param_key,param_name,param_value,description,updated_at) "
        "VALUES (?,?,?,?,datetime('now'))",
        params,
    )

# 2.4 在岗安检员
if cur.execute("SELECT COUNT(*) FROM T_StaffInfo").fetchone()[0] == 0:
    staff = [
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
    cur.executemany(
        "INSERT INTO T_StaffInfo (name,role,department,phone,status) VALUES (?,?,?,?,?)",
        staff,
    )

# 2.5 机场列表（优先从 data 目录的原始 Excel 导入；缺失则跳过，不阻塞建库）
if cur.execute("SELECT COUNT(*) FROM airports").fetchone()[0] == 0 and pd is not None:
    candidates = [
        os.path.join(paths.DATA_DIR, '2025年全国民用运输机场吞吐量排名_原始.xlsx'),
        os.path.join(paths.DATA_DIR, '2025年全国枢纽机场分航线吞吐量_原始.xlsx'),
    ]
    src = next((p for p in candidates if os.path.exists(p)), None)
    if src:
        try:
            df = pd.read_excel(src)
            airports = df.iloc[4:, 0].dropna().tolist()
            rows = []
            for a in airports:
                if '/' in a:
                    city, name = a.split('/')
                else:
                    city, name = '', a
                rows.append((name.strip(), '', city.strip()))
            cur.executemany("INSERT INTO airports (name,code,city) VALUES (?,?,?)", rows)
            print(f"已导入 {len(rows)} 个机场（来源：{os.path.basename(src)}）")
        except Exception as e:
            print(f"导入机场列表失败：{e}")
    else:
        print("未找到机场原始 Excel，跳过机场导入（可在数据导入页补充）。")

conn.commit()
conn.close()
print("数据库初始化完成：已确保全部 8 张表存在，基础数据已就绪（可重复执行）。")
