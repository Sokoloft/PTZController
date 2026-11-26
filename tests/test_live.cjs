const assert=require('node:assert/strict');
const {Timeline}=require('../src/ptz_controller/static/live.js');
function frame(sequence, time, pan, generation=0) {
  return {sequence,sample_time:time,generation,model:{pan,tilt:0,zoom:0,focus:0},input:{axes:[pan],pressed:[]},armed:true};
}
const line=new Timeline(35);
line.push(frame(1,1,0),1000);
line.push(frame(2,1.02,.2),1020);
assert(Math.abs(line.sample(1045).model.pan-.1)<1e-10); // between received frames
assert(Math.abs(line.sample(1045).input.axes[0]-.1)<1e-10);
assert(!line.push(frame(1,1,999),1046)); // late/duplicate packets never rewind
assert.equal(line.sample(2000).model.pan,.2); // no continued motion on lost telemetry
assert(line.sample(2000).stale);
line.push(frame(3,1.04,-.5,1),1040); // home/preset/stop must not glide through old positions
assert.equal(line.sample(1040).model.pan,-.5);
line.reset();
assert.equal(line.sample(3000),null);
// Sixty telemetry samples yield distinct intermediate positions on 120 Hz displays.
for(let i=0;i<60;i++)line.push(frame(i+1,i/60,i/60),i*1000/60);
const values=[];
for(let now=900;now<970;now+=1000/120)values.push(line.sample(now).model.pan);
assert(values.every((v,i)=>i===0 || v>values[i-1]));
console.log('PASS: timestamp interpolation, intermediate frames, stale-frame rejection, no extrapolation, discontinuity reset');
