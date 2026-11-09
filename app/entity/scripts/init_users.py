# -*- coding: utf-8 -*-
"""初始化用户表（首次建库用）。

注意：本脚本写入的是简易角色（admin/user）。真正的业务角色
（系统管理员 / 值班调度员 / 排班人员）由 scripts/init_db.py 统一维护，
因此这里使用 INSERT OR IGNORE——已存在的用户不会被覆盖，避免把角色降级。
"""
import os
import sys
import sqlite3

# --- 路径锚定：无论从哪个目录执行，都能定位项目根与各层模块 ---

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from entity.config import paths               # noqa: E402

# 数据库路径
db_path = paths.DB_PATH

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 创建用户表
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    real_name TEXT NOT NULL,
    role TEXT NOT NULL,
    student_id TEXT,
    department TEXT
)
''')

# 所有用户列表
users = [
    # 管理员
    ('张家瑞', '240340089', '张家瑞', 'admin', '240340089', '系统管理员'),
    # 组员
    ('张智涛', '240340097', '张智涛', 'user', '240340097', '组员'),
    ('石政', '240340094', '石政', 'user', '240340094', '组员'),
    ('胡幸乃', '240340091', '胡幸乃', 'user', '240340091', '组员'),
    ('黄伟煊', '240340294', '黄伟煊', 'user', '240340294', '组员'),
]

# 插入用户
for user in users:
    cursor.execute('''
    INSERT OR IGNORE INTO users (username, password, real_name, role, student_id, department)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', user)

# 提交并关闭
conn.commit()
print(f"用户表就绪：本次新增 {len(users)} 条候选记录（已存在的用户保持不变）")
conn.close()
