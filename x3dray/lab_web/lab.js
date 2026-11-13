(() => {
  'use strict';
  const B = JSON.parse(document.getElementById('lab-data').textContent);
  const $ = id => document.getElementById(id);
  const esc = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const C = {ingest:'#7cd6d6',bridge:'#b9efce',render:'#eea88b',hub:'#d8b1ef',tests:'#88a9c9',docs:'#a9bbc5',generated:'#718d9a'};
  const state = {cutoff:15,distance:'count',prune:true,selected:'evidence.py',edge:null,removed:null,witness:null,view:'map',tour:-1};
  const commits = new Map(B.commits.map(c=>[c.id,c]));
  let browser = null, sceneGeneration = 0, rendererPromise = null;
  let latestX3D = '', lastScenario = null;
  const steps = [
    {title:'01 · Find the seductive hub',text:'README.md lies on many shortest paths because routine commits repeatedly touch it. Its score describes coordination history; it does not establish a runtime dependency.',cutoff:15,distance:'count',prune:false,selected:'README.md',removed:null},
    {title:'02 · Separate a hub from a separator',text:'Set the README hub aside. evidence.py is now an articulation between ingestion and presentation. Other cut vertices may connect leaf tests: inspect which populations each separates.',cutoff:15,distance:'count',prune:true,selected:'evidence.py',removed:null},
    {title:'03 · Change the distance only',text:'Jaccard accounts for how often either endpoint changes. Compare this result with count under the same pruning and threshold. A changed ranking is a changed lens, not an additional observation.',cutoff:15,distance:'jaccard',prune:true,selected:'evidence.py',removed:null},
    {title:'04 · Let the generated sweep enter',text:'The 16-file catalog sweep now contributes a clique. Many new paths appear at once. The extra connectivity reflects one mass edit; inspect lab-057 before inferring architectural integration.',cutoff:30,distance:'jaccard',prune:true,selected:'evidence.py',removed:null},
    {title:'05 · Ask a graph counterfactual',text:'Return to the narrower scope and remove evidence.py. Scores and components are recomputed from that graph. The result describes observed co-change connectivity, not whether a real program would fail.',cutoff:15,distance:'count',prune:true,selected:'snapshot.py',removed:'evidence.py'},
    {title:'06 · Keep a replayable explanation',text:'Restore the observation lens. Save this investigation to preserve its fixture digest, parameters and selected witness. Replay refuses a different specimen rather than silently changing the meaning.',cutoff:15,distance:'count',prune:true,selected:'evidence.py',removed:null}
  ];
  // Derive the sweep label rather than assuming its fixture ordinal.
  steps[3].text=steps[3].text.replace('lab-057',B.commits.find(c=>c.kind==='generated_sweep').id);
  function key(){return `${state.cutoff}:${state.distance}:${+state.prune}`;}
  function scenario(){const p=B.scenarios[key()];return state.removed ? (p.removals[state.removed]||p.baseline) : p.baseline;}
  function number(v,n=2){return Number(v).toLocaleString(undefined,{maximumFractionDigits:n});}
  function setLens(values){Object.assign(state,values);state.edge=null;state.witness=null;if(state.removed&&!B.scenarios[key()].removals[state.removed])state.removed=null;render();}
  function select(id){state.selected=id;state.edge=null;state.witness=null;render();}
  function render(){
    const t0=performance.now(),s=scenario();lastScenario=s;
    if(!s.nodes.some(n=>n.id===state.selected))state.selected=(s.nodes[0]||{}).id||null;
    $('cutoff').value=String(state.cutoff);$('distance').value=state.distance;$('prune').checked=state.prune;
    $('distanceHelp').textContent=state.distance==='count'?'More co-changes make endpoints closer. Distances are exact rational reciprocals.':'Shared commits divided by either file’s commits measures similarity; distance is its reciprocal.';
    $('nodes').textContent=s.nodes.length;$('edges').textContent=s.edges.length;$('components').textContent=s.components.length;
    $('included').textContent=s.included_commits;$('excluded').textContent=s.excluded_commits.length;
    $('lensTitle').textContent=state.removed?`Graph without ${state.removed}`:`${state.distance==='count'?'Count':'Jaccard'} · ${state.prune?'hub set aside':'all observed files'}`;
    $('remove').disabled=!state.selected||!!state.removed;$('restore').hidden=!state.removed;
    $('scope').textContent=`Fixture ${B.fixture_digest.slice(0,12)} · ${B.commits.length} observations · ${state.removed?'counterfactual topology':'observed topology'}`;
    drawGraph(s);drawDetails(s);drawMatrix();drawWitnesses(s);
    $('updateTime').textContent=`UI ${number(performance.now()-t0,1)} ms`;
    if(state.view==='3d')update3D(s);
    window.__bridgeLab={state:{...state},scenario:s,fixtureDigest:B.fixture_digest,analysisDigest:B.analysis_digest,sceneReady:!!browser,exportInvestigation,replay};
  }
  function drawGraph(s){
    const byId=new Map(s.nodes.map(n=>[n.id,n]));
    const neighbours=new Set([state.selected]);s.edges.forEach(e=>{if(e.a===state.selected)neighbours.add(e.b);if(e.b===state.selected)neighbours.add(e.a);});
    const witnessed=state.witness?new Set(commits.get(state.witness).paths):null;
    let h='<defs><radialGradient id="nodeGlow"><stop stop-color="#8fe6d1" stop-opacity=".2"/><stop offset="1" stop-color="#8fe6d1" stop-opacity="0"/></radialGradient><pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" class="grid-line"/></pattern></defs><rect width="1000" height="590" fill="url(#grid)"/>';
    h+='<ellipse cx="220" cy="340" rx="180" ry="205" fill="#245568" opacity=".08"/><ellipse cx="795" cy="340" rx="185" ry="205" fill="#9b6756" opacity=".07"/><path d="M415 60V530M590 60V530" stroke="#35525e" stroke-dasharray="3 8" opacity=".3"/>';
    for(const e of s.edges){const a=byId.get(e.a),b=byId.get(e.b),active=e.a===state.selected||e.b===state.selected,hit=state.edge&&state.edge.a===e.a&&state.edge.b===e.b;
      const w=1+Math.min(4,Math.sqrt(e.count)),isWitness=witnessed&&e.witnesses.includes(state.witness);
      h+=`<path class="edge" data-a="${esc(e.a)}" data-b="${esc(e.b)}" d="M${a.pos[0]},${a.pos[1]} Q${(a.pos[0]+b.pos[0])/2},${(a.pos[1]+b.pos[1])/2-18} ${b.pos[0]},${b.pos[1]}" fill="none" stroke="${hit?'#ffffff':isWitness?'#f2d096':active?'#8acec5':'#517484'}" stroke-width="${hit?w+2:w}" opacity="${witnessed?(isWitness?.95:.08):(active?.72:.17)}"><title>${esc(e.a)} ↔ ${esc(e.b)} · ${e.count} witnesses</title></path>`;
    }
    for(const n of s.nodes){const selected=n.id===state.selected,r=8+20*Math.sqrt(n.share),colour=C[n.group],muted=witnessed?!witnessed.has(n.id):!neighbours.has(n.id);
      h+=`<g class="node ${muted?'is-muted':''} ${witnessed&&witnessed.has(n.id)?'witness':''}" data-id="${esc(n.id)}" role="button" tabindex="0" aria-label="${esc(n.id)}${n.cut?', cut vertex':''}" transform="translate(${n.pos[0]} ${n.pos[1]})"><circle r="${r+38}" fill="url(#nodeGlow)" opacity="${selected?1:.45}"/><circle class="halo" r="${r+7}" stroke="${selected?'#fff':n.cut?'#efc47b':colour}" opacity="${selected?1:.65}" ${n.cut?'stroke-dasharray="3 3"':''}/><circle class="body" r="${r}" fill="${colour}" stroke="#d3f4e8" stroke-width="${selected?2:.4}"/><circle r="${Math.max(2,r*.25)}" fill="#fff" opacity=".5" cx="${-r*.23}" cy="${-r*.23}"/><text text-anchor="middle" y="${r+27}">${esc(n.id)}</text></g>`;
    }
    $('graph').innerHTML=h;
    $('graph').querySelectorAll('.node').forEach(n=>{const run=()=>select(n.dataset.id);n.onclick=run;n.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();run();}};});
    $('graph').querySelectorAll('.edge').forEach(e=>e.onclick=()=>{state.edge=s.edges.find(x=>x.a===e.dataset.a&&x.b===e.dataset.b);state.witness=null;drawDetails(s);drawWitnesses(s);drawGraph(s);});
  }
  function drawDetails(s){
    const node=s.nodes.find(n=>n.id===state.selected),e=state.edge;
    $('selectedKind').textContent=e?'SELECTED RELATION / CO-CHANGE':'SELECTED FILE';
    if(e){$('selected').textContent=`${e.a} ↔ ${e.b}`;$('claim').innerHTML=`<strong>${e.count} synthetic observations</strong> changed both files. This is an undirected co-change relation.`;$('details').innerHTML=`<dt>distance</dt><dd>${esc(e.distance)}</dd><dt>source witnesses</dt><dd>${e.witnesses.length}</dd>`;}
    else if(node){$('selected').textContent=node.id;$('claim').innerHTML=node.cut?'<strong>A cut vertex in this lens.</strong> Removing this node increases the graph’s component count. Inspect the removal experiment to learn which parts separate.':'<strong>Not a cut vertex in this lens.</strong> Its betweenness can still be positive: shortest-path concentration does not require every path to cross it.';
      $('details').innerHTML=`<dt>betweenness</dt><dd>${number(node.betweenness)}</dd><dt>share of component pairs</dt><dd>${number(node.share*100)}%</dd><dt>component size</dt><dd>${node.component}</dd><dt>degree</dt><dd>${node.degree}</dd><dt>cut vertex</dt><dd>${node.cut?'yes':'no'}</dd><dt>usable source commits</dt><dd>${node.witnesses.length}</dd>`;}
    else{$('selected').textContent='No observed nodes';$('claim').textContent='This lens has no nodes.';$('details').innerHTML='';}
    const top=s.nodes.slice(0,6),max=Math.max(...top.map(n=>n.betweenness),1);
    $('ranking').innerHTML=top.map(n=>`<div class="rank" role="button" tabindex="0" data-id="${esc(n.id)}" aria-pressed="${n.id===state.selected}"><span>${esc(n.id)}</span><b>${number(n.betweenness,1)}</b><i style="width:${100*n.betweenness/max}%"></i></div>`).join('');
    $('ranking').querySelectorAll('.rank').forEach(n=>{n.onclick=()=>select(n.dataset.id);n.onkeydown=e=>{if(e.key==='Enter'){select(n.dataset.id);}};});
  }
  function drawMatrix(){
    let h='';for(const distance of ['count','jaccard'])for(const prune of [false,true]){const s=B.scenarios[`${state.cutoff}:${distance}:${+prune}`].baseline,top=s.nodes[0];h+=`<button data-distance="${distance}" data-prune="${+prune}" aria-pressed="${distance===state.distance&&prune===state.prune&&!state.removed}">${distance==='count'?'Count':'Jaccard'}<small>${prune?'hub set aside':'all observed files'}</small><b>${top?esc(top.id):'no nodes'}</b></button>`;}
    $('matrix').innerHTML=h;$('matrix').querySelectorAll('button').forEach(b=>b.onclick=()=>setLens({distance:b.dataset.distance,prune:!!+b.dataset.prune,removed:null}));
  }
  function drawWitnesses(s){
    const n=s.nodes.find(n=>n.id===state.selected),ids=state.edge?state.edge.witnesses:(n?n.witnesses:[]);
    $('witnessTitle').textContent=state.edge?'Observations supporting this edge':`Commits touching ${state.selected||'the selection'}`;
    $('witnessSummary').textContent=`${ids.length} witnesses · ${s.excluded_commits.length} mass edit${s.excluded_commits.length===1?'':'s'} set aside`;
    $('witnesses').innerHTML=ids.map(id=>`<button data-id="${id}" data-who="${commits.get(id).who}" aria-pressed="${state.witness===id}">${id} · ${commits.get(id).paths.length} files</button>`).join('');
    $('witnesses').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.witness=state.witness===b.dataset.id?null:b.dataset.id;drawWitnesses(s);drawGraph(s);if(state.view==='3d')update3D(s);});
    const c=commits.get(state.witness);$('commitDetail').innerHTML=c?`<b>${esc(c.id)} · ${esc(c.subject)}</b><br>${c.paths.map(esc).join(' · ')}<br><small>Recorded actor: ${esc(c.who)} · synthetic observation</small>`:'Select a witness to illuminate the files it changed. Every listed record is available; this strip is horizontally scrollable.';
  }
  function download(name,text,type){const u=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
  function exportInvestigation(){return {schema_version:'1',fixture_digest:B.fixture_digest,analysis_digest:B.analysis_digest,synthetic:true,lens:{cutoff:state.cutoff,distance:state.distance,prune:state.prune,removed:state.removed},selected:state.selected,edge:state.edge?{a:state.edge.a,b:state.edge.b}:null,witness:state.witness,tour_step:state.tour};}
  function replay(raw){
    if(!raw||raw.schema_version!=='1'||raw.fixture_digest!==B.fixture_digest||raw.analysis_digest!==B.analysis_digest||raw.synthetic!==true)throw Error('This investigation belongs to a different specimen or analytical result set.');
    const q=raw.lens;if(!q||![5,15,30].includes(q.cutoff)||!['count','jaccard'].includes(q.distance)||typeof q.prune!=='boolean')throw Error('Invalid analytical lens.');
    const p=B.scenarios[`${q.cutoff}:${q.distance}:${+q.prune}`];if(q.removed!==null&&!Object.hasOwn(p.removals,q.removed))throw Error('Invalid removal experiment.');
    const s=q.removed?p.removals[q.removed]:p.baseline;if(raw.selected!==null&&!s.nodes.some(n=>n.id===raw.selected))throw Error('Selected node is absent in this lens.');
    let edge=null;if(raw.edge!==null){if(!raw.edge||typeof raw.edge!=='object')throw Error('Invalid selected relation.');edge=s.edges.find(e=>e.a===raw.edge.a&&e.b===raw.edge.b);if(!edge)throw Error('Selected relation is absent in this lens.');}
    const allowed=edge?edge.witnesses:((s.nodes.find(n=>n.id===raw.selected)||{}).witnesses||[]);
    if(raw.witness!==null&&(!commits.has(raw.witness)||!allowed.includes(raw.witness)))throw Error('Witness is outside the selected observation scope.');
    Object.assign(state,{cutoff:q.cutoff,distance:q.distance,prune:q.prune,removed:q.removed,selected:raw.selected,witness:raw.witness,edge,tour:-1});render();$('message').textContent='Investigation replayed against the matching fixture and analytical results.';
  }
  function tour(i){state.tour=i;if(i>=steps.length){state.tour=-1;$('next').hidden=true;return;}const step=steps[i];setLens(step);$('lesson').innerHTML=`<b>${esc(step.title)}</b>${esc(step.text)}`;$('next').hidden=false;$('next').textContent=i===steps.length-1?'Finish investigation ✓':'Next observation →';}
  $('cutoff').onchange=e=>setLens({cutoff:+e.target.value,removed:null});$('distance').onchange=e=>setLens({distance:e.target.value});$('prune').onchange=e=>setLens({prune:e.target.checked,removed:null});
  $('remove').onclick=()=>{if(state.selected)setLens({removed:state.selected});};$('restore').onclick=()=>setLens({removed:null});
  $('tour').onclick=()=>tour(0);$('next').onclick=()=>tour(state.tour+1);
  $('save').onclick=()=>download('xray-investigation.json',JSON.stringify(exportInvestigation(),null,2),'application/json');
  $('load').onchange=async e=>{const f=e.target.files[0];try{if(f){if(f.size>65536)throw Error('Investigation file exceeds 64 KB.');replay(JSON.parse(await f.text()));}}catch(err){$('message').textContent=err.message;}finally{e.target.value='';}};
  const attr=s=>esc(s);const fmt=n=>Number(n).toFixed(5).replace(/\.?0+$/,'')||'0';
  function x3d(s){
    const pos=n=>[(n.pos[0]-500)/48,.5+Math.sqrt(n.share)*2.5,(n.pos[1]-300)/48],byId=new Map(s.nodes.map(n=>[n.id,n]));
    const colour=hex=>hex.match(/\w\w/g).map(v=>parseInt(v,16)/255);const rgb=(hex,k=1)=>colour(hex.replace('#','')).map(v=>fmt(v*k)).join(' ');
    let h=`<?xml version="1.0" encoding="UTF-8"?><X3D profile="Immersive" version="4.0"><head><meta name="title" content="Xray synthetic bridge laboratory"/><meta name="fixture-digest" content="${B.fixture_digest}"/><meta name="analysis-digest" content="${B.analysis_digest}"/><meta name="scope" content="Synthetic co-change observations; graph counterfactual is not runtime prediction"/></head><Scene><WorldInfo title="Synthetic bridge laboratory"/><Background skyColor="0.025 0.06 0.09"/><NavigationInfo type='"EXAMINE" "ANY"' headlight="false"/><Viewpoint DEF="Overview" position="0 17 22" orientation="1 0 0 -0.65" centerOfRotation="0 0 0" fieldOfView="0.8" description="Inspect the synthetic graph"/><DirectionalLight direction="-0.4 -1 -0.3" color="0.65 0.9 1" intensity="0.85"/><DirectionalLight direction="0.6 -0.2 0.5" color="1 0.65 0.4" intensity="0.65"/><PointLight location="0 7 0" color="0.5 1 0.85" intensity="0.7" radius="35"/>`;
    h+='<Transform translation="0 -0.17 0"><Shape><Appearance><Material diffuseColor="0.035 0.075 0.10" specularColor="0.08 0.15 0.17" shininess="0.4"/></Appearance><Box size="22 0.2 15"/></Shape></Transform>';
    let grid=[];for(let i=-10;i<=10;i+=2)grid.push([i,0,-7,i,0,7]);for(let z=-6;z<=6;z+=2)grid.push([-11,0,z,11,0,z]);
    h+=`<Shape><Appearance><Material emissiveColor="0.08 0.16 0.21"/></Appearance><IndexedLineSet coordIndex="${grid.map((_,i)=>`${2*i} ${2*i+1} -1`).join(' ')}"><Coordinate point="${grid.map(v=>v.join(' ')).join(', ')}"/></IndexedLineSet></Shape>`;
    for(const e of s.edges){const a=pos(byId.get(e.a)),b=pos(byId.get(e.b)),points=[];for(let j=0;j<=16;j++){const t=j/16;points.push(a.map((v,k)=>v+(b[k]-v)*t+(k===1?Math.sin(t*Math.PI)*.5:0)).map(fmt).join(' '));}const active=e.a===state.selected||e.b===state.selected;
      h+=`<Shape><MetadataSet name="relation"><MetadataString containerField="value" name="endpoints" value='"${attr(e.a)}" "${attr(e.b)}"'/><MetadataString containerField="value" name="witnesses" value='${e.witnesses.map(id=>'"'+id+'"').join(' ')}'/></MetadataSet><Appearance><Material emissiveColor="${active?'0.45 0.8 0.72':'0.12 0.27 0.34'}" transparency="${active?'0.05':'0.4'}"/><LineProperties linewidthScaleFactor="${1+Math.sqrt(e.count)}"/></Appearance><IndexedLineSet coordIndex="${points.map((_,i)=>i).join(' ')} -1"><Coordinate point="${points.join(', ')}"/></IndexedLineSet></Shape>`;
    }
    for(const [i,n] of s.nodes.entries()){const p=pos(n),r=.14+Math.sqrt(n.share)*.48,selected=n.id===state.selected,hex=selected?'#e5fff1':C[n.group];
      h+=`<Transform DEF="Node_${i}" translation="${p.map(fmt).join(' ')}"><MetadataString name="entity-id" value='"${attr(n.id)}"'/><TouchSensor DEF="Pick_${i}" description="${attr(n.id)}"/><Shape><Appearance><Material diffuseColor="${rgb(hex,.65)}" emissiveColor="${rgb(hex,.18)}" specularColor="0.8 0.95 1" shininess="0.9"/></Appearance><Sphere radius="${fmt(r)}"/></Shape>`;
      if(n.cut){const points=[];for(let j=0;j<=48;j++){const a=2*Math.PI*j/48;points.push(`${fmt(Math.cos(a)*(r+.14))} 0 ${fmt(Math.sin(a)*(r+.14))}`);}h+=`<Shape><Appearance><Material emissiveColor="0.94 0.72 0.36"/><LineProperties linewidthScaleFactor="2"/></Appearance><IndexedLineSet coordIndex="${points.map((_,j)=>j).join(' ')} -1"><Coordinate point="${points.join(', ')}"/></IndexedLineSet></Shape>`;}
      h+='</Transform>';
    }
    return h+'</Scene></X3D>';
  }
  async function loadRenderer(){
    if(window.X3D)return;
    if(rendererPromise)return rendererPromise;
    const add=src=>new Promise((resolve,reject)=>{const sc=document.createElement('script');sc.src=src;sc.onload=resolve;sc.onerror=reject;document.head.append(sc);});
    rendererPromise=(async()=>{let base='x_ite/';try{await add(base+'x_ite.min.js');}catch{base='https://cdn.jsdelivr.net/npm/x_ite@16.7.0/dist/';await add(base+'x_ite.min.js');}const css=document.createElement('link');css.rel='stylesheet';css.href=base+'x_ite.css';document.head.append(css);})();
    return rendererPromise;
  }
  async function update3D(s){
    const generation=++sceneGeneration;$('runtime').hidden=false;$('runtime').textContent='Preparing the evidence-linked X3D scene…';
    try{await loadRenderer();const el=$('xscene');const deadline=performance.now()+15000;while(!el.browser){if(performance.now()>deadline)throw Error('X_ITE did not initialize');await new Promise(r=>setTimeout(r,30));}
      if(generation!==sceneGeneration||state.view!=='3d')return;
      browser=el.browser;latestX3D=x3d(s);const scene=await browser.createX3DFromString(latestX3D);
      if(generation!==sceneGeneration||state.view!=='3d')return;
      await browser.replaceWorld(scene);
      s.nodes.forEach((n,i)=>{try{scene.getNamedNode('Pick_'+i).addFieldCallback('lab','touchTime',()=>select(n.id));}catch{}});
      if(generation===sceneGeneration){$('runtime').hidden=true;window.__bridgeLab.sceneReady=true;}
    }catch(err){if(generation!==sceneGeneration)return;$('runtime').textContent='3D unavailable: '+err.message+'. The map, scores and witnesses remain available.';}
  }
  $('twoD').onclick=()=>{state.view='map';++sceneGeneration;$('graph').hidden=false;$('xscene').hidden=true;$('runtime').hidden=true;$('twoD').setAttribute('aria-pressed','true');$('threeD').setAttribute('aria-pressed','false');};
  $('threeD').onclick=()=>{state.view='3d';$('graph').hidden=true;$('xscene').hidden=false;$('twoD').setAttribute('aria-pressed','false');$('threeD').setAttribute('aria-pressed','true');update3D(lastScenario);};
  $('exportX3D').onclick=()=>download('xray-bridge-laboratory.x3d',x3d(scenario()),'model/x3d+xml');
  $('digest').textContent='FIXTURE '+B.fixture_digest.slice(0,12);$('lesson').innerHTML='<b>A specimen you can interrogate.</b>Start the guided investigation, or choose a file and change one parameter. Select an edge to see the exact observations behind it.';
  render();
})();
