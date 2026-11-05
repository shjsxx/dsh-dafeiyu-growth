"""Isolated real renderer transport probe. Never loads companion data."""
import json, os, sys, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'native'))
from runtime.external_renderer import ExternalRenderer
out=ROOT/'native'/'verification'/'pipe-smoke'
out.mkdir(parents=True,exist_ok=True)
# Explicit smoke-only wrapper adds diagnostics; production entry never enables diagnostics.
wrapper=out/'entry.cjs'
if wrapper.exists(): raise SystemExit('Use a fresh smoke output directory')
wrapper.write_text('process.argv.push("--smoke-out",'+json.dumps(str(out))+');require('+json.dumps(str(ROOT/'desktop'/'main.cjs'))+');',encoding='utf-8')
config={'enabled':True,'electronPath':str(Path.home()/'.dsh/yunlan-dsh-pet-runtime/electron/DeepSeek Harness.exe'),
'scriptPath':str(wrapper),'runtimeRoot':str(Path.home()/'.dsh/yunlan-dsh-pet-runtime/prepared-3c99b1b'),'userDataDir':str(out/'chromium')}
events=[]; ready=threading.Event()
def receive(event):
    if event['kind']!='anchor': events.append(event)
    if event['kind']=='ready':ready.set()
bridge=ExternalRenderer(config,receive)
try:
    bridge.start()
    if not ready.wait(15): raise RuntimeError('Renderer not ready')
    time.sleep(2)
    if not bridge.present('云岚的新外壳已连接。','SUCCESS',7000,'spin'):raise RuntimeError('Presentation write failed')
    # Wait for explicit smoke capture / child exit, not arbitrary live user state.
    bridge.process.wait(timeout=20)
finally: bridge.close()
(out/'transport.json').write_text(json.dumps({'events':events,'exitCode':bridge.process.returncode},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ready':ready.is_set(),'exitCode':bridge.process.returncode,'output':str(out)}))
