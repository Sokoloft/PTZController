// Small timestamped buffer: interpolate measurements; never predict movement.
(function(root) {
  class Timeline {
    constructor(delay = 35) { this.delay=delay; this.reset(); }
    reset() { this.frames=[];this.offset=null;this.sequence=-1;this.lastReceipt=-Infinity; }
    push(frame, now) {
      if(frame.sequence<=this.sequence)return false;
      if(this.frames.length && frame.generation!==this.frames.at(-1).generation) this.frames=[];
      this.sequence=frame.sequence;
      this.offset=Math.min(this.offset ?? Infinity,now-frame.sample_time*1000);
      this.lastReceipt=now;
      this.frames.push(frame);
      while(this.frames.length>12)this.frames.shift();
      return true;
    }
    sample(now) {
      if(!this.frames.length)return null;
      const latest=this.frames.at(-1), time=now-this.offset-this.delay;
      let a=this.frames[0], b=a;
      for(const frame of this.frames) {
        b=frame;
        if(frame.sample_time*1000>=time)break;
        a=frame;
      }
      const span=(b.sample_time-a.sample_time)*1000;
      const t=span>0?Math.max(0,Math.min(1,(time-a.sample_time*1000)/span)):1;
      const lerp=(x,y)=>x+(y-x)*t;
      const model=Object.fromEntries(Object.keys(latest.model).map(k=>[k,lerp(a.model[k],b.model[k])]));
      const axes=a.input.axes.length===b.input.axes.length ? a.input.axes.map((v,i)=>lerp(v,b.input.axes[i])) : latest.input.axes;
      return {...latest,model,input:{axes,pressed:latest.input.pressed},stale:now-this.lastReceipt>250};
    }
  }
  root.PTZLive={Timeline};
  if(typeof module!=='undefined')module.exports={Timeline};
})(globalThis);
