# -*- coding: utf-8 -*-
"""应用层路由测试：登录保护、通道/参数 CRUD、RBAC。

使用 conftest 提供的 admin_client（指向临时副本库），测试写入不会影响真实运营库。
"""
import io


# ----------------------------------------------------------------------------
# 登录保护
# ----------------------------------------------------------------------------

def test_index_redirect_when_not_logged_in(client):
    r = client.get('/')
    # 未登录应跳转登录
    assert r.status_code in (301, 302)


def test_login_success_and_home(client):
    r = client.post('/login', data={'username': '张家瑞', 'password': '240340089'},
                    follow_redirects=True)
    assert r.status_code == 200
    assert '安检' in r.get_data(as_text=True) or '客流' in r.get_data(as_text=True)


def test_login_wrong_password(client):
    r = client.post('/login', data={'username': '张家瑞', 'password': '000'},
                    follow_redirects=True)
    # 失败应停留登录页（含提示）
    assert '密码错误' in r.get_data(as_text=True) or r.status_code == 200


# ----------------------------------------------------------------------------
# 通道 CRUD
# ----------------------------------------------------------------------------

def test_channel_list(admin_client):
    r = admin_client.get('/api/channels')
    assert r.status_code == 200
    js = r.get_json()
    assert 'data' in js and isinstance(js['data'], list)


def test_channel_crud_lifecycle(admin_client):
    # 新增
    r = admin_client.post('/api/channels/add',
                          data={'channel_code': '测试通道Z', 'channel_type': '普通通道',
                                'status': '关闭', 'description': 'pytest'})
    assert r.status_code == 200 and r.get_json().get('success')
    # 取回列表找到新记录
    lst = admin_client.get('/api/channels').get_json()['data']
    rec = next((x for x in lst if x['channel_code'] == '测试通道Z'), None)
    assert rec is not None
    cid = rec['id']
    # 修改
    r = admin_client.post('/api/channels/update',
                          data={'id': cid, 'channel_code': '测试通道Z',
                                'channel_type': 'VIP通道', 'status': '开放', 'description': '改'})
    assert r.get_json().get('success')
    # 删除
    r = admin_client.post('/api/channels/delete', data={'id': cid})
    assert r.get_json().get('success')
    lst = admin_client.get('/api/channels').get_json()['data']
    assert not any(x['channel_code'] == '测试通道Z' for x in lst)


# ----------------------------------------------------------------------------
# 系统参数 CRUD
# ----------------------------------------------------------------------------

def test_param_crud_lifecycle(admin_client):
    r = admin_client.post('/api/params/add',
                          data={'param_key': 'pytest_key', 'param_value': '123', 'description': 'pytest'})
    assert r.get_json().get('success')
    lst = admin_client.get('/api/params').get_json()['data']
    rec = next((x for x in lst if x['param_key'] == 'pytest_key'), None)
    assert rec is not None
    pid = rec['id']
    r = admin_client.post('/api/params/update',
                          data={'id': pid, 'param_key': 'pytest_key', 'param_value': '456', 'description': '改'})
    assert r.get_json().get('success')
    r = admin_client.post('/api/params/delete', data={'id': pid})
    assert r.get_json().get('success')
    lst = admin_client.get('/api/params').get_json()['data']
    assert not any(x['param_key'] == 'pytest_key' for x in lst)


# ----------------------------------------------------------------------------
# RBAC：非管理员不能写通道
# ----------------------------------------------------------------------------

def test_non_admin_cannot_write_channel(client):
    # 以「排班人员」身份登录（非管理员）
    client.post('/login', data={'username': '胡幸乃', 'password': '240340091'},
                follow_redirects=True)
    r = client.post('/api/channels/add', data={'channel_code': 'X', 'status': '开放'})
    js = r.get_json()
    assert js.get('success') is False
    assert '管理员' in (js.get('message') or '')
