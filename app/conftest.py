# -*- coding: utf-8 -*-
"""项目根 conftest：把项目根目录加入 sys.path。

这样测试既可以直接 `import app`，也可以按边界 / 控制 / 实体三层结构
`from control.subsystem import queuing` / `from entity import ...` 导入各层模块，
不受 pytest 导入模式与执行目录影响。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
