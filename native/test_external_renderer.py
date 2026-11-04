"""Restricted renderer bridge tests. Temporary files and fake child only."""
import ast
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from runtime.external_renderer import ExternalRenderer, load_renderer_config, sanitize_event


class BridgeTests(unittest.TestCase):
    def test_command_allowlist(self):
        for command in ('open-conversation', 'open-growth', 'open-menu', 'feedback-positive',
                        'feedback-negative', 'emergency-stop', 'show', 'hide',
                        'interact-head', 'interact-poke', 'toggle-roam', 'toggle-reduced'):
            self.assertEqual(sanitize_event({'kind':'command','command':command,'secret':'drop'}),
                             {'kind':'command','command':command})
        for command in ('eval', '_voice_start', 'cloud', 'confirm-operation', None, []):
            try:
                result = sanitize_event({'kind':'command','command':command})
            except TypeError:
                self.fail('malformed commands must not raise')
            self.assertIsNone(result)

    def test_anchor_status_types(self):
        self.assertIsNotNone(sanitize_event({'kind':'anchor','x':-20,'y':0,'width':250,'height':300}))
        self.assertIsNone(sanitize_event({'kind':'anchor','x':True,'y':0,'width':250,'height':300}))
        self.assertIsNone(sanitize_event({'kind':'status','visible':1,'connected':True}))
        self.assertIsNone(sanitize_event({'kind':'anchor','x':0,'y':0,'width':99999,'height':3}))

    def test_action_events_are_bounded_and_typed(self):
        good = sanitize_event({'kind':'action','name':'悠闲哼歌','actionKind':'action',
                               'distance':0,'private':'drop'})
        self.assertEqual(good, {'kind':'action','name':'悠闲哼歌','actionKind':'action','distance':0})
        self.assertEqual(sanitize_event({'kind':'action','name':'原地左转奔跑','actionKind':'move','distance':180})['distance'], 180)
        for bad in ({'kind':'action','name':'','actionKind':'action'},
                    {'kind':'action','name':'x'*81,'actionKind':'action'},
                    {'kind':'action','name':'x','actionKind':'execute'},
                    {'kind':'action','name':'x','actionKind':'action','distance':True},
                    {'kind':'action','name':'x','actionKind':'action','distance':10001}):
            self.assertIsNone(sanitize_event(bad))

    def test_config_uses_only_explicit_temporary_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            exe=root/'electron.exe'; exe.write_text('fake')
            script=root/'main.cjs'; script.write_text('fake')
            marker=root/'config.json'
            config={'enabled':True,'electronPath':str(exe),'scriptPath':str(script),
                    'runtimeRoot':str(root),'userDataDir':str(root/'user-data')}
            marker.write_text(json.dumps(config))
            self.assertEqual(load_renderer_config(marker), config)
            marker.write_text(json.dumps(dict(config, key='must reject')))
            with self.assertRaises(ValueError): load_renderer_config(marker)
            marker.write_text('{"enabled":false}')
            self.assertIsNone(load_renderer_config(marker))
            with self.assertRaises(ValueError): load_renderer_config(root/'absent.json')

    def bridge(self, incoming=''):
        process=SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(incoming), poll=lambda:None,
                                wait=Mock(), terminate=Mock(), kill=Mock())
        bridge=ExternalRenderer({}, Mock(), platform='linux')
        bridge.process=process
        return bridge, process

    def test_presentation_has_no_extra_data_and_is_bounded(self):
        bridge,process=self.bridge()
        self.assertTrue(bridge.present('x'*3000,'IDLE',999999,'hop'))
        value=json.loads(process.stdin.getvalue())
        self.assertEqual(set(value),{'kind','seq','text','state','ttlMs','intent'})
        self.assertEqual(len(value['text']),2000)
        self.assertEqual(value['ttlMs'],60000)
        self.assertFalse(bridge.present('x','OTHER',1000))
        self.assertFalse(bridge.present('x','IDLE',True))
        self.assertTrue(bridge.present('x','IDLE',1000,'悠闲哼歌'))
        self.assertFalse(bridge.present('x','IDLE',1000,'x'*81))
        self.assertFalse(bridge.present('x','IDLE',1000,'bad\nname'))
        self.assertFalse(bridge.present('x','IDLE',1000,['array']))

    def test_reader_whitelist_eof_and_oversize(self):
        bridge,_=self.bridge('{"kind":"ready"}\n{"kind":"command","command":"cloud"}\n')
        bridge._read()
        self.assertEqual(bridge.on_event.call_args_list[0].args[0],{'kind':'ready'})
        self.assertEqual(bridge.on_event.call_args_list[-1].args[0],
                         {'kind':'status','visible':False,'connected':False})
        self.assertEqual(bridge.on_event.call_count,2)
        bridge,_=self.bridge('x'*17000+'\n')
        bridge._read()
        self.assertEqual(bridge.on_event.call_count,1)

    def test_close_is_idempotent(self):
        bridge,process=self.bridge()
        bridge.close(); bridge.close()
        self.assertEqual(process.wait.call_count,1)
        self.assertTrue(bridge._closed)

    def test_spawn_uses_fixed_argv_and_no_provider_environment(self):
        config={'electronPath':str(Path('electron.exe').resolve()),
                'scriptPath':str(Path('main.cjs').resolve()),
                'runtimeRoot':str(Path('.').resolve()),'userDataDir':str(Path('user-data').resolve())}
        bridge,process=self.bridge()
        factory=Mock(return_value=process)
        bridge=ExternalRenderer(config, Mock(), popen=factory, platform='linux')
        with patch('runtime.external_renderer.threading.Thread') as thread, \
                patch.dict('os.environ', {'OPENAI_API_KEY':'not-forwarded','ELECTRON_RUN_AS_NODE':'1'}):
            bridge.start()
        argv=factory.call_args.args[0]
        self.assertEqual(argv,[config['electronPath'],config['scriptPath'],'--runtime-root',
                               config['runtimeRoot'],'--user-data-dir',config['userDataDir']])
        self.assertFalse(factory.call_args.kwargs['shell'])
        self.assertNotIn('OPENAI_API_KEY',factory.call_args.kwargs['env'])
        self.assertNotIn('ELECTRON_RUN_AS_NODE',factory.call_args.kwargs['env'])

    def test_windows_control_pipe_handshake_and_utf8(self):
        bridge, process = self.bridge()
        config = {'electronPath':'electron.exe','scriptPath':'main.cjs',
                  'runtimeRoot':'runtime','userDataDir':'user-data'}
        pipe = io.BytesIO()
        opener = Mock(side_effect=[OSError('not ready'), pipe])
        factory = Mock(return_value=process)
        sleep = Mock()
        bridge = ExternalRenderer(config, Mock(), popen=factory,
                                  pipe_opener=opener, sleep=sleep, platform='win32')
        with patch('runtime.external_renderer.threading.Thread'):
            bridge.start()
        env = factory.call_args.kwargs['env']
        self.assertRegex(env['DSH_YUNLAN_CONTROL_PIPE'], r'^\\\\\.\\pipe\\yunlan-renderer-[0-9a-f]{32}$')
        self.assertRegex(env['DSH_YUNLAN_CONTROL_KEY'], r'^[0-9a-f]{64}$')
        handshake = json.loads(pipe.getvalue().decode('utf-8'))
        self.assertEqual(handshake, {'kind':'handshake','key':env['DSH_YUNLAN_CONTROL_KEY']})
        opener.assert_called_with(env['DSH_YUNLAN_CONTROL_PIPE'], 'r+b', buffering=0)
        sleep.assert_called_once_with(0.1)
        self.assertTrue(bridge.present('你好', 'IDLE', 1000))
        messages = [json.loads(line) for line in pipe.getvalue().decode('utf-8').splitlines()]
        self.assertEqual(messages[1]['text'], '你好')
        self.assertEqual(process.stdin.getvalue(), '')
        bridge.close()
        self.assertTrue(pipe.closed)
        process.wait.assert_called_once()

    def test_windows_control_timeout_cleans_up_child(self):
        _, process = self.bridge()
        config = {'electronPath':'electron.exe','scriptPath':'main.cjs',
                  'runtimeRoot':'runtime','userDataDir':'user-data'}
        opener = Mock(side_effect=OSError('not ready'))
        sleep = Mock()
        bridge = ExternalRenderer(config, Mock(), popen=Mock(return_value=process),
                                  pipe_opener=opener, sleep=sleep, platform='win32')
        with self.assertRaisesRegex(OSError, '^renderer control unavailable$'):
            bridge.start()
        self.assertEqual(opener.call_count, 100)
        self.assertEqual(sleep.call_count, 99)
        self.assertTrue(bridge._closed)
        process.wait.assert_called_once()

    def helper_method(self, name):
        source=(Path(__file__).parent/'runtime'/'helper.py').read_text(encoding='utf-8')
        method=next(node for node in ast.walk(ast.parse(source))
                    if isinstance(node,ast.FunctionDef) and node.name==name)
        scope={'sanitize_event':sanitize_event}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),
                     '<isolated-method>','exec'),scope)
        return scope[name]

    def test_disconnect_stops_voice_and_motion_and_schedules_restart(self):
        host=SimpleNamespace(_voice_stop=Mock(),_stop_motion=Mock(),_schedule_renderer_exit=Mock())
        self.helper_method('_on_renderer_event')(host,{'kind':'status','visible':False,'connected':False})
        host._voice_stop.assert_called_once(); host._stop_motion.assert_called_once()
        host._schedule_renderer_exit.assert_called_once()
        self.assertFalse(host.renderer_connected)

    def test_ui_commands_never_dispatch_cloud_or_confirmation(self):
        host=SimpleNamespace(_open_conversation=Mock(),_show_growth_card=Mock())
        dispatch=self.helper_method('_on_renderer_event')
        dispatch(host,{'kind':'command','command':'open-conversation'})
        dispatch(host,{'kind':'command','command':'open-growth'})
        dispatch(host,{'kind':'command','command':'confirm-operation'})
        host._open_conversation.assert_called_once(); host._show_growth_card.assert_called_once()

    def test_helper_external_motion_never_creates_training(self):
        source=(Path(__file__).parent/'runtime'/'helper.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        method=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='_external_motion')
        wrapper=ast.Module(body=[method],type_ignores=[])
        scope={}; exec(compile(ast.fix_missing_locations(wrapper),'<isolated-method>','exec'),scope)
        host=SimpleNamespace(_presentation_visible=lambda:True,reduced_motion=False,dragging=False,
                             menu_open=False,tasks=[],model=SimpleNamespace(base_state='IDLE'),
                             _conversation_engaged=lambda:False,_schedule_next_motion=Mock(),
                             _present_external=Mock(return_value=True),motion=None,pending_neural=None)
        self.assertTrue(scope['_external_motion'](host,'hop'))
        self.assertIsNone(host.motion); self.assertIsNone(host.pending_neural)
        host._present_external.assert_called_once_with('','IDLE',1800,'hop')

if __name__=='__main__': unittest.main()
