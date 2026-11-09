# -*- coding: utf-8 -*-
"""/simulate 路由端到端冒烟测试（确定性引擎，不再依赖大模型联网）。

通过临时数据库夹具隔离写入，验证：
- 有吞吐量数据时返回成功且 hourlyFlow 长度为 24；
- 无数据时返回 success=False（确定性错误路径，不再抛「网络错误」）。

分层后「民航局官方数据」的进程内数据源位于数据层 entity.caac，
因此这里通过 entity.caac.set_caac_data() 注入/清除，而不是改模块全局变量。
"""
from entity import caac as caac_entity


def test_simulate_route_no_data(admin_client):
    caac_entity.set_caac_data(None)  # 清除官方数据来源
    rv = admin_client.post('/simulate',
                           data={'airport': '不存在机场', 'predictDate': '2025-08-10'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['success'] is False


def test_simulate_route_deterministic(admin_client):
    # 注入一条测试机场的官方吞吐量（8000 万人次）
    caac_entity.set_caac_data({
        'source': 'test', 'title': 't', 'year': 2025,
        'publish_date': '', 'download_url': '',
        'airports': [{'airport': '测试机场', 'city': '', 'name': '测试机场',
                      'passengers_2025': 8000 * 10000}],
    })
    rv = admin_client.post('/simulate',
                           data={'airport': '测试机场', 'predictDate': '2025-08-10', 'seed': '42'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['success'] is True
    assert 'data' in data
    assert len(data['data']['hourlyFlow']) == 24
    # 确定性：同输入两次结果一致
    rv2 = admin_client.post('/simulate',
                            data={'airport': '测试机场', 'predictDate': '2025-08-10', 'seed': '42'})
    assert rv2.get_json()['data']['hourlyFlow'] == data['data']['hourlyFlow']
