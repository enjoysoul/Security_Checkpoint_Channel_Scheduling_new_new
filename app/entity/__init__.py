# -*- coding: utf-8 -*-
"""《entity》数据层：系统全部数据实体的持久化与访问。

本包对应逻辑视图第三层《entity》数据层，为上层提供统一的数据读写能力：

    db.py         数据库连接工厂 —— 逻辑视图中 8 张数据库表的统一访问入口
                  （T_PassengerSim、T_ScheduleResult、T_StaffInfo、T_RosterResult、
                    system_params、users、channels、airports）
    caac.py       《file》年度吞吐量实体 —— 以 data/*.json 文件存储（非数据库表）
    importer.py   吞吐量文件解析与数据清洗（解析结果写入上述业务表）

本层不感知 HTTP 请求与会话：需要按当前用户数据查询时，由控制层把数据作为参数传入，
因此可脱离 Web 环境单独测试。
"""
