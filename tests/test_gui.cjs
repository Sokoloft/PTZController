// Exercise controller selection and raw-input rendering without a desktop/Qt.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const viewBox={width:400,height:200};
class Element {
  constructor() { this.value='';this.dataset={};this.children=[];this.style={};this.attributes={};this.classList={add(){},remove(){}};this.parentElement={getBoundingClientRect:()=>viewBox}; }
  setAttribute(name,value) { this.attributes[name]=value; }
  append(...children) { this.children.push(...children); }
  add(child) { this.append(child); if (!this.value) this.value=child.value; }
  replaceChildren(...children) { this.children=children; }
  click() { this.clicked=true; }
}
const elements=new Map();
const document={hidden:false,getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(){return new Element();},createElementNS(){return new Element();},querySelectorAll(){return [];},addEventListener(){}};
let current={config:{camera:{mode:'simulator',host:'localhost',port:52381,framing:'sony',address:1},controller:'virtual',speed:.5,axes:[],bindings:[]},devices:[{id:'pad:0',name:'Test pad',axes:2,buttons:2,hats:0}],input:{axes:[],pressed:[]},actions:{none:'Unmapped',home:'Home'},connected:false,confirmed:false,armed:false,logs:[],model:{pan:0,tilt:0,zoom:0},input_error:'',backend:'',presets:[],active_preset:null,preset_modified:false,capture:{id:0,listening:false,source:null}};
const saved=new Map();
const calls=[];
let probeDelay=null;
let probeResult={status:'success',responded:true,message:'Camera responded. VISCA communication verified.',latency_ms:5};
let source,now=1000,animation,resize;
const intervals=[];
const context=vm.createContext({document,window:{addEventListener(){}},TOKEN:'test',structuredClone,console,
PTZLive:require('../src/ptz_controller/static/live.js'),performance:{now:()=>now},
EventSource:class {constructor(url){this.url=url;source=this;}},
ResizeObserver:class {constructor(callback){this.callback=callback;resize=callback;}observe(){this.callback();}},
requestAnimationFrame(callback){animation=callback;},
Option:function(title,value){this.title=title;this.value=value;},setTimeout(){},clearTimeout(){},setInterval(callback,ms){intervals.push(ms);},fetch:async(url,options)=>{
  if(options) {
    const body=JSON.parse(options.body);calls.push([url,body]);let result={ok:true};
    if(url==='/api/test-connection'){if(probeDelay)await probeDelay;result.result=probeResult;}
    if(url==='/api/controller') current.config.controller=body.controller;
    if(url==='/api/connect'){if(body.config)current.config=structuredClone(body.config);current.connected=true;current.armed=true;}
    if(url==='/api/disconnect'){current.connected=false;current.armed=false;}
    if(url==='/api/capture-start'){current.armed=false;current.capture={id:current.capture.id+1,listening:true,source:null};result.capture_id=current.capture.id;}
    if(url==='/api/preset-save' || url==='/api/preset-import'){
      saved.set(body.name,structuredClone(body.config));current.config=structuredClone(body.config);current.active_preset=body.name;
      current.presets=[...saved].map(([name,config])=>({name,camera:config.camera,controller:config.controller}));
      result.state=structuredClone(current);
    }
    if(url==='/api/preset-import'){current.connected=false;current.armed=false;result.state=structuredClone(current);}
    if(url==='/api/preset-export')result={filename:body.name+'.json',config:saved.get(body.name)};
    if(url==='/api/preset-load'){current.config=structuredClone(saved.get(body.name));current.active_preset=body.name;current.connected=false;current.armed=false;result.state=structuredClone(current);}
    return {ok:true,json:async()=>result};
  }
  return {ok:true,json:async()=>structuredClone(current)};
}});
async function settle(){await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  vm.runInContext(fs.readFileSync('src/ptz_controller/static/app.js','utf8'),context);
  await settle();
  calls.length=0;
  document.getElementById('host').value='192.0.2.10';
  await document.getElementById('testConnection').onclick();
  assert.equal(calls[0][0],'/api/test-connection');
  assert.equal(calls[0][1].camera.host,'192.0.2.10');
  assert(!('config' in calls[0][1]));
  assert.equal(current.connected,false);assert.equal(current.armed,false);
  assert.equal(document.getElementById('connectionTest').className,'connection-test test-success');
  assert.equal(document.getElementById('status').textContent,'Disconnected'); // test is not Connect
  probeResult={status:'timeout',responded:false,message:'No VISCA reply'};
  await document.getElementById('testConnection').onclick();
  assert.equal(document.getElementById('connectionTest').className,'connection-test test-timeout');
  let releaseProbe;probeDelay=new Promise(resolve=>releaseProbe=resolve);
  const pending=document.getElementById('testConnection').onclick();
  assert.equal(document.getElementById('testConnection').disabled,true);
  document.getElementById('host').value='192.0.2.11';releaseProbe();await pending;probeDelay=null;
  assert.match(document.getElementById('connectionTest').textContent,/settings changed/i);
  assert.equal(document.getElementById('testConnection').disabled,false);
  document.getElementById('host').value=current.config.camera.host;
  console.log('PASS: connection test uses displayed camera fields, reports results, preserves offline state, rejects stale results');
  document.getElementById('controller').value='pad:0';
  await document.getElementById('controller').onchange();
  assert(calls.some(([url,body])=>url==='/api/controller' && body.controller==='pad:0'));
  assert(!calls.some(([url])=>url==='/api/config')); // no Save required for monitoring
  current.input={axes:[.7,-.4],pressed:['button:1']};
  await vm.runInContext('poll()',context);
  function emit(sequence) {
    source.onmessage({data:JSON.stringify({...current,sequence,sample_time:now/1000,generation:0})});
    animation(now);
  }
  emit(1);
  assert.match(document.getElementById('input').textContent,/0: 0.70.*1: -0.40.*button:1/);
  assert.equal(document.getElementById('axisMonitor').children.length,2);
  assert.match(document.getElementById('inputHint').textContent,/Input monitor only.*Connect/);
  assert.match(document.getElementById('connect').textContent,/Connect/);
  const meter=document.getElementById('axisMonitor').children[0];
  now+=20;current.input.axes=[.9,-.2];emit(2);
  assert.equal(document.getElementById('axisMonitor').children[0],meter); // reuse DOM
  assert.equal(source.url,'/api/live');
  assert.deepEqual(intervals,[1000,400]); // slow settings/logs, independent heartbeat
  calls.length=0;
  await document.getElementById('connect').onclick();
  assert.deepEqual(calls.map(([url])=>url),['/api/connect']);
  assert.equal(current.armed,true);
  assert.equal(document.getElementById('status').textContent,'Connected');
  assert.equal(document.getElementById('status').className,'status-connected');
  assert.equal(document.getElementById('connect').disabled,true);
  assert.equal(document.getElementById('disconnect').disabled,false);
  await document.getElementById('connect').onclick();
  assert(!calls.some(([url])=>url==='/api/disconnect')); // Connect never toggles off
  await document.getElementById('disconnect').onclick();
  assert.equal(current.connected,false);assert.equal(current.armed,false);
  assert.equal(document.getElementById('status').textContent,'Disconnected');
  assert.equal(document.getElementById('status').className,'status-disconnected');
  assert.equal(document.getElementById('disconnect').disabled,true);
  await document.getElementById('connect').onclick();
  await document.getElementById('addBinding').onclick();
  assert.equal(document.getElementById('captureStatus').hidden,false);
  assert.equal(current.armed,false);
  current.capture.listening=false;current.capture.source='button:3';now+=20;emit(3);
  assert.equal(document.getElementById('captureStatus').hidden,true);
  assert.equal(vm.runInContext('config.bindings[0].source',context),'button:3');
  document.getElementById('presetName').value='Studio';
  await document.getElementById('save').onclick();
  assert.equal(saved.get('Studio').bindings[0].source,'button:3');
  assert.equal(document.getElementById('presets').children.length,1);
  const card=document.getElementById('presets').children[0];
  await card.children[0].onclick();
  assert.equal(current.active_preset,'Studio');assert.equal(current.armed,true);
  assert.equal(document.getElementById('connect').textContent,'Connect');
  assert.equal(document.getElementById('headerPreset').textContent,'Studio');
  await document.getElementById('disconnect').onclick();calls.length=0;
  await vm.runInContext("usePreset('Studio')",context);
  assert(calls.some(([url])=>url==='/api/preset-load'));
  assert(!calls.some(([url])=>url==='/api/connect'));
  assert.equal(current.connected,false);assert.equal(current.armed,false);
  assert.equal(document.getElementById('headerPreset').textContent,'Studio');
  assert.equal(document.getElementById('status').textContent,'Disconnected');
  const picker=document.getElementById('presetFile');
  document.getElementById('importPreset').onclick();
  assert.equal(picker.clicked,true);
  const imported={...structuredClone(current.config),speed:.8,
    axes:[{index:0,target:'pan',deadzone:.46,response:3,invert:false}]};
  picker.files=[{name:'Stage camera.json',text:async()=>JSON.stringify(imported)}];
  calls.length=0;
  await picker.onchange();
  assert.deepEqual(calls.find(([url])=>url==='/api/preset-import')[1],{name:'Stage camera',config:imported});
  assert(!calls.some(([url])=>url==='/api/connect'));
  assert.equal(document.getElementById('presetName').value,'Stage camera');
  assert.equal(document.getElementById('speed').value,.8);
  assert.equal(vm.runInContext('config.axes[0].deadzone',context),.46);
  let suggestedName, exported;
  context.window.showSaveFilePicker=async options=>{
    suggestedName=options.suggestedName;
    assert.equal(options.startIn,'home');
    return {createWritable:async()=>({write:async text=>{exported=JSON.parse(text);},close:async()=>{}})};
  };
  await document.getElementById('exportPreset').onclick();
  assert.equal(suggestedName,'Stage camera.json');
  assert.deepEqual(exported,imported);
  let nativeExport;
  context.window.pywebview={api:{export_preset:async name=>{nativeExport=name;return {saved:true};}}};
  await document.getElementById('exportPreset').onclick();
  assert.equal(nativeExport,'Stage camera');
  let openStart;
  context.window.showOpenFilePicker=async options=>{
    openStart=options.startIn;
    return [{getFile:async()=>({name:'Stage camera.json',text:async()=>JSON.stringify(imported)})}];
  };
  await document.getElementById('importPreset').onclick();
  assert.equal(openStart,'home');
  picker.files=[{name:'Invalid.json',text:async()=>'{bad'}];
  calls.length=0;
  await picker.onchange();
  assert(!calls.some(([url])=>url==='/api/preset-import'));
  assert.match(document.getElementById('notice').textContent,/valid JSON/);
  await document.getElementById('connect').onclick();
  calls.length=0;
  await vm.runInContext("usePreset('Studio')",context);
  assert(calls.some(([url])=>url==='/api/connect'));
  assert.equal(current.connected,true);assert.equal(current.armed,true);
  current.config.camera.mode='udp';current.confirmed=false;
  await vm.runInContext('poll()',context);calls.length=0;
  await vm.runInContext("usePreset('Studio')",context);
  assert(!calls.some(([url])=>url==='/api/connect')); // unconfirmed socket also displays Disconnected
  assert.equal(current.connected,false);assert.equal(current.armed,false);
  await document.getElementById('connect').onclick();
  console.log('PASS: disconnected preset loads stay offline; connected switches reconnect; unconfirmed sockets stay offline');
  current.logs=[{kind:'ERROR',time:'12:00',message:'Camera error'},{kind:'TX',time:'12:01',message:'81 01 ff'}];
  await vm.runInContext('poll()',context);
  assert.match(document.getElementById('logs').textContent,/Camera error/);
  assert(!document.getElementById('logs').textContent.includes('81 01 ff'));
  current.devices[0].button_labels={'button:0':{label:'A',symbol:'A',style:'xbox-a'}};
  await vm.runInContext('poll()',context);
  assert.equal(vm.runInContext("buttonName('button:0')",context),'A');
  assert.equal(vm.runInContext("buttonBadge('button:0').className",context),'face-button xbox-a');
  assert.equal(vm.runInContext("buttonName('button:9')",context),'button:9');
  current.devices[0].button_labels={'button:3':{label:'Cross',symbol:'×',style:'sony-cross'}};
  await vm.runInContext('poll()',context);
  assert.equal(vm.runInContext("buttonBadge('button:3').textContent",context),'×');
  assert.equal(vm.runInContext("badgeNodes[0].badge.className",context),'face-button sony-cross');
  assert.equal(saved.get('Studio').bindings[0].source,'button:3'); // labels never alter saved codes
  current.devices[0].button_labels['button:4']={label:'Left bumper (L1)',symbol:'L1',style:'sony-leftshoulder',icon:'sony-leftshoulder'};
  current.devices[0].button_labels['hat:0:up']={label:'D-pad up',symbol:'↑',style:'sony-dpad-up',icon:'sony-dpad-up'};
  await vm.runInContext('poll()',context);
  assert.equal(vm.runInContext("buttonBadge('button:4').children[0].children[0].attributes.href",context),'/static/controller-icons.svg#sony-leftshoulder');
  vm.runInContext("renderInput({axes:[],pressed:['button:4','hat:0:up']})",context);
  assert.equal(document.getElementById('pressedButtons').children.length,2);
  assert.equal(document.getElementById('pressedButtons').children[1].className,'face-button sony-dpad-up');
  current.model.pan=.5;current.model.tilt=.5;now+=100;emit(4);animation(now+50);
  const crosshair=document.getElementById('crosshair');
  assert.match(crosshair.style.transform,/translate3d\(38px,38px,0\)/); // equal travel on wide grids
  viewBox.width=200;viewBox.height=400;resize();animation(now+50);
  assert.match(crosshair.style.transform,/translate3d\(38px,38px,0\)/); // and tall grids
  current.config.camera.mode='udp';current.confirmed=false;now+=100;emit(5);
  assert.equal(document.getElementById('status').className,'status-disconnected');
  current.confirmed=true;now+=20;emit(6);
  assert.equal(document.getElementById('status').className,'status-connected');
  animation(now+500);
  assert.equal(document.getElementById('status').textContent,'Disconnected');
  assert.equal(document.getElementById('status').className,'status-disconnected');
  const html=fs.readFileSync('src/ptz_controller/templates/index.html','utf8');
  const header=html.slice(html.indexOf('<header>'),html.indexOf('</header>'));
  assert(header.includes('id="connect"')&&header.includes('id="disconnect"'));
  assert(!html.includes('id="stop"'));
  assert.equal((html.match(/id="connect"/g)||[]).length,1);
  assert.equal((html.match(/id="disconnect"/g)||[]).length,1);
  console.log('PASS: header-only connection buttons, colored status, disconnect stops controls, no connect toggle');
  console.log('PASS: equal pan/tilt pixel speeds on wide and tall virtual-camera grids');
  console.log('PASS: Xbox/Sony face badges and raw-string fallback; stored bindings unchanged');
  console.log('PASS: live UI, one-click connect, button capture, saved presets, quick switching, activity log filtering');
})().catch(e=>{console.error(e);process.exitCode=1;});
