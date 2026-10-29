/* In-page select popups avoid native Qt popup focus/click inconsistencies. */
(function(root) {
  const instances=new WeakMap();
  let opened=null, nextId=0;
  class Dropdown {
    constructor(source) {
      this.source=source;
      this.wrapper=document.createElement('span');this.wrapper.className='select-control';
      this.trigger=document.createElement('button');this.trigger.type='button';this.trigger.className='select-trigger';
      this.trigger.setAttribute('role','combobox');this.trigger.setAttribute('aria-haspopup','listbox');
      this.trigger.setAttribute('aria-expanded','false');
      this.menu=document.createElement('div');this.menu.className='select-menu';this.menu.hidden=true;
      this.menu.id='select-menu-'+(++nextId);this.menu.setAttribute('role','listbox');
      this.trigger.setAttribute('aria-controls',this.menu.id);
      source.before(this.wrapper);this.wrapper.append(source,this.trigger);document.body.append(this.menu);
      source.classList.add('select-native');source.tabIndex=-1;source.setAttribute('aria-hidden','true');
      const label=source.closest('label');
      this.trigger.setAttribute('aria-label',label?.firstChild?.textContent?.trim() || 'Choose an option');
      this.trigger.addEventListener('click',()=>this.menu.hidden?this.open():this.close());
      this.trigger.addEventListener('keydown',event=>this.key(event));
      source.addEventListener('change',()=>this.sync());
      this.observer=new MutationObserver(()=>this.sync());
      this.observer.observe(source,{childList:true,subtree:true,attributes:true});
      this.sync();
    }
    sync() {
      const text=this.source.selectedOptions[0]?.textContent || 'Choose…';
      if(this.trigger.textContent!==text)this.trigger.textContent=text;
      this.trigger.disabled=this.source.disabled;
      if(!this.menu.hidden)this.build();
    }
    build() {
      this.menu.replaceChildren();this.items=[];
      for(const [index,option] of Array.from(this.source.options).entries()) {
        if(option.hidden)continue;
        const item=document.createElement('div');item.className='select-option';
        item.id=this.menu.id+'-'+index;item.textContent=option.textContent;
        item.setAttribute('role','option');item.setAttribute('aria-selected',String(index===this.source.selectedIndex));
        item.setAttribute('aria-disabled',String(option.disabled));
        item.addEventListener('click',()=>{if(!option.disabled)this.choose(index);});
        this.menu.append(item);this.items.push({index,item,disabled:option.disabled});
      }
      this.highlight(this.source.selectedIndex);
    }
    position() {
      const box=this.trigger.getBoundingClientRect();
      const below=innerHeight-box.bottom-8, above=box.top-8;
      const height=Math.min(280,Math.max(below,above));
      this.menu.style.width=Math.min(Math.max(box.width,220),innerWidth-16)+'px';
      this.menu.style.left=Math.max(8,Math.min(box.left,innerWidth-this.menu.offsetWidth-8))+'px';
      this.menu.style.maxHeight=Math.max(80,height)+'px';
      this.menu.style.top=(below>=Math.min(180,above)?box.bottom+4:Math.max(8,box.top-Math.min(this.menu.scrollHeight,height)-4))+'px';
    }
    open() {
      if(this.source.disabled)return;
      if(opened && opened!==this)opened.close();opened=this;
      this.menu.hidden=false;this.build();this.position();this.highlight(this.active);
      this.trigger.setAttribute('aria-expanded','true');
      this.trigger.focus();
    }
    close() {
      this.menu.hidden=true;this.trigger.setAttribute('aria-expanded','false');
      this.trigger.removeAttribute('aria-activedescendant');
      if(opened===this)opened=null;
    }
    highlight(index) {
      this.active=index;
      for(const row of this.items)row.item.classList.toggle('highlighted',row.index===index);
      const row=this.items.find(row=>row.index===index);
      if(row){
        this.trigger.setAttribute('aria-activedescendant',row.item.id);
        const top=row.item.offsetTop, bottom=top+row.item.offsetHeight;
        if(top<this.menu.scrollTop)this.menu.scrollTop=top;
        else if(bottom>this.menu.scrollTop+this.menu.clientHeight)this.menu.scrollTop=bottom-this.menu.clientHeight;
      }
    }
    choose(index) {
      const changed=this.source.selectedIndex!==index;
      this.source.selectedIndex=index;this.close();this.sync();this.trigger.focus();
      if(changed){this.source.dispatchEvent(new Event('input',{bubbles:true}));this.source.dispatchEvent(new Event('change',{bubbles:true}));}
    }
    key(event) {
      const key=event.key;
      if(key==='Tab'){this.close();return;}
      if(key==='Escape'){this.close();event.preventDefault();return;}
      if(['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(key)) {
        event.preventDefault();
        if(this.menu.hidden){this.open();return;}
        if(key==='Enter' || key===' '){if(this.items.some(i=>i.index===this.active&&!i.disabled))this.choose(this.active);return;}
        const items=this.items.filter(i=>!i.disabled), current=items.findIndex(i=>i.index===this.active);
        if(!items.length)return;
        const index=key==='Home'?0:key==='End'?items.length-1:Math.max(0,Math.min(items.length-1,current+(key==='ArrowUp'?-1:1)));
        this.highlight(items[index].index);
      } else if(key.length===1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        if(this.menu.hidden)this.open();
        const now=performance.now();this.search=now-(this.searchTime||0)<700?(this.search||'')+key:key;this.searchTime=now;
        const match=this.items.find(i=>!i.disabled&&i.item.textContent.toLowerCase().startsWith(this.search.toLowerCase()));
        if(match)this.highlight(match.index);
      }
    }
    destroy() { this.close();this.observer.disconnect();this.menu.remove(); }
  }
  function scan(node=document) {
    if(node.matches?.('select') && !instances.has(node))instances.set(node,new Dropdown(node));
    for(const source of node.querySelectorAll?.('select') || [])if(!instances.has(source))instances.set(source,new Dropdown(source));
  }
  function refresh() { scan();for(const source of document.querySelectorAll('select'))instances.get(source)?.sync(); }
  new MutationObserver(records=>{
    for(const record of records) {
      for(const node of record.addedNodes)if(node.nodeType===1)scan(node);
      for(const node of record.removedNodes) {
        const sources=[...(node.matches?.('select')?[node]:[]),...(node.querySelectorAll?.('select') || [])];
        for(const source of sources)if(!source.isConnected){instances.get(source)?.destroy();instances.delete(source);}
      }
    }
  }).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('click',event=>{if(opened&&!opened.wrapper.contains(event.target)&&!opened.menu.contains(event.target))opened.close();});
  document.addEventListener('scroll',event=>{if(opened&&!opened.menu.contains(event.target))opened.position();},true);
  window.addEventListener('resize',()=>opened?.position());
  window.addEventListener('blur',()=>opened?.close());
  root.PTZDropdowns={refresh};refresh();
})(globalThis);
