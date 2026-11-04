// Restricted Electron owner. Reuses the pinned dsh-pet renderer, not its cloud/API host.
'use strict';
const {app,BrowserWindow,ipcMain,protocol,session,screen,Tray,Menu,nativeImage}=require('electron');
const fs=require('node:fs'); const path=require('node:path'); const readline=require('node:readline');
const {safeFile,COMMANDS,PresentationHost,PIN}=require('./presentation-host.cjs');
const {buildConfig}=require('./config.cjs');
function arg(name) { const i=process.argv.indexOf(name); return i>=0?process.argv[i+1]:null; }
function smokeInteraction() { return process.argv.includes('--smoke-interact'); }
const runtimeRoot=arg('--runtime-root'); const dataDir=arg('--user-data-dir')||arg('--user-data');
if(!runtimeRoot || !path.isAbsolute(runtimeRoot) || !dataDir || !path.isAbsolute(dataDir)) throw Error('Explicit runtime and data directories required');
const rendererRoot=path.join(runtimeRoot,'runtime','electron-helper');
const cfg=buildConfig(runtimeRoot); cfg.sleepFile=path.join(dataDir,'sleep.json'); const host=new PresentationHost(cfg);
app.setName('yunlan-dsh-pet'); app.setPath('userData',dataDir);
app.commandLine.appendSwitch('autoplay-policy','no-user-gesture-required');
app.commandLine.appendSwitch('disable-features','HardwareMediaKeyHandling');
// The pet window is deliberately never focused; stop Chromium from throttling
// its timers/video when it is unfocused or considered occluded.
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
protocol.registerSchemesAsPrivileged([{scheme:'yunlan-pet',privileges:{standard:true,secure:true,supportFetchAPI:true}},{scheme:'dsh-pet-bridge',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true}}]);
let win=null,tray=null,ready=false,inputBusy=false,lastBounds=null,interactive=false,lastAnchorAt=0,anchorTimer=null,lastBoundsAt=0;
function emit(value) {
  process.stdout.write(JSON.stringify(value)+'\n');
  const smoke=arg('--smoke-out');
  if(smoke){try{fs.mkdirSync(smoke,{recursive:true});fs.appendFileSync(path.join(smoke,'events.jsonl'),JSON.stringify(value)+'\n');}catch{}}
}
function emitAnchor(value) {
  const now=Date.now();
  if(now-lastAnchorAt>=100){lastAnchorAt=now;if(anchorTimer){clearTimeout(anchorTimer);anchorTimer=null;}emit(value);}
  else if(!anchorTimer){anchorTimer=setTimeout(()=>{anchorTimer=null;lastAnchorAt=Date.now();if(lastBounds)emit(lastBounds.anchor);},100);}
  if(lastBounds) lastBounds.anchor=value;
}
const controlPipe=process.env.DSH_YUNLAN_CONTROL_PIPE,controlKey=process.env.DSH_YUNLAN_CONTROL_KEY;
const {createControlServer}=require('./control-channel.cjs');
function handleControl(message) {
  if(!message||typeof message!=='object')return;
  if(message.kind==='shutdown') app.quit();
  else if(message.kind==='visibility' && typeof message.visible==='boolean' && win) {
    if(message.visible) {stoppedByCore=false;win.showInactive(); win.webContents.send('yunlan:control',{kind:'resume'});} else {stoppedByCore=true;win.webContents.send('yunlan:control',{kind:'stop'});win.hide();}
    emit({kind:'status',visible:message.visible,connected:ready});
  } else if(message.kind==='configure' && win && typeof message.reducedMotion==='boolean' && typeof message.motionEnabled==='boolean' && Number.isFinite(message.scale) && message.scale>=.55 && message.scale<=1.4 && Number.isFinite(message.bubbleScale) && message.bubbleScale>=.8 && message.bubbleScale<=1.2) {
    cfg.renderConfig.main.pets[0].size = Math.round(360*message.scale);
    win.webContents.send('yunlan:control',message);
  } else if(message.kind==='stop' && win) {stoppedByCore=true;win.webContents.send('yunlan:control',{kind:'stop'});}
  else {const applied=host.apply(message);if(applied&&message.kind==='presentation'&&win){if(message.intent)stoppedByCore=false;if(!stoppedByCore)win.webContents.send('yunlan:control',{kind:'resume'});}}
}
let controlServer=null,stoppedByCore=false;
if(controlPipe&&controlKey) {
  try{controlServer=createControlServer({pipe:controlPipe,key:controlKey,onMessage:handleControl,onDisconnect:()=>app.quit()});}
  catch{app.exit(4);}
  controlServer.on('error',()=>app.exit(4));
  app.on('before-quit',()=>controlServer.shutdown());
} else if(process.platform!=='win32') {
  const input=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
  input.on('line',line=>{
    if(line.length>16384){process.stdin.destroy();return;}
    try{handleControl(JSON.parse(line));}catch{}
  });
  input.on('close',()=>app.quit());
} else if(!arg('--smoke-out')) throw Error('Authenticated control pipe required on Windows');
function json(value,status=200) {return new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json','access-control-allow-origin':'*','cache-control':'no-store'}});}
const mime={'.js':'text/javascript','.cjs':'text/javascript','.html':'text/html','.webm':'video/webm','.png':'image/png','.ttf':'font/ttf','.svg':'image/svg+xml'};
async function fileResponse(file,request) {
  const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.appendFileSync(path.join(smoke,'files.log'),String(file)+'\n');}
  if(!file) return new Response('not found',{status:404});
  const data=await fs.promises.readFile(file); const headers={'content-type':mime[path.extname(file)]||'application/octet-stream','access-control-allow-origin':'*','accept-ranges':'bytes'};
  const range=request.headers.get('range');
  if(range) {
    const match=/^bytes=(\d+)-(\d*)$/.exec(range);
    if(!match) return new Response(null,{status:416});
    const start=Number(match[1]),end=match[2]?Math.min(Number(match[2]),data.length-1):data.length-1;
    if(start> end || start>=data.length) return new Response(null,{status:416,headers:{'content-range':`bytes */${data.length}`}});
    headers['content-range']=`bytes ${start}-${end}/${data.length}`;headers['content-length']=String(end-start+1);
    return new Response(new Uint8Array(data.subarray(start,end+1)),{status:206,headers});
  }
  return new Response(new Uint8Array(data),{headers:{...headers,'content-length':String(data.length)}});
}
function permitted(event) {return win && event.sender===win.webContents && event.senderFrame===win.webContents.mainFrame;}
app.whenReady().then(async()=>{
  session.defaultSession.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
  session.defaultSession.setPermissionCheckHandler(()=>false);
  session.defaultSession.on('will-download',event=>event.preventDefault());
  session.defaultSession.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('yunlan-pet://ui/')&&!details.url.startsWith('dsh-pet-bridge://dsh-pet/')}));
  protocol.handle('yunlan-pet',async request=>{try{
    const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.appendFileSync(path.join(smoke,'requests.log'),request.url+'\n');}
    const url=new URL(request.url);if(url.hostname!=='ui' || request.method!=='GET') return new Response(null,{status:403});
    let name;try{name=decodeURIComponent(url.pathname.slice(1));}catch{return new Response(null,{status:400});}
    if(name==='extension.js') return fileResponse(safeFile(__dirname,'extension.js'),request);
    const file=safeFile(rendererRoot,name);
    {const smoke=arg('--smoke-out');if(smoke)fs.appendFileSync(path.join(smoke,'resolve.log'),JSON.stringify({name,file,version:process.versions})+'\n');}
    if(name==='index.html' && file) {
      let html=await fs.promises.readFile(file,'utf8');
      html=html.replace(/content="default-src[^\"]*"/,`content="default-src 'self' dsh-pet-bridge: data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' dsh-pet-bridge: data:; media-src 'self' dsh-pet-bridge: blob:; font-src 'self' dsh-pet-bridge: data:; connect-src dsh-pet-bridge:; object-src 'none'; frame-src 'none'"`);
      html=html.replace('<script src="./events.js"></script>','<script src="./extension.js"></script><script src="./events.js"></script>');
      return new Response(html,{headers:{'content-type':'text/html'}});
    }
    return fileResponse(file,request);
  }catch(error){const smoke=arg('--smoke-out');if(smoke)fs.writeFileSync(path.join(smoke,'protocol-error.txt'),String(error.stack||error));return new Response('restricted host failure',{status:500});}});
  protocol.handle('dsh-pet-bridge',async request=>{
    const url=new URL(request.url); if(url.hostname!=='dsh-pet') return json({ok:false},403);
    let relative;try{relative=decodeURIComponent(url.pathname.slice('/dsh-pet-7340/'.length));}catch{return json({ok:false},400);}
    // Asset paths are confined and cannot name user data, arbitrary files or executables.
    if(request.method==='GET') {
      const video=/^(?:webm\/|thumb\/main\/)(.+)$/.exec(relative);
      if(video) return fileResponse(safeFile(path.join(runtimeRoot,'assets','webm'),video[1].endsWith('.webm')?video[1]:video[1]+'.webm'),request);
      if(relative.startsWith('font/')) return fileResponse(safeFile(path.join(runtimeRoot,'assets','font'),relative.slice(5)),request);
      if(relative.startsWith('pic/')) return fileResponse(safeFile(path.join(runtimeRoot,'assets','pic'),relative.slice(4)),request);
    }
    let body;
    if(request.method==='POST') {const raw=await request.text();if(raw.length>16384)return json({ok:false},413);if(raw){try{body=JSON.parse(raw);}catch{return json({ok:false},400);}}}
    const result=host.route(url,request.method,body);
    // Avoid a tight loop: this restricted host returns full snapshots rather than implementing the upstream delta store.
    if(relative==='state') await new Promise(resolve=>setTimeout(resolve,150));
    return json(result.value,result.status);
  });
  const displays=screen.getAllDisplays(), primary=screen.getPrimaryDisplay();
  const areas=displays.map(d=>d.workArea),panels=displays.map(d=>d.bounds);
  const left=Math.min(...areas.map(a=>a.x)),top=Math.min(...areas.map(a=>a.y));
  const right=Math.max(...areas.map(a=>a.x+a.width)),bottom=Math.max(...areas.map(a=>a.y+a.height));
  win=new BrowserWindow({width:720,height:640,x:primary.workArea.x+primary.workArea.width-720,y:primary.workArea.y+primary.workArea.height-640,transparent:true,frame:false,alwaysOnTop:true,skipTaskbar:true,resizable:false,show:false,hasShadow:false,webPreferences:{preload:path.join(__dirname,'preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,backgroundThrottling:false}});
  win.setIgnoreMouseEvents(true,{forward:true}); win.setAlwaysOnTop(true,'floating');
  win.webContents.on('did-fail-load',(_event,code,description,url)=>{const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.writeFileSync(path.join(smoke,'load-failure.json'),JSON.stringify({code,description,url}));}});
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('yunlan-pet://ui/index.html?'))event.preventDefault();});
  win.webContents.on('render-process-gone',(_event,details)=>{const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.writeFileSync(path.join(smoke,'renderer-gone.json'),JSON.stringify(details));}emit({kind:'status',visible:false,connected:false});app.exit(3);});
  win.on('closed',()=>{win=null;app.quit();});
  ipcMain.on('pet:set-bounds',(event,b)=>{
    if(!permitted(event)||!b||!['x','y','width','height','boxX','boxY','size'].every(k=>Number.isFinite(b[k])))return;
    if(b.width<40||b.height<40||b.width>2048||b.height>2048||b.x<left-2048||b.y<top-2048||b.x>right+2048||b.y>bottom+2048)return;
    const bounds={x:Math.round(b.x),y:Math.round(b.y),width:Math.round(b.width),height:Math.round(b.height)};
    const sizeChanged=!lastBounds||lastBounds.width!==bounds.width||lastBounds.height!==bounds.height;
    const positionChanged=!lastBounds||lastBounds.x!==bounds.x||lastBounds.y!==bounds.y;
    if(sizeChanged) win.setBounds(bounds,false);
    else if(positionChanged) win.setPosition(bounds.x,bounds.y,false);
    lastBounds={...bounds,boxX:b.boxX,boxY:b.boxY,size:b.size,marginL:b.marginL,marginT:b.marginT};
    if(positionChanged||sizeChanged) lastBoundsAt=Date.now();
    emitAnchor({kind:'anchor',x:Math.round(b.boxX+left),y:Math.round(b.boxY+top),width:Math.round(b.size),height:Math.round(b.size*9/16)});
  });
  ipcMain.on('pet:set-interactive',(event,value)=>{if(permitted(event)){interactive=value===true;win.setIgnoreMouseEvents(!interactive,{forward:true});}});
  ipcMain.on('pet:input-busy',(event,value)=>{if(permitted(event)) inputBusy=value===true;});
  ipcMain.on('yunlan:focus',event=>{if(permitted(event))win.focus();});
  ipcMain.on('yunlan:command',(event,command)=>{if(permitted(event)&&COMMANDS.has(command))emit({kind:'command',command});});
  ipcMain.on('yunlan:action',(event,payload)=>{
    if(!permitted(event)||!payload||typeof payload.name!=='string'||!['move','jump','hop','action','click'].includes(payload.kind))return;
    if(!host.animationNames().has(payload.name))return;
    if(!Number.isFinite(payload.distance)||payload.distance<0||payload.distance>10000)return;
    emit({kind:'action',name:payload.name,actionKind:payload.kind,distance:Math.round(payload.distance)});
  });
  ipcMain.on('yunlan:ready',event=>{if(permitted(event)&&!ready){ready=true;win.showInactive();emit({kind:'ready'});emit({kind:'status',visible:true,connected:true});}});
  // Pointer-forward may be unreliable with transparent windows: keep the body hit area interactive as fallback.
  const pointerTimer=setInterval(()=>{
    if(!win || !lastBounds || inputBusy) return;
    // While the pet is moving, its own bounds churn is enough work; do not
    // toggle hit-testing every 80ms on top of it.
    if(Date.now()-lastBoundsAt<250) return;
    const point=screen.getCursorScreenPoint(),b=lastBounds;
    const x=point.x-b.x-(b.marginL||b.size*.5),y=point.y-b.y-(b.marginT||b.size*.5);
    const hit=x>b.size*.24&&x<b.size*.76&&y>b.size*.03&&y<b.size*.6;
    if(hit!==interactive){interactive=hit;win.setIgnoreMouseEvents(!hit,{forward:true});}
  },80);pointerTimer.unref();
  const image=nativeImage.createFromPath(path.join(runtimeRoot,'assets','pic','cursor-grab.png'));
  tray=new Tray(image.isEmpty()?nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='):image);
  tray.setToolTip('云岚 · dsh-pet 外壳');
  tray.setContextMenu(Menu.buildFromTemplate([{label:'和云岚聊天',click:()=>emit({kind:'command',command:'open-conversation'})},{label:'查看成长卡',click:()=>emit({kind:'command',command:'open-growth'})},{label:'让云岚睡觉',click:()=>host.setSleep(true)},{label:'叫醒云岚',click:()=>host.setSleep(false)},{label:'显示云岚',click:()=>emit({kind:'command',command:'show'})},{label:'紧急停止',click:()=>emit({kind:'command',command:'emergency-stop'})}]));
  const query=new URLSearchParams({bridge:'1',configUrl:'dsh-pet-bridge://dsh-pet/dsh-pet-7340/config',scale:'1',petIndex:'0',workAreaX:String(left),workAreaY:String(top),workAreaW:String(right-left),workAreaH:String(bottom-top),areas:JSON.stringify(areas),panels:JSON.stringify(panels),workAreas:JSON.stringify(areas),primaryIndex:String(displays.findIndex(d=>d.id===primary.id))});
  await win.loadURL('yunlan-pet://ui/index.html?'+query);
  if(smokeInteraction()) {
    setTimeout(()=>{if(win)win.webContents.executeJavaScript("window.yunlanBridge.command('hide')").catch(()=>{});},8000);
    setTimeout(()=>{if(win)win.webContents.executeJavaScript("window.yunlanBridge.command('show')").catch(()=>{});},9500);
    setTimeout(()=>{
      if(!win) return;
      win.webContents.executeJavaScript("(()=>{const hit=document.querySelector('.pet-hit');if(!hit)return 'no-hit';const r=hit.getBoundingClientRect();hit.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2}));return 'clicked';})()").catch(()=>{});
    },10500);
    setTimeout(()=>{
      if(!win) return;
      win.webContents.executeJavaScript("(()=>{if(!sprites.length)return 'no-sprite';const t=performance.now();sprites[0].onContextMenu({preventDefault(){},clientX:360,clientY:320});return performance.now()-t;})()").then(value=>{const smoke=arg('--smoke-out');if(smoke)fs.writeFileSync(path.join(smoke,'menu-time.json'),JSON.stringify({buildMs:value}));}).catch(()=>{});
    },11200);
  }
  // Explicit smoke mode only: records renderer diagnostics without reading user data or starting core.
  const smoke=arg('--smoke-out');
  if(smoke){setTimeout(async()=>{
    try{
      const diag=await win.webContents.executeJavaScript('JSON.stringify({debug:window.__dshPetDebug,videos:[...document.querySelectorAll("video")].map(v=>({readyState:v.readyState,currentTime:v.currentTime,paused:v.paused,error:v.error?.code||null,src:v.currentSrc})),config:typeof config!=="undefined"&&!!config})');
      fs.mkdirSync(smoke,{recursive:true});fs.writeFileSync(path.join(smoke,'renderer.json'),diag);
      const image=await win.webContents.capturePage();fs.writeFileSync(path.join(smoke,'renderer.png'),image.toPNG());
    }finally{app.quit();}
  },12000);}
}).catch(error=>{const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.writeFileSync(path.join(smoke,'startup-error.txt'),String(error.stack||error));}emit({kind:'status',connected:false,visible:false});app.exit(2);});
app.on('before-quit',()=>{const smoke=arg('--smoke-out');if(smoke){fs.mkdirSync(smoke,{recursive:true});fs.writeFileSync(path.join(smoke,'quitting.txt'),new Error('quit trace').stack);}tray?.destroy();});
app.on('window-all-closed',()=>app.quit());
