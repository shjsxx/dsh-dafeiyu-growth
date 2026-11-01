"""Render cloud settings and confirmation cards for visual review."""
import os
import sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'))
from PySide6.QtWidgets import QApplication
from cloud_ui import CloudSettingsDialog, CloudConfirmDialog

app = QApplication(sys.argv[:1])
out = Path(__file__).resolve().parent / 'verification'
out.mkdir(parents=True, exist_ok=True)
settings = CloudSettingsDialog({
    'endpoint': 'https://api.example.com/v1/chat/completions',
    'model': 'deepseek-chat', 'max_tokens': 256, 'key_saved': True,
    'error': None, 'path': str(out / 'cloud.json'),
})
settings.show()
app.processEvents()
settings.grab().save(str(out / 'cloud-settings.png'))
confirm = CloudConfirmDialog('https://api.example.com/v1/chat/completions', 'deepseek-chat', 256,
                             '今天有点累，陪我待一会儿。')
confirm.show()
app.processEvents()
confirm.grab().save(str(out / 'cloud-confirm.png'))
print(settings.size().toTuple(), confirm.size().toTuple())
