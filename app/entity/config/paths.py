# -*- coding: utf-8 -*-
"""全局路径常量：项目内所有文件位置的唯一来源。

各层只从这里取路径，不再各自拼接相对路径，
避免出现「换个工作目录执行就找不到数据库/数据文件」的问题。

本模块位于 <项目根>/entity/config/ 下，故需上溯两级才是项目根。
"""
import os

# 项目根目录（本文件位于 <项目根>/entity/config/ 下，需上溯两级）
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

# 《boundary》界面层资源：页面模板根与静态文件
TEMPLATE_DIR = os.path.join(BASE_DIR, 'boundary')
STATIC_DIR = os.path.join(BASE_DIR, 'boundary', 'static')

# 《entity》数据层资源（数据库、数据文件、上传、备份、日志均归入数据层）
DB_PATH = os.path.join(BASE_DIR, 'entity', 'security_checkpoint.db')   # SQLite 数据库
DATA_DIR = os.path.join(BASE_DIR, 'entity', 'data')                    # 原始数据与民航局数据文件
UPLOAD_DIR = os.path.join(BASE_DIR, 'entity', 'uploads')               # 用户上传文件
BACKUP_DIR = os.path.join(BASE_DIR, 'entity', 'backups')               # 数据库备份
LOG_DIR = os.path.join(BASE_DIR, 'entity', 'logs')                     # 运行日志

# 民航局公开吞吐量数据
CAAC_DATA_FILE = os.path.join(DATA_DIR, '2025_caac_throughput.json')
CAAC_LIST_URL = 'http://www.caac.gov.cn/XXGK/XXGK/TJSJ/index_1216.html'
