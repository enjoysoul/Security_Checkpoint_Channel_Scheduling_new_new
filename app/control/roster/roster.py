# -*- coding: utf-8 -*-
"""UC04 人员排班（控制层）。

  /roster          排班页面
  /generate-roster 由总人数需求生成排班（遗传算法优化 + 缺员自动补位）
  /save-roster     保存排班结果
  /export-roster   导出排班 CSV
"""
import json
from flask import render_template, request, jsonify, session, make_response

from control.common.registry import route
from control.common import get_db, get_current_sim
from control.subsystem import staffing


@route('/roster')
def roster():
    """人员排班界面"""
    # 从session获取当前模拟结果
    simulation_result = session.get('simulation_result', None)
    return render_template('roster/roster.html', active_page='roster', 
                          simulation_result=simulation_result)


@route('/generate-roster', methods=['POST'])
def generate_roster():
    """生成排班表"""
    week_start = request.form.get('weekStart', '')
    if not week_start:
        return jsonify({'success': False, 'message': '请选择排班周期'})

    # 获取当前客流模拟结果（优先会话，缺失时回退数据库最新记录）
    sim_result = get_current_sim()
    if not sim_result or not sim_result.get('hourlyFlow'):
        return jsonify({'success': False, 'message': '请先在数据导入页面完成客流模拟'})

    # 获取系统参数与在岗人员
    conn = get_db()
    params = conn.execute('SELECT * FROM system_params').fetchall()
    params_dict = {p['param_key']: p['param_value'] for p in params}
    staff_rows = conn.execute("SELECT * FROM T_StaffInfo WHERE status='在岗' ORDER BY staff_id").fetchall()
    conn.close()

    hourly_flow = sim_result.get('hourlyFlow', [])
    if isinstance(hourly_flow, str):
        hourly_flow = json.loads(hourly_flow)

    avg_speed = float(params_dict.get('avg_security_speed', '3.5'))
    staff_per_channel = int(params_dict.get('staff_per_channel', '5') or 5)
    staff_names = [s['name'] for s in staff_rows]

    week_days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

    # ---- 1) 计算各班次每天所需人数（确定性定员引擎，不再调用大模型）----
    # 由 24 小时客流分布 + 排队论（Erlang-C）推导各班次所需通道数与人数：
    # 窗口到达率 -> 满足等待阈值的最小通道数 -> 通道数×每通道定员 -> 备勤/周末系数 -> 限幅。
    # 旧实现把人数交给豆包大模型生成（联网失败即整条链路不可用），失败回退时用
    # flow/(60*avg_speed) 的粗略吞吐法，未考虑排队波动，高峰会系统性低估人手。
    try:
        c_max_cfg = int(float(params_dict.get('max_available_channels', '28') or 28))
        threshold_cfg = float(params_dict.get('warning_threshold', '15') or 15)
        weekend_cfg = float(params_dict.get('weekend_staff_factor', '1.15') or 1.15)
        reserve_cfg = float(params_dict.get('reserve_staff_factor', '0') or 0)
        departure_cfg = float(params_dict.get('departure_ratio', '0.5') or 0.5)
        max_staff_cfg = int(float(params_dict.get('max_shift_staff', '100000') or 100000))
        max_work_cfg = int(float(params_dict.get('max_work_days', '5') or 5))
    except Exception:
        c_max_cfg, threshold_cfg, weekend_cfg = 28, 10.0, 1.15
        reserve_cfg, departure_cfg, max_staff_cfg = 0.0, 0.5, 100000
        max_work_cfg = 5

    # 完整链路：年吞吐量 -> 24h 客流 -> 各班次通道数 -> 每班人数 -> 需求总编制人数。
    # 注意：单班人数上限只按「运营配置上限」限幅，不按在岗人数限幅，
    # 否则定员需求会被人为压低、导致真实编制需求被掩盖。
    plan = staffing.headcount_plan(
        hourly_flow,
        mu=avg_speed,
        staff_per_channel=staff_per_channel,
        threshold=threshold_cfg,
        c_max=max(1, c_max_cfg),
        weekend_factor=weekend_cfg,
        reserve_factor=reserve_cfg,
        min_staff=2,
        max_staff=max_staff_cfg,
        # 客流模拟已按出港占比折算为「安检客流」，这里不再二次折算
        security_ratio=1.0,
        max_work_days=max_work_cfg,
    )
    counts = plan['counts']
    staffing_detail = plan['shifts']
    required_hc = plan['headcount']['total']

    # ---- 1.5) 按测算编制补足人员池，使排班真正达到需求人数 ----
    # 在岗人数不足以支撑测算编制时，用「待补员」占位补足。这些占位代表
    # 需要招聘/调配的岗位，与真实在岗人员在名称上明确区分，并在返回结果中标注，
    # 避免把它们误当作已有员工。
    on_duty = list(staff_names)
    to_fill = []
    if len(on_duty) < required_hc:
        to_fill = ['待补员%02d' % (i + 1) for i in range(required_hc - len(on_duty))]
    pool_names = on_duty + to_fill
    roster_rows = [{'name': n} for n in pool_names]

    coverage = staffing.coverage_check(counts, len(on_duty), max_work_cfg)
    print('定员计算完成：%s' % {k: v['channels'] for k, v in staffing_detail.items()})
    print('测算编制：需 %d 人，在岗 %d 人，待补 %d 人（每人每周最多出勤 %d 天）'
          % (required_hc, len(on_duty), len(to_fill), max_work_cfg))

    # ---- 2) 依据人数生成具体人员排班 ----
    # 优先使用遗传算法约束优化引擎（满足休息/资质/公平/不重复等约束），
    # 失败或人员不足时回退到原轮转分配算法。
    opt_metrics = None
    try:
        from control.subsystem.scheduling_opt import build_roster as _ga_build
        # 使用「按年客流量测算后补足」的人员池（在岗 + 待补员占位）
        if roster_rows and sum(len(v) for v in counts.values()) > 0:
            ga_res = _ga_build(
                roster_rows, counts,
                params={'max_consecutive': 6, 'max_night_week': 3},
                generations=25, pop_size=30, seed=2026
            )
            roster_data = ga_res['roster']
            opt_metrics = ga_res['metrics']
            print('排班采用遗传算法优化，评分=%s，硬冲突=%s' % (
                opt_metrics.get('score'), opt_metrics.get('hard_conflicts')))
        else:
            roster_data = staffing.assign_names(counts, pool_names)
    except Exception as e:
        print(f"遗传算法排班失败，回退轮转分配：{e}")
        roster_data = staffing.assign_names(counts, pool_names)

    # 保存排班到session
    session['roster_data'] = roster_data
    session['roster_week'] = week_start
    
    # 持久化到数据库
    try:
        conn = get_db()
        sim_id = sim_result.get('sim_id')
        # 先删除该sim_id的旧排班记录
        conn.execute('DELETE FROM T_RosterResult WHERE sim_id = ?', (sim_id,))
        for shift_type, shift_info in [('早班', '06:00-14:00'), ('中班', '14:00-22:00'), ('晚班', '22:00-06:00')]:
            for i, day in enumerate(week_days):
                roster_date = f"{week_start}_{day}"
                data = roster_data[shift_type][i]
                conn.execute('''
                    INSERT INTO T_RosterResult 
                    (sim_id, roster_date, shift_type, shift_time, staff_count, staff_names, channels)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (sim_id, roster_date, shift_type, shift_info, 
                      data['count'], ','.join(data['names']), '通道01-04'))
        conn.commit()
        conn.close()
        print(f"排班已保存到数据库，sim_id={sim_id}")
    except Exception as e:
        print(f"保存排班到数据库失败：{e}")
    
    msg = ('排班生成成功（按年客流量测算需 %d 人：在岗 %d 人，另需补员 %d 人）'
           % (required_hc, len(on_duty), len(to_fill)))

    return jsonify({
        'success': True,
        'message': msg,
        'data': {
            'week_start': week_start,
            'week_days': week_days,
            'roster': roster_data,
            'optimizer': opt_metrics,
            # 定员中间量（可解释性：到达率/通道数/等待时间/利用率）
            'staffing': staffing_detail,
            # 由年客流量反算的编制方案：需求总人数 / 在岗 / 待补员
            'headcount': {
                'required': required_hc,
                'on_duty': len(on_duty),
                'to_fill': len(to_fill),
                'max_work_days': max_work_cfg,
                'per_shift': plan['headcount']['per_shift'],
                'message': ('按年客流量测算需 %d 人（在岗 %d 人，另需补员 %d 人）'
                            % (required_hc, len(on_duty), len(to_fill))),
            },
            'coverage': coverage,
        }
    })


@route('/save-roster', methods=['POST'])
def save_roster():
    """保存手动调整后的排班"""
    roster_data = request.form.get('rosterData', '')
    if not roster_data:
        return jsonify({'success': False, 'message': '没有排班数据'})
    
    try:
        data = json.loads(roster_data)
        session['roster_data'] = data
        return jsonify({'success': True, 'message': '排班已保存'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败：{str(e)}'})


@route('/export-roster')
def export_roster():
    """导出排班表为CSV"""
    import csv
    from io import StringIO
    from flask import make_response
    
    roster_data = session.get('roster_data', None)
    week_start = session.get('roster_week', '')
    
    if not roster_data:
        return jsonify({'success': False, 'message': '请先生成排班'})
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['安检通道调度系统 - 人员排班表'])
    writer.writerow([f'排班周期：{week_start}'])
    writer.writerow([])
    writer.writerow(['时段', '周一', '周二', '周三', '周四', '周五', '周六', '周日'])
    
    week_days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    for shift in ['早班 (06:00-14:00)', '中班 (14:00-22:00)', '晚班 (22:00-06:00)']:
        shift_key = shift.split(' ')[0]
        row = [shift]
        for i in range(7):
            if shift_key in roster_data and i < len(roster_data[shift_key]):
                count = roster_data[shift_key][i]['count']
                names = ','.join(roster_data[shift_key][i]['names'])
                row.append(f"{count}人({names})")
            else:
                row.append('')
        writer.writerow(row)
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=roster_{week_start}.csv'
    return response
