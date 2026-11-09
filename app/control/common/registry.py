# -*- coding: utf-8 -*-
"""路由登记器。

控制层各模块不持有全局 Flask 实例，而用本模块的 route() 装饰器登记路由，
应用装配时由 control.register_all() 统一挂载。
这样既把控制层按业务用例拆成多个文件，又保持 URL 规则与端点名（函数名）
与原单文件写法完全一致——模板与 url_for 均无需任何改动。
"""
_ROUTES = []


def route(rule, **options):
    """登记一条路由；返回原函数，端点名仍为函数名。"""
    def decorator(func):
        _ROUTES.append((rule, options, func))
        return func
    return decorator


def routes():
    """返回已登记的全部路由（保持定义顺序）。"""
    return list(_ROUTES)
