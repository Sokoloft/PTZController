const $ = id => document.getElementById(id);
let state, config, axes = Array(8).fill(0), pressed = new Set(), polling = false;
const timeline=new PTZLive.Timeline();
let meterNodes=[], lastModelText=0;
let runtime={}, dirty=false, connecting=false, presetSignature='';
let captureBinding=null, captureId=null, captureToken=0;
let badgeNodes=[], buttonSignature='', pressedSignature='';
const deviceCache=new Map();
function faceInfo(source) {
  const selected=$('controller').value || config?.controller;
  const device=state?.devices.find(d=>d.id===selected) || deviceCache.get(selected);
  return device?.button_labels?.[source];
}
function buttonName(source) { return faceInfo(source)?.label || source; }
function paintButtonBadge(node,source) {
  const info=faceInfo(source);
  node.className=info?'face-button '+info.style:'raw-button';
  node.title=info?`${info.label} (${source})`:source;node.ariaLabel=node.title;
  if(info?.icon) {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const use=document.createElementNS('http://www.w3.org/2000/svg','use');
    svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('aria-hidden','true');
    use.setAttribute('href','/static/controller-icons.svg#'+info.icon);
    svg.append(use);node.replaceChildren(svg);
    if(info.icon.endsWith('-paddle'))node.append(element('span',{className:'icon-caption',textContent:info.symbol}));
  } else node.textContent=info?.symbol || source;
}
function buttonBadge(source) {
  const badge=element('span');paintButtonBadge(badge,source);return badge;
}
function refreshButtonBadges() {
  for(const node of badgeNodes) {
    const info=faceInfo(node.binding.source);
    node.badge.hidden=!info;
    paintButtonBadge(node.badge,node.binding.source);
    node.group.className='source-display'+(info?' recognized':'');
  }
}
function markDirty() { dirty=true; }
function setText(id,text) { if($(id).textContent!==text)$(id).textContent=text; }
function connectionStatus(connected, detail='') {
  setText('status',connected?'Connected':'Disconnected');
  $('status').className=connected?'status-connected':'status-disconnected';
  $('status').title=detail;
}
function connectionReady(current) {
  return Boolean(current.connected && (current.confirmed || current.config?.camera.mode==='simulator') && !current.error);
}
function notice(message) { $('notice').textContent = message; $('notice').style.display = 'block'; clearTimeout(notice.timer); notice.timer = setTimeout(() => $('notice').style.display = 'none', 6000); }
async function api(op, data = {}) {
  const res = await fetch('/api/' + op, {method:'POST', headers:{'Content-Type':'application/json','X-PTZ-Token':TOKEN}, body:JSON.stringify(data)});
  const result = await res.json(); if (!res.ok) throw Error(result.error || 'Request failed'); return result;
}
function run(fn) { return async () => { try { await fn(); } catch(e) { notice(e.message); } }; }
function element(tag, props = {}) { return Object.assign(document.createElement(tag), props); }
function field(text, control) { const label = element('label', {textContent:text}); label.append(control); return label; }
function select(options, value, changed) { const s = element('select'); for (const [v, title] of Object.entries(options)) s.add(new Option(title, v)); s.value = value; s.onchange = () => {changed(s.value);markDirty();}; return s; }
function input(value, type, changed) { const i = element('input', {value, type}); i.onchange = () => {changed(type === 'number' ? Number(i.value) : i.value);markDirty();}; return i; }
function renderMappings() {
  badgeNodes=[];
  $('axes').replaceChildren();
  config.axes.forEach((a, index) => {
    const row = element('div', {className:'row'});
    const dz = input(a.deadzone, 'number', v => a.deadzone = v); Object.assign(dz, {min:0, max:.95, step:.01});
    const invert = element('input', {type:'checkbox', checked:a.invert}); invert.onchange = () => {a.invert = invert.checked;markDirty();};
    const remove = element('button', {textContent:'Remove'}); remove.onclick = () => { config.axes.splice(index,1);markDirty();renderMappings(); };
    row.append(field('Axis index', input(a.index, 'number', v => a.index = v)), field('Motion', select({pan:'Pan',tilt:'Tilt',zoom:'Zoom',focus:'Focus'}, a.target, v => a.target = v)), field('Deadzone (0–0.95)', dz), field('Invert', invert), remove);
    $('axes').append(row);
  });
  $('bindings').replaceChildren();
  config.bindings.forEach((b,index) => {
    const row = element('div', {className:'row'+(captureBinding===b?' listening':'')});
    const source=input(b.source,'text',v=>{b.source=v;refreshButtonBadges();});
    source.className='source-code';
    const badge=element('span'), group=element('div');group.append(badge,source);
    badgeNodes.push({binding:b,badge,group});
    source.placeholder=captureBinding===b?'Press a controller button…':'Click to listen, or type a source';
    source.onfocus=run(()=>startLearning(b));
    const learn=element('button',{textContent:captureBinding===b?'Listening…':'Listen'});
    learn.onclick=run(()=>startLearning(b));
    const remove = element('button', {textContent:'Remove'}); remove.onclick = () => { if(captureBinding===b)cancelLearning();config.bindings.splice(index,1);markDirty();renderMappings(); };
    row.append(field('Input source',group),learn, field('Command',select(state.actions,b.action,v=>b.action=v)), field('Memory slot / hex',input(b.param ?? 0,'text',v=>b.param=v)),remove);
    $('bindings').append(row);
  });
  refreshButtonBadges();
}
function devices() {
  state.devices.forEach(d=>deviceCache.set(d.id,d));
  const selected = $('controller').value || config.controller;
  const options = {virtual:'Virtual controller'};
  state.devices.forEach(d => options[d.id] = `${d.name} (${d.buttons} buttons / ${d.axes} axes / ${d.hats} hats)`);
  if (!(selected in options)) options[selected] = 'Disconnected controller ' + selected;
  const signature = JSON.stringify(options);
  if ($('controller').dataset.signature !== signature) {
    $('controller').replaceChildren(...Object.entries(options).map(([v,t])=>new Option(t,v)));
    $('controller').value=selected; $('controller').dataset.signature=signature;
    globalThis.PTZDropdowns?.refresh();
  }
  const labels=JSON.stringify([selected,faceInfo('button:0'),deviceCache.get(selected)?.button_labels]);
  if(labels!==buttonSignature){buttonSignature=labels;refreshButtonBadges();}
}
function initialize() {
  applyConfig(state);
  $('command').replaceChildren(...Object.entries(state.actions).filter(([v])=>!['none','pan_left','pan_right','tilt_up','tilt_down','zoom_in','zoom_out','focus_near','focus_far'].includes(v)).map(([v,t])=>new Option(t,v)));
  for(let index=0;index<8;index++) {
    const range = element('input',{type:'range', min:-1, max:1, step:.01, value:0});
    range.oninput=()=> { axes[index]=Number(range.value); pushVirtual(); };
    range.dataset.axis=index;
    $('virtualAxes').append(field('Axis '+index,range));
  }
  for(let index=0;index<16;index++) addVirtualButton('B'+index, 'button:'+index);
  for(const dir of ['up','down','left','right']) addVirtualButton(dir, 'hat:0:'+dir);
}
function applyConfig(next) {
  state=next;runtime={...runtime,...next};config=structuredClone(next.config);dirty=false;
  for (const key of ['mode','host','port','framing','address']) $(key).value=config.camera[key];
  $('speed').value=config.speed; $('speedValue').textContent=Math.round(config.speed*100)+'%';
  $('controller').replaceChildren(new Option('Virtual controller','virtual'));
  if(config.controller!=='virtual') $('controller').add(new Option(config.controller,config.controller));
  $('controller').value=config.controller;
  $('controller').dataset.signature='';
  $('presetName').value=next.active_preset || '';
  $('connectionTest').hidden=true;
  renderMappings();
  renderPresets();
  globalThis.PTZDropdowns?.refresh();
}
function addVirtualButton(title,source) {
  const b=element('button',{textContent:title});
  b.onpointerdown=e=>{e.preventDefault();b.setPointerCapture(e.pointerId);pressed.add(source);b.classList.add('active');pushVirtual();};
  const release=()=>{pressed.delete(source);b.classList.remove('active');pushVirtual();};
  b.onpointerup=release;b.onpointercancel=release;b.onlostpointercapture=release;
  $('virtualButtons').append(b);
}
// Serialize input; coalesce slider changes, preserving button press/release order.
let virtualPending=[], virtualSending=false;
async function pushVirtual() {
  const next={axes:[...axes],pressed:[...pressed]}, tail=virtualPending.at(-1);
  if(tail && JSON.stringify(tail.pressed)===JSON.stringify(next.pressed))virtualPending[virtualPending.length-1]=next;
  else virtualPending.push(next);
  if(virtualSending)return;
  virtualSending=true;
  try {
    while(virtualPending.length)await api('virtual',virtualPending.shift());
  } catch(e) { notice(e.message); }
  finally { virtualSending=false; }
}
function center() { axes.fill(0);pressed.clear();document.querySelectorAll('[data-axis]').forEach(el=>el.value=0);document.querySelectorAll('.active').forEach(el=>el.classList.remove('active'));pushVirtual(); }
function collectConfig() {
  const next=structuredClone(config);
  next.camera=cameraSettings();
  next.speed=Number($('speed').value);next.controller=$('controller').value;
  return next;
}
function cameraSettings() {
  return {mode:$('mode').value,host:$('host').value,port:Number($('port').value),
    framing:$('framing').value,address:Number($('address').value)};
}
let testingConnection=false;
function showConnectionTest(status,message) {
  $('connectionTest').hidden=false;$('connectionTest').className='connection-test test-'+status;
  setText('connectionTest',message);
}
$('testConnection').onclick=async()=>{
  if(testingConnection)return;
  testingConnection=true;
  const camera=cameraSettings(), key=JSON.stringify(camera);
  $('testConnection').disabled=true;setText('testConnection','Testing…');
  showConnectionTest('pending','Waiting for a camera reply…');
  try {
    const {result}=await api('test-connection',{camera});
    if(JSON.stringify(cameraSettings())!==key)showConnectionTest('pending','Connection settings changed. Test again to verify the displayed settings.');
    else showConnectionTest(result.status,result.message);
  } catch(e) { showConnectionTest('error',e.message); }
  finally { testingConnection=false;$('testConnection').disabled=false;setText('testConnection','Test Connection'); }
};
async function refreshState() {
  const response=await fetch('/api/state');if(!response.ok)throw Error('Status unavailable');
  state=await response.json();runtime={...runtime,...state};return state;
}
async function connectSetup() {
  if(connecting)return;
  connecting=true;
  try {
    await cancelLearning();
    const next=collectConfig();
    if(runtime.connected && runtime.armed && !dirty && !runtime.error)return;
    else if(runtime.connected && !dirty && !runtime.error)await api('arm');
    else await api('connect',{config:next});
    await refreshState();dirty=false;
  } finally { connecting=false;renderStatus(runtime);renderPresets(); }
}
$('connect').onclick=run(connectSetup);
$('save').onclick=run(async()=>{
  await cancelLearning();
  const result=await api('preset-save',{name:$('presetName').value,config:collectConfig()});
  applyConfig(result.state);notice('Preset saved: '+result.state.active_preset);
});
async function usePreset(name) {
  if(connecting)return;
  const reconnect=connectionReady(runtime) && !timeline.sample(performance.now())?.stale;
  connecting=true;
  try {
    await cancelLearning();
    const result=await api('preset-load',{name});applyConfig(result.state);
    center();
    if(reconnect)await api('connect');
    await refreshState();
  } catch(e) { await refreshState();throw e; }
  finally { connecting=false;renderStatus(runtime);renderPresets(); }
}
function renderPresets() {
  const presets=state?.presets || [], active=runtime.active_preset;
  const signature=JSON.stringify([presets,active,runtime.preset_modified,dirty,connecting,runtime.connected,runtime.confirmed,runtime.armed]);
  if(signature===presetSignature)return;presetSignature=signature;
  $('presets').replaceChildren();
  if(!presets.length)$('presets').append(element('p',{className:'muted',textContent:'No saved presets yet. Name this setup and choose Save as preset.'}));
  for(const p of presets) {
    const card=element('div',{className:'preset-card'+(p.name===active?' selected':'')});
    const load=element('button',{className:'preset-load',disabled:connecting});
    load.append(element('strong',{textContent:p.name}),element('span',{textContent:p.camera.mode==='simulator'?'Virtual camera':`${p.camera.host}:${p.camera.port} · ${p.camera.mode.toUpperCase()}`}));
    load.onclick=run(()=>usePreset(p.name));
    const remove=element('button',{className:'secondary',textContent:'Delete',disabled:connecting});
    remove.onclick=run(async()=>{const result=await api('preset-delete',{name:p.name});state=result.state;runtime={...runtime,...state};renderPresets();});
    card.append(load,remove);$('presets').append(card);
  }
}
async function cancelLearning() {
  const id=captureId;captureToken++;captureId=null;captureBinding=null;
  $('captureStatus').hidden=true;
  if(id!==null)await api('capture-cancel',{capture_id:id});
}
async function startLearning(binding) {
  if(captureBinding===binding && captureId!==null)return;
  await cancelLearning();
  const token=++captureToken;
  captureBinding=binding;$('captureStatus').hidden=false;
  setText('captureHint','Listening — press a button or D-pad direction. Release any held buttons first.');
  renderMappings();
  try {
    const result=await api('capture-start');
    if(token!==captureToken){await api('capture-cancel',{capture_id:result.capture_id});return;}
    captureId=result.capture_id;
  } catch(e) { await cancelLearning();renderMappings();throw e; }
}
function captureInput(capture) {
  if(!captureBinding || !capture || capture.id!==captureId || capture.listening)return;
  const binding=captureBinding;
  if(capture.source && config.bindings.some(b=>b!==binding && b.source===capture.source)) {
    captureId=null;notice(capture.source+' is already mapped. Press a different button.');
    startLearning(binding).catch(e=>notice(e.message));return;
  }
  if(capture.source){binding.source=capture.source;markDirty();notice('Assigned '+buttonName(capture.source)+(faceInfo(capture.source)?` (${capture.source})`:''));}
  captureBinding=null;captureId=null;$('captureStatus').hidden=true;renderMappings();
}
$('cancelCapture').onclick=run(async()=>{await cancelLearning();renderMappings();});
$('settings').ontoggle=()=>{if(!$('settings').open)cancelLearning().then(renderMappings).catch(e=>notice(e.message));};
$('showTraffic').onchange=()=>renderLogs();
function renderLogs() {
  const logs=(state?.logs || []).filter(l=>$('showTraffic').checked || !['TX','RX'].includes(l.kind))
    .map(l=>`${l.time} ${l.kind.padEnd(6)} ${l.message}`).join('\n');
  if($('logs').textContent!==logs) {
    const pinned=$('logs').scrollHeight-$('logs').scrollTop-$('logs').clientHeight<40;
    setText('logs',logs);if(pinned)$('logs').scrollTop=$('logs').scrollHeight;
  }
}
/* Config edits stay local until Connect or Save as preset. */
document.addEventListener('input',event=>{
  if(['mode','host','port','framing','address','speed'].includes(event.target.id))markDirty();
  if(['mode','host','port','framing','address'].includes(event.target.id))$('connectionTest').hidden=true;
});
$('controller').onchange=run(async()=>{
  const selected=$('controller').value;
  try {
    await cancelLearning();await api('controller',{controller:selected});config.controller=selected;markDirty();refreshButtonBadges();
    notice('Input monitor is live. Click Connect to enable motion.');
  } catch(e) { $('controller').value=config.controller;throw e; }
});
$('disconnect').onclick=run(async()=>{await cancelLearning();center();await api('disconnect');await refreshState();renderStatus(runtime);});
$('reset').onclick=run(()=>api('sequence-reset'));
$('send').onclick=run(()=>api('command',{action:$('command').value,param:$('param').value}));
$('speed').oninput=()=>$('speedValue').textContent=Math.round(Number($('speed').value)*100)+'%';
$('addAxis').onclick=()=>{const target=['pan','tilt','zoom','focus'].find(t=>!config.axes.some(a=>a.target===t));if(!target)return notice('All motion targets already have an axis.');config.axes.push({index:0,target,deadzone:.15,invert:false});markDirty();renderMappings();};
$('addBinding').onclick=run(async()=>{const binding={source:'',action:'none',param:0};config.bindings.push(binding);markDirty();await startLearning(binding);});
$('center').onclick=center;
window.addEventListener('blur',()=>{cancelLearning().catch(()=>{});center();api('stop').catch(()=>{});});
document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelLearning().catch(()=>{});center();api('stop').catch(()=>{});}});
async function poll() {
  if(polling)return;polling=true;
  try {
    await refreshState();
    if(!config)initialize();devices();
    renderStatus(state);
    renderPresets();renderLogs();
  } catch(e) { connectionStatus(false,'GUI connection lost'); }
  finally{polling=false;}
}
function renderStatus(current) {
    runtime={...runtime,...current};
    const state={...windowState(),...current};
    if(!state.config)return;
    const preset=state.active_preset || 'Custom setup';
    const modified=dirty || state.preset_modified;
    const ready=connectionReady(state);
    connectionStatus(ready,state.error || (state.connected && !ready?'Awaiting camera reply':state.armed?'Controller enabled':'Controller disarmed'));
    setText('connect',connecting?'Connecting…':'Connect');
    $('connect').disabled=connecting || (state.connected && state.armed && !dirty && !state.error);
    $('connect').className='primary';
    $('connect').title=state.connected && !state.armed?'Enable controller motion':'Apply setup and connect';
    $('disconnect').disabled=!state.connected && !connecting;
    setText('headerPreset',preset+(modified?' · modified':''));
    setText('activePreset',preset+(modified?' · modified':''));
    const selected=state.devices.find(d=>d.id===state.config.controller);
    const controllerName=state.config.controller==='virtual'?'Virtual controller':selected?.name || 'Disconnected controller';
    setText('inputHint',state.input_error ? 'Controller input error: '+state.input_error :
      controllerName+' · '+(!state.connected?'Input monitor only — click Connect to enable the camera or simulator.':
      !state.armed?'Input monitor only — click Connect to enable motion.':'Controls enabled — move a mapped axis or press a mapped button.')+
      (state.config.bindings.length ? '' : ' Buttons are unmapped. Add mappings below; analog axes have defaults.'));
    setText('backend',!state.connected?'No camera connection':state.config.camera.mode==='simulator'?
      'Virtual camera ready — no network connection required':
      `${state.config.camera.host}:${state.config.camera.port} · ${state.config.camera.mode.toUpperCase()}`);
}
function windowState() { return state || {}; }
function renderInput(input) {
  setText('input','Axes: '+input.axes.map((v,i)=>`${i}: ${v.toFixed(2)}`).join('  ')+' | Pressed: '+(input.pressed.map(buttonName).join(', ')||'none'));
  const signature=JSON.stringify(input.pressed.map(source=>[source,faceInfo(source)]));
  if(signature!==pressedSignature){pressedSignature=signature;$('pressedButtons').replaceChildren(...input.pressed.map(buttonBadge));}
  if(meterNodes.length!==input.axes.length) {
    meterNodes=input.axes.map(()=>{
      const label=element('label'), text=element('span'), meter=element('meter',{min:-1,max:1});
      label.append(text,meter);return {label,text,meter};
    });
    $('axisMonitor').replaceChildren(...meterNodes.map(n=>n.label));
  }
  input.axes.forEach((value,i)=>{
    const node=meterNodes[i], text=`Axis ${i}: ${value.toFixed(2)}`;
    if(node.meter.value!==value)node.meter.value=value;
    if(node.text.textContent!==text)node.text.textContent=text;
  });
}
let viewScale=0;
function resizeView() {
  const box=$('crosshair').parentElement.getBoundingClientRect();
  // Use one pixel scale for both axes, with room inside the shorter dimension.
  viewScale=Math.min(box.width,box.height)*.38;
}
new ResizeObserver(resizeView).observe($('crosshair').parentElement);
function animate(now) {
  const frame=timeline.sample(now);
  if(frame && !document.hidden) {
    renderInput(frame.input);
    const transform=`translate(-50%,-50%) translate3d(${frame.model.pan*viewScale}px,${frame.model.tilt*viewScale}px,0) scale(${1+frame.model.zoom*.5})`;
    if($('crosshair').style.transform!==transform)$('crosshair').style.transform=transform;
    if(now-lastModelText>=100) {
      setText('model',Object.entries(frame.model).map(([k,v])=>`${k}: ${v.toFixed(3)}`).join('\n'));
      lastModelText=now;
    }
    if(frame.stale)connectionStatus(false,'Live input connection lost; awaiting telemetry');
  }
  requestAnimationFrame(animate);
}
let heartbeatPending=false;
async function heartbeat() {
  if(document.hidden || heartbeatPending)return;
  heartbeatPending=true;
  try { await api('heartbeat'); } catch(e) { /* watchdog disarms on lost contact */ }
  finally { heartbeatPending=false; }
}
const live=new EventSource('/api/live');
live.onopen=()=>timeline.reset();
live.onmessage=event=>{
  const frame=JSON.parse(event.data);
  if(timeline.push(frame,performance.now())){captureInput(frame.capture);renderStatus(frame);}
};
poll();heartbeat();setInterval(poll,1000);setInterval(heartbeat,400);
requestAnimationFrame(animate);
