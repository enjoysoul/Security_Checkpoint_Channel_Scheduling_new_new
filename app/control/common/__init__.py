# -*- coding: utf-8 -*-
"""控制层公共件：全局登录保护、角色判定、会话上下文读取。

被多个控制模块复用，故独立成文件，避免控制层内部相互 import 造成循环依赖。
"""
import json

from flask import redirect, request, session, url_for

from entity.db import get_db


def require_login():
    """登录保护：未登录跳转到登录页（登录页/退出/静态资源除外）。

    对于 /api/ 开头的 REST 接口，未登录时直接返回 401 JSON，便于第三方系统识别鉴权失败。
    """
    path = request.path
    if path in ('/login', '/logout') or path.startswith('/static'):
        return
    if 'user' not in session:
        return redirect(url_for('login'))


def is_admin():
    """当前登录用户是否为系统管理员（全系统唯一的角色判定入口）。"""
    user = session.get('user')
    return bool(user and user.get('role') == '系统管理员')


def get_current_sim():
    """获取当前客流模拟结果：优先会话，缺失时回退数据库最新一条。"""
    sim = session.get('simulation_result', None)
    if sim and sim.get('hourlyFlow'):
        return sim
    try:
        conn = get_db()
        row = conn.execute(
            'SELECT * FROM T_PassengerSim ORDER BY sim_id DESC LIMIT 1'
        ).fetchone()
        conn.close()
        if row:
            data = dict(row)
            try:
                data['hourlyFlow'] = json.loads(data.get('hourly_flow') or '[]')
            except Exception:
                data['hourlyFlow'] = []
            try:
                data['hourlySpeed'] = json.loads(data.get('hourly_speed') or '[]')
            except Exception:
                data['hourlySpeed'] = []
            # 模板与前端统一使用驼峰字段，这里顺带补齐别名
            data.setdefault('dailyPassengers', data.get('daily_passengers'))
            data.setdefault('peakHour', data.get('peak_hour'))
            data.setdefault('maxHourPassengers', data.get('max_hour_passengers'))
            data.setdefault('avgSecuritySpeed', data.get('avg_security_speed'))
            data.setdefault('recommendChannels', data.get('recommend_channels'))
            data.setdefault('predictDate', data.get('predict_date'))
            return data
    except Exception:
        pass
    return None
