"""Passive visual tokens and original vector icons for the growth workspace."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

PALETTE = {
    'canvas':'#F3F6F7', 'surface':'#FFFFFF', 'soft':'#EAF2F3',
    'ink':'#17343D', 'text':'#435E68', 'muted':'#627B84',
    'border':'#D9E6E8', 'teal':'#187E83', 'deep':'#11595F',
    'mint':'#EFF8F5', 'gold':'#A77739', 'danger':'#AF4C56', 'warning':'#FFF5E5',
}


def icon(name, color='#187E83', size=20):
    """Draw small original line icons; no fonts, assets, IO or external links."""
    image = QPixmap(size * 2, size * 2)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == 'shield':
        path = QPainterPath(QPointF(12, 3))
        path.lineTo(20, 6); path.lineTo(19, 13); path.quadTo(17, 19, 12, 21)
        path.quadTo(7, 19, 5, 13); path.lineTo(4, 6); path.closeSubpath()
        p.drawPath(path); p.drawLine(8, 11, 11, 14); p.drawLine(11, 14, 16, 9)
    elif name in ('growth', 'skills'):
        p.drawLine(5, 20, 5, 15); p.drawLine(11, 20, 11, 11); p.drawLine(17, 20, 17, 7)
        p.drawLine(4, 10, 19, 3); p.drawLine(14, 3, 19, 3); p.drawLine(19, 3, 19, 8)
    elif name == 'history':
        p.drawEllipse(QRectF(4, 4, 16, 16)); p.drawLine(12, 7, 12, 12); p.drawLine(12, 12, 16, 14)
    elif name == 'emotion':
        path = QPainterPath(QPointF(12, 20))
        path.cubicTo(3, 14, 1, 7, 6, 5); path.cubicTo(9, 4, 11, 6, 12, 8)
        path.cubicTo(14, 4, 18, 4, 20, 7); path.cubicTo(23, 12, 16, 18, 12, 20)
        p.drawPath(path)
    elif name == 'data':
        p.drawEllipse(QRectF(4, 3, 16, 6)); p.drawArc(QRectF(4, 10, 16, 6), 180 * 16, 180 * 16)
        p.drawArc(QRectF(4, 16, 16, 6), 180 * 16, 180 * 16); p.drawLine(4, 6, 4, 19); p.drawLine(20, 6, 20, 19)
    elif name == 'training':
        p.drawRoundedRect(QRectF(4, 5, 16, 15), 3, 3); p.drawLine(8, 9, 16, 9)
        p.drawLine(8, 13, 13, 13); p.drawLine(8, 17, 11, 17)
    elif name == 'voice':
        p.drawRoundedRect(QRectF(9, 3, 6, 12), 3, 3); p.drawArc(QRectF(5, 6, 14, 13), 180 * 16, 180 * 16)
        p.drawLine(12, 19, 12, 22); p.drawLine(8, 22, 16, 22)
    elif name == 'arrow':
        p.drawLine(5, 12, 19, 12); p.drawLine(14, 7, 19, 12); p.drawLine(19, 12, 14, 17)
    elif name == 'chat':
        p.drawRoundedRect(QRectF(3, 5, 18, 13), 4, 4)
        p.drawLine(8, 18, 7, 21); p.drawLine(7, 21, 12, 18)
        p.drawLine(8, 10, 16, 10); p.drawLine(8, 13, 13, 13)
    elif name == 'spin':
        p.drawArc(QRectF(4, 4, 16, 16), 40 * 16, 280 * 16)
        p.drawLine(19, 3, 19, 8); p.drawLine(19, 8, 14, 8)
    elif name == 'heart':
        path = QPainterPath(QPointF(12, 20))
        path.cubicTo(3, 14, 2, 7, 7, 5); path.cubicTo(10, 4, 11, 6, 12, 8)
        path.cubicTo(13, 6, 15, 4, 18, 6); path.cubicTo(22, 9, 19, 15, 12, 20)
        p.drawPath(path)
    elif name == 'hide':
        p.drawEllipse(QRectF(3, 7, 18, 10)); p.drawEllipse(QRectF(10, 10, 4, 4))
        p.drawLine(5, 20, 19, 4)
    elif name == 'close':
        p.drawLine(6, 6, 18, 18); p.drawLine(18, 6, 6, 18)
    elif name == 'check':
        p.drawLine(5, 12, 10, 17); p.drawLine(10, 17, 19, 7)
    elif name == 'spark':
        p.drawLine(12, 3, 12, 9); p.drawLine(12, 15, 12, 21)
        p.drawLine(3, 12, 9, 12); p.drawLine(15, 12, 21, 12)
        p.drawLine(6, 6, 9, 9); p.drawLine(15, 15, 18, 18)
        p.drawLine(18, 6, 15, 9); p.drawLine(9, 15, 6, 18)
    elif name == 'reset':
        p.drawArc(QRectF(5, 5, 15, 15), -40 * 16, 290 * 16)
        p.drawLine(4, 4, 4, 10); p.drawLine(4, 10, 10, 10)
    else:
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 4, 4); p.drawLine(8, 9, 16, 9); p.drawLine(8, 14, 14, 14)
    p.end()
    return QIcon(image)


MENU_QSS = """
QMenu { background:#FFFFFF; border:1px solid #D9E6E8; border-radius:12px; padding:8px; }
QMenu::item { padding:8px 30px 8px 34px; margin:1px 4px; border-radius:8px;
    color:#284B54; font-family:'Microsoft YaHei UI'; font-size:13px; }
QMenu::item:selected { background:#EFF8F5; color:#11595F; }
QMenu::item:disabled { color:#9AAFB5; }
QMenu::separator { height:1px; background:#E5EEEE; margin:7px 12px; }
QMenu::section { padding:10px 26px 3px 34px; color:#7A939B; font-size:11px; font-weight:700; }
QMenu::indicator { width:13px; height:13px; margin-left:10px; }
QMenu::indicator:unchecked { border:1px solid #B9CFD3; border-radius:4px; background:#FFFFFF; }
QMenu::indicator:checked { background:#187E83; border:3px solid #B6D9D8; border-radius:4px; }
QMenu::right-arrow { width:8px; height:8px; margin-right:10px; }
QMenu::scroller { height:18px; }
"""

DIALOG_QSS = """
QDialog#growthJournal { background:#F3F6F7; }
QLabel { background:transparent; color:#435E68; font-family:'Microsoft YaHei UI'; font-size:13px; }
QDialog#growthJournal QScrollArea { border:none; background:transparent; }
QWidget#growthContent { background:transparent; }
QLabel#coverEyebrow { color:#B6DFDF; font-size:12px; }
QLabel#coverName { color:#FFFFFF; font-size:30px; font-weight:700; }
QLabel#growthLevel { color:#E3F0CA; font-size:15px; font-weight:600; }
QLabel#growthTitle { color:#D2E8E8; font-size:12px; }
QLabel#growthXp, QLabel#coverProgressHint { color:#E2EEEE; font-size:12px; }
QLabel[heading='true'] { font-size:16px; font-weight:700; color:#17343D; }
QLabel#sectionAside { color:#627B84; font-size:12px; }
QLabel#pageKicker { color:#187E83; font-size:12px; font-weight:600; }
QLabel#pageCaption { color:#627B84; font-size:12px; }
QFrame[journalCard='true'] { background:#FFFFFF; border:1px solid #D9E6E8; border-radius:14px; }
QFrame#heroCard { background:transparent; border:none; }
QFrame[traitTile='true'] { background:#F3F8F8; border:none; border-radius:10px; }
QLabel[traitNumber='true'] { color:#17343D; font-size:19px; font-weight:700; }
QLabel#memoryStatus, QLabel#growthBond { color:#627B84; font-size:12px; }
QLabel#permissionText, QLabel#neuralInfo, QLabel#cloudStatus { color:#435E68; font-size:13px; }
QLabel#growthSkills, QLabel#growthLearned { color:#284B54; font-size:14px; }
QLabel#emotionName { color:#11595F; font-size:25px; font-weight:700; }
QLabel#heardEmotion { color:#17343D; font-size:22px; font-weight:700; }
QLabel#heardDetail, QLabel#emotionInfo, QLabel#learningStatus { color:#435E68; font-size:13px; }
QLabel#emotionFootnote, QLabel#footerHint { color:#627B84; font-size:12px; }
QLabel#feelingCaption { color:#627B84; font-size:12px; }
QLabel#feelingValue { color:#17343D; font-size:19px; font-weight:700; }
QFrame#companionFeelingCard { background:#EFF8F5; border:1px solid #D1E6DF; border-radius:16px; }
QFrame#feelingTile { background:#FFFFFF; border:1px solid #D9E6E8; border-radius:12px; }
QFrame#moodOrb { background:transparent; border:none; }
QProgressBar { border:none; background:#E3EDEE; border-radius:3px; }
QProgressBar::chunk { border:none; background:#238B8A; border-radius:3px; }
QProgressBar#growthXpProgress { background:#4A7C80; border-radius:3px; }
QProgressBar#growthXpProgress::chunk { background:#D8EAC0; border-radius:3px; }
QProgressBar#emotionSignal::chunk { background:#55A4A5; }
QTabWidget::pane { border:none; background:transparent; top:6px; }
QTabBar { background:#E4EDEE; border-radius:11px; }
QTabBar::tab { color:#4D6972; background:transparent; padding:9px 5px; margin:3px 1px; border:1px solid transparent; border-radius:8px; font-family:'Microsoft YaHei UI'; font-size:13px; }
QTabBar::tab:selected { background:#FFFFFF; color:#11595F; border-color:#D4E3E5; font-weight:700; }
QTabBar::tab:hover:!selected { background:#D9E7E8; color:#11595F; }
QPushButton { background:#FFFFFF; color:#284E58; border:1px solid #CDDEE1; border-radius:9px; padding:7px 12px; font-family:'Microsoft YaHei UI'; font-size:13px; }
QPushButton:hover { background:#EFF8F7; border-color:#8FBFBF; }
QPushButton:pressed { background:#D9ECEB; border-color:#187E83; }
QPushButton:focus { border:1px solid #187E83; }
QPushButton:disabled { color:#98ACB3; background:#EAF0F2; border-color:#DFE8EA; }
QPushButton#closeJournal, QPushButton#emotionLearn, QPushButton[primary='true'] { background:#187E83; border-color:#187E83; color:white; font-weight:600; }
QPushButton#closeJournal:hover, QPushButton#emotionLearn:hover, QPushButton[primary='true']:hover { background:#115F67; }
QPushButton#closeJournal:pressed, QPushButton#emotionLearn:pressed, QPushButton[primary='true']:pressed { background:#104F57; }
QPushButton#emotionLearn:disabled, QPushButton[primary='true']:disabled { background:#DDE9EA; color:#8A9FA6; border-color:#DDE9EA; }
QPushButton#forgetMemory, QPushButton#neuralReset { color:#AF4C56; background:#FFF9F9; border-color:#E9D9DD; }
QPushButton#forgetMemory:hover, QPushButton#neuralReset:hover { background:#FBECEE; border-color:#D79FA9; }
QPushButton#emotionSection { background:#E4EDEE; color:#546E77; border:1px solid transparent; padding:7px 12px; }
QPushButton#emotionSection:checked { background:#FFFFFF; color:#11595F; border-color:#C7DDDE; font-weight:600; }
QLabel[badge='true'] { color:#187E83; background:#EAF6F1; border:1px solid #D2E8DE; border-radius:7px; padding:4px 8px; font-size:12px; }
QLabel[status='warning'] { color:#886021; background:#FFF5E5; border:1px solid #EBDAB7; border-radius:7px; padding:6px; }
QLabel[status='error'] { color:#A34550; background:#FFF0F1; border:1px solid #EAD0D5; border-radius:7px; padding:6px; }
QCheckBox { color:#284B54; font-family:'Microsoft YaHei UI'; font-size:13px; spacing:8px; }
QCheckBox::indicator { width:17px; height:17px; border:1px solid #9AB9BE; border-radius:5px; background:white; }
QCheckBox::indicator:hover { border-color:#187E83; }
QCheckBox::indicator:checked { background:#187E83; border:3px solid #B6D9D8; }
QCheckBox::indicator:disabled { background:#E4ECEE; border-color:#CDDADD; }
QScrollBar:vertical { background:transparent; width:6px; margin:3px 0; }
QScrollBar::handle:vertical { background:#B9CFD3; border-radius:3px; min-height:28px; }
QScrollBar::handle:vertical:hover { background:#7AAEB2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QToolTip { background:#17343D; color:white; border:1px solid #17343D; border-radius:5px; padding:6px; font-size:12px; }
"""
