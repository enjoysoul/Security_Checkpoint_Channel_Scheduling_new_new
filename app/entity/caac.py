# -*- coding: utf-8 -*-
"""民航局公开吞吐量数据访问（数据层）。

内置数据文件读写、进程内缓存，以及按机场名匹配的年度吞吐量查询。
文件路径与公告页地址统一来自 config.paths。
"""
import json

from entity.config.paths import CAAC_DATA_FILE, CAAC_LIST_URL  # noqa: F401 公告页地址供控制层复用

caac_data = None  # 进程内缓存：当前使用的民航局官方数据


def load_caac_data():
    """加载内置的民航局官方吞吐量数据"""
    global caac_data
    try:
        with open(CAAC_DATA_FILE, 'r', encoding='utf-8') as f:
            caac_data = json.load(f)
        return True
    except Exception as e:
        print(f"加载民航局数据失败: {e}")
        caac_data = None
        return False

def save_caac_data(data):
    """保存民航局数据到内置文件"""
    global caac_data
    try:
        with open(CAAC_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        caac_data = data
        return True
    except Exception as e:
        print(f"保存民航局数据失败: {e}")
        return False


def set_caac_data(data):
    """仅更新进程内数据源（不写文件），用于运行时切换数据源或测试注入。"""
    global caac_data
    caac_data = data
    return caac_data


def get_airport_throughput(airport, custom_records=None):
    """获取指定机场的年度吞吐量（万人次）：优先调用方传入的自定义数据，其次民航局官方数据。

    custom_records: 由控制层传入的自定义数据（来自会话或上传文件）。
    数据层不直接读取 Web 会话，保持与请求上下文解耦，便于单独测试。
    """
    def match_airport(sel, item):
        """匹配所选机场与数据项：支持'义乌机场'匹配'义乌'、'北京/大兴'匹配'大兴'等"""
        if item.get('airport') == sel:
            return True
        name = item.get('name', '')
        if not name:
            return False
        if sel.endswith(name) or name in sel:
            return True
        if sel.endswith('机场'):
            base = sel[:-2]
            if base == name or name.endswith(base) or base.endswith(name):
                return True
        return False
    
    try:
        # 1. 调用方传入的自定义数据（来自会话或上传文件）
        for item in (custom_records or []):
            if match_airport(airport, item):
                return round(item['passengers'] / 10000, 1)

        # 2. 民航局官方数据
        if caac_data:
            for item in caac_data.get('airports', []):
                if match_airport(airport, item):
                    key = f"passengers_{caac_data.get('year', 2025)}"
                    val = item.get(key) or item.get('passengers_2025')
                    if val:
                        return round(val / 10000, 1)
    except Exception as e:
        print(f"获取机场吞吐量失败: {e}")
    return 0
