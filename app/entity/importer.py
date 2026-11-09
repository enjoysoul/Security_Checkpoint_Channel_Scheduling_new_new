# -*- coding: utf-8 -*-
"""吞吐量数据接入（数据层）：文件类型校验、记录清洗、Excel/CSV 解析。

入库前的数据质量问题（非正数、重复机场、量级异常）在此统一拦截并回传告警，
避免脏数据进入 T_PassengerSim 后影响仿真与排班结果。
"""


ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_throughput_records(records):
    """对解析出的吞吐量记录做数据校验与清洗。

    规则：
    - 丢弃缺失机场名或旅客吞吐量 <= 0 的非法行；
    - 单机场年吞吐量（人次）超过 2 亿视为可疑，保留但给出告警；
    - 按机场名去重，同一机场保留数值最大的一条（避免表尾合计/重复占用）。

    返回 (clean_records, warnings)。
    """
    MAX_PAX = 200_000_000  # 单机场年旅客吞吐量合理上限（人次），约全球最繁忙机场两倍
    seen = {}          # name -> (pax, city)
    warnings = []
    for r in records:
        name = (r.get('airport') or '').strip()
        city = (r.get('city') or '').strip()
        try:
            pax = int(float(r.get('passengers', 0)))
        except (TypeError, ValueError):
            warnings.append(f'「{name or "未知"}」吞吐量不是有效数字，已忽略')
            continue
        if not name or pax <= 0:
            warnings.append(f'「{name or "未知"}」吞吐量缺失或非正，已忽略')
            continue
        if pax > MAX_PAX:
            warnings.append(f'「{name}」年吞吐量 {pax:,} 人次超过合理上限，请核对数据')
        if name in seen:
            if pax > seen[name][0]:
                seen[name] = (pax, city)
            continue
        seen[name] = (pax, city)
    clean = [{'airport': n, 'city': c, 'name': n, 'passengers': p}
             for n, (p, c) in seen.items()]
    return clean, warnings


def parse_throughput_file(filepath):
    """解析吞吐量表格文件。

    返回 (记录列表, 数据质量告警列表)：记录列表为 None 表示解析失败。
    本函数不写会话、不依赖请求上下文，便于单元测试与复用。
    """
    ext = filepath.rsplit('.', 1)[1].lower()
    try:
        if ext in ('xlsx', 'xls'):
            import openpyxl
            from openpyxl import load_workbook
            wb = load_workbook(filepath, data_only=True)
            ws = wb.active
            # 自动定位：机场名列（含"/"或"机场"字样）+ 数字旅客列
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if not rows:
                return None, []
            
            # 找到机场列和吞吐量列
            airport_col = None
            passenger_col = None
            for row_idx, row in enumerate(rows[:6]):
                for col_idx, cell in enumerate(row):
                    if cell is None:
                        continue
                    text = str(cell).strip()
                    if ('机场' in text or '/' in text or '吞吐量' in text) and airport_col is None:
                        airport_col = col_idx
                    if '吞吐量' in text and ('旅客' in text or '人次' in text or '排名' in text):
                        passenger_col = col_idx
                if airport_col is not None and passenger_col is not None:
                    break
            
            # 如果没找到明确表头，用启发式：第一列是机场名，找一列数字大的是吞吐量
            if airport_col is None:
                airport_col = 0
            
            parsed = []
            for row in rows[4:]:  # 跳过前几行表头
                if row[airport_col] is None:
                    continue
                name = str(row[airport_col]).strip()
                if not name or name in ('合计', '机场', '机场名称'):
                    continue
                # 找到该行中最大的数字作为吞吐量
                best = None
                for val in row:
                    if isinstance(val, (int, float)) and val > 1000:
                        if best is None or val > best:
                            best = val
                if best is None:
                    continue
                if '/' in name:
                    city, airport_name = name.split('/', 1)
                else:
                    city, airport_name = '', name
                parsed.append({
                    'airport': name,
                    'city': city,
                    'name': airport_name,
                    'passengers': int(best)
                })
            parsed, warnings = validate_throughput_records(parsed)
            return (parsed if parsed else None), warnings

        elif ext == 'csv':
            import csv
            with open(filepath, 'r', encoding='utf-8-sig', errors='ignore') as f:
                reader = csv.reader(f)
                rows = list(reader)
            parsed = []
            for row in rows[4:]:
                if not row:
                    continue
                name = row[0].strip()
                if not name or name in ('合计', '机场', '机场名称'):
                    continue
                best = None
                for val in row[1:]:
                    try:
                        num = float(val.replace(',', '').strip())
                        if num > 1000 and (best is None or num > best):
                            best = num
                    except:
                        continue
                if best is None:
                    continue
                if '/' in name:
                    city, airport_name = name.split('/', 1)
                else:
                    city, airport_name = '', name
                parsed.append({
                    'airport': name,
                    'city': city,
                    'name': airport_name,
                    'passengers': int(best)
                })
            parsed, warnings = validate_throughput_records(parsed)
            return (parsed if parsed else None), warnings
    except Exception as e:
        print(f"解析吞吐量文件失败: {e}")
        return None, []
    return None, []
