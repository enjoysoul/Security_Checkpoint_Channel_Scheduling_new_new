# -*- coding: utf-8 -*-
"""UC00 用户认证（控制层）：首页跳转、登录、登出。"""
from flask import render_template, request, redirect, url_for, flash, session

from control.common.registry import route

from control.common import get_db


@route('/')
def index():
    """首页 - 重定向到登录页"""
    return redirect(url_for('login'))


@route('/login', methods=['GET', 'POST'])
def login():
    """用户登录界面"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # 数据库验证
        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        conn.close()
        
        if user:
            # 登录成功，写入会话并跳转到数据导入页
            session['user'] = {
                'username': user['username'],
                'real_name': user['real_name'],
                'role': user['role'],
                'department': user['department']
            }
            return redirect(url_for('data_import'))
        else:
            # 登录失败，重定向回登录页并显示错误信息
            flash('用户名或密码错误', 'error')
            return redirect(url_for('login'))
    
    return render_template('login/login.html')



@route('/logout')
def logout():
    """退出登录"""
    session.clear()
    return redirect(url_for('login'))
