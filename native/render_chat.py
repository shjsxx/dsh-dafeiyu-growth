"""Render the chat input card offscreen for visual review."""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from chat import ChatDialog  # noqa: E402

app = QApplication(sys.argv[:1])
dialog = ChatDialog(name="小林")
dialog.input.setText("跑一圈给我看")
dialog.show()
app.processEvents()
out = Path(__file__).resolve().parent / 'verification' / 'chat.png'
out.parent.mkdir(parents=True, exist_ok=True)
dialog.grab().save(str(out), 'PNG')
print(out, dialog.size().width(), dialog.size().height())
