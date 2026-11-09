# -*- coding: utf-8 -*-
"""UC01 数据导入与客流模拟（控制层）。

  /data-import     数据导入页（机场选择 + 民航局数据 + 文件上传）
  /api/data-source 当前数据源状态
  /fetch-caac      抓取民航局官网吞吐量公告
  /use-caac        启用民航局内置数据
  /upload          上传吞吐量文件（xlsx/csv）并解析入库
  /simulate        以年度吞吐量为锚点执行确定性客流模拟
"""
import os
import json

from flask import render_template, request, jsonify, session, current_app
from werkzeug.utils import secure_filename
from pypinyin import lazy_pinyin, Style

from entity.config.paths import CAAC_LIST_URL
from control.common.registry import route
from control.common import get_db, get_current_sim
from entity import caac, importer
from control.subsystem import queuing, simulation


@route('/data-import')
def data_import():
    """数据导入界面"""
    # 从数据库获取机场列表
    conn = get_db()
    airports = conn.execute('SELECT * FROM airports ORDER BY name').fetchall()
    conn.close()
    
    # 按拼音首字母分组
    airport_groups = {}
    for airport in airports:
        name = airport['name']
        if name:
            # 获取拼音首字母
            pinyin_first = lazy_pinyin(name[0], style=Style.FIRST_LETTER)
            first_letter = pinyin_first[0].upper() if pinyin_first else '#'
            if first_letter not in airport_groups:
                airport_groups[first_letter] = []
            airport_groups[first_letter].append(airport)
    
    # 按首字母排序
    sorted_groups = sorted(airport_groups.items())
    
    # 读取当前模拟结果：优先会话，缺失时按 sim_id 从数据库回读（刷新页面不丢失）
    simulation_result = get_current_sim()
    
    # 当前数据源信息
    data_source = {
        'using_caac': session.get('using_caac', True),
        'caac_year': caac.caac_data.get('year') if caac.caac_data else None,
        'caac_title': caac.caac_data.get('title') if caac.caac_data else None,
        'caac_source': caac.caac_data.get('source') if caac.caac_data else None,
        'caac_count': len(caac.caac_data.get('airports', [])) if caac.caac_data else 0,
        'custom_file': session.get('custom_file', None)
    }
    
    return render_template('data_import/data_import.html', 
                         active_page='data_import', 
                         airport_groups=sorted_groups,
                         simulation_result=simulation_result,
                         data_source=data_source)


@route('/api/data-source', methods=['GET'])
def api_data_source():
    """获取当前吞吐量数据源状态"""
    return jsonify({
        'success': True,
        'using_caac': session.get('using_caac', True),
        'caac_year': caac.caac_data.get('year') if caac.caac_data else None,
        'caac_title': caac.caac_data.get('title') if caac.caac_data else None,
        'caac_source': caac.caac_data.get('source') if caac.caac_data else None,
        'caac_publish_date': caac.caac_data.get('publish_date') if caac.caac_data else None,
        'caac_count': len(caac.caac_data.get('airports', [])) if caac.caac_data else 0,
        'custom_file': session.get('custom_file', None)
    })


@route('/fetch-caac', methods=['POST'])
def fetch_caac():
    """从中国民航局官网抓取最新年度机场吞吐量排名表"""
    import urllib.request
    import ssl
    import re
    import tempfile
    import io
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        # 1. 抓取统计数据列表页
        req = urllib.request.Request(CAAC_LIST_URL, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        html = urllib.request.urlopen(req, timeout=20, context=ctx).read().decode('utf-8', errors='ignore')
        
        # 2. 找到最新的"全国民用运输机场生产统计公报"详情页链接
        # 页面结构：<a href="./202602/t20260226_230119.html">2025年全国民用运输机场生产统计公报</a>
        match = re.search(r'href="\.?/?([^"]*?t\d{8}_\d+\.html)"[^>]*>(\d{4})年全国民用运输机场生产统计公报', html)
        if not match:
            # 尝试更宽松的匹配
            match = re.search(r'href="\.?/?([^"]*?t\d{8}_\d+\.html)"[^>]*>(\d{4})年[^<]*生产统计公报', html)
        if not match:
            return jsonify({'success': False, 'message': '未能从民航局官网找到最新年度公报链接'})
        
        detail_rel = match.group(1)
        year = int(match.group(2))
        
        # 拼接详情页完整URL
        if detail_rel.startswith('http'):
            detail_url = detail_rel
        else:
            base = CAAC_LIST_URL.rsplit('/', 1)[0]
            detail_url = base + '/' + detail_rel.lstrip('./')
        
        print(f"找到公报详情页: {detail_url} (年份{year})")
        
        # 3. 抓取详情页，找吞吐量排名xlsx
        req = urllib.request.Request(detail_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        detail_html = urllib.request.urlopen(req, timeout=20, context=ctx).read().decode('utf-8', errors='ignore')
        
        xlsx_match = re.search(r'href="([^"]*?吞吐量排名[^"]*?\.xlsx)"', detail_html)
        if not xlsx_match:
            # 退而求其次：找任何xlsx附件
            xlsx_match = re.search(r'href="([^"]*?\.xlsx)"', detail_html)
        if not xlsx_match:
            return jsonify({'success': False, 'message': f'公报页面中未找到吞吐量排名Excel附件（{year}年度）'})
        
        xlsx_rel = xlsx_match.group(1)
        if xlsx_rel.startswith('http'):
            xlsx_url = xlsx_rel
        else:
            base = detail_url.rsplit('/', 1)[0]
            xlsx_url = base + '/' + xlsx_rel.lstrip('./')
        
        print(f"找到吞吐量Excel: {xlsx_url}")
        
        # 4. 下载Excel
        req = urllib.request.Request(xlsx_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        xlsx_data = urllib.request.urlopen(req, timeout=30, context=ctx).read()
        
        # 5. 解析Excel（使用openpyxl，需要先安装）
        try:
            import openpyxl
            from openpyxl import load_workbook
        except ImportError:
            return jsonify({'success': False, 'message': '服务器缺少openpyxl库，无法解析Excel'})
        
        wb = load_workbook(io.BytesIO(xlsx_data), data_only=True)
        ws = wb.active
        
        airports = []
        for row in ws.iter_rows(min_row=6, values_only=True):
            name = row[0]
            if not name or str(name).strip() in ('合计', ''):
                continue
            name = str(name).strip()
            passengers = row[2]
            if passengers is None:
                continue
            try:
                passengers_num = int(float(passengers))
            except:
                continue
            if '/' in name:
                city, airport_name = name.split('/', 1)
            else:
                city, airport_name = '', name
            airports.append({
                'airport': name,
                'city': city,
                'name': airport_name,
                f'passengers_{year}': passengers_num
            })
        wb.close()
        
        if not airports:
            return jsonify({'success': False, 'message': 'Excel解析结果为空，可能格式已变化'})

        # 5.5 数据校验：过滤非法/重复行（与上传文件共用同一套规则）
        norm = [{'airport': a.get('airport', ''), 'city': a.get('city', ''),
                 'name': a.get('name', ''),
                 'passengers': a.get(f'passengers_{year}') or a.get('passengers_2025')}
                for a in airports]
        clean, caac_warnings = importer.validate_throughput_records(norm)
        if caac_warnings:
            print('CAAC数据校验告警:', caac_warnings)
        keep = {c['airport'] for c in clean}
        airports = [a for a in airports if a.get('airport') in keep]

        # 6. 保存为内置数据
        new_data = {
            'source': '中国民用航空局',
            'title': f'{year}年全国民用运输机场吞吐量排名',
            'year': year,
            'publish_date': '',
            'download_url': xlsx_url,
            'airports': airports
        }
        caac.save_caac_data(new_data)
        
        session['using_caac'] = True
        session.pop('custom_file', None)
        
        return jsonify({
            'success': True,
            'message': f'已从民航局官网获取{year}年度吞吐量数据（{len(airports)}个机场）',
            'year': year,
            'count': len(airports)
        })
        
    except Exception as e:
        print(f"从官网抓取数据失败: {e}")
        return jsonify({'success': False, 'message': f'从民航局官网获取数据失败：{str(e)}'})


@route('/use-caac', methods=['POST'])
def use_caac():
    """切换回使用民航局官方数据"""
    session['using_caac'] = True
    session.pop('custom_file', None)
    return jsonify({'success': True, 'message': '已切换为使用民航局官方数据'})


@route('/upload', methods=['POST'])
def upload_file():
    """文件上传接口：上传自定义吞吐量表格并解析为数据源"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    if file and importer.allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # 尝试解析吞吐量表格（机场+旅客吞吐量）；解析层同时回传数据质量告警
        parsed, warnings = importer.parse_throughput_file(filepath)
        if parsed:
            session['using_caac'] = False
            session['custom_file'] = filename
            session['custom_throughput'] = parsed
            msg = f'文件上传成功，已解析{len(parsed)}个机场的吞吐量数据'
            if warnings:
                msg += f'（{len(warnings)}条异常记录已自动过滤/提示）'
            return jsonify({
                'success': True,
                'message': msg,
                'filename': filename,
                'parsed_count': len(parsed),
                'warnings': warnings
            })
        else:
            session['custom_file'] = filename
            session.pop('custom_throughput', None)
            return jsonify({'success': True, 'message': '文件上传成功（未识别到吞吐量数据，模拟时将仅作为参考）', 'filename': filename, 'warnings': warnings})
    
    return jsonify({'success': False, 'message': '不支持的文件格式'})



@route('/simulate', methods=['POST'])
def simulate():
    """客流模拟接口：以年度吞吐量为锚点，使用确定性多层分解引擎生成客流。

    不再调用大模型生成数字（避免联网失败与数值幻觉），符合需求规格
    UC01「公开数据锚定 + AI 多层分解 + 外部数据验证」的设计。
    """
    airport = request.form.get('airport')
    predict_date = request.form.get('predictDate')
    file = request.form.get('file')  # 兼容原页面，年度吞吐量优先取系统已有数据

    # 获取该机场年度吞吐量参考数据（万人次），优先用户自定义，其次民航局官方数据
    throughput_ref = caac.get_airport_throughput(airport, session.get('custom_throughput'))
    if not throughput_ref or throughput_ref <= 0:
        return jsonify({
            'success': False,
            'message': '缺少该机场年度旅客吞吐量数据，请先在「数据导入」配置或选择有公开数据的机场'
        })

    # 读取排队论与模拟参数（可在系统参数配置页调整）
    conn = get_db()
    pr = {row['param_key']: row['param_value']
          for row in conn.execute('SELECT param_key, param_value FROM system_params')}
    conn.close()
    try:
        mu = float(pr.get('avg_security_speed', 3.5) or 3.5)
        threshold = float(pr.get('warning_threshold', 15) or 15)
        departure_ratio = float(pr.get('departure_ratio', 0.5) or 0.5)
    except Exception:
        mu, threshold, departure_ratio = 3.5, 15.0, 0.5

    sim_params = {'mu': mu, 'departure_ratio': departure_ratio}
    try:
        # 默认使用随机种子（体现客流波动）；显式传入 seed 时结果可复现
        import random as _random
        try:
            _seed = int(request.form.get('seed'))
        except (TypeError, ValueError):
            _seed = _random.randint(1, 999999)
        result_data = simulation.simulate_day(airport, predict_date, throughput_ref,
                                               sim_params, seed=_seed)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})

    # 依据峰值小时到达率，用排队论搜索建议开放通道数（UC02）
    peak_idx = result_data.get('_peak_idx', 0)
    peak_flow = result_data['_hourly_security'][peak_idx]   # 安检只服务出港旅客
    lam_peak = peak_flow / 60.0
    conn = get_db()
    ch_rows = conn.execute('SELECT status FROM channels').fetchall()
    conn.close()
    available = [c for c in ch_rows if c['status'] != '维护中']
    c_max = max(1, len(available)) if available else 20
    bc = queuing.best_channel(lam_peak, mu, threshold, c_max)
    result_data['recommendChannels'] = '%d条' % bc['channels']
    result_data['_channel_feasible'] = bc['feasible']
    result_data['_peak_wait_min'] = (round(bc['wq'], 2)
                                     if bc['wq'] != float('inf') else None)

    # 持久化到数据库，支持历史分析查询（T_PassengerSim 字段兼容）
    try:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("ALTER TABLE T_PassengerSim ADD COLUMN hourly_speed TEXT")
            conn.commit()
        except Exception:
            pass
        cursor.execute('''
            INSERT INTO T_PassengerSim
            (airport, predict_date, daily_passengers, peak_hour,
             max_hour_passengers, recommend_channels, avg_security_speed,
             hourly_flow, hourly_speed, operator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            airport,
            result_data.get('predict_date'),
            result_data.get('dailyPassengers', ''),
            result_data.get('peakHour', ''),
            result_data.get('maxHourPassengers', ''),
            result_data.get('recommendChannels', ''),
            result_data.get('avgSecuritySpeed', ''),
            json.dumps(result_data.get('hourlyFlow', [])),
            json.dumps(result_data.get('hourlySpeed', [])),
            session.get('user', {}).get('real_name') or 'admin'
        ))
        conn.commit()
        sim_id = cursor.lastrowid
        conn.close()
        result_data['sim_id'] = sim_id
        # 会话里只记录 sim_id，明细由 get_current_sim() 从数据库回读，避免 cookie 超过 4KB 被丢弃
        session['simulation_result'] = {'sim_id': sim_id}
        print(f"模拟结果已持久化，sim_id={sim_id}")
    except Exception as e:
        print(f"持久化模拟结果失败：{e}")

    return jsonify({
        'success': True,
        'message': f'已完成{airport}机场{predict_date}客流模拟',
        'data': result_data
    })
