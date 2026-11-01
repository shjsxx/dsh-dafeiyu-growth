"""All device objects are fakes; never captures or plays physical audio."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudio
from realtime_audio import RealtimeAudio, CHUNK, MAX_CAPTURE, MAX_PLAYBACK

APP = QApplication.instance() or QApplication([])


class Input(QObject):
    readyRead = Signal()
    def __init__(self):
        super().__init__()
        self.data = bytearray()
    def bytesAvailable(self):
        return len(self.data)
    def read(self, count):
        data = bytes(self.data[:count])
        del self.data[:count]
        return data


class Device:
    def __init__(self, source=False):
        self.source = source
        self.io = Input() if source else Mock()
        if not source:
            self.io.write.side_effect = lambda value: len(value)
        self.stop = Mock()
        self.deleteLater = Mock()
        self.setBufferSize = Mock()
        self.mic_error = QAudio.Error.NoError
        self.time = 0
        self.starts = 0
    def start(self):
        self.time = 0
        self.starts += 1
        return self.io
    def error(self):
        return self.mic_error
    def bytesFree(self):
        return CHUNK * 4
    def processedUSecs(self):
        return self.time
    def reset(self):
        self.time = 0


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.source, self.sink = Device(True), Device()
        self.upload, self.played, self.error = Mock(return_value=True), Mock(), Mock()
        self.audio = RealtimeAudio(self.upload, self.played, self.error)
        self.patches = [patch.object(RealtimeAudio, 'capabilities', return_value={'available': True}),
            patch('PySide6.QtMultimedia.QAudioSource', return_value=self.source),
            patch('PySide6.QtMultimedia.QAudioSink', return_value=self.sink),
            patch('PySide6.QtMultimedia.QMediaDevices.defaultAudioInput', return_value=None),
            patch('PySide6.QtMultimedia.QMediaDevices.defaultAudioOutput', return_value=None)]
        for item in self.patches:
            item.start()
    def tearDown(self):
        self.audio.stop()
        for item in reversed(self.patches):
            item.stop()
    def begin(self):
        self.assertTrue(self.audio.start())
    def test_construction_does_not_start_devices(self):
        self.assertEqual(self.source.starts, 0)
        self.assertFalse(self.audio.active)
    def test_unsupported_device_fails_before_capture(self):
        with patch.object(RealtimeAudio, 'capabilities', return_value={'available': False}):
            self.assertFalse(self.audio.start())
        self.error.assert_called_once_with('audio_format')
        self.assertEqual(self.source.starts, 0)
    def test_start_is_idempotent(self):
        self.begin()
        self.begin()
        self.assertEqual(self.source.starts, 1)
    def test_capture_in_fixed_chunks_with_remainder_only_in_ram(self):
        self.begin()
        self.source.io.data.extend(b'\x00' * (CHUNK * 2 + 12))
        self.audio._capture()
        self.assertEqual(self.upload.call_count, 2)
        self.assertEqual(self.upload.call_args.args[0], b'\x00' * CHUNK)
        self.assertEqual(len(self.audio.capture), 12)
    def test_capture_buffer_overflow_stops(self):
        self.begin()
        self.source.io.data.extend(b'\x00' * (MAX_CAPTURE + 2))
        self.audio._capture()
        self.assertFalse(self.audio.active)
        self.upload.assert_not_called()
        self.error.assert_called_with('capture_backpressure')
    def test_upload_rejection_stops_and_erases_remainder(self):
        self.begin()
        self.upload.return_value = False
        self.source.io.data.extend(b'\x00' * CHUNK * 2)
        self.audio._capture()
        self.assertFalse(self.audio.active)
        self.assertEqual(self.upload.call_count, 1)
        self.assertFalse(self.audio.capture)
    def test_received_audio_is_not_counted_as_heard(self):
        self.begin()
        self.assertTrue(self.audio.enqueue(b'\x00' * CHUNK * 2, 'item_a'))
        self.played.assert_not_called()
        self.sink.time = 25000
        self.audio._pump()
        self.assertEqual(self.played.call_args.args, ('item_a', 25))
    def test_partial_playback_progress_is_cumulative(self):
        self.begin()
        self.audio.enqueue(b'\x00' * CHUNK * 2, 'item_a')
        self.sink.time = 25000
        self.audio._pump()
        self.sink.time = 50000
        self.audio._pump()
        self.assertEqual(self.played.call_args.args, ('item_a', 50))
    def test_progress_never_exceeds_written_samples(self):
        self.begin()
        self.audio.enqueue(b'\x00' * CHUNK, 'item_a')
        self.sink.time = 10000000
        self.audio._pump()
        self.assertEqual(self.played.call_args.args, ('item_a', 50))
    def test_interrupt_reports_progress_then_drops_old_playback(self):
        self.begin()
        self.audio.enqueue(b'\x00' * CHUNK * 2, 'item_a')
        self.sink.time = 20000
        self.audio.clear_playback()
        self.assertEqual(self.played.call_args.args, ('item_a', 20))
        self.assertFalse(self.audio.timeline)
        self.assertFalse(self.audio.output)
        self.assertTrue(self.audio.active)
        self.assertEqual(self.sink.starts, 2)
    def test_playback_queue_overflow_stops(self):
        self.begin()
        self.sink.bytesFree = lambda: 0
        for _ in range(MAX_PLAYBACK // 48000):
            self.assertTrue(self.audio.enqueue(b'\x00' * 48000, 'item_a'))
        self.assertFalse(self.audio.enqueue(b'\x00' * 2, 'item_a'))
        self.assertFalse(self.audio.active)
        self.error.assert_called_with('playback_backpressure')
    def test_odd_pcm_or_missing_item_fails_closed(self):
        self.begin()
        self.assertFalse(self.audio.enqueue(b'a', 'item_a'))
        self.error.assert_called_with('playback_format')
    def test_device_error_stops_capture_and_playback(self):
        self.begin()
        self.source.mic_error = QAudio.Error.IOError
        self.audio._pump()
        self.assertFalse(self.audio.active)
        self.source.stop.assert_called()
        self.sink.stop.assert_called()
    def test_stop_erases_buffers_and_late_capture_is_ignored(self):
        self.begin()
        self.source.io.data.extend(b'\x00' * CHUNK)
        self.audio.stop()
        self.audio._capture()
        self.upload.assert_not_called()
        self.assertFalse(self.audio.output)
        self.assertIsNone(self.audio.input_io)
    def test_output_short_write_preserves_unwritten_bytes(self):
        self.begin()
        self.sink.bytesFree = lambda: 100
        self.sink.io.write.side_effect = lambda data: min(20, len(data))
        self.audio.enqueue(b'\x00' * 200, 'item_a')
        self.assertEqual(self.audio.written, 100)
        self.assertEqual(self.audio.queued, 100)


if __name__ == '__main__':
    unittest.main()
