"""Read-only render of all real-size growth-card pages (no screen-size override)."""
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
# Default to a synthetic profile and isolated state so rendering never reads
# the owner's real diary/session history. Set DSH_DAFEIYU_RENDER_REAL=1 only
# for a deliberate, local-only screenshot of real data.
if os.environ.get('DSH_DAFEIYU_RENDER_REAL') != '1':
    render_dir = ROOT / 'verification' / 'render'
    render_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('DSH_DAFEIYU_GROWTH_FILE', str(ROOT / 'fixtures' / 'smoke-profile.json'))
    for variable, name in (
        ('DSH_DAFEIYU_BEHAVIOR_FILE', 'behavior.json'), ('DSH_DAFEIYU_NEURAL_FILE', 'neural.json'),
        ('DSH_DAFEIYU_EMOTION_FILE', 'emotion.json'), ('DSH_DAFEIYU_CLOUD_FILE', 'cloud.json'),
        ('DSH_DAFEIYU_LEARNING_FILE', 'learning.json'), ('DSH_DAFEIYU_ONLINE_FILE', 'online.json'),
        ('DSH_DAFEIYU_TRAINING_FILE', 'training.json'), ('DSH_DAFEIYU_KNOWLEDGE_FILE', 'knowledge.json'),
        ('DSH_DAFEIYU_COMPANION_FILE', 'companion.json'),
    ):
        os.environ.setdefault(variable, str(render_dir / name))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'))
from PySide6.QtWidgets import QApplication
from growth_native import GrowthDialog, GrowthReader
from behavior import BehaviorLearner
from neural_brain import NeuralBrain
from cloud_chat import load_config
from emotion_model import EmotionModel
from learning_state import PublicLearningState
from live_online import LiveOnlineState
from training_journal import TrainingJournal
from knowledge_store import KnowledgeStore
from companion_state import CompanionState
from knowledge_sources import platform_catalog_snapshot
from learning_hub import LearningHub
app = QApplication(sys.argv[:1])
dialog = GrowthDialog()
dialog.update_state(GrowthReader().refresh())
dialog.update_behavior(BehaviorLearner().snapshot())
brain = NeuralBrain()
dialog.update_neural(brain.info())
dialog.update_cloud(load_config())
emotion = EmotionModel()
dialog.update_emotion(emotion.snapshot())
dialog.update_learning(PublicLearningState().snapshot())
dialog.update_online({'live': LiveOnlineState().snapshot(), 'curriculum': PublicLearningState().snapshot()})
dialog.update_companion(CompanionState(read_only=True).snapshot())
journal = TrainingJournal(read_only=True).snapshot()
knowledge = KnowledgeStore(read_only=True).snapshot()
dialog.update_training({'journal': journal, 'knowledge': knowledge, 'brain': brain.info(),
                        'sourceCatalog': LearningHub.source_catalog(knowledge, journal, emotion.snapshot()),
                        'platformSources': platform_catalog_snapshot()})
brain.close()
dialog.show()
app.processEvents()
out = Path(__file__).resolve().parent / 'verification'
out.mkdir(parents=True, exist_ok=True)
locations = {}
def position(widget):
    point = widget.mapTo(dialog, widget.rect().center())
    return [point.x(), point.y()]

for index, name in enumerate(('overview', 'memory', 'diary', 'permissions', 'emotion', 'training', 'data')):
    dialog.tabs.setCurrentIndex(index)
    app.processEvents()
    bar = dialog.tabs.tabBar()
    mode = 'compact' if index >= 4 else 'full'
    locations[mode] = [[bar.mapTo(dialog, bar.tabRect(i).center()).x(), bar.mapTo(dialog, bar.tabRect(i).center()).y()] for i in range(7)]
    dialog.grab().save(str(out / f'dialog-{name}.png'))
    print(name, dialog.width(), dialog.height())
    if index == 4:
        locations['emotion-settings'] = position(dialog.emotion_sections.button(1))
        dialog.emotion_sections.button(1).click()
        app.processEvents()
        dialog.grab().save(str(out / 'dialog-emotion-settings.png'))
        dialog.emotion_sections.button(0).click()
    if index == 5:
        for label, suffix in (('记录', 'records'), ('来源', 'sources')):
            button = dialog.training_panel.view_buttons[label]
            locations['training-' + suffix] = position(button)
            button.click()
            app.processEvents()
            dialog.grab().save(str(out / f'dialog-training-{suffix}.png'))
        dialog.training_panel.view_buttons['总览'].click()
    if index == 6:
        button = dialog.data_panel.view_buttons.button(1)
        locations['data-emotion'] = position(button)
        button.click()
        app.processEvents()
        dialog.grab().save(str(out / 'dialog-data-emotion.png'))
(out / 'probe-layout.json').write_text(json.dumps(locations), encoding='utf-8')
dialog.close()
