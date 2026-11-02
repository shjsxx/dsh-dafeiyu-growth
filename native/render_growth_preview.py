"""Synthetic-only, offline screenshots of the passive native growth journal.

Run: python native/render_growth_preview.py [--out native/verification/redesign]
No owner config, profile, store, learner, microphone, provider or test is loaded.
All numbers describe explicitly invented fixtures, never real usage or quality.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path

# Must precede the Qt imports. Rendering never creates a runtime/engine.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import QPoint, QCoreApplication
from growth_native import GrowthDialog, derive

import time as _time
FIXED_TIME = int(_time.time()) - 3 * 86400  # Synthetic recent audit time; not owner data.


def synthetic_fixture(state='populated'):
    """Return small legitimate snapshot shapes; no persistence or model work."""
    if state == 'empty':
        return {'profile': {}, 'training': {}, 'emotion': {}, 'companion': {},
                'behavior': {}, 'learning': {}, 'online': {}, 'neural': {}}
    events = [
        {'id': 6, 'kind': 'voice_io', 'domain': 'general', 'source': 'realtime_voice',
         'status': 'observed', 'labels': {'voice_event': 'rt_usage'},
         'metrics': {'input_tokens': 640, 'output_tokens': 240, 'trained': 0}},
        {'id': 5, 'kind': 'voice_io', 'domain': 'general', 'source': 'local_voice',
         'status': 'observed', 'labels': {'voice_event': 'recognized'},
         'metrics': {'characters': 8, 'trained': 0}},
        {'id': 4, 'kind': 'computer_control', 'domain': 'action', 'source': 'typed_direct',
         'status': 'skipped', 'labels': {'decision': 'blocked'}, 'metrics': {'trained': 0}},
        {'id': 3, 'kind': 'knowledge_index', 'domain': 'geography', 'source': 'countries',
         'status': 'success', 'labels': {}, 'metrics': {'added': 12, 'validated': 12, 'trained': 0}},
        {'id': 2, 'kind': 'emotion_fit', 'domain': 'emotion', 'source': 'local_chat',
         'status': 'success', 'labels': {'emotion': 'calm'},
         'metrics': {'trained': 1, 'adaptation_passes': 1, 'samples_before': 2, 'samples_after': 3}},
        {'id': 1, 'kind': 'action_fit', 'domain': 'action', 'source': 'local_feedback',
         'status': 'success', 'labels': {'action': 'hop'},
         'metrics': {'trained': 1, 'weight_steps': 1, 'loss_before': .9, 'loss_after': .8}},
    ]
    for index, row in enumerate(events):
        row.update(started=FIXED_TIME - index * 60, finished=FIXED_TIME - index * 60 + 1)
    totals = {'runs': 6, 'failed': 0, 'weight_steps': 1, 'emotion_adaptation_passes': 1,
              'knowledge_added': 12, 'voice_sessions': 1, 'voice_segments': 1,
              'voice_spoken': 0, 'voice_interrupted': 0, 'voice_rejected': 0,
              'computer_proposals': 1, 'computer_executed': 0, 'computer_blocked': 1,
              'realtime_sessions': 1, 'realtime_segments': 0, 'realtime_interrupted': 0,
              'realtime_errors': 0, 'realtime_duration_ms': 18000,
              'realtime_played_ms': 4000, 'realtime_audio_input_bytes': 32768,
              'realtime_audio_output_bytes': 16384,
              'realtime_input_tokens': 640, 'realtime_output_tokens': 240}
    brain = {'status': 'ready', 'samples': 1, 'updates': 1, 'version': 1}
    fixture = {
        'profile': {'xp': 186, 'activeMs': 2400000,
                    'counters': {'turns': 18, 'toolCalls': 9, 'toolErrors': 1, 'recoveries': 1, 'approvals': 2},
                    'days': {'2026-10-31': 4, '2026-10-30': 3},
                    'tools': {'read': 5, 'edit': 4},
                    'diary': [{'id': 'synthetic-stage', 'kind': 'stage', 'at': '2026-10-31T10:00:00',
                               'text': '合成预览：一次小小的成长'}]},
        'behavior': {'agility': 24, 'balance': 31, 'pets': 3, 'chats': 2},
        'neural': brain,
        'emotion': {'name': '平静', 'confidence': .64, 'status': 'ready', 'revision': 2,
                    'fit_samples': 3, 'adaptation_passes': 1, 'lexicon': 24,
                    'features': 18, 'events': 4, 'learned': 3},
        'companion': {'mood': 'relaxed', 'valence': .35, 'energy': .72, 'trust': .48, 'curiosity': .57},
        'learning': {'enabled': True, 'knowledgeEnabled': True, 'lastSuccess': FIXED_TIME,
                     'lastAdded': 12, 'nextCheck': FIXED_TIME + 3600},
        'online': {'live': {'enabled': True, 'working': True, 'lastSuccess': FIXED_TIME,
                            'sourceUpdatedAtMs': FIXED_TIME * 1000, 'nextCheck': FIXED_TIME + 300},
                   'comparison': {'modelLabel': 'calm', 'publicLabel': 'neutral'}},
        'training': {'brain': brain,
                     'knowledge': {'status': 'ready', 'count': 12, 'revision': 1,
                                   'domains': {'geography': 12}, 'sources': {'countries': 12}},
                     'sourceCatalog': [{'id': 'countries', 'status': 'indexed'},
                                       {'id': 'cldr_zh', 'status': 'available'},
                                       {'id': 'core_curriculum', 'status': 'available'}],
                     'journal': {'status': 'ready', 'revision': 1, 'events': events, 'totals': totals}},
    }
    if state == 'error':
        fixture['training']['journal'].update(status='protected', revision=2)
        fixture['neural'] = {'status': 'protected', 'samples': 1, 'updates': 1, 'version': 1}
        fixture['training']['brain'] = fixture['neural']
        fixture['learning'].update(protected=True)
        fixture['online']['live'].update(working=False, errorCode='network')
        fixture['emotion']['status'] = 'protected'
    return fixture


def apply_fixture(dialog, fixture):
    state = derive(fixture['profile'], datetime(2026, 10, 31, 12))
    state['engine'] = {'version': 1, 'revision': 3, 'status': 'ready', 'milestones': 5}
    dialog.update_state(state)
    for name in ('behavior', 'neural', 'emotion', 'companion', 'learning', 'training', 'online'):
        getattr(dialog, 'update_' + name)(fixture[name])
    dialog.update_memory({'status': 'ready', 'revision': 4,
                          'counts': {'preference': 1, 'habit': 1, 'diary': 1, 'public': 2, 'culture': 1},
                          'recent': [{'kindLabel': '诗文', 'text': '关雎：关关雎鸠，在河之洲。'},
                                     {'kindLabel': '偏好', 'text': '喜欢：夜里散步'},
                                     {'kindLabel': '公开知识', 'text': '法国：首都是巴黎'}]})
    dialog.update_personality({'status': 'ready', 'simulated': True, 'toneLabel': '温暖',
                               'initiative': .58, 'traits': {'warmth': 62, 'curiosity': 58,
                               'playfulness': 41, 'steadiness': 60, 'expressiveness': 55}})


def render(out, width=456, height=640):
    app = QApplication.instance() or QApplication([])
    # Offscreen Qt on Windows may not discover CJK fallback fonts. Only fixed
    # OS font assets are read; never inspect an owner profile or config.
    font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        font_path = font_dir / name
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'synthetic_fixture': True, 'owner_data': False,
                'description': 'Invented passive UI snapshots, not actual training or usage.', 'images': []}
    sizes = [(width, height), (420, 480)]
    if sizes[0] == sizes[1]:
        sizes.pop()
    for state in ('empty', 'populated', 'error'):
        dialog = GrowthDialog()
        apply_fixture(dialog, synthetic_fixture(state))
        dialog.show()
        for w, h in sizes:
            dialog.resize(w, h)
            views = [(i, name, None) for i, name in enumerate(
                ('overview', 'skills', 'review', 'permissions', 'emotion-current', 'training-overview', 'data-voice'))]
            views += [(4, 'emotion-settings', 1), (5, 'training-records', 1),
                      (5, 'training-sources', 2), (6, 'data-knowledge', 0), (6, 'data-emotion', 1)]
            for index, name, section in views:
                dialog.tabs.setCurrentIndex(index)
                dialog.emotion_stack.setCurrentIndex(section if index == 4 and section is not None else 0)
                dialog.training_panel.view_stack.setCurrentIndex(section if index == 5 and section is not None else 0)
                if index == 4:
                    dialog.emotion_sections.button(dialog.emotion_stack.currentIndex()).setChecked(True)
                if index == 5:
                    dialog.training_panel.view_group.button(dialog.training_panel.view_stack.currentIndex()).setChecked(True)
                if index == 6 and section is not None:
                    dialog.data_panel.views.setCurrentIndex(section)
                    dialog.data_panel.view_buttons.button(section).setChecked(True)
                app.processEvents()
                scroll = dialog.page_scrolls[index]
                scroll.verticalScrollBar().setValue(0)
                if index == 6 and section is None:
                    scroll.ensureWidgetVisible(dialog.voice_data_panel, 0, 0)
                # Tab/stack switches post multiple LayoutRequest events. Flush
                # them deterministically, without timers, sleeps or runtime work.
                for _ in range(3):
                    QCoreApplication.sendPostedEvents()
                    app.processEvents()
                    dialog.layout().activate()
                filename = f'synthetic-{state}-{name}-{w}x{h}.png'
                origin = dialog.close_button.mapTo(dialog, QPoint(0, 0))
                footer = {'x': origin.x(), 'y': origin.y(),
                          'width': dialog.close_button.width(), 'height': dialog.close_button.height(),
                          'dialog_width': dialog.width(), 'dialog_height': dialog.height()}
                footer['inside_margin'] = (footer['x'] >= 8 and footer['y'] >= 8
                    and footer['x'] + footer['width'] <= dialog.width() - 8
                    and footer['y'] + footer['height'] <= dialog.height() - 8)
                if not footer['inside_margin']:
                    raise RuntimeError(f'Footer outside capture for {filename}: {footer}')
                pixmap = dialog.grab()
                if not pixmap.save(str(out / filename)):
                    raise RuntimeError(f'Unable to save {filename}')
                manifest['images'].append({'file': filename, 'state': state, 'view': name,
                                           'width': pixmap.width(), 'height': pixmap.height(),
                                           'synthetic_fixture': True, 'footer_geometry': footer})
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
    (out / 'synthetic-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'synthetic_fixture': True, 'owner_data': False, 'image_count': len(manifest['images']),
                      'out': str(out.resolve())}))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path(__file__).parent / 'verification' / 'redesign')
    parser.add_argument('--width', type=int, default=456)
    parser.add_argument('--height', type=int, default=640)
    args = parser.parse_args()
    if not 420 <= args.width <= 1600 or not 480 <= args.height <= 1600:
        parser.error('size must be bounded: width 420..1600 and height 480..1600')
    render(args.out, args.width, args.height)


if __name__ == '__main__':
    main()
