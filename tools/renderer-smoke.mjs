import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
const [electron,runtime,out]=process.argv.slice(2);
if(!electron||!runtime||!out)throw Error('electron runtime output required');
fs.mkdirSync(out,{recursive:true});
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(electron,[path.resolve('desktop/main.cjs'),'--runtime-root',runtime,'--user-data-dir',path.join(out,'chromium'),'--smoke-out',out],{env,stdio:['pipe','pipe','pipe']});
let pending='',events=[],stderr='';
child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
child.stderr.on('data',data=>{stderr=(stderr+data).slice(-16000);});
child.stdout.on('data',data=>{
 pending+=data;let newline;
 while((newline=pending.indexOf('\n'))>=0){const line=pending.slice(0,newline);pending=pending.slice(newline+1);try{const event=JSON.parse(line);if(event.kind!=='anchor')events.push(event);if(event.kind==='ready'){/* interaction fixtures are opt-in via --smoke-interact in the core probe */}}catch{}}
});
const timer=setTimeout(()=>{child.kill();},30000);
const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});clearTimeout(timer);
fs.writeFileSync(path.join(out,'bootstrap.json'),JSON.stringify({code,events,stderr},null,2));
console.log(JSON.stringify({code,events,output:out}));process.exitCode=code||0;
