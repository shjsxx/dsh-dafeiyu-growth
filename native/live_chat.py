"""Live check: right-click menu -> 和云岚说句话… -> input card."""
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
    raise SystemExit('维护者脚本：会操作真实桌宠窗口。设置 DSH_DAFEIYU_ALLOW_LIVE_PROBE=1 后再运行。')

OUT = Path(__file__).resolve().parent / 'verification' / 'live'
OUT.mkdir(parents=True, exist_ok=True)


def windows():
    rows = []
    win32gui.EnumWindows(
        lambda handle, _: rows.append((handle, win32gui.GetWindowText(handle)))
        if win32gui.IsWindowVisible(handle) else None, None)
    return rows


def find(title):
    return next((handle for handle, text in windows() if text == title), None)


def wait_for(title, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        handle = find(title)
        if handle:
            return handle
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
        if not ctypes.windll.user32.PrintWindow(handle, target.GetSafeHdc(), 2):
            raise RuntimeError('PrintWindow failed')
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(OUT / filename)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        try:
            source.DeleteDC()
        except win32ui.error:
            pass
        win32gui.ReleaseDC(handle, window_dc)


def open_menu(pet):
    left, top, right, bottom = win32gui.GetWindowRect(pet)
    x, y = (left + right) // 2, bottom - 45
    local_x, local_y = win32gui.ScreenToClient(pet, (x, y))
    point = (local_x & 0xffff) | ((local_y & 0xffff) << 16)
    try:
        win32gui.SetForegroundWindow(pet)
    except Exception:
        pass
    win32gui.PostMessage(pet, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, point)
    win32gui.PostMessage(pet, win32con.WM_RBUTTONUP, 0, point)
    pid = win32process.GetWindowThreadProcessId(pet)[1]
    until = time.monotonic() + 5
    while time.monotonic() < until:
        menu = next((h for h, _ in windows()
                     if 'Popup' in win32gui.GetClassName(h)
                     and win32process.GetWindowThreadProcessId(h)[1] == pid), None)
        if menu:
            return menu
        time.sleep(.1)
    return None


def click(menu, y):
    point = 60 | (y << 16)
    win32gui.PostMessage(menu, win32con.WM_MOUSEMOVE, 0, point)
    win32gui.PostMessage(menu, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
    win32gui.PostMessage(menu, win32con.WM_LBUTTONUP, 0, point)


pet = wait_for('DSH 云岚 · 成长版')
if not pet:
    raise SystemExit('pet window missing')
result = {'ok': False}
rows = os.environ.get('YUNLAN_MENU_ROWS', '40,56')
expected = os.environ.get('YUNLAN_EXPECT_TITLE', '和云岚聊天')
shot = os.environ.get('YUNLAN_SHOT', 'chat.png')
for attempt, row_y in enumerate(int(value) for value in rows.split(',')):
    menu = open_menu(pet)
    if not menu:
        result['error'] = 'menu missing'
        break
    left, top, right, bottom = win32gui.GetWindowRect(menu)
    result['menuSize'] = [right - left, bottom - top]
    capture(menu, f'menu-chat-{attempt}.png')
    click(menu, row_y)
    chat = wait_for(expected, 3)
    card = find('云岚 · 成长卡')
    if chat:
        result.update({'ok': True, 'menuSize': [right - left, bottom - top], 'rowY': row_y})
        capture(chat, shot)
        win32gui.PostMessage(chat, win32con.WM_CLOSE, 0, 0)
        until = time.monotonic() + 3
        while find(expected) and time.monotonic() < until:
            time.sleep(.1)
        break
    if card:
        win32gui.PostMessage(card, win32con.WM_CLOSE, 0, 0)
        time.sleep(.4)
    time.sleep(.3)
(OUT / 'chat-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
