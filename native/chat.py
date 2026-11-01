"""Local conversation for 云岚 plus the small input card.

No model and no network: a handful of keyword rules, the owner's name, and a
few learned preferences. The point is a companion that answers immediately and
occasionally acts on what you ask ("跑一圈" really makes it run).
"""
from __future__ import annotations

import re

# Only a complete, explicit declaration may write personal memory. Searching
# inside arbitrary sentences also learns questions, quotations and negations.
NAME_PATTERNS = (
    re.compile(r"(?:我叫|叫我)\s*([^\s，。,.!！?？：:；;\"'“”‘’]{1,20})[。.!！]?$"),
)
MEMORY_QUESTION = re.compile(r"(?:什么|谁|啥|干嘛).*|.*(?:吗|呢|么|还是.*)$")
MEMORY_AMBIGUITY = re.compile(r"但是|不过|不是|还是|如果|假如|要是|的话|别记|不要记|别保存|不要保存|不许记|不准记")
NAME_NON_DECLARATION = re.compile(r"^(?:你|您|他|她|它|了|过|着)|^(?:起床|起来|吃饭|开会|睡觉|跑步|跳舞|一声)$")

DEFAULT_REPLIES = (
    "嗯，我在呢。想聊什么就慢慢说。",
    "想让我陪你听一听，还是一起想办法？",
    "然后呢？我还在听。",
    "我在呢，慢慢说，不着急。",
    "我还没完全懂，但愿意听你多说一点。", 
)

LIKE_PATTERN = re.compile(r"我(?:很|最|超|挺)?喜欢\s*([^\s，。,.!！?？：:；;\"'“”‘’]{1,30})[。.!！]?$")
# A local keyword companion must prefer doing nothing over acting on a refusal,
# a question about an activity, or a quoted/hypothetical request.
ACTION_REFUSAL = re.compile(r"(?:不|别|勿|莫|禁止|停止|不用|无需|不要|不能|不许|不准|别再|不想|不愿|不必)")
ACTION_CONTEXT = re.compile(r"[?？\"'“”‘’]|(?:如果|假如|要是|比如|例如|为什么|为何|怎么|如何|喜欢)")
ACTION_WORDS = ("跑", "冲刺", "跳", "蹦", "玩", "表演", "动一下", "转圈", "旋转")
COMMAND_PREFIX = r"(?:云岚[，, ]*)?(?:请你|请|麻烦你|给我|帮我)?\s*"
COMMAND_SUFFIX = r"(?:给我看|给我看看)?(?:吧|呀|哦)?[。.!！]?$"
RUN_COMMAND = re.compile(COMMAND_PREFIX + r"(?:跑(?:一圈|一下|一段|起来|步)?|冲刺(?:一下)?)" + COMMAND_SUFFIX)
HOP_COMMAND = re.compile(COMMAND_PREFIX + r"(?:跳(?:一个|一下|一跳|起来)?|蹦(?:一下|一个)?)" + COMMAND_SUFFIX)
SPIN_COMMAND = re.compile(COMMAND_PREFIX + r"(?:转(?:一?圈|一下|个圈|起来)?|旋转(?:一下|一圈)?)" + COMMAND_SUFFIX)
PLAY_COMMAND = re.compile(COMMAND_PREFIX + r"(?:玩(?:一下|一会儿)?|表演(?:一下|一个)?|动一下)" + COMMAND_SUFFIX)


def _pick(text, options):
    index = sum(ord(char) for char in text) % len(options)
    return options[index]


def reply_to(text, name="", mood=""):
    """Return {text, action, name} for one short message."""
    raw = " ".join(str(text or "").split())[:200]
    if not raw:
        return {"text": "嗯？你刚才想说什么？", "action": None, "name": ""}
    learned_name = ""
    for pattern in NAME_PATTERNS:
        match = pattern.fullmatch(raw)
        if match:
            candidate = match.group(1)
            if not (MEMORY_QUESTION.fullmatch(candidate) or MEMORY_AMBIGUITY.search(candidate) or NAME_NON_DECLARATION.search(candidate)):
                learned_name = candidate
                break
    if learned_name:
        return {"text": f"记住啦，{learned_name}。以后我就这么叫你。", "action": None, "name": learned_name}

    if any(word in raw for word in ("你叫什么", "你的名字", "你是谁")):
        return {"text": "我叫云岚。是住在你桌面上的那只。", "action": None, "name": ""}
    if any(word in raw for word in ("我叫什么", "我是谁", "记得我")):
        if name:
            return {"text": f"当然记得，你是{name}呀。", "action": None, "name": ""}
        return {"text": "你还没告诉我名字呢。说「我叫……」我就记住。", "action": None, "name": ""}
    like = LIKE_PATTERN.fullmatch(raw)
    if like and not (MEMORY_QUESTION.fullmatch(like.group(1)) or MEMORY_AMBIGUITY.search(like.group(1))) and like.group(1) not in ("你", "您", "他", "她", "它"):
        value = like.group(1)
        return {"text": f"记住啦，你喜欢{value}。", "action": None, "name": "", "like": value}
    if any(word in raw for word in ACTION_WORDS) and (ACTION_REFUSAL.search(raw) or ACTION_CONTEXT.search(raw)):
        return {"text": "嗯，我先不动。想看动作时，可以说「跑一圈」或「跳一个」。", "action": None, "name": ""}
    if RUN_COMMAND.fullmatch(raw):
        return {"text": "好，我跑一圈给你看。", "action": "run", "name": ""}
    if HOP_COMMAND.fullmatch(raw):
        return {"text": "看好了，跳一个。", "action": "hop", "name": ""}
    if SPIN_COMMAND.fullmatch(raw):
        return {"text": "好，转一圈给你看。", "action": "spin", "name": ""}
    if PLAY_COMMAND.fullmatch(raw):
        action = "hop" if sum(ord(char) for char in raw) % 2 else "run"
        return {"text": "那我动给你看。", "action": action, "name": ""}
    if any(word in raw for word in ("晚安", "睡觉")):
        return {"text": "晚安。我把灯留着，也把桌面看住。", "action": None, "name": ""}
    if any(word in raw for word in ("累", "困", "休息", "睡")):
        return {"text": "累了就靠一会儿，我帮你看着桌面。", "action": None, "name": ""}
    if any(word in raw for word in ("早", "早安", "早上好")):
        return {"text": "早呀。今天想先做哪件事？", "action": None, "name": ""}
    if any(word in raw for word in ("你好", "嗨", "在吗", "hello", "hi")):
        greeting = f"{name}，我在呢。" if name else "我在呢。"
        return {"text": greeting + "今天也一起加油。", "action": None, "name": ""}
    if not ACTION_REFUSAL.search(raw) and any(word in raw for word in ("喜欢你", "爱你", "想你")):
        return {"text": "我也喜欢你。会一直待在你桌面上的。", "action": None, "name": ""}
    if "喜欢" in raw:
        return {"text": "我只会记下明确的偏好，比如「我喜欢喝咖啡」。这句话没有保存成偏好。", "action": None, "name": ""}
    if any(word in raw for word in ("摸头", "摸摸", "摸一下")):
        return {"text": "摸头就摸头，别把我发型弄乱。", "action": None, "name": ""}
    if any(word in raw for word in ("谢谢", "多谢")):
        return {"text": "不用谢，我本来就想帮你。", "action": None, "name": ""}
    if any(word in raw for word in ("学习", "练习", "本领")):
        return {"text": "我在练动作，也在慢慢积累情感线索和基础知识。成长卡的训练页会把每次权重更新、资料收录和状态变化分开给你看。", "action": None, "name": ""}
    if any(word in raw for word in ("名字叫云岚", "云岚")):
        return {"text": "嗯，云岚，山里的雾。", "action": None, "name": ""}
    if any(word in raw for word in ("可爱", "好看", "漂亮")):
        return {"text": "谢谢，你这么说我很开心。", "action": None, "name": ""}
    if mood == "sad":
        return {"text": "听起来不太好受。我不追问，先陪你待一会儿。", "action": None, "name": ""}
    if mood == "tired":
        return {"text": "那就先歇一会儿。桌面我盯着，不用撑着。", "action": None, "name": ""}
    if mood == "anxious":
        return {"text": "先慢慢呼吸。我们把事情拆成一小块，只做下一步。", "action": None, "name": ""}
    if mood == "anger":
        return {"text": "我先不闹。你想说就说，不想说我就安静待着。", "action": None, "name": ""}
    if mood == "joy":
        return {"text": "听得出来你心情不错，我也跟着高兴。", "action": None, "name": ""}
    if mood == "calm":
        return {"text": "嗯，慢慢来就好。", "action": None, "name": ""}
    return {"text": _pick(raw, DEFAULT_REPLIES), "action": None, "name": ""}


def _dialog_class():
    # Keep the deterministic rules usable without importing the optional GUI.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    )

    class ChatDialog(QDialog):
        """Small always-on-top input card; ask() returns the typed line or ''."""

        def __init__(self, parent=None, name=""):
            super().__init__(parent)
            self.setWindowTitle("和云岚说话")
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.setModal(True)
            self.setMinimumWidth(360)
            self.setStyleSheet("""
                QDialog { background: #F4F6FA; }
                QLabel { color: #2B3846; font-family: "Microsoft YaHei UI"; font-size: 12px; background: transparent; }
                QLabel#chatTitle { font-size: 16px; font-weight: 700; color: #1B2838; }
                QLabel#chatHint { font-size: 11px; color: #7A8796; }
                QLineEdit { background: #FFFFFF; border: 1px solid #DCE3EB; border-radius: 9px; padding: 9px 11px; font-size: 13px; color: #22303F; }
                QLineEdit:focus { border-color: #8FBFD4; }
                QPushButton { background: #FFFFFF; color: #415066; border: 1px solid #DCE3EB; border-radius: 8px; padding: 7px 16px; }
                QPushButton:hover { background: #F1F6FB; }
                QPushButton#chatSend { background: #E8F2F9; border-color: #C9DEEC; color: #2E5B78; font-weight: 600; }
            """)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 16, 18, 14)
            layout.setSpacing(8)
            title = QLabel("和云岚说句话")
            title.setObjectName("chatTitle")
            title.setTextFormat(Qt.TextFormat.PlainText)
            hint = QLabel(f"{name}，想说什么都可以。它会记得你的名字和几句偏好。" if name else "说「我叫……」它会记住你的名字。")
            hint.setObjectName("chatHint")
            hint.setTextFormat(Qt.TextFormat.PlainText)
            hint.setWordWrap(True)
            self.input = QLineEdit()
            self.input.setMaxLength(200)
            self.input.setPlaceholderText("比如：我叫小林 / 跑一圈 / 晚安")
            self.input.returnPressed.connect(self.accept)
            buttons = QHBoxLayout()
            buttons.addStretch()
            cancel = QPushButton("取消")
            cancel.setAutoDefault(False)
            cancel.clicked.connect(self.reject)
            send = QPushButton("说给它听")
            send.setObjectName("chatSend")
            send.setDefault(True)
            send.clicked.connect(self.accept)
            buttons.addWidget(cancel)
            buttons.addWidget(send)
            layout.addWidget(title)
            layout.addWidget(hint)
            layout.addSpacing(2)
            layout.addWidget(self.input)
            layout.addLayout(buttons)
            self.input.setFocus()

        def ask(self):
            if self.exec() == QDialog.DialogCode.Accepted:
                return self.input.text().strip()
            return ""

    return ChatDialog


def __getattr__(attribute):
    if attribute == "ChatDialog":
        dialog = _dialog_class()
        globals()[attribute] = dialog
        return dialog
    raise AttributeError(f"module {__name__!r} has no attribute {attribute!r}")
