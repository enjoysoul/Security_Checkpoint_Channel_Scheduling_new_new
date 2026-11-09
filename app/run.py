# -*- coding: utf-8 -*-
"""便携版启动器：启动 Web 服务并自动打开浏览器。

特点：
  * 自己把 runtime\\site-packages 加入搜索路径，因此可直接用
    runtime\\python\\pythonw.exe app\\run.py 启动，不依赖任何环境变量；
  * 端口从 5000 起自动探测，写入 <根目录>/.running_port；
  * 已有实例在跑时只打开浏览器，不重复启动。
"""
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, 'runtime', 'site-packages')

for _p in (SITE, HERE):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

PORT_FILE = os.path.join(ROOT, '.running_port')
LOGIN_PATH = '/login'


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def pick_port():
    for p in range(5000, 5011):
        if port_free(p):
            return p
    return 5000


def running_port():
    """已有实例在跑就返回它的端口，否则返回 None。"""
    try:
        with open(PORT_FILE, 'r', encoding='utf-8') as f:
            port = int(f.read().strip())
    except Exception:
        return None
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, LOGIN_PATH),
                                    timeout=1.5) as r:
            body = r.read(4096).decode('utf-8', 'ignore')
            if r.status == 200 and '安检通道调度系统' in body:
                return port
    except Exception:
        pass
    return None


def open_browser(url):
    """尽量把浏览器叫起来；两种方式都用上。"""
    if os.environ.get('NO_BROWSER'):
        return
    ok = False
    try:
        ok = webbrowser.open(url)
    except Exception:
        ok = False
    if not ok:
        try:
            os.startfile(url)          # Windows 专用，最贴近"双击打开"
            ok = True
        except Exception:
            pass
    return ok


def main():
    alive = running_port()
    if alive:
        url = 'http://127.0.0.1:%d%s' % (alive, LOGIN_PATH)
        print('系统已经在运行：%s' % url)
        print('如果浏览器没有自动打开，请手动把上面的地址复制到浏览器里访问。')
        if not open_browser(url):
            print('自动打开浏览器失败，请手动访问上面的地址。')
        time.sleep(2.5)      # 等浏览器真正起来再退出
        return

    port = pick_port()
    url = 'http://127.0.0.1:%d%s' % (port, LOGIN_PATH)

    try:
        with open(PORT_FILE, 'w', encoding='utf-8') as f:
            f.write(str(port))
    except Exception:
        pass

    def wait_ready(port, timeout=20.0):
        """等端口真正开始监听，再打开浏览器，避免出现"无法访问"页。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.4):
                    return True
            except OSError:
                time.sleep(0.2)
        return False

    def open_later():
        if os.environ.get('NO_BROWSER'):
            return
        wait_ready(port)
        open_browser(url)

    threading.Thread(target=open_later, daemon=True).start()

    print('=' * 58)
    print('   安检通道调度系统')
    print('   本机访问地址: %s' % url)
    print('   若浏览器未自动打开，请手动复制上面的地址访问。')
    print('   使用完毕后，关闭本窗口即可停止服务。')
    print('=' * 58)
    print()

    from app import app
    try:
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print('\n服务已停止。')
    finally:
        try:
            os.remove(PORT_FILE)
        except Exception:
            pass


if __name__ == '__main__':
    main()
