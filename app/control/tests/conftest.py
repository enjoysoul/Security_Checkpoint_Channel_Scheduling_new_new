# -*- coding: utf-8 -*-
"""pytest 公共夹具：用临时数据库隔离测试，避免污染真实运营数据。"""
import os
import shutil
import tempfile

import pytest

import app as appmod
from entity.config import paths


@pytest.fixture
def client():
    """提供一个指向「临时副本数据库」的 Flask 测试客户端。

    分层重构后数据库路径由 config.paths.DB_PATH 统一提供，
    这里把它重定向到临时副本，使数据层与各控制模块都落在临时库，
    测试过程中产生的写入不会影响真实 security_checkpoint.db。
    """
    src_db = paths.DB_PATH
    tmp_root = tempfile.mkdtemp(prefix='sccs_test_')
    tmp_db_dir = os.path.join(tmp_root, 'db')
    os.makedirs(tmp_db_dir, exist_ok=True)
    dst_db = os.path.join(tmp_db_dir, 'security_checkpoint.db')
    if os.path.exists(src_db):
        shutil.copy(src_db, dst_db)

    original_db_path = paths.DB_PATH
    paths.DB_PATH = dst_db
    appmod.app.config['TESTING'] = True

    with appmod.app.test_client() as c:
        yield c

    paths.DB_PATH = original_db_path
    shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.fixture
def admin_client(client):
    """已登录系统管理员的客户端。"""
    client.post('/login', data={'username': '张家瑞', 'password': '240340089'},
                follow_redirects=True)
    return client
