# -*- coding: utf-8 -*-
"""安检通道调度系统 —— 应用入口（装配层）。

本文件只负责把边界 / 控制 / 实体三层装配成一个可运行的应用，不含任何业务实现。
目录结构与软件体系结构逻辑视图的分析类构造型一一对应：

    ┌ 《boundary》 界面层      boundary/              7 个界面类各建一个文件夹 + static/
    │ 《control》  业务逻辑层  control/               7 个控制器包（各建一个文件夹）
    │                         control/subsystem/    《subsystem》算法子系统（6 个算法）
    │                         control/common/       路由登记器与登录保护等公共件
    │                         control/tests/        测试（覆盖控制层与算法子系统）
    │ 《entity》   数据层      entity/                数据读写模块 + config/ + scripts/
    │                                                + data/ + 数据库文件
    └

依赖方向自上而下单向：boundary → control → subsystem → entity，无反向依赖。

说明：页面模板按界面类分文件夹存放，模板根为 boundary/，渲染时写作
'login/login.html' 等形式；静态资源目录为 boundary/static/，
但 static_url_path 仍为 '/static'，页面与浏览器访问路径均保持不变。
"""
import os

from flask import Flask

from entity.config import paths
from control import common as ctrl_common
from control import register_all
from entity import caac


def create_app():
    """构建 Flask 应用：装配静态资源、上传目录、全局登录保护与各用例路由。"""
    app = Flask(
        __name__,
        static_url_path='/static',
        static_folder=paths.STATIC_DIR,
        template_folder=paths.TEMPLATE_DIR,
    )

    # 系统配置
    app.config['SECRET_KEY'] = 'security-checkpoint-system-2026'
    app.config['UPLOAD_FOLDER'] = paths.UPLOAD_DIR
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

    # 确保上传目录存在
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # 全局登录保护（控制层公共件，原先为 app.py 内的 @app.before_request）
    app.before_request(ctrl_common.require_login)

    # 挂载控制层全部路由（URL 与端点名与历史版本完全一致）
    register_all(app)

    # 启动时加载内置的民航局官方吞吐量数据（数据层）
    caac.load_caac_data()
    # 模板过滤器：从 "2.7 分钟" / "99%" 这类历史数据里取出数字并格式化
    import re as _re

    def _pick_num(v):
        if isinstance(v, (int, float)):
            return v
        m = _re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
        return float(m.group(0)) if m else None

    def _fmt1(v):
        return '-' if v is None else '%.1f' % float(v)

    app.add_template_filter(_pick_num, 'pick_num')
    app.add_template_filter(_fmt1, 'fmt1')

    return app


# 项目根目录别名：兼容旧脚本与测试夹具引用
basedir = paths.BASE_DIR

app = create_app()


# ==========================================
# 启动程序
# ==========================================
if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
