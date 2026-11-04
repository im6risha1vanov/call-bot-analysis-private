/* Pure geometry shared by every lesson. No DOM, data, navigation or API calls. */
(function(root){
  'use strict';
  const clamp=(v,min,max)=>Math.max(min,Math.min(Math.max(min,max),v));
  const rect=r=>({left:r.left,top:r.top,right:r.right??r.left+r.width,bottom:r.bottom??r.top+r.height,width:r.width??r.right-r.left,height:r.height??r.bottom-r.top});
  const overlap=(a,b,gap=0)=>a.left<b.right+gap&&a.right>b.left-gap&&a.top<b.bottom+gap&&a.bottom>b.top-gap;
  function place({target,card,viewport,obstacles=[],narrow=false}){
    const v=rect(viewport),margin=10,gap=14;
    const width=Math.min(card.width,Math.max(1,v.width-2*margin)),height=Math.min(card.height,Math.max(1,v.height-2*margin));
    const t=target?rect(target):null;
    const blocks=obstacles.map(rect).filter(r=>r.width>0&&r.height>0&&overlap(r,v));
    const visible=t&&t.width>0&&t.height>0&&overlap(t,v);
    const candidates=[];
    function candidate(side,left,top){
      left=clamp(left,v.left+margin,v.right-width-margin);top=clamp(top,v.top+margin,v.bottom-height-margin);
      const box={left,top,width,height,right:left+width,bottom:top+height};
      if(visible&&overlap(box,t,6))return;
      if(blocks.some(r=>overlap(box,r,5)))return;
      if(side==='right'&&box.left<t.right+6)return;
      if(side==='left'&&box.right>t.left-6)return;
      if(side==='bottom'&&box.top<t.bottom+6)return;
      if(side==='top'&&box.bottom>t.top-6)return;
      candidates.push({...box,side,detached:!visible});
    }
    if(visible){
      const ys=[t.top,t.bottom+gap,t.top-height-gap,...blocks.flatMap(r=>[r.bottom+gap,r.top-height-gap])];
      // Side priority is intentional: right first on desktop; avoid real controls.
      if(!narrow){for(const y of ys)candidate('right',t.right+gap,y);for(const y of ys)candidate('left',t.left-width-gap,y);}
      const xs=[t.left,t.right-width,v.right-width-margin,v.left+margin];
      for(const x of xs)candidate('bottom',x,t.bottom+gap);
      for(const x of xs)candidate('top',x,t.top-height-gap);
    }
    // Missing/off-screen elements: a compact recoverable status, no phantom outline.
    for(const y of [v.bottom-height-margin,v.top+margin,...blocks.flatMap(r=>[r.bottom+gap,r.top-height-gap])]){
      candidate('dock',v.right-width-margin,y);candidate('dock',v.left+margin,y);
    }
    if(!candidates.length)return {blocked:true,width,height}; // collapse/retry, never cover the target
    const result=candidates[0];
    if(result.side==='dock')result.detached=true;
    result.arrowX=visible?clamp((t.left+t.right)/2-result.left,18,width-18):width/2;
    result.arrowY=visible?clamp((t.top+t.bottom)/2-result.top,18,height-18):height/2;
    return result;
  }
  // A user-chosen position remains near the drop point, inside the visual
  // viewport and clear of the target/controls. No DOM or operational actions.
  function move({position,card,viewport,target,obstacles=[],dragging=false}){
    const v=rect(viewport),margin=10,gap=7;
    const width=Math.min(card.width,Math.max(1,v.width-2*margin)),height=Math.min(card.height,Math.max(1,v.height-2*margin));
    const t=target?rect(target):null,visible=t&&t.width>0&&t.height>0&&overlap(t,v);
    const blocks=[...(visible?[t]:[]),...obstacles.map(rect)].filter(r=>r.width>0&&r.height>0&&overlap(r,v));
    const x=clamp(position.left,v.left+margin,v.right-width-margin),y=clamp(position.top,v.top+margin,v.bottom-height-margin);
    const candidates=[];
    function add(left,top){
      left=clamp(left,v.left+margin,v.right-width-margin);top=clamp(top,v.top+margin,v.bottom-height-margin);
      const r={left,top,width,height,right:left+width,bottom:top+height};
      if(!dragging&&blocks.some(b=>overlap(r,b,5)))return;
      candidates.push(r);
    }
    add(x,y);
    if(!dragging){
      for(const b of blocks){
        for(const left of [b.left-width-gap,b.right+gap]){add(left,y);for(const top of [b.top-height-gap,b.bottom+gap])add(left,top);}
        for(const top of [b.top-height-gap,b.bottom+gap])add(x,top);
      }
      for(const left of [v.left+margin,v.right-width-margin])for(const top of [v.top+margin,v.bottom-height-margin])add(left,top);
    }
    if(!candidates.length)return {blocked:true,width,height};
    candidates.sort((a,b)=>(a.left-x)**2+(a.top-y)**2-((b.left-x)**2+(b.top-y)**2));
    const result=candidates[0];result.side='dock';result.detached=true;
    if(visible&&!dragging){
      if(result.left>=t.right+5)result.side='right';else if(result.right<=t.left-5)result.side='left';
      else if(result.top>=t.bottom+5)result.side='bottom';else if(result.bottom<=t.top-5)result.side='top';
      result.detached=result.side==='dock';
    }
    result.arrowX=visible?clamp((t.left+t.right)/2-result.left,18,width-18):width/2;
    result.arrowY=visible?clamp((t.top+t.bottom)/2-result.top,18,height-18):height/2;
    return result;
  }
  const api={place,move,overlap,rect};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AveronLearningPlacement=api;
}(this));
