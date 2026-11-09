# -*- coding: utf-8 -*-
"""UC07 基础数据与系统参数管理（控制层，写操作仅系统管理员）。

  /api/airports        机场字典（数据导入页下拉）
  /data-management     数据管理页面
  /api/users/*         用户管理（与登录共用 users 表）
  /api/channels/*      安检通道配置
  /api/params/*        系统参数配置（中文名由 config.param_labels 统一提供）
"""
import sqlite3
from flask import render_template, request, redirect, url_for, jsonify, flash, session

from control.common.registry import route
from control.common import get_db, is_admin
from entity.config import param_labels


@route('/api/airports')
def api_airports():
    """获取所有机场列表"""
    conn = get_db()
    # 从模拟记录表中获取所有机场
    airports = conn.execute('SELECT DISTINCT airport FROM T_PassengerSim ORDER BY airport').fetchall()
    # 也从airports表中获取
    try:
        all_airports = conn.execute('SELECT DISTINCT name FROM airports ORDER BY name').fetchall()
        airport_set = set([a['airport'] for a in airports] + [a['name'] for a in all_airports])
    except:
        airport_set = set([a['airport'] for a in airports])
    conn.close()
    return jsonify({'airports': sorted(list(airport_set))})


@route('/data-management')
def data_management():
    """数据管理界面（仅系统管理员）"""
    user = session.get('user')
    if not user or user.get('role') not in ('系统管理员', '调度人员'):
        flash('请先登录后再访问数据管理', 'error')
        return redirect(url_for('login'))
    # 从数据库读取系统参数
    conn = get_db()
    system_params = conn.execute('SELECT * FROM system_params ORDER BY id').fetchall()
    conn.close()
    
    # 将参数转换为字典，方便模板使用
    params_dict = {}
    for param in system_params:
        params_dict[param['param_key']] = param['param_value']
    
    return render_template('data_management/data_management.html', 
                         active_page='data_management',
                         system_params=params_dict)



@route('/api/users')
def api_users():
    """用户列表：密码默认掩码，前端点「编辑」再按需拉取明文。"""
    me = session.get('user') or {}
    admin = me.get('role') == '系统管理员'
    conn = get_db()
    rows = conn.execute(
        'SELECT id, username, real_name, role, password FROM users ORDER BY id'
    ).fetchall()
    conn.close()

    data = []
    for r in rows:
        is_self = (r['username'] == me.get('username'))
        data.append({
            'id': r['id'],
            'username': r['username'],
            'real_name': r['real_name'] or r['username'],
            'role': r['role'],
            'password_masked': '*' * len(r['password'] or ''),
            'can_edit': bool(admin or is_self),
            'is_self': is_self,
            'is_admin': r['role'] == '系统管理员',
        })
    return jsonify({'success': True, 'data': data, 'is_admin': admin})
@route('/api/users/password', methods=['POST'])
def api_users_password():
    """点「编辑」时取明文密码：系统管理员可取任意用户，其余只能取自己。"""
    me = session.get('user') or {}
    uid = request.form.get('id')
    conn = get_db()
    row = conn.execute('SELECT username, password FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'message': '用户不存在'})
    if me.get('role') != '系统管理员' and row['username'] != me.get('username'):
        return jsonify({'success': False, 'message': '只能查看本人的密码'})
    return jsonify({'success': True, 'password': row['password']})


@route('/api/users/update', methods=['POST'])
def api_users_update():
    """更新用户信息。管理员可改所有人与角色；调度人员只能改自己（角色不可改）。"""
    me = session.get('user') or {}
    admin = me.get('role') == '系统管理员'
    uid = request.form.get('id')
    role = request.form.get('role')
    password = request.form.get('password')

    if not uid:
        return jsonify({'success': False, 'message': '参数不完整'})

    conn = get_db()
    target = conn.execute('SELECT id, username, role FROM users WHERE id = ?', (uid,)).fetchone()
    if not target:
        conn.close()
        return jsonify({'success': False, 'message': '用户不存在'})

    if not admin and target['username'] != me.get('username'):
        conn.close()
        return jsonify({'success': False, 'message': '只能修改本人的信息'})

    fields, values = [], []
    if role:
        if admin:
            fields.append('role = ?')
            values.append(role)
        elif role != target['role']:
            conn.close()
            return jsonify({'success': False, 'message': '角色仅系统管理员可调整'})
    if password:
        fields.append('password = ?')
        values.append(password)

    if not fields:
        conn.close()
        return jsonify({'success': False, 'message': '没有需要保存的修改'})

    values.append(uid)
    conn.execute('UPDATE users SET %s WHERE id = ?' % ', '.join(fields), values)
    conn.commit()
    conn.close()

    # 改的是自己，同步刷新会话里的用户名（密码不影响会话）
    if target['username'] == me.get('username'):
        me['role'] = role or me.get('role')
        session['user'] = me

    return jsonify({'success': True, 'message': '用户信息已保存'})
@route('/api/users/add', methods=['POST'])
def api_users_add():
    """添加用户（默认密码123456，学号留空）"""
    user = session.get('user')
    if not user or user.get('role') != '系统管理员':
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    username = request.form.get('username')
    role = request.form.get('role') or '调度人员'
    department = ''
    
    password = request.form.get('password') or '123456'
    if not username:
        return jsonify({'success': False, 'message': '请输入用户名'})
    
    conn = get_db()
    exists = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({'success': False, 'message': '该用户名已存在'})
    
    conn.execute(
        'INSERT INTO users (username, password, real_name, role, student_id, department) VALUES (?, ?, ?, ?, ?, ?)',
        (username, password, username, role, '', '')
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': '新用户已添加'})


@route('/api/users/delete', methods=['POST'])
def api_users_delete():
    """删除用户"""
    user = session.get('user')
    if not user or user.get('role') != '系统管理员':
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    user_id = request.form.get('id')
    if not user_id:
        return jsonify({'success': False, 'message': '参数不完整'})
    
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': '用户已删除'})



@route('/api/channels')
def api_channels():
    """列出全部安检通道"""
    conn = get_db()
    rows = conn.execute(
        'SELECT id, channel_code, channel_type, status, description FROM channels ORDER BY id'
    ).fetchall()
    conn.close()
    return jsonify({'success': True, 'data': [dict(r) for r in rows]})


@route('/api/channels/add', methods=['POST'])
def api_channels_add():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    code = (request.form.get('channel_code') or '').strip()
    ctype = (request.form.get('channel_type') or '普通通道').strip()
    status = (request.form.get('status') or '开放').strip()
    desc = (request.form.get('description') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '请输入通道编号'})
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO channels (channel_code,channel_type,status,description,created_at,updated_at) "
            "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
            (code, ctype, status, desc),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': '通道编号已存在'})
    conn.close()
    return jsonify({'success': True, 'message': '通道已添加'})


@route('/api/channels/update', methods=['POST'])
def api_channels_update():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    cid = request.form.get('id')
    code = (request.form.get('channel_code') or '').strip()
    ctype = (request.form.get('channel_type') or '普通通道').strip()
    status = (request.form.get('status') or '开放').strip()
    desc = (request.form.get('description') or '').strip()
    if not cid or not code:
        return jsonify({'success': False, 'message': '参数不完整'})
    conn = get_db()
    conn.execute(
        "UPDATE channels SET channel_code=?,channel_type=?,status=?,description=?,updated_at=datetime('now') WHERE id=?",
        (code, ctype, status, desc, cid),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '通道已更新'})


@route('/api/channels/delete', methods=['POST'])
def api_channels_delete():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    cid = request.form.get('id')
    if not cid:
        return jsonify({'success': False, 'message': '参数不完整'})
    conn = get_db()
    conn.execute('DELETE FROM channels WHERE id=?', (cid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '通道已删除'})


@route('/api/params')
def api_params():
    """列出全部系统参数

    `param_name` 为参数中文名，供界面展示；`param_key` 为程序内部英文标识，
    仅作小号附注。历史数据若缺少中文名，则按 param_labels 映射自动补齐。
    """
    conn = get_db()
    rows = conn.execute(
        'SELECT id, param_key, param_name, param_value, description FROM system_params ORDER BY id'
    ).fetchall()
    hidden = {}
    rows = [r for r in rows if r['param_key'] not in ('max_channel_capacity', 'max_open_channels')]
    conn.close()
    data = []
    for r in rows:
        item = dict(r)
        name = (item.get('param_name') or '').strip()
        item['param_name'] = name or param_labels.display_name(item.get('param_key'))
        if not (item.get('description') or '').strip():
            item['description'] = param_labels.description(item.get('param_key'))
        data.append(item)
    return jsonify({'success': True, 'data': data})


@route('/api/params/add', methods=['POST'])
def api_params_add():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    key = (request.form.get('param_key') or '').strip()
    name = (request.form.get('param_name') or '').strip()
    value = (request.form.get('param_value') or '').strip()
    desc = (request.form.get('description') or '').strip()
    if not key:
        return jsonify({'success': False, 'message': '请输入参数标识'})
    if not name:
        name = param_labels.display_name(key)
    if not desc:
        desc = param_labels.description(key)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO system_params (param_key,param_name,param_value,description,updated_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (key, name, value, desc),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': '该参数标识已存在'})
    conn.close()
    return jsonify({'success': True, 'message': '参数已添加'})


@route('/api/params/update', methods=['POST'])
def api_params_update():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    pid = request.form.get('id')
    key = (request.form.get('param_key') or '').strip()
    name = (request.form.get('param_name') or '').strip()
    value = (request.form.get('param_value') or '').strip()
    desc = (request.form.get('description') or '').strip()
    if not pid or not key:
        return jsonify({'success': False, 'message': '参数不完整'})
    if not name:
        name = param_labels.display_name(key)
    conn = get_db()
    conn.execute(
        "UPDATE system_params SET param_key=?,param_name=?,param_value=?,description=?,"
        "updated_at=datetime('now') WHERE id=?",
        (key, name, value, desc, pid),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '参数已更新'})


@route('/api/params/delete', methods=['POST'])
def api_params_delete():
    if not is_admin():
        return jsonify({'success': False, 'message': '仅系统管理员可操作'})
    pid = request.form.get('id')
    if not pid:
        return jsonify({'success': False, 'message': '参数不完整'})
    conn = get_db()
    conn.execute('DELETE FROM system_params WHERE id=?', (pid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '参数已删除'})
