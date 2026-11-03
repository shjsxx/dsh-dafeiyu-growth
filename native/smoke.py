"""Opt-in Qt integration test; never runs during normal helper launch."""
import json
from pathlib import Path


def install_smoke_probe(application, window, directory):
    from PySide6.QtCore import QPoint, QTimer, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu
    from growth_native import summary

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    result = {}

    def finish_idle():
        """Let the temporary IDLE greeting expire so only the pill remains."""
        window.overlay_message = ""
        window.overlay_deadline_ms = None
        window.status_deadline_ms = window._now_ms() - 1
        window._sync_idle_layout()

    def click_bubble():
        x, y, width, height = window._bubble_rect()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(x + width // 2, y + height // 2))

    def exercise():
        try:
            # A packaged PYZ module has __file__ but not its source bytes unless
            # build.ps1 explicitly includes them. Test real compatibility IO.
            import embedding_backend
            import semantic_memory
            from embedding_benchmark import Reranker
            assert Path(embedding_backend.__file__).is_file()
            assert Path(semantic_memory.__file__).is_file()
            rerank_path = out / 'smoke-rerank-v2.json'
            empty_assets = out / 'empty-embedding-model'
            Reranker().save(rerank_path, model_dir=empty_assets)
            loaded = Reranker.load(rerank_path, model_dir=empty_assets)
            assert loaded is not None and loaded.valid
            result['rerankerV2Compatibility'] = loaded.compatibility

            window.apply_message({"protocolVersion": 1, "kind": "state", "state": "IDLE",
                                  "message": "云岚陪你待命", "detail": "DSH · 等待下一次任务"})
            window.apply_message({"protocolVersion": 1, "kind": "balance", "summary": "余额未配置"})
            window._refresh_growth()
            assert window.growth_state, 'growth data not loaded'
            result['summary'] = summary(window.growth_state)

            # 1. Idle collapses to a one-line pill by default.
            finish_idle()
            assert window._compact_mode(), 'idle bubble should start collapsed'
            result['compactHeight'] = window._card_height()
            result['compactWidth'] = window._card_width()
            assert result['compactHeight'] < 60 and result['compactWidth'] < 300
            assert window.grab().save(str(out / 'compact.png'))

            # 2. Clicking the pill expands the idle card; clicking again collapses.
            click_bubble()
            assert not window._compact_mode(), 'click should expand the idle card'
            result['expandedHeight'] = window._card_height()
            assert result['expandedHeight'] > result['compactHeight']
            assert window._can_talk(), 'expanded idle bubble must not silence proactive replies'
            assert window.grab().save(str(out / 'expanded.png'))
            click_bubble()
            assert window._compact_mode(), 'second click should collapse again'

            # 3. A live dialogue always wins over the collapsed preference.
            window.apply_message({"protocolVersion": 1, "kind": "state", "state": "WORKING",
                                  "message": "云岚正在干活", "detail": "default-workspace · 运行测试"})
            assert not window._compact_mode(), 'dialogue must force the full card'
            assert window._card_height() > result['compactHeight']
            assert window.grab().save(str(out / 'working.png'))
            click_bubble()
            assert not window._compact_mode(), 'dialogue must ignore the collapse click'
            result['dialogueClickIgnored'] = True
            # A working task no longer suppresses replyable proactive text, but
            # its progress bubble must not be replaced by companion chatter.
            status_before = window.status_message
            assert window._can_talk()
            window._maybe_talk(force=True)
            assert window.conversation is not None and window.conversation.isVisible()
            assert window.status_message == status_before
            window.conversation.close()
            application.processEvents()
            result['workDoesNotBlockChat'] = True

            # 4. After the dialogue ends it returns to the pill.
            window.apply_message({"protocolVersion": 1, "kind": "state", "state": "IDLE",
                                  "message": "云岚陪你待命", "detail": "DSH · 等待下一次任务"})
            finish_idle()
            assert window._compact_mode()
            result['collapsedAgain'] = True

            # 5. Self-directed practice: the local network chooses the action.
            window.pointer_over = False  # offscreen has no meaningful cursor position
            window.next_motion_ms = window._now_ms() - 1
            for _ in range(16):
                window._motion_tick()
                if window.motion is not None or window.overlay_message:
                    break
            result['autoStarted'] = window.motion is not None or bool(window.overlay_message)
            assert result['autoStarted'], f'idle scheduler should start an action: {result}'
            window._stop_motion()
            finish_idle()
            QTest.qWait(40)
            # 5b. It can start a conversation by itself.
            window.motion_enabled = True
            window.next_talk_ms = window._now_ms() - 1
            window._maybe_talk()
            result['talked'] = bool(window.overlay_message)
            assert result['talked'], 'companion should talk when idle'
            result['greetings'] = window.learner.data["greetings"]
            assert window.conversation is not None and window.conversation.isVisible()
            assert window.conversation.input.isEnabled()
            assert window.conversation.grab().save(str(out / 'proactive-conversation.png'))
            window.conversation.input.setText('早安')
            QTest.keyClick(window.conversation.input, Qt.Key.Key_Return)
            application.processEvents()
            assert window.conversation.input.text() == ''
            assert window.emotion_comparison.get('localOnly') is True
            result['proactiveReplyable'] = True
            window.conversation.close()
            application.processEvents()
            finish_idle()
            window.motion_enabled = False

            # 5c. Running posture: facing the direction and bouncing in arcs.
            start_x = window.pet_x
            base_y = window.pet_y
            assert window._start_run(distance=220, direction=1, force_fall=False)
            QTest.qWait(160)
            if window.motion and window.motion.get("kind") == "run":
                result['facing'] = window.pet_facing
                result['tilt'] = round(window.pet_tilt, 2)
                result['bounced'] = window.pet_y < base_y
                assert window.pet_facing == window.motion["direction"], 'run should face its direction'
                assert abs(window.pet_tilt) <= 3.5, 'run tilt must stay subtle'
            result['runMoved'] = window.pet_x != start_x
            assert result['runMoved'], 'run should move the pet'
            assert window.grab().save(str(out / 'running.png'))
            window._stop_motion()
            QTest.qWait(40)
            samples_before = window.brain.sample_count
            assert window._start_run(distance=260, force_fall=False)
            QTest.qWait(1400)
            result['neuralSamples'] = window.brain.sample_count
            assert result['neuralSamples'] > samples_before, 'run outcome must train the network'
            last = window.last_neural
            if last:
                window._neural_feedback(1.0)
                assert window.brain.sample_count > result['neuralSamples']
                result['explicitFeedback'] = True
            window._stop_motion()
            finish_idle()

            base_y = window.pet_y
            assert window._start_hop(height=44)
            QTest.qWait(240)
            result['hopRaised'] = window.pet_y < base_y
            assert result['hopRaised'], 'hop should raise the window'
            assert window.grab().save(str(out / 'jump.png'))
            window._stop_motion()
            QTest.qWait(40)
            assert window.pet_y == base_y

            # 5d. 360° orbit: full silhouette, never a flattened paper strip.
            spin_start = (window.pet_x, window.pet_y)
            assert window._start_spin(1)
            QTest.qWait(180)
            result['spinScale'] = round(window.pet_spin_scale, 3)
            result['spinMotion'] = bool(window.motion and window.motion.get("kind") == "spin")
            result['spinOffset'] = abs(window.pet_x - spin_start[0]) + abs(window.pet_y - spin_start[1])
            assert result['spinMotion'], 'spin should be the active motion'
            assert result['spinScale'] >= 0.99, 'spin must keep the full silhouette'
            assert result['spinOffset'] > 6, 'spin should orbit around the start point'
            assert window.grab().save(str(out / 'spin.png'))
            QTest.qWait(1500)
            result['spinRestored'] = (
                abs(window.pet_spin_scale - 1.0) < 0.01
                and window.motion is None
                and abs(window.pet_x - spin_start[0]) <= 1
                and abs(window.pet_y - spin_start[1]) <= 1
            )
            assert result['spinRestored'], 'spin should return to the start point'

            # 5d-2. Emotion reactions draw procedural effects without new assets.
            for reaction in ('cry', 'bored', 'sleepy', 'funny', 'sad'):
                window._stop_motion()
                assert window._trigger_reaction(reaction), reaction
                QTest.qWait(500)
                assert window.reaction and window.reaction['key'] == reaction
                assert window.grab().save(str(out / f'reaction-{reaction}.png'))
                window.reaction = None
                QTest.qWait(30)
            result['reactions'] = ['cry', 'bored', 'sleepy', 'funny', 'sad']

            # 5e. Local emotion reader learns explicit self-report without saving raw text.
            reading = window.learning.observe_emotion("我今天有点难过")
            window.learning.adapt("chat", reading.get("mood", "neutral"), "local_chat")
            result['emotion'] = reading.get('mood')
            assert result['emotion'] == 'sad'
            result['emotionLearned'] = window.emotion.snapshot()['learned'] > 0
            window._spawn_hearts(3)
            result['hearts'] = len(window.hearts)
            assert result['hearts'] >= 3

            # 5f. Public updates are scheduled, silent and discardable on switch-off.
            assert not window.public_learning.enabled, 'isolated smoke must never grant itself network access'
            assert window.learning_timer.isActive()
            assert window.public_learning.set_enabled(True)
            original_worker = window._learning_worker
            window._learning_worker = lambda automatic: window.learning_result.emit({
                "ok": True, "automatic": automatic,
                "mapping": {"sad": ["惆怅"]}, "host": "local-test",
                "sources": ["local fixture only"]})
            window._auto_learning_tick()
            QTest.qWait(120)
            window._learning_worker = original_worker
            result['autoLearningApplied'] = window.public_learning.last_success > 0 and not window.learning_busy
            assert result['autoLearningApplied']
            before_public = window.emotion.snapshot()['lexicon']
            assert window.public_learning.set_enabled(False)
            window._on_learning_result({"ok": True, "automatic": True,
                "mapping": {"joy": ["欢欣雀跃"]}, "host": "local-test"})
            result['autoLearningDisabledResultIgnored'] = window.emotion.snapshot()['lexicon'] == before_public
            assert result['autoLearningDisabledResultIgnored']

            falls_before = window.learner.data["falls"]
            window._begin_fall({"direction": 1, "y": window.pet_y})
            assert window.pet_scale_y < 0.7, 'falling should squash the pet'
            assert window.grab().save(str(out / 'fall.png'))
            QTest.qWait(2200)
            result['fallLearned'] = window.learner.data["falls"] == falls_before + 1
            result['gotUp'] = abs(window.pet_tilt) < 5 and window.pet_scale_y > 0.95
            assert result['fallLearned'] and result['gotUp'], result
            skills = window.learner.snapshot()
            result['skills'] = {key: skills[key] for key in ("agility", "balance", "runs", "falls")}
            assert window.learner.data["balance"] > 0

            # 6. Right-click menu still opens the growth card on demand.
            window.learner.note_pet("head")
            window.learner.note_chat()
            result['actionSelected'] = False

            def select_growth():
                menu = QApplication.activePopupWidget()
                if not isinstance(menu, QMenu):
                    result['menuError'] = 'no active native QMenu'
                    return
                actions = [a for a in menu.actions() if not a.isSeparator()]
                result['menuActions'] = [a.text() for a in actions]
                action = next((a for a in actions if a.objectName() == 'dafeiyu-growth-card'), None)
                assert menu.grab().save(str(out / 'menu.png'))
                if action:
                    menu.setActiveAction(action)
                    result['actionSelected'] = True
                    QTest.keyClick(menu, Qt.Key.Key_Return)
                else:
                    menu.close()

            point = QPoint(window.width() // 2, window.height() - 30)
            QTimer.singleShot(150, select_growth)
            QTimer.singleShot(3000, lambda: QApplication.activePopupWidget().close()
                              if isinstance(QApplication.activePopupWidget(), QMenu) else None)
            window.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, window.mapToGlobal(point)))
            application.processEvents()
            result['dialogVisible'] = bool(window.growth_dialog and window.growth_dialog.isVisible())
            assert result['actionSelected'] and result['dialogVisible'], result
            assert '和云岚聊天…' in result['menuActions'], result['menuActions']
            assert '转一圈' in result['menuActions'], result['menuActions']
            assert '桌面漫游' in result['menuActions'], result['menuActions']
            assert '主动搭话' in result['menuActions'], result['menuActions']
            result['tabs'] = window.growth_dialog.tabs.count()
            assert result['tabs'] == 7
            window.growth_dialog.tabs.setCurrentIndex(5)
            application.processEvents()
            result['trainingEvents'] = window.learning.journal.snapshot()['retained']
            result['knowledgeFacts'] = window.learning.knowledge.snapshot()['count']
            assert result['trainingEvents'] >= 4 and result['knowledgeFacts'] >= 30
            assert window.growth_dialog.training_panel.history_list.count() > 0
            result['companionMood'] = window.learning.companion.snapshot()['mood']
            assert result['companionMood'] == 'comforting'
            assert window.growth_dialog.grab().save(str(out / 'training-card.png'))
            window.growth_dialog.training_panel.view_buttons['记录'].click()
            application.processEvents()
            assert window.growth_dialog.training_panel.history_list.isVisible()
            assert window.growth_dialog.grab().save(str(out / 'training-records.png'))
            window.growth_dialog.training_panel.view_buttons['来源'].click()
            application.processEvents()
            assert '未连接' in window.growth_dialog.training_panel.platform_label.text()
            assert window.growth_dialog.grab().save(str(out / 'training-sources.png'))
            window.growth_dialog.tabs.setCurrentIndex(6)
            application.processEvents()
            assert window.growth_dialog.data_panel.isVisible()
            assert window.growth_dialog.grab().save(str(out / 'data-card.png'))
            window.growth_dialog.tabs.setCurrentIndex(4)
            application.processEvents()
            assert window.growth_dialog.companion_orb.isVisible()
            assert window.growth_dialog.grab().save(str(out / 'emotion-card.png'))
            window.growth_dialog.tabs.setCurrentIndex(1)
            application.processEvents()
            assert '10 → 12 → 4' in window.growth_dialog.neural_label.text()
            result['neural'] = window.growth_dialog.neural_label.text()
            assert '现在' in window.growth_dialog.emotion_label.text()
            result['emotionCard'] = window.growth_dialog.emotion_label.text()
            window.growth_dialog.tabs.setCurrentIndex(3)
            application.processEvents()
            assert '云端接口' in window.growth_dialog.cloud_status.text()
            window.growth_dialog.tabs.setCurrentIndex(1)
            application.processEvents()
            assert window.growth_dialog.bond_label.isVisible()
            result['bond'] = window.growth_dialog.bond_label.text()
            assert window.growth_dialog.grab().save(str(out / 'growth-card.png'))
            result['skillLines'] = window.growth_dialog.skills_label.text()
            assert window.growth_dialog.skill_bars['balance'].value() > 0
            version_before = window.brain.model_version
            window.growth_dialog.neural_rollback_requested.emit()
            application.processEvents()
            result['neuralRollback'] = window.brain.model_version <= version_before
            assert result['neuralRollback']
            balance_before = window.learner.data["balance"]
            window.growth_dialog.forget_requested.emit()
            application.processEvents()
            assert window.learner.data["pets"] == 0 and window.learner.data["chats"] == 0
            assert window.learner.data["balance"] == balance_before
            result['forgotMemory'] = True
            window.memory.ingest_state({'diary': [{'text': '今天完成了一次本地回顾'}]})
            memory_state = window.memory.snapshot()
            result['memoryEmbedding'] = memory_state.get('embedding')
            if result['memoryEmbedding'] == 'ready':
                neural_hits = window.memory.recall('写了一次总结', 3)
                result['memoryNeuralHits'] = len(neural_hits)
                assert neural_hits, 'local ONNX embedding should rerank a paraphrase'
            assert window.growth_dialog.grab().save(str(out / 'growth-card-memory.png'))
            window.growth_dialog.close()
            window._refresh_growth()
            assert not window.growth_dialog.isVisible()

            # Audited dynamic fixture runs entirely on the isolated profile;
            # a deterministic worker stands in for HTTP (never uses owner keys).
            import time
            from live_online import _record
            moment = time.time()
            counters = {'count': 0, 'maxMagnitude': None, 'latestEventTimeMs': None, 'reviewedCount': 0, 'rejected': 0}
            fake = {'ok': True, 'records': [_record(counters, moment * 1000)], 'counters': counters,
                    'sourceUpdatedAtMs': moment * 1000, 'metrics': {'bytes': 254, 'validated': 0, 'rejected': 0, 'duration_ms': 1}}
            original_worker = window._online_worker
            window._online_worker = lambda token, manual: window.online_result.emit({'token': token, 'manual': manual, 'result': fake})
            assert window.online_state.set_enabled(True, owner_action=True)
            assert window._start_online(manual=True)
            QTest.qWait(80)
            application.processEvents()
            assert not window.online_worker_busy
            assert window.online_state.snapshot()['lastSuccess'] > 0
            assert window.learning.knowledge.has_source('usgs_live')
            event = next(row for row in window.learning.journal.snapshot()['events'] if row['source'] == 'usgs_live')
            assert event['status'] == 'success' and event['metrics']['trained'] == 0
            assert not window._start_online(manual=True), 'manual clicks must respect persisted 60s floor'
            window._online_worker = original_worker
            window.online_state.set_enabled(False, owner_action=True)
            result['auditedLiveFixture'] = True
            window._ensure_conversation()
            window.conversation.show()
            window.memory.ingest_state({'diary': [{'text': '公园有一座白桥'}]})
            window._conversation_local('我昨天去了公园')
            window._conversation_local('你还记得公园吗')
            assert '白桥' in window.last_reply_text
            assert window.conversation._rows[-1]['private_context'] is True
            window._reset_conversation_context()
            assert not window.conversation._rows
            assert not window.last_reply_text and not window._latest_retrieval
            assert not window.conversation.memory_context_enabled()
            result['conversationRuntimeAndPrivacyReset'] = True
            window.conversation.set_cloud_state({'endpoint': 'https://changed.invalid/v1/chat/completions', 'model': 'changed', 'max_tokens': 16})
            window._conversation_cloud('do not send this to a changed target')
            assert not window.cloud_busy
            old_overlay = window.overlay_message
            window._on_cloud_reply({'ok': True, 'text': 'must not appear', 'requestSession': window.conversation_serial - 1})
            assert window.overlay_message == old_overlay
            window.conversation.close()
            application.processEvents()
            result['cloudTargetAndLateReplyGuard'] = True

            # Voice readiness is enumeration only: never open a microphone or
            # physical speaker. The operation executor is replaced before any
            # UI authorization; these actions affect ONLY this isolated journal.
            from computer_control import ComputerController
            from voice_probe import run_synthetic_probe
            import os
            window._open_voice_tools()
            assert not window.voice_active and not window.computer.snapshot()['enabled']
            caps = window.voice_caps
            result['voiceReadiness'] = {key: caps.get(key) for key in ('chinese_available', 'tts_available', 'recognizer_count', 'voice_count', 'audio_input_count', 'full_duplex')}
            application.processEvents()  # let wrapped layout settle before capture
            assert window.voice_tools.grab().save(str(out / 'voice-tools.png'))
            from realtime_audio import RealtimeAudio
            from realtime_probe import run_loopback_probe
            from realtime_config import load_config, save_config, public_config, consent_identity, RealtimeConfigError
            assert not load_config().get('key')  # isolated, never owner credentials
            # Real current-user DPAPI roundtrip in this isolated profile. The
            # fixture key is not a provider credential and never leaves RAM/disk
            # ciphertext; changing the endpoint must NOT forward it silently.
            fixture_key = 'test-key-fixture-not-a-provider-credential-0123456789'
            saved = save_config('wss://api.openai.com/v1/realtime', 'gpt-realtime', 'marin', 1024, 600, 'ga', key=fixture_key)
            assert saved['key'] == fixture_key and saved['key_saved'] is True
            assert fixture_key not in (out / 'realtime.json').read_text(encoding='utf-8')
            assert load_config()['key'] == fixture_key
            assert public_config(load_config()).get('key') is None
            try:
                save_config('wss://example.invalid/v1/realtime', 'gpt-realtime', 'marin', 1024, 600, 'ga')
            except RealtimeConfigError as error:
                assert error.code == 'target', error.code
            else:
                raise AssertionError('endpoint change must not reuse the old key')
            identity = consent_identity(load_config())
            assert len(identity) == 64 and consent_identity(load_config()) == identity
            save_config('wss://api.openai.com/v1/realtime', 'gpt-realtime', 'marin', 1024, 600, 'ga', clear_key=True)
            assert not load_config().get('key')
            result['realtimeConfigDPAPI'] = True
            result['realtimeAudioFormats'] = RealtimeAudio.capabilities()
            QTest.mouseClick(window.voice_tools.realtime_settings_button, Qt.MouseButton.LeftButton)
            application.processEvents()
            settings = window.realtime_settings
            assert settings and not window.realtime and not window.realtime_active and not window.voice_active
            assert settings.model.text() == 'gpt-realtime' and settings.key.text() == ''
            assert settings.grab().save(str(out / 'realtime-api-settings.png'))
            settings.scroll.verticalScrollBar().setValue(settings.scroll.verticalScrollBar().maximum())
            application.processEvents()
            assert settings.grab().save(str(out / 'realtime-api-key.png'))
            settings.reject()
            application.processEvents()
            result['realtimeSettingsPassive'] = True
            result['hermeticRealtime'] = run_loopback_probe()
            assert result['hermeticRealtime']['ok'], result['hermeticRealtime']
            dispatched = []
            window.computer = ComputerController(executor=lambda action: dispatched.append(action) or True)
            window._register_safety_hotkey = lambda: True  # no real global shortcut
            window._computer_permission_changed(True)
            window._conversation_local('云岚，打开记事本')
            assert not dispatched and window.operation_row
            assert window.voice_tools.grab().save(str(out / 'voice-tools-confirm.png'))
            QTest.mouseClick(window.voice_tools.confirm_button, Qt.MouseButton.LeftButton)
            assert len(dispatched) == 1 and not window.operation_row
            assert window.learning.journal.snapshot()['totals']['computer_executed'] == 1
            # Semantic adaptation of high-score voice is separate from SAPI:
            # no acoustic/synthesis fitting occurs and private words are absent
            # from the audit. Default off must not update semantic weights.
            before = window.emotion.fit_samples
            window.direct_input_source = 'voice_direct'
            window.voice_confidence = .95
            window.voice_learning = False
            window._chat_reading('我现在很开心')
            assert window.emotion.fit_samples == before
            window.voice_learning = True
            window._chat_reading('我现在很开心')
            assert window.emotion.fit_samples > before
            window.direct_input_source = 'typed_direct'
            result['voiceLearningOptIn'] = True
            window.voice_tools.close()
            assert not window.computer.snapshot()['enabled']
            window._refresh_learning_ui()
            window._show_growth_card()
            window.growth_dialog.tabs.setCurrentIndex(6)
            application.processEvents()
            assert window.growth_dialog.grab().save(str(out / 'voice-data-card.png'))
            window.growth_dialog.close()
            if os.environ.get('DSH_DAFEIYU_VOICE_PROBE') == '1':
                result['syntheticVoice'] = run_synthetic_probe()
                assert result['syntheticVoice']['ok']
            result['controlledOperationMockOnly'] = True

            # 7. Multi-task layout and the hidden mode still work.
            window.apply_message({"protocolVersion": 1, "kind": "tasks", "tasks": [
                {"state": "WORKING", "project": "项目甲"}, {"state": "THINKING", "project": "项目乙"}]})
            assert not window._compact_mode()
            assert window.grab().save(str(out / 'multi-task.png'))
            window.apply_message({"protocolVersion": 1, "kind": "tasks", "tasks": []})
            window.bubble_mode = 'hidden'
            window._sync_bubble_size()
            assert not window._bubble_visible()
            result['ok'] = True
        except Exception as error:
            result['ok'] = False
            result['error'] = repr(error)
        (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        application.exit(0 if result.get('ok') else 1)

    QTimer.singleShot(350, exercise)
