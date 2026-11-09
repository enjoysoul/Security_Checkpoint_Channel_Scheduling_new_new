# -*- coding: utf-8 -*-
"""UC05 客流量预测（控制层）。

  /predict      客流预测页面
  /api/predict  执行预测，返回历史/预测序列与评估指标
"""
from flask import request, jsonify, session

from control.common.registry import route
from control.common import get_db
from entity import caac
from control.subsystem import forecasting


@route('/api/predict', methods=['POST'])
def api_predict():
    """执行客流预测并返回历史/预测序列与评估指标"""
    airport = request.form.get('airport') or (session.get('simulation_result') or {}).get('airport') or ''
    try:
        days = max(1, min(365, int(request.form.get('days', 30))))
    except (TypeError, ValueError):
        days = 30
    method = request.form.get('method', 'linear')
    try:
        season_length = max(1, min(30, int(request.form.get('season', 7))))
    except (TypeError, ValueError):
        season_length = 7

    # 优先使用数据库已有模拟记录作为真实历史
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT daily_passengers FROM T_PassengerSim WHERE airport=? ORDER BY sim_id',
            (airport,)
        ).fetchall()
    finally:
        conn.close()

    if rows and len(rows) >= 8:
        res = forecasting.forecast_from_records(
            [{'daily_passengers': r[0]} for r in rows], days, method
        )
        if res.get('ok'):
            res['source'] = 'history'
            return jsonify({'success': True, 'data': res})

    # 回退：基于年吞吐量合成历史后预测
    tp = caac.get_airport_throughput(airport, session.get('custom_throughput')) or 800
    res = forecasting.forecast_daily(tp, days, method, n_days=120)
    res['source'] = 'synthetic'
    return jsonify({'success': True, 'data': res})


# 用户管理API（与登录共用users表）
