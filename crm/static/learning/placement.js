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
  const api={place,overlap,rect};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AveronLearningPlacement=api;
}(this));
