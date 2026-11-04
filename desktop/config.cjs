const fs = require('node:fs');
function parseJsonc(text) {
  let out = '', quoted = false, escape = false;
  for (let i=0; i<text.length; i++) {
    const a=text[i], b=text[i+1];
    if (quoted) { out+=a; if(escape) escape=false; else if(a==='\\') escape=true; else if(a==='"') quoted=false; }
    else if(a==='"') { quoted=true; out+=a; }
    else if(a==='/' && b==='/') { while(i<text.length && text[i]!=='\n') i++; out+='\n'; }
    else if(a==='/' && b==='*') { i+=2; while(i<text.length && !(text[i]==='*'&&text[i+1]==='/')) i++; i++; out+=' '; }
    else out+=a;
  }
  return JSON.parse(out);
}
function buildConfig(runtimeRoot) {
  const path = require('node:path');
  const defaults = parseJsonc(fs.readFileSync(path.join(runtimeRoot,'assets','config.jsonc'),'utf8'));
  // Only rendering configuration survives. Prompts, cloud model choices, upstream memory and generation are excluded.
  const conf = {};
  for(const name of ['animations','animationWeights','physics','font','confineToScreen','sleepRefuseTexts','sleepStartTexts','sleepEndTexts']) if(defaults[name]!==undefined) conf[name]=defaults[name];
  conf.pets = [{id:'yunlan',name:'云岚',size:360,display:'desktop',position:{corner:'bottom-right',marginX:35,marginY:20},fixedEnabled:false,balanceEnabled:false,whisperEnabled:false,workStatusEnabled:false}];
  conf.voiceEnabled=false; conf.clickSoundEnabled=false; conf.chatImageEnabled=false; conf.chatAnimationEnabled=false;
  conf.eventsRefreshSec={whisper:86400,sleepTalk:86400,balance:86400};
  const renderConfig={main:conf};
  const first = value => Array.isArray(value) ? first(value[0]) : value;
  const pool=defaults.animations.events.workStatus;
  const stateMap={THINKING:first(pool[0]),WORKING:first(pool[1]),WAITING:first(pool[3]),SUCCESS:first(pool[4]),ERROR:first(pool[5])};
  const intentMap={run:'原地左转奔跑',hop:'点击回应-开心跃动',spin:'小幅度原地360度旋转展示'};
  return {renderConfig,stateMap,intentMap};
}
module.exports={parseJsonc,buildConfig};
