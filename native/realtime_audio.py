"""Main-thread, RAM-only PCM audio I/O for OpenAI Realtime.

Qt Multimedia is imported only when queried/started. Audio is 24kHz, mono,
16-bit little-endian. No disk, transcript, provider, OS action or network code.
This raw duplex device has NO acoustic echo cancellation: require headphones.
"""
from collections import deque
from PySide6.QtCore import QObject, QTimer

RATE = 24000
BYTES_PER_SECOND = RATE * 2
CHUNK = 2400  # 50ms
MAX_CAPTURE = BYTES_PER_SECOND
MAX_PLAYBACK = BYTES_PER_SECOND * 4


class RealtimeAudio(QObject):
    def __init__(self, on_audio, on_played, on_error, parent=None):
        super().__init__(parent)
        self.on_audio, self.on_played, self.on_error = on_audio, on_played, on_error
        self.active = False
        self.source = self.sink = self.input_io = self.output_io = None
        self.capture = bytearray()
        self.output = deque()
        self.queued = 0
        self.written = 0
        self.timeline = deque()
        self.played_by_item = {}
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._pump)

    @staticmethod
    def capabilities():
        try:
            from PySide6.QtMultimedia import QMediaDevices, QAudioFormat
            fmt = QAudioFormat()
            fmt.setSampleRate(RATE)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            input_device, output_device = QMediaDevices.defaultAudioInput(), QMediaDevices.defaultAudioOutput()
            capture = not input_device.isNull() and input_device.isFormatSupported(fmt)
            playback = not output_device.isNull() and output_device.isFormatSupported(fmt)
            return {'available': bool(capture and playback), 'input': bool(capture), 'output': bool(playback), 'rate': RATE,
                    'channels': 1, 'sample_bits': 16, 'echo_cancellation': False}
        except Exception:
            return {'available': False, 'input': False, 'output': False, 'rate': RATE,
                    'channels': 1, 'sample_bits': 16, 'echo_cancellation': False}

    def start(self):
        if self.active:
            return True
        if not self.capabilities()['available']:
            self.on_error('audio_format')
            return False
        try:
            from PySide6.QtMultimedia import QMediaDevices, QAudioFormat, QAudioSource, QAudioSink
            fmt = QAudioFormat()
            fmt.setSampleRate(RATE)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self.source = QAudioSource(QMediaDevices.defaultAudioInput(), fmt, self)
            self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
            self.source.setBufferSize(CHUNK * 4)
            self.sink.setBufferSize(CHUNK * 4)
            self.output_io = self.sink.start()
            self.input_io = self.source.start()
            if self.output_io is None or self.input_io is None:
                raise RuntimeError('unavailable')
            self.active = True
            self.input_io.readyRead.connect(self._capture)
            self.timer.start()
            return True
        except Exception:
            self.stop()
            self.on_error('audio_start')
            return False

    def _fail(self, code):
        self.stop()
        self.on_error(code)

    def _capture(self):
        if not self.active or self.input_io is None:
            return
        try:
            size = self.input_io.bytesAvailable()
            if size > MAX_CAPTURE or size + len(self.capture) > MAX_CAPTURE:
                self._fail('capture_backpressure')
                return
            self.capture.extend(bytes(self.input_io.read(size)))
            while self.active and len(self.capture) >= CHUNK:
                chunk = bytes(self.capture[:CHUNK])
                del self.capture[:CHUNK]
                if self.on_audio(chunk) is not True:
                    self._fail('upload_backpressure')
                    return
        except Exception:
            self._fail('capture_failed')

    def enqueue(self, pcm, item_id):
        if not self.active:
            return False
        if (not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 65536
                or not isinstance(item_id, str) or not 1 <= len(item_id) <= 128):
            self._fail('playback_format')
            return False
        if self.queued + len(pcm) > MAX_PLAYBACK:
            self._fail('playback_backpressure')
            return False
        self.output.append((pcm, item_id))
        self.queued += len(pcm)
        self._pump()
        return True

    def _report_played(self):
        if self.sink is None:
            return
        processed = min(self.written, max(0, int(self.sink.processedUSecs()) * BYTES_PER_SECOND // 1000000))
        while self.timeline and processed > self.timeline[0]['start']:
            row = self.timeline[0]
            reached = min(processed, row['end'])
            amount = max(0, reached - row['reported'])
            if amount:
                self.played_by_item[row['item']] = self.played_by_item.get(row['item'], 0) + amount
                row['reported'] = reached
                self.on_played(row['item'], self.played_by_item[row['item']] * 1000 // BYTES_PER_SECOND)
            if reached < row['end']:
                break
            self.timeline.popleft()
        # Keep bounded RAM metadata for at most recent completed audio items.
        if len(self.played_by_item) > 64:
            active = {row['item'] for row in self.timeline}
            for key in list(self.played_by_item):
                if key not in active:
                    del self.played_by_item[key]
                    if len(self.played_by_item) <= 32:
                        break

    def _pump(self):
        if not self.active or self.sink is None:
            return
        try:
            from PySide6.QtMultimedia import QAudio
            if self.source.error() != QAudio.Error.NoError or self.sink.error() not in (QAudio.Error.NoError, QAudio.Error.UnderrunError):
                self._fail('device_error')
                return
            self._report_played()
            budget = min(max(0, self.sink.bytesFree()), CHUNK * 4)
            while self.output and budget >= 2:
                pcm, item = self.output[0]
                amount = min(budget, len(pcm), CHUNK)
                amount -= amount % 2
                wrote = self.output_io.write(pcm[:amount])
                if wrote < 0 or wrote % 2:
                    self._fail('playback_failed')
                    return
                if wrote == 0:
                    break
                start = self.written
                self.written += wrote
                self.timeline.append({'item': item, 'start': start, 'end': self.written, 'reported': start})
                self.queued -= wrote
                budget -= wrote
                if wrote == len(pcm):
                    self.output.popleft()
                else:
                    self.output[0] = (pcm[wrote:], item)
        except Exception:
            self._fail('playback_failed')

    def clear_playback(self):
        if self.sink is None:
            self.output.clear()
            self.queued = 0
            return
        try:
            self._report_played()
            self.sink.reset()
            self.sink.stop()
            self.output.clear()
            self.timeline.clear()
            self.played_by_item.clear()
            self.queued = self.written = 0
            if self.active:
                self.output_io = self.sink.start()
                if self.output_io is None:
                    raise RuntimeError('unavailable')
        except Exception:
            self._fail('playback_failed')

    def stop(self):
        self.active = False
        self.timer.stop()
        self.capture.clear()
        self.output.clear()
        self.timeline.clear()
        self.played_by_item.clear()
        self.queued = self.written = 0
        source, sink = self.source, self.sink
        self.source = self.sink = self.input_io = self.output_io = None
        for device in (source, sink):
            if device is not None:
                try:
                    device.stop()
                    device.deleteLater()
                except RuntimeError:
                    pass

    close = stop
