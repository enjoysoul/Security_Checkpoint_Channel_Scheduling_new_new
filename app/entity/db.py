# -*- coding: utf-8 -*-
"""数据库访问：连接工厂。

路径统一取自 config.paths，因此测试可通过改写 paths.DB_PATH 指向临时库完成隔离。
"""
import sqlite3

from entity.config import paths


def get_db():
    """获取数据库连接（行工厂 sqlite3.Row，可按列名取值）。"""
    conn = sqlite3.connect(paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
