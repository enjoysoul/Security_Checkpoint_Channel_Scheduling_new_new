# -*- coding: utf-8 -*-
"""UC02/UC03 安检通道调度与决策建议（控制层）。

  /schedule            通道调度页面
  /calculate-schedule  依据当前客流模拟结果计算开放通道数、等待时间与决策建议
"""
import json

from flask import render_template, request, jsonify, session

from control.common.registry import route
from control.common import get_db, get_current_sim
from control.subsystem import advisor


@route('/schedule')
def schedule():
    """通道调度界面"""
    # 从session中获取模拟结果
    simulation_result = get_current_sim()
    
    # 从数据库读取通道数据
    conn = get_db()
    channels = conn.execute('SELECT * FROM channels ORDER BY id').fetchall()
    conn.close()
    
    return render_template('schedule/schedule.html', 
                         active_page='schedule', 
                         simulation_result=simulation_result,
                         channels=channels)


@route('/calculate-schedule', methods=['POST'])
def calculate_schedule():
    """计算调度建议API"""
    date = request.form.get('date')
    hour = request.form.get('hour')
    
    try:
        hour_idx = int(hour)
    except:
        return jsonify({'success': False, 'message': '时段参数错误'})
    
    # 从session获取模拟结果
    simulation_result = get_current_sim()
    
    if not simulation_result:
        return jsonify({'success': False, 'message': '请先在数据导入页面完成客流模拟'})
    
    # 获取该时段的客流量
    hourly_flow = simulation_result.get('hourlyFlow', [])
    if hour_idx >= len(hourly_flow):
        return jsonify({'success': False, 'message': '模拟数据中没有该时段的数据'})
    
    hour_flow = hourly_flow[hour_idx]
    
    # 从数据库获取系统参数和通道数据
    conn = get_db()
    avg_speed_row = conn.execute('SELECT param_value FROM system_params WHERE param_key = ?', ('avg_security_speed',)).fetchone()
    warning_threshold_row = conn.execute('SELECT param_value FROM system_params WHERE param_key = ?', ('warning_threshold',)).fetchone()
    staff_row = conn.execute("SELECT param_value FROM system_params WHERE param_key = 'staff_per_channel'").fetchone()
    
    # 获取所有通道数据，计算可用通道数（排除维护中的）
    all_channels = conn.execute('SELECT * FROM channels ORDER BY id').fetchall()
    total_channels = len(all_channels)
    available_channels = [c for c in all_channels if c['status'] != '维护中']
    available_count = len(available_channels)
    open_channels = [c for c in all_channels if c['status'] == '开放']
    open_count = len(open_channels)
    conn.close()
    
    avg_speed = float(avg_speed_row['param_value']) if avg_speed_row else 3.5
    warning_threshold = float(warning_threshold_row['param_value']) if warning_threshold_row else 10
    staff_per_channel = int(staff_row['param_value']) if staff_row else 4
    
    # 该时段安检速度：优先使用模拟生成的24小时分时段速度（高峰时段速度低、低谷时段速度高）
    hourly_speeds = simulation_result.get('hourlySpeed', [])
    if isinstance(hourly_speeds, list) and hour_idx < len(hourly_speeds):
        try:
            avg_speed = float(hourly_speeds[hour_idx])
        except:
            pass
    
    # 计算建议开放通道数（标准 Erlang C 排队论）
    # 单位统一为“人/分钟”，等待时间以“分钟”计，与前端显示单位一致
    mu = avg_speed  # 单通道服务率（人/分钟）
    if mu <= 0:
        mu = 3.5
    lam = hour_flow / 60.0  # 到达率（人/分钟）
    # 决策建议引擎（UC03）：生成候选方案 -> 多维打分 -> 规则裁决 -> 输出带依据的建议。
    # 取代原先「排队论求出通道数后拼一句固定话术」的做法：
    # 建议改为可复现、可解释（返回候选方案打分与触发的规则链），且完全离线可用。
    maintain_count = len([c for c in all_channels if c['status'] == '维护中'])
    closed_count = len([c for c in all_channels if c['status'] == '关闭'])
    channel_info = f"当前共有{total_channels}个通道，其中{open_count}个开放，{closed_count}个关闭，{maintain_count}个维护中。"
    speed_info = f"该时段安检速度{avg_speed}人/分钟"

    advice = advisor.advise(
        lam, mu,
        c_current=open_count,
        c_available=max(1, available_count),
        threshold=warning_threshold,
        staff_per_channel=staff_per_channel,
        hour_slot=f"当前时段（{hour_idx}:00-{hour_idx+1}:00）",
        channel_info=channel_info,
        speed_info=speed_info,
    )
    recommend_channels = advice['recommendChannels']
    avg_wait_time = advice['avgWaitTime']
    utilization = advice['utilization']

    # 经验校准：M/M/c 在 ρ 中等时给出接近 0 的排队时间（如 0.02 分钟），与现场体验不符；
    # 叠加「旅客成批到达 + 开包复检」造成的附加等待，使等待随负荷平滑上升：
    #   ρ=0.82 → 约 1.0 分钟；ρ=0.90 → 约 2.5 分钟；ρ=0.95 → 约 4 分钟；ρ→1 → 约 6 分钟
    # 仅在系统可排队（ρ<1）时校准，超载仍由下方逻辑标记为「>20 分钟」。
    try:
        _rho = float(utilization or 0) / 100.0
        if 0 < _rho < 0.99 and avg_wait_time is not None:
            avg_wait_time = round(float(avg_wait_time) + 0.5 + 10.0 * (_rho ** 12), 1)
    except Exception:
        pass
    suggestion = advice['suggestion']

    # 现有开放通道仍不满足等待要求时，从可用通道（备用/补班）中补足
    if avg_wait_time is not None and avg_wait_time > warning_threshold \
            and available_count > recommend_channels:
        fix = advisor.queuing.best_channel(lam, mu, warning_threshold, available_count)
        if fix['feasible'] and fix['channels'] > recommend_channels:
            recommend_channels = fix['channels']
            avg_wait_time = round(fix['wq'], 2) if fix['wq'] != float('inf') else None
            utilization = round(fix['rho'] * 100, 1)
            suggestion = ('当前开放 %d 条通道时平均等待超过 %.0f 分钟阈值，'
                          '建议加开至 %d 条（启用备用/补班通道），预计等待 %.1f 分钟。'
                          % (open_count, warning_threshold, recommend_channels,
                             avg_wait_time or 0))
            advice['recommendChannels'] = recommend_channels
            advice['avgWaitTime'] = avg_wait_time
            advice['utilization'] = utilization
            advice['suggestion'] = suggestion
        else:
            suggestion += '（可用通道已达上限 %d 条，仍无法满足等待要求，建议限流或增援）' % available_count

    print('决策建议：%s -> %d 条通道（评分 %.3f），触发规则 %d 条'
          % (advice['best_name'], recommend_channels, advice['score'], len(advice['rules'])))

    # 若当前方案不可行（等待超阈值且加满通道也不够），建议值应给到「全部可用通道」，
    # 而不是停留在当前开放数，否则界面会显示"建议 23 条却等待 20 分钟"的自相矛盾结果。
    if (avg_wait_time is None or avg_wait_time > warning_threshold) and available_count > recommend_channels:
        recommend_channels = available_count
        if avg_wait_time is not None and avg_wait_time > warning_threshold:
            suggestion = ('当前时段客流超出运力：全部开放 %d 条通道后预计等待仍约 %.1f 分钟，'
                          '高于 %.0f 分钟阈值，建议启动限流、增派支援或引导旅客错峰。'
                          % (available_count, avg_wait_time, warning_threshold))

    result_data = {
        'hourFlow': hour_flow,
        'recommendChannels': recommend_channels,
        'avgWaitTime': avg_wait_time,
        'utilization': utilization,
        'avgSpeed': avg_speed,
        'suggestion': suggestion,
        # 决策过程（候选方案打分与触发规则），用于可解释性追溯
        'advisor': {
            'best_name': advice['best_name'],
            'score': advice['score'],
            'rules': advice['rules'],
            'scenarios': advice['scenarios'],
        },
    }
    
    # 落库：把本次计算结果写入 T_ScheduleResult，供历史分析做「等待时间 vs 阈值」比对
    try:
        sim_res = session.get('simulation_result') or {}
        sim_id = sim_res.get('sim_id')
        if not sim_id:      # 会话丢失时回退到最近一次模拟记录
            _c = get_db()
            _row = _c.execute('SELECT MAX(sim_id) AS m FROM T_PassengerSim').fetchone()
            sim_id = _row['m'] if _row else None
            _c.close()
        if sim_id:
            conn = get_db()
            conn.execute('DELETE FROM T_ScheduleResult WHERE sim_id = ? AND time_slot = ?',
                         (sim_id, str(hour_idx)))
            # 开放通道编号：按顺序取前 N 条（优先已开放，不足再启备用）
            pool = [c['channel_code'] for c in all_channels if c['status'] == '开放']
            pool += [c['channel_code'] for c in all_channels if c['status'] != '开放']
            picked = pool[:int(recommend_channels or 0)]
            detail = []
            for code in picked:
                detail.append({
                    'channel_code': code,
                    'channel_type': next((c['channel_type'] for c in all_channels
                                          if c['channel_code'] == code), ''),
                    'status': '开放',
                    'wait_count': int(round((lam * (avg_wait_time or 0)) / max(1, len(picked)))),
                    'wait_time': round(avg_wait_time or 0, 1),
                })
            conn.execute(
                'INSERT INTO T_ScheduleResult (sim_id, schedule_date, time_slot, expected_flow, '
                'open_channels, channel_ids, wait_time, utilization, channel_details) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (sim_id, simulation_result.get('predict_date', ''), str(hour_idx), hour_flow,
                 recommend_channels, ','.join(picked), avg_wait_time, utilization,
                 json.dumps(detail, ensure_ascii=False)))
            conn.commit()
            conn.close()
    except Exception as e:
        print('调度结果落库失败：%s' % e)

    # 超载标记：等待已超过阈值，前端据此显示「＞N 分钟（严重超载）」而不是精确值
    try:
        if avg_wait_time is not None and float(avg_wait_time) > float(warning_threshold):
            result_data['overload'] = True
    except Exception:
        pass

    return jsonify({'success': True, 'data': result_data})
