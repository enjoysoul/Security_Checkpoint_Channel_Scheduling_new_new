# -*- coding: utf-8 -*-
"""控制层：Flask 路由视图，按业务用例分文件。

本层只做「参数解析 → 调用 control/subsystem 算法子系统 → 组装模板/JSON 响应」，
不承担算法实现与 SQL 细节；URL 规则、端点名与模板均保持不变。
"""
from control.common.registry import route, routes


def register_all(app):
    """导入全部控制模块（导入即登记路由）并挂载到应用。"""
    from control import auth      # noqa: F401  UC00 登录鉴权
    from control import data      # noqa: F401  UC01 数据导入与客流模拟
    from control import schedule  # noqa: F401  UC02/UC03 通道调度与决策建议
    from control import roster    # noqa: F401  UC04 人员排班
    from control import analysis   # noqa: F401  UC06 历史数据查询与导出
    from control import predict   # noqa: F401  UC05 客流预测
    from control import system    # noqa: F401  UC07 基础数据与系统参数管理
    for rule, options, func in routes():
        app.add_url_rule(rule, func.__name__, func, **options)
    return app
