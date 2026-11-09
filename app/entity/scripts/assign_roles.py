# -*- coding: utf-8 -*-
"""将4名组员随机分配为值班调度员或排班人员"""
import random
import sqlite3

# --- 路径锚定：无论从哪个目录执行，都能定位项目根与各层模块 ---
import os
import sys

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from entity.config import paths               # noqa: E402

DB = paths.DB_PATH
random.seed()  # 真随机

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

members = ['张智涛', '石政', '胡幸乃', '黄伟煊']
roles = ['值班调度员', '值班调度员', '排班人员', '排班人员']
random.shuffle(roles)
assign = dict(zip(members, roles))

for name, role in assign.items():
    conn.execute('UPDATE users SET role = ? WHERE username = ?', (role, name))

# 张家瑞保持系统管理员
conn.execute("UPDATE users SET role = '系统管理员', department = '系统管理员' WHERE username = '张家瑞'")
# 组员部门更新为对应部门
dept_map = {'值班调度员': '调度中心', '排班人员': '人事部'}
for name, role in assign.items():
    conn.execute('UPDATE users SET department = ? WHERE username = ?', (dept_map[role], name))

conn.commit()

print('=== 分配结果 ===')
for r in conn.execute('SELECT username, role, department FROM users ORDER BY id'):
    print(dict(r))
conn.close()
