(() => {
  'use strict';
  const B=JSON.parse(document.getElementById('proof-data').textContent),$=id=>document.getElementById(id);
  const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone=x=>JSON.parse(JSON.stringify(x));
  const eventEngine=window.XrayInvestigation,tool=window.XraySharedTools.makeTools(B.tool_bundle).find(t=>t.name==='architecture');
  const graph=B.tool_bundle.relations,entities=new Map(B.plan.entities.map(e=>[e.id,e])),typed=new Map(graph.records.map(r=>[r.id,r]));
  const caseMap=new Map(B.cases.map(c=>[c.id,c])),baseline=B.cases[0];
  const temporalTool=B.temporal_fixture?window.XraySharedTools.makeTools(B.temporal_fixture).find(t=>t.name==='architecture'):null;
  let timeQuery=null,locatorIndex=0,scaleKey=null,selectedScaleEntity=null;
  let log=clone(B.example_log),state=clone(B.example_state),queue=Promise.resolve(),tourPromise=Promise.resolve(),logAvailable=true,view='audit',tourStep=-1,queryResult=null;
  let browser=null,rendererPromise=null,sceneGeneration=0,renderedCase=null,pendingCase=null;
  const defaults={from_entity:'node:review',to_entity:'node:ingest'};
  const activeCase=()=>caseMap.get(state.requested_mutation)||baseline;
  const activeQuery=()=>state.query||defaults;
  const queryArgs=()=>{const q=activeQuery();return {from:entities.get(q.from_entity).source_name,to:entities.get(q.to_entity).source_name,
    direction:state.lens.direction,predicates:state.lens.predicates.join(','),grades:state.lens.grade==='all'?'':state.lens.grade,max_hops:state.lens.max_hops,limit:6,...(state.time||{})};};
  const expected=B.plan.entities.map(e=>e.position),xs=expected.map(p=>p[0]),ys=expected.map(p=>p[1]);
  const xmin=Math.min(...xs)-3,xmax=Math.max(...xs)+3,ymin=Math.min(...ys)-3,ymax=Math.max(...ys)+3;
  const scale=Math.min(820/(xmax-xmin),440/(ymax-ymin));
  const project=p=>[500+(p[0]-(xmin+xmax)/2)*scale,295-(p[1]-(ymin+ymax)/2)*scale];
  const line=points=>points.map((p,i)=>`${i?'L':'M'}${project(p).map(v=>v.toFixed(2)).join(',')}`).join(' ');
  const number=x=>Number(x).toLocaleString(undefined,{maximumFractionDigits:3});
  function localReduce(event){
    if(event.action==='select_entity'){state.selected_entity=event.id;state.selected_relation=null;}
    else if(event.action==='select_relation'){state.selected_relation=event.id;state.selected_entity=null;}
    else if(event.action==='set_lens'){state.lens=clone(event.lens);state.requested_mutation=null;state.selected_finding=null;}
    else if(event.action==='set_query')state.query=clone(event.query);
    else if(event.action==='set_time')state.time=clone(event.time);
    else if(event.action==='run_mutation'){state.requested_mutation=event.id;state.selected_finding=null;}
    else if(event.action==='inspect_finding')state.selected_finding=event.id;
    else if(event.action==='reset')state={selected_entity:null,selected_relation:null,query:null,time:null,lens:{predicates:[],direction:'outgoing',max_hops:3,grade:'all'},requested_mutation:null,selected_finding:null};
  }
  function dispatch(event){
    queue=queue.then(async()=>{
      if(logAvailable){log=await eventEngine.appendEvent(log,event,B.catalog);state=await eventEngine.replay(log,B.catalog,B.fixture_digest,B.analysis_digest);}
      else localReduce(event);
      render();
    }).catch(error=>{$('logStatus').textContent='The interaction was not recorded: '+(error.code||error.message);});
    return queue;
  }
  function render(){
    const start=performance.now(),c=activeCase(),v=c.verification;
    $('cases').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===c.id)));
    $('caseTitle').textContent=c.label;$('caseDescription').textContent=c.description;
    $('verdict').dataset.kind=c.scope_probe?'scope':v.ok?'accept':'reject';
    $('verdict').innerHTML=(c.scope_probe?'Outside contract':v.ok?'Conforms':'Rejected')+`<small>${c.scope_probe?'Scoped check accepts; broader meaning unverified':'static geometry result'}</small>`;
    $('entityCount').textContent=v.checks.entities;$('relationCount').textContent=v.checks.relations;$('findingCount').textContent=v.finding_count;
    $('findings').innerHTML=v.findings.length?v.findings.map(f=>`<button data-code="${esc(f.code)}" aria-pressed="${state.selected_finding===f.code}">${esc(f.message)}</button>`).join(''):
      `<span class="no-findings">${c.scope_probe?'No in-scope violation. The added outside-layer geometry is not certified.':'No mismatch within this static contract.'}</span>`;
    $('findings').querySelectorAll('button').forEach(b=>b.onclick=()=>dispatch({action:'inspect_finding',id:b.dataset.code}));
    const q=activeQuery();$('queryFrom').value=q.from_entity;$('queryTo').value=q.to_entity;
    $('direction').value=state.lens.direction;$('hops').value=String(state.lens.max_hops);$('grade').value=state.lens.grade;
    $('predicates').querySelectorAll('input').forEach(i=>i.checked=state.lens.predicates.includes(i.value));
    queryResult=tool.execute(queryArgs());drawQuery();drawMap();drawSelection();drawHistory();drawContinuum();
    $('updateTime').textContent=`overlay ${number(performance.now()-start)} ms`;
    if(view==='3d'&&renderedCase!==c.id&&pendingCase!==c.id)update3D(c);
  }
  function sourceSemantic(r){const raw=r.source_endpoints||r;return B.plan.relations.find(p=>p.source===r.source&&(r.source==='curated'?p.id===r.source_key:p.from===raw.from&&p.to===raw.to));}
  function selectedSemantic(){const r=typed.get(state.selected_relation);return r?sourceSemantic(r):null;}
  function drawMap(){
    const c=activeCase(),actual=c.verification.observed||{entities:[],relations:[]},byId=new Map(actual.entities.map(e=>[e.id,e]));
    const selectedEdge=selectedSemantic();let h='<defs><pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" class="grid-line"/></pattern><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L8 4L0 8" fill="#b6e5ce"/></marker></defs><rect width="1000" height="590" fill="url(#grid)"/>';
    h+='<ellipse cx="500" cy="295" rx="380" ry="225" fill="none" stroke="#355865" stroke-dasharray="1 12" opacity=".5"/>';
    if(view==='paths'){
      const recordIds=new Set((queryResult.paths||[]).flatMap(p=>p.hops.flatMap(h=>h.relations.map(r=>r.id))));
      for(const r of graph.records){const a=entities.get('node:'+r.from),b=entities.get('node:'+r.to);if(!a||!b)continue;
        const selected=r.id===state.selected_relation,active=recordIds.has(r.id),aa=project(a.position),bb=project(b.position),dx=bb[0]-aa[0],dy=bb[1]-aa[1],len=Math.hypot(dx,dy)||1;
        const offset=(graph.records.filter(x=>x.from===r.from&&x.to===r.to).indexOf(r)-.5)*28;
        const ctrl=[(aa[0]+bb[0])/2-dy/len*offset,(aa[1]+bb[1])/2+dx/len*offset];
        h+=`<path class="relation-line" data-record="${esc(r.id)}" d="M${aa} Q${ctrl} ${bb}" fill="none" stroke="${selected?'#fff':active?'#b6e5ce':'#688d9c'}" stroke-width="${selected?4:active?2.5:1.2}" opacity="${selected||active?.95:.2}" ${r.directed?'marker-end="url(#arrow)"':''}><title>${esc(r.from)} ${r.directed?'→':'↔'} ${esc(r.to)} · ${esc(r.predicate)}</title></path>`;
      }
    }else{
      for(const e of B.plan.relations)h+=`<path d="${line(e.points)}" fill="none" stroke="#8ce3cb" stroke-width="1.5" stroke-dasharray="5 6" opacity=".46"/>`;
      for(const e of actual.relations){const selected=selectedEdge&&selectedEdge.id===e.id;h+=`<path class="relation-line" data-semantic="${esc(e.id)}" d="${line(e.points)}" fill="none" stroke="${selected?'#fff':c.verification.ok?'#e9b59b':'#f49d85'}" stroke-width="${selected?4:2.5}" opacity=".85"/>`;}
    }
    for(const e of B.plan.entities){const p=project(e.position),observed=byId.get(e.id),ap=observed?project(observed.position):p,radius=e.geometry.radius||e.geometry.size[0]/2,r=Math.max(13,radius*scale);
      h+=`<circle cx="${p[0]}" cy="${p[1]}" r="${r+8}" fill="none" stroke="#8ce3cb" stroke-dasharray="3 5" opacity=".6"/>`;
      if(observed&&(Math.abs(p[0]-ap[0])+Math.abs(p[1]-ap[1])>.5))h+=`<path d="M${p}L${ap}" stroke="#edba94" stroke-dasharray="2 3"/>`;
      const g=observed?observed.geometry:e.geometry,ar=Math.max(10,(g.radius||(g.size||[radius*2])[0]/2)*scale),selected=e.id===state.selected_entity;
      h+=`<g class="entity" data-id="${esc(e.id)}" role="button" tabindex="0" aria-label="${esc(e.source_name)}" transform="translate(${ap})"><circle r="${ar+23}" fill="#8ce3cb" opacity=".035"/><circle class="entity-ring" r="${ar+4}" fill="none" stroke="${selected?'#fff':'#749eaa'}" opacity="${selected?1:.6}"/><circle r="${ar}" fill="${e.source_name==='evidence'?'#e4ce94':'#93d7cb'}" opacity="${observed||view==='paths'?1:.18}"/><circle r="${ar*.22}" cx="${-ar*.23}" cy="${-ar*.23}" fill="#fff" opacity=".45"/><text y="${ar+28}" text-anchor="middle">${esc(e.source_name)}</text></g>`;
    }
    $('map').innerHTML=h;
    $('map').querySelectorAll('.entity').forEach(e=>{const run=()=>dispatch({action:'select_entity',id:e.dataset.id});e.onclick=run;e.onkeydown=x=>{if(x.key==='Enter'||x.key===' '){x.preventDefault();run();}};});
    $('map').querySelectorAll('[data-record]').forEach(e=>e.onclick=()=>dispatch({action:'select_relation',id:e.dataset.record}));
    $('map').querySelectorAll('[data-semantic]').forEach(e=>e.onclick=()=>{const r=graph.records.find(r=>sourceSemantic(r)?.id===e.dataset.semantic);if(r)dispatch({action:'select_relation',id:r.id});});
    $('projectionNote').textContent=view==='paths'?'arrows = recorded predicate direction':'decorations excluded';
  }
  function drawQuery(){
    let h=queryResult.paths&&queryResult.paths.length?`<b>${queryResult.total_paths} recorded shortest path${String(queryResult.total_paths)==='1'?'':'s'}</b> · ${queryResult.hops} hop${queryResult.hops===1?'':'s'}`:'<b>No recorded path in this lens.</b>';
    for(const path of queryResult.paths||[]){h+=`<div class="path"><span>${path.nodes.map(esc).join(' → ')}</span>`;for(const hop of path.hops){for(const r of hop.relations)h+=`<button data-id="${esc(r.id)}">${esc(r.predicate)} · ${esc(r.grade)} · ${r.evidence.length} supplied witness${r.evidence.length===1?'':'es'}</button>`;if(hop.omitted_relations)h+=`<p class="muted">${hop.omitted_relations} supporting records omitted.</p>`;}h+='</div>';}
    if(queryResult.omitted_paths)h+=`<p class="muted">Returned ${queryResult.returned_paths}; omitted ${queryResult.omitted_paths} paths.</p>`;
    h+='<p class="muted">Filtered, recorded paths within the hop bound. This does not establish causal flow or runtime execution.</p>';
    $('queryResult').innerHTML=h;$('queryResult').querySelectorAll('button').forEach(b=>b.onclick=()=>dispatch({action:'select_relation',id:b.dataset.id}));
    const selected=typed.get(state.selected_relation);
    $('selectedRelation').innerHTML=selected?`<strong>${esc(selected.predicate)}</strong><br>${esc(selected.from)} ${selected.directed?'→':'↔'} ${esc(selected.to)}<br>${esc(selected.reason)}`:'';
  }
  function drawSelection(){
    const e=entities.get(state.selected_entity),r=typed.get(state.selected_relation),c=activeCase();
    if(r){$('selectionType').textContent='TYPED RELATION';$('selectionTitle').textContent=r.predicate;
      $('selectionDetail').innerHTML=`<p>${esc(r.from)} ${r.directed?'→':'↔'} ${esc(r.to)} · grade ${esc(r.grade)}</p><p>${esc(r.reason)}</p><dl><dt>source detector</dt><dd>${esc(r.detector_kind)}</dd><dt>method</dt><dd>${esc(r.method.id)} / ${esc(r.method.version)}</dd><dt>sample / observations</dt><dd>${r.evidence_records.returned} / ${r.evidence_records.total}</dd></dl>`+r.evidence.map(e=>'<pre>'+esc(JSON.stringify(e,null,2))+'</pre>').join('');
    }else if(e){const actual=(c.verification.observed||{entities:[]}).entities.find(a=>a.id===e.id);$('selectionType').textContent='STATIC GLYPH';$('selectionTitle').textContent=e.source_name;
      $('selectionDetail').innerHTML=`<dl><dt>planned world centre</dt><dd>${e.position.map(number).join(' / ')}</dd><dt>read world centre</dt><dd>${actual?actual.position.map(number).join(' / '):'not available within this checked subset'}</dd><dt>primitive</dt><dd>${esc(e.geometry.type)}</dd><dt>source identity</dt><dd>${esc(e.id)}</dd></dl><p>Position and dimensions come from XML primitives and composed transforms. Metadata supplies identity, not the coordinate answer.</p>`;
    }else{$('selectionType').textContent='INSPECTION';$('selectionTitle').textContent=state.selected_finding||'Inspectable evidence';$('selectionDetail').textContent='Select a glyph, a typed relation or a verifier finding. The plan and source remain unchanged across XML mutations.';}
    if(state.selected_finding)$('selectionDetail').insertAdjacentHTML('beforeend','<p><b>Finding:</b> '+esc(state.selected_finding.replaceAll('_',' '))+'</p>');
  }
  function drawHistory(){
    $('history').innerHTML=log.events.slice(-8).map(r=>`<div>${String(r.seq).padStart(2,'0')} · ${esc(r.event.action)}${r.event.id?' · '+esc(r.event.id).slice(0,90):''}<br><small>${r.digest.slice(0,18)}…</small></div>`).join('');
    if(logAvailable)$('logStatus').textContent=`${log.events.length} events · chain and context checked · state ${(state.state_digest||'').slice(0,14)}`;
    $('saveLog').disabled=!logAvailable;
  }
  function lensFromControls(){return {predicates:Array.from($('predicates').querySelectorAll('input:checked')).map(e=>e.value),direction:$('direction').value,max_hops:+$('hops').value,grade:$('grade').value};}
  function drawContinuum(){
    if(!$('timeMode'))return;
    const time=state.time;$('timeMode').value=time?time.time_mode:'none';$('timeUnknown').value=time?time.unknown_time:'exclude';
    if(time)$('timeAt').value=time.at;
    $('timeContext').textContent=time?`${time.time_mode==='observed_by'?'Observation cutoff':'Claimed validity instant'}: ${time.at} · unknown times ${time.unknown_time==='include'?'included provisionally':'excluded'}.`:'Unfiltered snapshot: no claim that its relationships existed together.';
    if(temporalTool){timeQuery=temporalTool.execute({from:'alpha',to:'gamma',direction:'association',max_hops:3,limit:6,...(time||{})});
      let h=`<b>${timeQuery.total_paths} recorded shortest path${String(timeQuery.total_paths)==='1'?'':'s'}</b>`;
      for(const p of timeQuery.paths||[])h+=`<div class="time-path ${p.time_status==='provisional_unknown_time'?'provisional':''}">${p.nodes.map(esc).join(' → ')}<br><small>${p.time_status==='provisional_unknown_time'?'PROVISIONAL · unknown time support':time?'Known time under this filter':'Snapshot association; simultaneity unverified'}</small></div>`;
      if(timeQuery.time_coverage){const c=timeQuery.time_coverage;h+=`<p>${c.eligible_records}/${c.candidate_records} eligible records · ${c.included_unknown_records} unknown included</p><p class="muted">${esc(c.scope)}</p>`;}
      $('temporalResult').innerHTML=h;drawTimeline();}
    drawScale();
  }
  function drawTimeline(){
    const graph=B.temporal_fixture.relations,begin=Date.parse('2026-01-01T00:00:00Z'),end=Date.parse('2026-01-06T00:00:00Z'),px=t=>220+480*(Date.parse(t)-begin)/(end-begin);
    let h='<defs><pattern id="unknownHatch" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M0 8L8 0" stroke="#677e89" stroke-width="1"/></pattern></defs>';
    for(let day=1;day<=6;day++){const x=220+(day-1)*96;h+=`<path d="M${x}30V260" stroke="#335260" stroke-dasharray="2 6"/><text x="${x}" y="23" fill="#8faeb9" font-size="10" text-anchor="middle">Jan ${day}</text>`;}
    for(const [i,r] of graph.records.entries()){const y=65+i*53;h+=`<text x="12" y="${y+4}" fill="#c7dfdf" font-size="12">${esc(r.from)} ↔ ${esc(r.to)}</text>`;
      if(r.valid_time){const x=px(r.valid_time.start),w=px(r.valid_time.end)-x;h+=`<rect x="${x}" y="${y-10}" width="${w}" height="20" rx="4" fill="#69c8b7" opacity=".75"/><path d="M${x+w} ${y-13}V${y+13}" stroke="#c6e7df" stroke-dasharray="2 2"/>`;}
      else h+=`<rect x="220" y="${y-10}" width="480" height="20" rx="4" fill="url(#unknownHatch)" stroke="#607f8a" stroke-dasharray="4 4"/>`;
      if(r.observed_at)h+=`<circle cx="${px(r.observed_at)}" cy="${y}" r="5" fill="#f1cc81" stroke="#152e39" stroke-width="2"><title>Observed ${esc(r.observed_at)}</title></circle>`;}
    if(state.time){const x=px(state.time.at);if(x>=220&&x<=700)h+=`<path d="M${x}32V264" stroke="#f3a187" stroke-width="2"/><text x="${x}" y="283" fill="#f3a187" text-anchor="middle" font-size="10">query instant</text>`;}
    $('timeline').innerHTML=h;
  }
  function drawLocator(){
    const audit=B.source_verification||{checked:0,records:[]},record=audit.records[locatorIndex];
    $('locatorStatus').textContent=`${audit.checked} immutable Git witnesses checked`;
    $('locatorList').querySelectorAll('button').forEach((b,i)=>b.setAttribute('aria-pressed',String(i===locatorIndex)));
    $('locatorDetail').textContent=record?JSON.stringify(record,null,2):'No locally verified file locator is present. Unlocated assertions remain explicit.';
    const probe=audit.tamper_probe;$('locatorProbe').textContent=probe?`Actual cached tamper probe: ${probe.ok?'unexpected acceptance':'rejected'} · ${probe.findings.join(', ')}. Only the recorded span digest was changed.`:'';
    $('downloadLocator').disabled=!record;
  }
  function drawScale(){
    const previews=B.scale_previews||[],preview=previews.find(p=>String(p.size)===$('scaleSize').value);
    if(!preview){$('scaleStatus').textContent='No measured fixture embedded. Build with --benchmark to inspect real results.';$('scaleMap').innerHTML='';$('scaleScene').hidden=true;return;}
    $('scaleScene').hidden=false;$('scaleScene').href=preview.scene;
    const key=preview.size+':'+preview.topology;if(key===scaleKey)return;scaleKey=key;selectedScaleEntity=null;
    const start=performance.now(),ps=preview.entities.map(e=>e.position),xmin=Math.min(...ps.map(p=>p[0]))-1,xmax=Math.max(...ps.map(p=>p[0]))+1,ymin=Math.min(...ps.map(p=>p[1]))-1,ymax=Math.max(...ps.map(p=>p[1]))+1;
    const k=Math.min(740/(xmax-xmin),440/(ymax-ymin)),proj=p=>[400+(p[0]-(xmin+xmax)/2)*k,250-(p[1]-(ymin+ymax)/2)*k];
    let h='';for(const r of preview.relations)h+=`<path d="${r.points.map((p,i)=>(i?'L':'M')+proj(p).join(',')).join(' ')}" fill="none" stroke="#4e8892" stroke-width=".8" opacity=".3"/>`;
    for(const e of preview.entities){const p=proj(e.position),r=Math.max(1.7,(e.geometry.radius||(e.geometry.size||[.5])[0]/2)*k);h+=`<circle class="scale-dot" data-id="${esc(e.id)}" cx="${p[0]}" cy="${p[1]}" r="${r}"><title>${esc(e.source_name)}</title></circle>`;}
    $('scaleMap').innerHTML=h;$('scaleStatus').textContent=`${preview.entities.length} planned glyphs · ${preview.relations.length} source lines · ${number(performance.now()-start)} ms DOM update (excludes paint)`;
    $('scaleMap').querySelectorAll('.scale-dot').forEach(dot=>dot.onclick=()=>{if(selectedScaleEntity)$('scaleMap').querySelector('.active')?.classList.remove('active');selectedScaleEntity=dot.dataset.id;dot.classList.add('active');const e=preview.entities.find(e=>e.id===dot.dataset.id);$('scaleSelection').textContent=`${e.source_name} · XYZ ${e.position.map(number).join(' / ')} · plan geometry; pixel appearance unverified.`;});
    $('benchmarkTable').querySelectorAll('[data-size]').forEach(row=>row.classList.toggle('active',row.dataset.size===String(preview.size)&&row.dataset.topology===preview.topology));
  }
  function initializeContinuum(){
    if(!$('timeMode'))return;
    const audit=B.source_verification||{records:[]};$('locatorList').innerHTML=audit.records.map((r,i)=>`<button class="locator-button" data-index="${i}">${esc(r.locator.repo_id)} / ${esc(r.locator.path)}<br><small>${r.locator.revision.oid.slice(0,16)}… · ${r.locator.span.kind} ${r.locator.span.start}–${r.locator.span.end}</small></button>`).join('');
    $('locatorList').querySelectorAll('button').forEach(b=>b.onclick=()=>{locatorIndex=+b.dataset.index;drawLocator();});$('downloadLocator').onclick=()=>save('immutable-witness.json',audit.records[locatorIndex].locator);drawLocator();
    $('temporalDescription').textContent=B.temporal_fixture?.description||'';
    $('timeApply').onclick=()=>dispatch({action:'set_time',time:$('timeMode').value==='none'?null:{at:$('timeAt').value.trim(),time_mode:$('timeMode').value,unknown_time:$('timeUnknown').value}});
    $('timeClear').onclick=()=>dispatch({action:'set_time',time:null});$('scaleSize').onchange=()=>drawScale();
    const report=B.scale_results;$('downloadBenchmark').disabled=!report;$('downloadBenchmark').onclick=()=>save('scale-benchmark.json',report);
    if(report){$('benchmarkScope').textContent=report.disclosure;$('benchmarkEnvironment').textContent=`${report.environment?.platform||''} · Python ${String(report.environment?.python||'').split(' ')[0]} · ${report.environment?.cpu_count||'?'} logical CPUs. Stage p50/p95 in ms; repeat/warmup details and fingerprints are in the downloadable evidence.`;
      $('benchmarkTable').innerHTML='<thead><tr><th>entities / topology</th><th>compile p50/p95</th><th>check p50/p95</th><th>glyph overlaps</th></tr></thead><tbody>'+report.rows.map(r=>`<tr data-size="${r.size}" data-topology="${esc(r.topology)}"><td>${r.size} / ${esc(r.topology)}</td><td>${number(r.timings.compile.p50_ms)} / ${number(r.timings.compile.p95_ms)}</td><td>${number(r.timings.verify.p50_ms)} / ${number(r.timings.verify.p95_ms)}</td><td>${r.geometry.glyph_overlap_pairs}</td></tr>`).join('')+'</tbody>';
    }else $('benchmarkScope').textContent='No timing data was supplied. Run python3 -m x3dray scale and rebuild this export with --benchmark; no timings are fabricated.';
  }
  function save(name,value,type='application/json'){const text=typeof value==='string'?value:JSON.stringify(value,null,2),url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function replayLog(raw){const task=queue.then(async()=>{const next=await eventEngine.replay(raw,B.catalog,B.fixture_digest,B.analysis_digest);log=clone(raw);state=next;logAvailable=true;render();return clone(state);});queue=task.catch(()=>{});return task;}
  const tour=[
    {title:'01 · Read the original geometry',text:'The baseline was independently parsed from the XML. Its labels are not the coordinate evidence.',events:[{action:'reset'},{action:'run_mutation',id:'baseline'},{action:'select_entity',id:'node:evidence'}]},
    {title:'02 · Corrupt the endpoint',text:'Only the used line coordinates change. The source and plan remain fixed; the verifier rejects the disagreement.',events:[{action:'run_mutation',id:'wrong_edge_endpoint'}]},
    {title:'03 · Preserve meaning while changing bytes',text:'Nested inverse transforms alter the XML but preserve world geometry. A byte comparison would object; the semantic check accepts it.',events:[{action:'run_mutation',id:'equivalent_nested_transforms'}]},
    {title:'04 · Inspect the boundary of the proof',text:'The extra line lies outside the declared semantic layers. This scoped check accepts the instrumented content; it does not certify the added decoration.',events:[{action:'run_mutation',id:'unlabelled_outside_layer'}]},
    {title:'05 · Follow a precise direction',text:'The recorded configuration and static references connect review → render → evidence → ingest. Incoming traversal asks a different question.',events:[{action:'set_lens',lens:{predicates:['declares_use','dependency_reference'],direction:'outgoing',max_hops:3,grade:'all'}},{action:'set_query',query:{from_entity:'node:review',to_entity:'node:ingest'}},{action:'run_mutation',id:'baseline'}],view:'paths'},
    {title:'06 · Replay intent, retain evidence',text:'Save the event chain. Its source and analysis identities bind selections and filters. Actual checker results remain separate recorded evidence.',events:[]}
  ];
  async function runTour(i){tourStep=i;if(i>=tour.length){$('nextTour').hidden=true;return;}const step=tour[i];for(const event of step.events)await dispatch(event);if(step.view)setView(step.view);$('lesson').innerHTML='<b>'+esc(step.title)+'</b>'+esc(step.text);$('nextTour').hidden=false;$('nextTour').textContent=i===tour.length-1?'Finish investigation ✓':'Next observation →';}
  function setView(next){view=next;++sceneGeneration;for(const [id,value] of [['auditView','audit'],['pathView','paths'],['threeView','3d']])$(id).setAttribute('aria-pressed',String(next===value));$('map').toggleAttribute('hidden',next==='3d');$('xscene').hidden=next!=='3d';$('sceneStatus').hidden=true;if(next==='3d')update3D(activeCase());else drawMap();}
  async function loadRenderer(){if(window.X3D)return;if(rendererPromise)return rendererPromise;
    const add=src=>new Promise((res,rej)=>{const s=document.createElement('script');s.src=src;s.onload=res;s.onerror=rej;document.head.append(s);});
    rendererPromise=(async()=>{let base='x_ite/';try{await add(base+'x_ite.min.js');}catch{base='https://cdn.jsdelivr.net/npm/x_ite@16.7.0/dist/';await add(base+'x_ite.min.js');}const css=document.createElement('link');css.rel='stylesheet';css.href=base+'x_ite.css';document.head.append(css);})();return rendererPromise;
  }
  async function update3D(c){if(renderedCase===c.id){$('sceneStatus').hidden=true;return;}const generation=++sceneGeneration;pendingCase=c.id;$('sceneStatus').hidden=false;$('sceneStatus').textContent='Loading the selected X3D artifact…';try{await loadRenderer();const el=$('xscene'),deadline=performance.now()+15000;while(!el.browser){if(performance.now()>deadline)throw Error('Renderer did not initialize');await new Promise(r=>setTimeout(r,25));}if(generation!==sceneGeneration||view!=='3d')return;browser=el.browser;const scene=await browser.createX3DFromString(c.xml);if(generation!==sceneGeneration||view!=='3d')return;await browser.replaceWorld(scene);if(generation===sceneGeneration){renderedCase=c.id;$('sceneStatus').hidden=true;}}catch(error){if(generation===sceneGeneration)$('sceneStatus').textContent='The selected artifact did not render: '+error.message+'. Some mutations deliberately make invalid X3D. Recorded static checks and the map remain available.';}finally{if(pendingCase===c.id)pendingCase=null;}}
  $('cases').innerHTML=B.cases.map(c=>`<button class="case-button" data-id="${esc(c.id)}" data-kind="${c.scope_probe?'scope':c.expected_ok?'accept':'reject'}" aria-pressed="false"><span class="status-dot"></span>${esc(c.label)}</button>`).join('');
  $('cases').querySelectorAll('button').forEach(b=>b.onclick=()=>dispatch({action:'run_mutation',id:b.dataset.id}));
  for(const id of ['queryFrom','queryTo']){$(id).innerHTML=B.plan.entities.map(e=>`<option value="${esc(e.id)}">${esc(e.source_name)}</option>`).join('');$(id).onchange=()=>dispatch({action:'set_query',query:{from_entity:$('queryFrom').value,to_entity:$('queryTo').value}});}
  const predicates=Array.from(new Set(graph.records.map(r=>r.predicate))).sort();
  $('predicates').innerHTML=predicates.map(p=>`<label><input type="checkbox" value="${esc(p)}"><span>${esc(p.replaceAll('_',' '))}<small>${graph.predicates[p].directed?'directed →':'symmetric ↔'}</small></span></label>`).join('');
  $('predicates').querySelectorAll('input').forEach(i=>i.onchange=()=>dispatch({action:'set_lens',lens:lensFromControls()}));for(const id of ['direction','hops','grade'])$(id).onchange=()=>dispatch({action:'set_lens',lens:lensFromControls()});
  $('auditView').onclick=()=>setView('audit');$('pathView').onclick=()=>setView('paths');$('threeView').onclick=()=>setView('3d');
  const beginStep=i=>{if($('startTour').disabled)return;$('startTour').disabled=true;$('nextTour').disabled=true;tourPromise=runTour(i).finally(()=>{$('startTour').disabled=false;$('nextTour').disabled=false;});};
  $('startTour').onclick=()=>beginStep(0);$('nextTour').onclick=()=>beginStep(tourStep+1);$('reset').onclick=()=>dispatch({action:'reset'});
  $('saveLog').onclick=()=>save('xray-investigation-history.json',log);
  $('loadLog').onchange=async e=>{const f=e.target.files[0];try{if(f){if(f.size>262144)throw Error('History exceeds the bounded import size');await queue;await replayLog(JSON.parse(await f.text()));}}catch(error){$('logStatus').textContent='Replay refused: '+(error.code||error.message);}finally{e.target.value='';}};
  $('saveXML').onclick=()=>save('selected.x3d',activeCase().xml,'model/x3d+xml');$('savePlan').onclick=()=>save('scene-plan.json',B.plan);$('saveBundle').onclick=()=>save('witness-bundle.json',B.tool_bundle);$('saveAudit').onclick=()=>save('recorded-audit.json',{fixture_digest:B.fixture_digest,analysis_digest:B.analysis_digest,case:activeCase().id,verification:activeCase().verification,mode:B.verification_mode});
  $('fixtureDigest').textContent=B.fixture_digest.slice(0,24)+'…';$('analysisDigest').textContent=B.analysis_digest.slice(0,24)+'…';$('scope').textContent=B.scope;$('verificationMode').textContent=B.verification_mode;
  $('sourceFiles').innerHTML=Object.entries(B.source_tips).map(([repo,files])=>`<details><summary>${esc(repo)}</summary>${Object.entries(files).map(([name,text])=>`<p>${esc(name)}</p><pre>${esc(text)}</pre>`).join('')}</details>`).join('');
  $('lesson').innerHTML='<b>Make the proof boundary visible.</b>Begin the guided investigation, or select an altered XML case. Typed paths always query the unchanged source graph.';
  initializeContinuum();
  const ready=eventEngine.replay(log,B.catalog,B.fixture_digest,B.analysis_digest).then(next=>{state=next;render();}).catch(error=>{logAvailable=false;$('logStatus').textContent='Event hashing unavailable ('+(error.code||error.message)+'). Analytical views and recorded checks remain available.';render();});
  queue=ready;
  window.__observatory={ready,whenIdle:async()=>{await tourPromise;await queue;},exportLog:()=>clone(log),replayLog,get state(){return clone(state);},get query(){return clone(queryResult);},get temporalQuery(){return clone(timeQuery);},get selectedCase(){return activeCase().id;},dispatch};
  render();
})();
