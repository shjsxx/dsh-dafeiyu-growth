"""Verify installed native pet via Windows messages; never stop the DSH host."""
import json
import os
from pathlib import Path
import time
import ctypes
import win32gui
import win32ui
import win32con
import win32process
from PIL import Image

if os.environ.get('DSH_DAFEIYU_ALLOW_LIVE_PROBE') != '1':
    raise SystemExit('维护者脚本：会操作真实桌宠窗口。设置 DSH_DAFEIYU_ALLOW_LIVE_PROBE=1 后再运行。')

OUT = Path(__file__).resolve().parent / 'verification' / 'live'
OUT.mkdir(parents=True, exist_ok=True)


def windows():
    rows = []
    win32gui.EnumWindows(lambda handle, _: rows.append((handle, win32gui.GetWindowText(handle))) if win32gui.IsWindowVisible(handle) else None, None)
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
    raise RuntimeError(f'Window not visible: {title}')


def capture(handle, filename):
    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width, height = right-left, bottom-top
    window_dc = win32gui.GetWindowDC(handle)
    source = win32ui.CreateDCFromHandle(window_dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    target.SelectObject(bitmap)
    try:
        ok = ctypes.windll.user32.PrintWindow(handle, target.GetSafeHdc(), 2)
        if not ok:
            raise RuntimeError('PrintWindow failed')
        pixels = bitmap.GetBitmapBits(True)
        Image.frombuffer('RGB', (width, height), pixels, 'raw', 'BGRX', 0, 1).save(OUT / filename)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        try:
            source.DeleteDC()
        except win32ui.error:
            pass
        win32gui.ReleaseDC(handle, window_dc)


pet = wait_for('DSH 云岚 · 成长版')
initial_card = find('云岚 · 成长卡')
# Preserve a card the owner may already have opened while we were testing.
capture(pet, 'bubble.png')
left, top, right, bottom = win32gui.GetWindowRect(pet)
x, y = (left+right)//2, bottom-45
local_x, local_y = win32gui.ScreenToClient(pet, (x, y))
point_param = (local_x & 0xffff) | ((local_y & 0xffff) << 16)
try:
    win32gui.SetForegroundWindow(pet)
except Exception:
    pass
win32gui.PostMessage(pet, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, point_param)
win32gui.PostMessage(pet, win32con.WM_RBUTTONUP, 0, point_param)
pet_pid = win32process.GetWindowThreadProcessId(pet)[1]
# The Qt popup is a separate native window; only target the pet's menu.
menu = None
until = time.monotonic()+5
while time.monotonic() < until:
    menu = next((h for h, _ in windows() if 'Popup' in win32gui.GetClassName(h) and win32process.GetWindowThreadProcessId(h)[1] == pet_pid), None)
    if menu:
        break
    time.sleep(.1)
if not menu:
    raise RuntimeError('Native context menu not found')
capture(menu, 'menu.png')
# Growth is deliberately the first enabled action, so Home+Return tests the
# actual menu selection without moving the owner's pointer.
item_point = 48 | (12 << 16)
win32gui.PostMessage(menu, win32con.WM_MOUSEMOVE, 0, item_point)
win32gui.PostMessage(menu, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, item_point)
win32gui.PostMessage(menu, win32con.WM_LBUTTONUP, 0, item_point)
card = wait_for('云岚 · 成长卡', 5)
time.sleep(.25)
capture(card, 'growth-card.png')
if not initial_card and os.environ.get('DSH_DAFEIYU_VERIFY_JOURNAL_TABS') == '1':
    # Target only this known native dialog; no desktop-wide mouse input.
    # Coordinates come from this exact native UI's offline layout renderer,
    # not guessed screen positions. Only this known dialog receives messages.
    layout = json.loads((OUT.parent / 'probe-layout.json').read_text(encoding='utf-8'))
    def click(point):
        tx, ty = point
        click_at = tx | (ty << 16)
        win32gui.PostMessage(card, win32con.WM_MOUSEMOVE, 0, click_at)
        win32gui.PostMessage(card, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, click_at)
        win32gui.PostMessage(card, win32con.WM_LBUTTONUP, 0, click_at)
        time.sleep(.25)
    compact = False
    for index, name in enumerate(('overview', 'memory', 'diary', 'permissions', 'emotion', 'training', 'data')):
        click(layout['compact' if compact else 'full'][index])
        compact = index >= 4
        capture(card, f'journal-{name}.png')
        if index == 5:
            for suffix in ('records', 'sources'):
                click(layout['training-' + suffix])
                capture(card, f'journal-training-{suffix}.png')
        if index == 6 and os.environ.get('DSH_DAFEIYU_VERIFY_DATA_SUBTAB') == '1':
            click(layout['data-emotion'])
            capture(card, 'journal-data-emotion.png')
if not initial_card:
    win32gui.PostMessage(card, win32con.WM_CLOSE, 0, 0)
    until = time.monotonic()+3
    while find('云岚 · 成长卡') and time.monotonic() < until:
        time.sleep(.1)
    assert not find('云岚 · 成长卡'), 'Card failed to close'
result = {'ok': True, 'nativePet': pet, 'nativeMenu': menu, 'nativeCard': card,
          'cardOpenedByRightClickMenu': True, 'preservedOwnerOpenCard': bool(initial_card),
          'closedAfterTest': not bool(initial_card)}
(OUT/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
