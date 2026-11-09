# -*- coding: utf-8 -*-
import re
"""UC06 历史数据查询与导出（控制层）。

  /analysis              历史分析页面
  /query-history         按条件查询历史模拟/排班记录
  /analysis/detail/<id>  单条记录明细
  /export-history        导出历史记录 CSV
  /save-schedule-result  保存一次调度结果
"""
import json
from flask import render_template, request, jsonify, session, make_response

from control.common.registry import route
from control.common import get_db


@route('/analysis')
def analysis():
    """历史分析界面"""
    return render_template('analysis/analysis.html', active_page='analysis')


@route('/query-history', methods=['POST'])
def query_history():
    """查询历史模拟记录"""
    start_date = request.form.get('startDate', '')
    end_date = request.form.get('endDate', '')
    airport = request.form.get('airport', '')
    page = int(request.form.get('page', '1'))
    page_size = 10
    
    conn = get_db()
    
    # 构建查询条件
    query = "SELECT * FROM T_PassengerSim WHERE 1=1"
    params = []
    
    if start_date:
        query += " AND date(sim_time) >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date(sim_time) <= ?"
        params.append(end_date)
    if airport:
        query += " AND airport LIKE ?"
        params.append(f'%{airport}%')
    
    # 统计总数
    count_query = query.replace('SELECT *', 'SELECT COUNT(*)')
    total = conn.execute(count_query, params).fetchone()[0]
    
    # 分页查询
    query += " ORDER BY sim_time DESC LIMIT ? OFFSET ?"
    params.append(page_size)
    params.append((page - 1) * page_size)
    
    records = conn.execute(query, params).fetchall()

    # 该批记录对应的调度结果（平均/最大等待时间、开放通道数）与预警阈值
    sched = {}
    threshold = None
    try:
        row = conn.execute(
            "SELECT param_value FROM system_params WHERE param_key='warning_threshold'"
        ).fetchone()
        threshold = float(row['param_value']) if row else None
    except Exception:
        threshold = None
    ids = [r['sim_id'] for r in records]
    if ids:
        q = ("SELECT sim_id, AVG(CAST(wait_time AS REAL)) AS avg_wait, "
             "MAX(CAST(wait_time AS REAL)) AS max_wait, "
             "MAX(open_channels) AS max_channels FROM T_ScheduleResult "
             "WHERE sim_id IN (%s) GROUP BY sim_id" % ','.join('?' * len(ids)))
        for r in conn.execute(q, ids):
            sched[r['sim_id']] = r
    conn.close()
    
    # 转换为字典列表
    record_list = []
    for r in records:
        # 将UTC时间转换为北京时间
        sim_time = r['sim_time']
        try:
            from datetime import datetime, timedelta
            dt = datetime.strptime(sim_time, '%Y-%m-%d %H:%M:%S')
            dt = dt + timedelta(hours=8)
            sim_time = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass
        record_list.append({
            'sim_id': r['sim_id'],
            'sim_time': sim_time,
            'airport': r['airport'],
            'predict_date': r['predict_date'],
            'daily_passengers': r['daily_passengers'],
            'peak_hour': r['peak_hour'],
            'has_schedule': r['sim_id'] in sched,
            'has_roster': True,
            'schedule_channels': (sched[r['sim_id']]['max_channels']
                                  if r['sim_id'] in sched else None),
            'avg_wait': (round(sched[r['sim_id']]['avg_wait'], 1)
                         if r['sim_id'] in sched and sched[r['sim_id']]['avg_wait'] is not None else None),
            'max_wait': (round(sched[r['sim_id']]['max_wait'], 1)
                         if r['sim_id'] in sched and sched[r['sim_id']]['max_wait'] is not None else None),
            'wait_threshold': threshold
        })
    
    total_pages = max(1, (total + page_size - 1) // page_size)
    
    return jsonify({
        'success': True,
        'data': {
            'records': record_list,
            'total': total,
            'page': page,
            'total_pages': total_pages
        }
    })


@route('/analysis/detail/<int:record_id>')
def analysis_detail(record_id):
    """模拟详情界面"""
    conn = get_db()
    record = conn.execute('SELECT * FROM T_PassengerSim WHERE sim_id = ?', (record_id,)).fetchone()
    
    if not record:
        conn.close()
        return "记录不存在", 404
    
    # 将UTC时间转换为北京时间
    record_dict = dict(record)
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(record_dict['sim_time'], '%Y-%m-%d %H:%M:%S')
        dt = dt + timedelta(hours=8)
        record_dict['sim_time'] = dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        pass
    
    # 查询关联的排班结果
    roster_results = conn.execute('SELECT * FROM T_RosterResult WHERE sim_id = ?', (record_id,)).fetchall()
    
    # 查询关联的调度结果
    schedule_results = conn.execute('SELECT * FROM T_ScheduleResult WHERE sim_id = ? ORDER BY time_slot', (record_id,)).fetchall()
    conn.close()
    
    # 解析channel_details为Python对象
    schedule_list = []
    for s in schedule_results:
        s_dict = dict(s)
        if s_dict.get('channel_details'):
            try:
                s_dict['channel_details'] = json.loads(s_dict['channel_details'])
            except:
                s_dict['channel_details'] = None
        schedule_list.append(s_dict)
    
    # 解析每小时客流量
    hourly_flow = record['hourly_flow']
    if isinstance(hourly_flow, str):
        try:
            hourly_flow = json.loads(hourly_flow)
        except:
            hourly_flow = []
    
    # 组织排班数据
    roster_data = {'早班': [], '中班': [], '晚班': []}
    week_days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    for shift in ['早班', '中班', '晚班']:
        shift_records = [r for r in roster_results if r['shift_type'] == shift]
        for day in week_days:
            day_record = next((r for r in shift_records if day in r['roster_date']), None)
            if day_record:
                roster_data[shift].append({
                    'count': day_record['staff_count'],
                    'names': day_record['staff_names'].split(',') if day_record['staff_names'] else []
                })
            else:
                roster_data[shift].append({'count': 0, 'names': []})
    
    # 时段文本：'10' -> '10:00-11:00'，避免详情页只显示裸数字
    for _s in schedule_list:
        try:
            _h = int(str(_s.get('time_slot', '')).strip())
            _s['slot_text'] = '%02d:00-%02d:00' % (_h, (_h + 1) % 24)
        except Exception:
            _s['slot_text'] = str(_s.get('time_slot') or '-')

    return render_template('analysis/analysis_detail.html', 
                          active_page='analysis',
                          record=record_dict,
                          hourly_flow=hourly_flow,
                          roster_data=roster_data,
                          schedule_results=schedule_list)


@route('/export-history')
def export_history():
    """导出历史记录为CSV"""
    import csv
    from io import StringIO
    from flask import make_response
    
    conn = get_db()
    records = conn.execute('SELECT * FROM T_PassengerSim ORDER BY sim_time DESC').fetchall()
    conn.close()
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['模拟时间', '机场', '预测日期', '日客流量', '高峰时段', '小时最大客流', '建议通道数', '平均安检速度'])
    
    for r in records:
        writer.writerow([
            r['sim_time'], r['airport'], r['predict_date'],
            r['daily_passengers'], r['peak_hour'],
            r['max_hour_passengers'], r['recommend_channels'],
            r['avg_security_speed']
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=history_records.csv'
    return response


@route('/save-schedule-result', methods=['POST'])
def save_schedule_result():
    """保存通道调度结果到数据库"""
    try:
        data = request.get_json()
        sim_id = data.get('sim_id')
        schedule_date = data.get('schedule_date', '')
        time_slot = data.get('time_slot', '')

        def _num(v, default=None):
            """从 "2.7 分钟" / "99%" / "28 个" 这类文本里取出数字。"""
            if isinstance(v, (int, float)):
                return float(v)
            m = re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
            return float(m.group(0)) if m else default

        def _dedupe(ids):
            """通道编号去重，保持原顺序。"""
            if isinstance(ids, (list, tuple)):
                seq = [str(x).strip() for x in ids]
            else:
                seq = [x.strip() for x in str(ids or '').replace('，', ',').split(',')]
            seen, out = set(), []
            for x in seq:
                if x and x not in seen:
                    seen.add(x)
                    out.append(x)
            return ','.join(out)

        expected_flow = _num(data.get('expected_flow'), 0)
        open_channels = int(_num(data.get('open_channels'), 0) or 0)
        channel_ids = _dedupe(data.get('channel_ids'))
        wait_time = _num(data.get('wait_time'))
        utilization = _num(data.get('utilization'))
        channel_details = data.get('channel_details', '')
        
        if not sim_id:
            sim_result = session.get('simulation_result', {})
            sim_id = sim_result.get('sim_id')
        
        if not sim_id:
            return jsonify({'success': False, 'message': '未找到关联的模拟记录'})
        
        conn = get_db()
        conn.execute('DELETE FROM T_ScheduleResult WHERE sim_id = ? AND time_slot = ?', (sim_id, time_slot))
        conn.execute('''
            INSERT INTO T_ScheduleResult 
            (sim_id, schedule_date, time_slot, expected_flow, open_channels, channel_ids, wait_time, utilization, channel_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (sim_id, schedule_date, time_slot, expected_flow, open_channels, channel_ids, wait_time, utilization, channel_details))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': '调度结果已保存'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败：{str(e)}'})
