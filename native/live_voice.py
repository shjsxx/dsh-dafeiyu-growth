"""Inspect installed voice controls, propose then emergency-cancel a fixed action.

Never enable the microphone, learning, cloud consent, TTS or execution. Preserve
already-open owner windows by skipping this probe rather than interacting.
"""
import ctypes
import json
import os
from pathlib import Path
import time
import win32gui
import win32con
import win32process
import win32ui
from PIL import Image
from training_journal import TrainingJournal

if os.environ.get('DSH_DAFEIYU_ALLOW_LIVE_PROBE') != '1':
    raise SystemExit('维护者脚本：会操作真实桌宠窗口。设置 DSH_DAFEIYU_ALLOW_LIVE_PROBE=1 后再运行。')

OUT = Path(__file__).resolve().parent / 'verification' / 'live'
OUT.mkdir(parents=True, exist_ok=True)
CHAT = '和云岚聊天'
TOOLS = '云岚 · 连续语音与安全权限'
SETTINGS = '云岚 · 实时语音 API 设置'


def windows():
    result = []
    win32gui.EnumWindows(lambda h, _: result.append((h, win32gui.GetWindowText(h))) if win32gui.IsWindowVisible(h) else None, None)
    return result


def find(title):
    return next((h for h, name in windows() if name == title), None)


def wait_for(title, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        handle = find(title)
        if handle:
            return handle
        time.sleep(.1)
    return None


def click(handle, x, y):
    point = (round(x) & 0xffff) | ((round(y) & 0xffff) << 16)
    win32gui.PostMessage(handle, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
    win32gui.PostMessage(handle, win32con.WM_LBUTTONUP, 0, point)
    time.sleep(.2)


def capture(handle, name):
    time.sleep(.4)
    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width, height = right-left, bottom-top
    dc = win32gui.GetWindowDC(handle)
    src = win32ui.CreateDCFromHandle(dc)
    target = src.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src, width, height)
    target.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(handle, target.GetSafeHdc(), 2):
            raise RuntimeError('capture_failed')
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(OUT/name)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(handle, dc)


def coords():
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from conversation_ui import ConversationDialog
    from voice_ui import VoiceSessionDialog
    app = QApplication.instance() or QApplication([])
    chat = ConversationDialog()
    chat.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    chat.show()
    tools = VoiceSessionDialog()
    tools.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    tools.update_state({'capabilities':{'chinese_available':True,'tts_available':True}})
    tools.show()
    app.processEvents()
    result = {}
    for key, widget, owner in [('voice_button',chat.voice_tools_button,chat),('input',chat.input,chat),('control',tools.control,tools),('rt_settings',tools.realtime_settings_button,tools),('rt_start',tools.realtime_start_button,tools)]:
        point = widget.mapTo(owner, widget.rect().center())
        result[key] = [point.x(),point.y(),owner.width(),owner.height()]
    chat.close()
    tools.close()
    app.processEvents()
    return result


def click_coord(handle, point):
    _, _, width, height = win32gui.GetClientRect(handle)
    x,y,bw,bh = point
    click(handle, x*width/bw, y*height/bh)


def main():
    pet = wait_for('DSH 云岚 · 成长版')
    if not pet:
        raise RuntimeError('pet_missing')
    if find(CHAT) or find(TOOLS) or find('云岚 · 成长卡') or find(SETTINGS):
        print(json.dumps({'ok':True,'skipped':True,'preservedOwnerOpenWindows':True}))
        return
    stored = Path(os.environ.get('DSH_HOME') or Path.home()/'.dsh')/'dsh-dafeiyu-growth'/'realtime.json'
    if stored.exists():
        # Never touch an owner-configured provider; this probe must not connect.
        print(json.dumps({'ok':True,'skipped':True,'preservedOwnerRealtimeConfig':True}))
        return
    before = TrainingJournal(read_only=True).snapshot()['totals']
    points = coords()
    thread,pid = win32process.GetWindowThreadProcessId(pet)
    chat = tools = None
    result = {'ok':False,'microphoneEnabled':False,'cloudRequests':0,'executions':0}
    try:
        for row in (40,56):
            left,top,right,bottom = win32gui.GetWindowRect(pet)
            x,y = win32gui.ScreenToClient(pet,((left+right)//2,bottom-45))
            packed = (x & 0xffff) | ((y & 0xffff)<<16)
            try: win32gui.SetForegroundWindow(pet)
            except Exception: pass
            win32gui.PostMessage(pet, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, packed)
            win32gui.PostMessage(pet, win32con.WM_RBUTTONUP, 0, packed)
            until=time.monotonic()+3
            menu=None
            while not menu and time.monotonic()<until:
                menu=next((h for h,_ in windows() if 'Popup' in win32gui.GetClassName(h) and win32process.GetWindowThreadProcessId(h)[1]==pid),None)
                if not menu: time.sleep(.1)
            if not menu: raise RuntimeError('native_menu_missing')
            click(menu,60,row)
            chat=wait_for(CHAT,2)
            if chat: break
            card=find('云岚 · 成长卡')
            if card:
                win32gui.PostMessage(card,win32con.WM_CLOSE,0,0)
                time.sleep(.3)
        if not chat: raise RuntimeError('chat_missing')
        click_coord(chat,points['voice_button'])
        tools=wait_for(TOOLS,8)
        if not tools: raise RuntimeError('voice_tools_missing')
        capture(tools,'voice-native.png')
        # Readiness only: the independent API settings window must not save,
        # connect, open a microphone or need any provider key.
        click_coord(tools,points['rt_settings'])
        settings = wait_for(SETTINGS, 8)
        if not settings:
            raise RuntimeError('realtime_settings_missing')
        capture(settings,'realtime-api-native.png')
        if stored.exists():
            raise RuntimeError('unexpected_realtime_config_written')
        snapshot = TrainingJournal(read_only=True).snapshot()
        if snapshot['totals'].get('realtime_sessions', 0) != before.get('realtime_sessions', 0):
            raise RuntimeError('unexpected_realtime_session')
        win32gui.PostMessage(settings,win32con.WM_CLOSE,0,0)
        until=time.monotonic()+3
        while find(SETTINGS) and time.monotonic()<until:
            time.sleep(.1)
        if find(SETTINGS):
            raise RuntimeError('realtime_settings_close_failed')
        click_coord(tools,points['rt_start'])
        # With no saved key, starting must reopen settings rather than connect.
        again = wait_for(SETTINGS, 5)
        if not again:
            raise RuntimeError('unconfigured_start_did_not_ask_for_settings')
        capture(again,'realtime-api-required.png')
        win32gui.PostMessage(again,win32con.WM_CLOSE,0,0)
        until=time.monotonic()+3
        while find(SETTINGS) and time.monotonic()<until:
            time.sleep(.1)
        snapshot = TrainingJournal(read_only=True).snapshot()
        if (snapshot['totals'].get('realtime_sessions', 0) != before.get('realtime_sessions', 0)
                or any(r.get('source') == 'realtime_voice' and r.get('labels', {}).get('voice_event') in ('rt_connect', 'rt_start') for r in snapshot['events'])):
            raise RuntimeError('unconfigured_realtime_must_not_connect')
        if stored.exists():
            raise RuntimeError('unexpected_realtime_config_written_after_start')
        click_coord(tools,points['control'])
        capture(tools,'voice-native-permission.png')
        try: win32gui.SetForegroundWindow(chat)
        except Exception: pass
        click_coord(chat,points['input'])
        for char in '云岚，打开计算器':
            win32gui.PostMessage(chat,win32con.WM_CHAR,ord(char),0)
        win32gui.PostMessage(chat,win32con.WM_KEYDOWN,win32con.VK_RETURN,0)
        win32gui.PostMessage(chat,win32con.WM_KEYUP,win32con.VK_RETURN,0)
        capture(chat,'voice-test-command.png')
        until=time.monotonic()+5
        row=None
        while time.monotonic()<until:
            snapshot=TrainingJournal(read_only=True).snapshot()
            row=next((r for r in snapshot['events'] if r['kind']=='computer_control' and r['status']=='queued'),None)
            if row: break
            time.sleep(.1)
        if not row: raise RuntimeError('no_pending_audited_proposal')
        if snapshot['totals']['computer_executed'] != before['computer_executed']:
            raise RuntimeError('unexpected_execution_before_confirmation')
        capture(tools,'voice-native-pending.png')
        # Exercise the native WM_HOTKEY dispatcher path; only denies authority.
        if not ctypes.windll.user32.PostThreadMessageW(thread,0x0312,0x594C,0):
            raise RuntimeError('stop_message_failed')
        until=time.monotonic()+5
        stopped=False
        while time.monotonic()<until:
            snapshot=TrainingJournal(read_only=True).snapshot()
            stopped=any(r['id'] > row['id'] and r['kind']=='computer_control' and r.get('labels',{}).get('decision')=='emergency_stop' for r in snapshot['events'])
            if stopped: break
            time.sleep(.1)
        if not stopped: raise RuntimeError('global_stop_dispatch_failed')
        capture(tools,'voice-native-stopped.png')
        if snapshot['totals']['computer_executed'] != before['computer_executed'] or snapshot['totals']['voice_sessions'] != before['voice_sessions']:
            raise RuntimeError('unexpected_operation_or_recording')
        result.update(ok=True,nativeControls=True,proposalAuditedBeforeDispatch=True,noExecutionWithoutClick=True,nativeStopDispatcher=True)
    finally:
        if tools and win32gui.IsWindow(tools): win32gui.PostMessage(tools,win32con.WM_CLOSE,0,0)
        if chat and win32gui.IsWindow(chat): win32gui.PostMessage(chat,win32con.WM_CLOSE,0,0)
        (OUT/'voice-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
