"""Hermetic real-WebSocket duplex fixture: no microphone, speaker or cloud.

The server emits fixed transcripts; this validates protocol wiring, NOT ASR,
voice quality, production latency, account permissions or provider billing.
"""
import base64
import json
import queue
import threading
import time
from realtime_transport import RealtimeTransport


def run_loopback_probe():
    from websockets.sync.server import serve
    events = queue.Queue(maxsize=128)
    seen = {'cancel': False, 'truncate_ms': None, 'append_count': 0, 'authenticated_fixture': False}
    deadline = time.monotonic() + 8
    pcm = b'\x00' * 2400

    def handler(socket):
        seen['authenticated_fixture'] = socket.request.headers.get('Authorization') == 'Bearer loopback-fixture-not-a-provider-key'
        socket.send(json.dumps({'type':'session.created', 'session':{}}))
        while time.monotonic() < deadline:
            try:
                message = json.loads(socket.recv(timeout=1))
            except TimeoutError:
                continue
            except Exception:
                return
            kind = message.get('type')
            if kind == 'session.update':
                socket.send(json.dumps({'type':'session.updated', 'session': {'model':'gpt-realtime', 'tools':[], 'tool_choice':'none'}}))
            elif kind == 'input_audio_buffer.append':
                seen['append_count'] += 1
                index = seen['append_count']
                rid, iid = f'resp_fixture_{index}', f'item_fixture_{index}'
                socket.send(json.dumps({'type':'response.created', 'response':{'id':rid}}))
                socket.send(json.dumps({'type':'conversation.item.input_audio_transcription.completed', 'item_id':f'user_fixture_{index}', 'transcript':'固定回环测试转写'}))
                socket.send(json.dumps({'type':'response.output_audio.delta', 'response_id':rid, 'item_id':iid, 'content_index':0, 'delta':base64.b64encode(pcm * 2).decode('ascii')}))
                if index == 2:
                    socket.send(json.dumps({'type':'response.output_audio_transcript.done', 'response_id':rid, 'item_id':iid, 'transcript':'固定回环测试回复'}))
                    socket.send(json.dumps({'type':'response.done', 'response':{'id':rid,'status':'completed','usage':{'input_tokens':4,'output_tokens':3,'input_token_details':{'audio_tokens':3},'output_token_details':{'audio_tokens':2}}}}))
            elif kind == 'response.cancel':
                seen['cancel'] = True
            elif kind == 'conversation.item.truncate':
                seen['truncate_ms'] = message.get('audio_end_ms')
                socket.send(json.dumps({'type':'response.done', 'response':{'id':'resp_fixture_1','status':'cancelled','usage':{'input_tokens':4,'output_tokens':3}}}))

    server = serve(handler, '127.0.0.1', 0, ping_interval=None, close_timeout=0.2)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = RealtimeTransport(events.put_nowait)
    result = {'ok':False, 'synthetic_only':True, 'microphone_used':False, 'speaker_used':False,
              'external_network_used':False, 'keys_saved':False, 'training_performed':False}
    try:
        accepted = transport.start({'endpoint':f'ws://127.0.0.1:{port}/v1/realtime', 'model':'gpt-realtime',
                                   'voice':'marin', 'key':'loopback-fixture-not-a-provider-key', 'max_tokens':16,
                                   'session_seconds':60, 'protocol':'ga'})
        if not accepted:
            return dict(result, code='start_failed')
        audio_count = user_count = done_count = 0
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            kind = event.get('kind')
            if kind == 'session_ready':
                if not transport.send_audio(pcm):
                    return dict(result, code='append_failed')
            elif kind == 'audio_delta':
                audio_count += 1
                if event.get('pcm') != pcm * 2:
                    return dict(result, code='audio_mismatch')
                if audio_count == 1:
                    # Synthetic rendered-boundary metadata only: NO playback.
                    if not transport.mark_played(event['item_id'], 40) or not transport.interrupt():
                        return dict(result, code='cancel_failed')
            elif kind == 'transcript' and event.get('role') == 'user' and event.get('final'):
                user_count += 1
            elif kind == 'response_done':
                done_count += 1
                if done_count == 1:
                    if not transport.send_audio(pcm):
                        return dict(result, code='second_append_failed')
                elif done_count == 2:
                    break
            elif kind in ('error', 'limit'):
                return dict(result, code='transport_failed')
        result.update(ok=bool(audio_count == 2 and user_count == 2 and done_count == 2 and seen['cancel']
                              and seen['truncate_ms'] == 40 and seen['authenticated_fixture']),
                      turns=done_count, audio_chunks_received=audio_count, fixed_transcripts=user_count,
                      cancel_sent=seen['cancel'], truncate_ms=seen['truncate_ms'])
        return result
    finally:
        transport.stop()
        server.shutdown()
        thread.join(timeout=2)
        server.socket.close()


if __name__ == '__main__':
    value = run_loopback_probe()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    raise SystemExit(0 if value['ok'] else 1)
