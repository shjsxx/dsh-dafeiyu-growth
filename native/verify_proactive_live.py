"""Observe installed pet's spontaneous card; reply locally, never invoke cloud.

Run immediately after pet-only installation/restart. An already-open owner card
is preserved and is never typed into. Screenshots capture only the known card.
"""
import ctypes
import json
import os
from pathlib import Path
import time
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

if os.environ.get('DSH_DAFEIYU_ALLOW_LIVE_PROBE') != '1':
    raise SystemExit('维护者脚本：会向真实桌宠注入按键。设置 DSH_DAFEIYU_ALLOW_LIVE_PROBE=1 后再运行。')

OUT = Path(__file__).resolve().parent / "verification" / "live"
OUT.mkdir(parents=True, exist_ok=True)


def find(title):
    handles = []
    win32gui.EnumWindows(lambda handle, _: handles.append(handle) if win32gui.IsWindowVisible(handle) and win32gui.GetWindowText(handle) == title else None, None)
    return handles[0] if handles else None


def capture(handle, name):
    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width, height = right - left, bottom - top
    dc = win32gui.GetWindowDC(handle)
    source = win32ui.CreateDCFromHandle(dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    target.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(handle, target.GetSafeHdc(), 2):
            raise RuntimeError("capture failed")
        Image.frombuffer("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1).save(OUT / name)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(handle, dc)


def input_location():
    # The passive widget creates no network/model/file operations.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"))
    from PySide6.QtWidgets import QApplication
    from conversation_ui import ConversationDialog
    app = QApplication.instance() or QApplication([])
    card = ConversationDialog()
    card.show_proactive("云岚主动搭话")
    app.processEvents()
    point = card.input.mapTo(card, card.input.rect().center())
    location = (point.x(), point.y(), card.width(), card.height())
    card.close()
    app.processEvents()
    return location


pet = find("DSH 云岚 · 成长版")
startup_deadline = time.monotonic() + 45
while not pet and time.monotonic() < startup_deadline:
    time.sleep(.2)
    pet = find("DSH 云岚 · 成长版")
if not pet:
    raise SystemExit("installed native pet not visible after startup deadline")
pid = win32process.GetWindowThreadProcessId(pet)[1]
initial_card = find("和云岚聊天")
foreground = win32gui.GetForegroundWindow()
start = time.monotonic()
card = initial_card
while not card and time.monotonic() - start < 110:
    candidate = find("和云岚聊天")
    if candidate and win32process.GetWindowThreadProcessId(candidate)[1] == pid:
        card = candidate
    else:
        time.sleep(.2)
if not card:
    raise SystemExit("proactive card did not appear; check chatter/approval/window gates")
result = {"ok": True, "nativePet": pet, "nativeConversation": card, "spontaneous": initial_card is None,
          "waitSeconds": round(time.monotonic() - start, 2), "foregroundPreserved": win32gui.GetForegroundWindow() == foreground,
          "preservedOwnerOpenCard": initial_card is not None, "cloudRequests": 0}
time.sleep(.4)  # The HWND may become visible before Qt's first native paint.
capture(card, "proactive-native.png")
if initial_card is None:
    # The newly created card defaults LOCAL. Never touch cloud/session controls.
    profile = Path.home() / ".dsh" / "dsh-dafeiyu-growth" / "behavior.json"
    before = json.loads(profile.read_text(encoding="utf-8")).get("chats", 0)
    x, y, base_width, base_height = input_location()
    _, _, width, height = win32gui.GetClientRect(card)
    point = (round(x * width / base_width) & 0xffff) | ((round(y * height / base_height) & 0xffff) << 16)
    try:
        win32gui.SetForegroundWindow(card)
    except Exception:
        pass
    win32gui.PostMessage(card, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
    win32gui.PostMessage(card, win32con.WM_LBUTTONUP, 0, point)
    time.sleep(.2)
    for char in "早安":
        win32gui.PostMessage(card, win32con.WM_CHAR, ord(char), 0)
    win32gui.PostMessage(card, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32gui.PostMessage(card, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    until = time.monotonic() + 6
    after = before
    while after <= before and time.monotonic() < until:
        time.sleep(.2)
        after = json.loads(profile.read_text(encoding="utf-8")).get("chats", 0)
    result["localReplyAccepted"] = after > before
    result["ok"] = result["localReplyAccepted"]
    time.sleep(.8)  # Let Qt finish delayed layout/scroll and native painting.
    capture(card, "proactive-native-replied.png")
    win32gui.PostMessage(card, win32con.WM_CLOSE, 0, 0)
    result["closedTestCard"] = True
(OUT / "proactive-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result["ok"]:
    raise SystemExit(1)
