"""One-off live probe: reproduce the four screenshot turns in the real chat."""
import ctypes
import json
import os
import time
from pathlib import Path
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

if os.environ.get('DSH_DAFEIYU_ALLOW_LIVE_PROBE') != '1':
    raise SystemExit('maintainer probe')
OUT = Path(__file__).resolve().parent / 'verification' / 'live'
OUT.mkdir(parents=True, exist_ok=True)

def windows():
    rows = []
    win32gui.EnumWindows(lambda h, _: rows.append((h, win32gui.GetWindowText(h)))
                         if win32gui.IsWindowVisible(h) else None, None)
    return rows

def find(title):
    return next((h for h, t in windows() if t == title), None)

def wait_for(title, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        h = find(title)
        if h:
            return h
        time.sleep(.2)
    return None

def capture(handle, filename):
    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width, height = right - left, bottom - top
    window_dc = win32gui.GetWindowDC(handle)
    source = win32ui.CreateDCFromHandle(window_dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    target.SelectObject(bitmap)
    try:
        ctypes.windll.user32.PrintWindow(handle, target.GetSafeHdc(), 2)
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(OUT / filename)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(handle, window_dc)

def send(handle, text):
    for char in text:
        win32gui.PostMessage(handle, win32con.WM_CHAR, ord(char), 0)
        time.sleep(.02)
    win32gui.PostMessage(handle, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32gui.PostMessage(handle, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    time.sleep(1.2)

pet = wait_for('DSH 云岚 · 成长版')
if not pet:
    raise SystemExit('pet missing')
existing = find('和云岚聊天')
if existing:
    win32gui.PostMessage(existing, win32con.WM_CLOSE, 0, 0)
    time.sleep(1)
# right-click menu -> 和云岚聊天
left, top, right, bottom = win32gui.GetWindowRect(pet)
x, y = (left + right) // 2, bottom - 45
point = (win32gui.ScreenToClient(pet, (x, y))[0] & 0xffff) | ((win32gui.ScreenToClient(pet, (x, y))[1] & 0xffff) << 16)
win32gui.PostMessage(pet, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, point)
win32gui.PostMessage(pet, win32con.WM_RBUTTONUP, 0, point)
time.sleep(.5)
pid = win32process.GetWindowThreadProcessId(pet)[1]
menu = next((h for h, _ in windows() if 'Popup' in win32gui.GetClassName(h)
             and win32process.GetWindowThreadProcessId(h)[1] == pid), None)
if not menu:
    raise SystemExit('menu missing')
pt = 60 | (56 << 16)
win32gui.PostMessage(menu, win32con.WM_MOUSEMOVE, 0, pt)
win32gui.PostMessage(menu, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, pt)
win32gui.PostMessage(menu, win32con.WM_LBUTTONUP, 0, pt)
chat = wait_for('和云岚聊天', 10)
if not chat:
    raise SystemExit('chat missing')
left, top, right, bottom = win32gui.GetWindowRect(chat)
win32gui.SetWindowPos(chat, 0, left, top, 520, 900, 0)
time.sleep(1)
result = {'replies': []}
for text in ('你好呀，小云岚', '不想加油了', '为什么是答非所问呢', '为什么答非所问'):
    send(chat, text)
    result['replies'].append(text)
time.sleep(1)
capture(chat, 'dialogue-live.png')
print(json.dumps({'ok': True, 'sent': result['replies'], 'shot': str(OUT / 'dialogue-live.png')}, ensure_ascii=False))
