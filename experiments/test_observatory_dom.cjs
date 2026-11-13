/* DOM integration of the built observatory; intentionally no GPU claim. */
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
const {TextEncoder,TextDecoder}=require('node:util');
const {JSDOM}=require('jsdom');
const errors=[],downloads=[],checks=[];
const html=fs.readFileSync(process.argv[2],'utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/observatory/',beforeParse(w){
  Object.defineProperty(w,'crypto',{value:webcrypto});w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;
  w.Blob=class{constructor(parts,options){this.parts=parts;this.type=options.type;}};
  w.URL.createObjectURL=b=>{downloads.push(b);return 'blob:synthetic';};w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){};w.addEventListener('error',e=>errors.push(e.message));
}});
const w=dom.window,d=w.document,$=id=>d.getElementById(id),api=w.__observatory;
const realm=x=>w.JSON.parse(JSON.stringify(x));
const send=e=>api.dispatch(realm(e));
async function test(name,fn){await fn();checks.push(name);}
async function clickCase(id){d.querySelector(`[data-id="${id}"]`).click();await api.whenIdle();}
(async()=>{
  assert.ok(api,'Application initialized');await api.ready;
  await test('baseline renders independently read geometry and typed three-hop query',async()=>{
    assert.equal(d.querySelectorAll('.case-button').length,13);assert.equal(d.querySelectorAll('#map .entity').length,4);
    assert.equal($('verdict').dataset.kind,'accept');assert.equal(api.query.hops,3);
    assert.deepEqual(Array.from(api.query.paths[0].nodes),['review','render','evidence','ingest']);
    assert.match($('logStatus').textContent,/chain and context checked/);
  });
  await test('corrupt endpoint exposes actual checker findings',async()=>{
    await clickCase('wrong_edge_endpoint');assert.equal(api.selectedCase,'wrong_edge_endpoint');
    assert.equal($('verdict').dataset.kind,'reject');assert.ok(+$('findingCount').textContent>0);
    const f=d.querySelector('#findings button');f.click();await api.whenIdle();assert.equal(api.state.selected_finding,f.dataset.code);
  });
  await test('benign equivalent transforms are accepted',async()=>{await clickCase('equivalent_nested_transforms');assert.equal($('verdict').dataset.kind,'accept');});
  await test('outside-layer acceptance is presented as a scope boundary',async()=>{await clickCase('unlabelled_outside_layer');assert.equal($('verdict').dataset.kind,'scope');assert.match($('verdict').textContent,/Outside contract/);});
  await test('incoming direction changes the result with the same endpoints',async()=>{
    $('direction').value='incoming';$('direction').dispatchEvent(new w.Event('change'));await api.whenIdle();assert.equal(api.query.total_paths,0);
  });
  await test('association lens reveals attributed symmetric shortcut',async()=>{
    await send({action:'set_lens',lens:{predicates:['curated_association'],direction:'association',max_hops:3,grade:'all'}});
    assert.equal(api.query.hops,1);assert.equal(api.query.paths[0].hops[0].relations[0].directed,false);
    assert.equal(api.query.paths[0].hops[0].relations[0].grade,'S');
  });
  await test('relation inspection exposes original supplied evidence',async()=>{
    $('pathView').click();const button=d.querySelector('#queryResult button');assert.ok(button);button.click();await api.whenIdle();
    assert.match($('selectionType').textContent,/TYPED RELATION/);assert.match($('selectionDetail').textContent,/Synthetic curator statement/);
  });
  await test('endpoint controls are part of replay state',async()=>{
    $('queryFrom').value='node:render';$('queryFrom').dispatchEvent(new w.Event('change'));await api.whenIdle();
    $('queryTo').value='node:evidence';$('queryTo').dispatchEvent(new w.Event('change'));await api.whenIdle();
    assert.equal(api.state.query.from_entity,'node:render');assert.equal(api.state.query.to_entity,'node:evidence');
  });
  let saved,expected;
  await test('history restores exact selection endpoints lens and mutation',async()=>{
    await send({action:'set_lens',lens:{predicates:['dependency_reference'],direction:'outgoing',max_hops:2,grade:'C'}});
    await clickCase('primitive_size');await send({action:'select_entity',id:'node:evidence'});
    saved=api.exportLog();expected=api.state;await send({action:'reset'});await api.replayLog(saved);
    assert.equal(api.state.state_digest,expected.state_digest);assert.equal(api.selectedCase,'primitive_size');assert.equal(api.query.hops,1);
  });
  await test('altered event chain is refused without replacing current state',async()=>{
    const bad=realm(saved);bad.events[0].event.lens.max_hops=5;await assert.rejects(api.replayLog(bad));assert.equal(api.state.state_digest,expected.state_digest);
  });
  await test('different analysis identity is refused',async()=>{const bad=realm(saved);bad.analysis_digest='f'.repeat(64);await assert.rejects(api.replayLog(bad));});
  await test('downloads contain selected XML and unchanged plan plus source bundle',async()=>{
    for(const id of ['saveXML','savePlan','saveBundle','saveAudit','saveLog'])$(id).click();
    const [xml,plan,bundle,audit,history]=downloads.slice(-5).map(x=>x.parts.join(''));
    assert.match(xml,/<X3D/);assert.equal(JSON.parse(plan).entities.length,4);assert.equal(JSON.parse(bundle).relations.records.length,7);
    assert.equal(JSON.parse(audit).verification.ok,false);assert.equal(JSON.parse(history).head,saved.head);
  });
  await test('selection preserves loaded world identity with a renderer stand-in',async()=>{
    let created=0,replaced=0;w.X3D={};$('xscene').browser={createX3DFromString:async xml=>{created++;return {xml};},replaceWorld:async()=>{replaced++;}};
    $('threeView').click();await new Promise(r=>setTimeout(r,20));assert.equal(created,1);assert.equal(replaced,1);
    await send({action:'select_entity',id:'node:render'});assert.equal(created,1);assert.equal(replaced,1);
    await clickCase('baseline');await new Promise(r=>setTimeout(r,20));assert.equal(created,2);assert.equal(replaced,2);
    $('auditView').click();
  });
  await test('all six guided observations complete through shared event handling',async()=>{
    $('startTour').click();for(let i=0;i<6;i++){await new Promise(r=>setTimeout(r,15));await api.whenIdle();if(i<5)$('nextTour').click();}
    assert.match($('lesson').textContent,/Replay intent/);assert.equal(api.query.hops,3);assert.match($('logStatus').textContent,/chain and context checked/);
  });
  await test('immutable source audit exposes actual Git checks and rejected span tamper',async()=>{
    assert.equal(d.querySelectorAll('.locator-button').length,4);assert.match($('locatorDetail').textContent,/raw_content_sha256/);
    assert.match($('locatorProbe').textContent,/rejected.*blob_content_or_span_mismatch/);$('downloadLocator').click();
    const locator=JSON.parse(downloads.at(-1).parts.join(''));assert.equal(locator.revision.oid.length,40);assert.equal(locator.blob.content_sha256.length,64);
  });
  const applyTime=async(mode,at,unknown='exclude')=>{$('timeMode').value=mode;$('timeAt').value=at;$('timeUnknown').value=unknown;$('timeApply').click();await api.whenIdle();};
  await test('same-instant validity excludes disjoint-time path and unknown Git lifetimes',async()=>{
    await applyTime('valid_at','2026-01-02T12:00:00Z');assert.equal(api.temporalQuery.total_paths,0);assert.equal(api.query.total_paths,0);
    assert.equal(api.temporalQuery.time_coverage.excluded_unknown_records,1);
  });
  await test('unknown-time inclusion marks supporting path provisional',async()=>{
    await applyTime('valid_at','2026-01-02T12:00:00Z','include');assert.equal(api.temporalQuery.total_paths,1);
    assert.equal(api.temporalQuery.paths[0].time_status,'provisional_unknown_time');assert.match($('temporalResult').textContent,/PROVISIONAL/);
  });
  await test('observation cutoff permits a later snapshot without asserting simultaneous validity',async()=>{
    await applyTime('observed_by','2026-01-06T00:00:00Z');assert.equal(api.temporalQuery.total_paths,2);assert.equal(api.query.hops,3);
    assert.match($('timeContext').textContent,/Observation cutoff/);
  });
  await test('timezone equivalent temporal controls canonicalize and preserve exact query state',async()=>{
    await applyTime('valid_at','2026-01-02T07:00:00.000000-05:00','include');assert.equal(api.state.time.at,'2026-01-02T12:00:00Z');assert.equal($('timeAt').value,api.state.time.at);
    const context=api.exportLog(),expectedTime=api.state.time;await send({action:'reset'});await api.replayLog(context);assert.deepEqual(api.state.time,expectedTime);assert.equal(api.temporalQuery.total_paths,1);
  });
  await test('naive timestamp is refused without mutating recorded intent',async()=>{
    const before=api.state.state_digest;await applyTime('valid_at','2026-01-02T12:00:00');assert.equal(api.state.state_digest,before);assert.match($('logStatus').textContent,/not recorded/);
  });
  await test('clearing time restores unfiltered source snapshot',async()=>{
    $('timeClear').click();await api.whenIdle();assert.equal(api.state.time,null);assert.equal(api.temporalQuery.total_paths,2);assert.equal(api.query.hops,3);
    await applyTime('valid_at','2026-01-02T12:00:00Z','include');saved=api.exportLog();expected=api.state;
  });
  const data=JSON.parse($('proof-data').textContent);
  if(data.scale_previews?.length){
    const population=Math.max(...data.scale_previews.map(p=>p.size));
    await test(`measured ${population}-entity preview keeps the full planned population`,async()=>{
      $('scaleSize').value=String(population);$('scaleSize').dispatchEvent(new w.Event('change'));assert.equal(d.querySelectorAll('.scale-dot').length,population);
      assert.equal(d.querySelectorAll('#benchmarkTable tbody tr').length,data.scale_results.rows.length);assert.ok($('scaleScene').href.endsWith(`modular-${population}.x3d`));
    });
    await test('scale glyph selection exposes complete identity without altering investigation intent',async()=>{
      const before=api.state.state_digest;d.querySelector('#scaleMap .scale-dot').dispatchEvent(new w.MouseEvent('click',{bubbles:true}));assert.ok($('scaleSelection').textContent.includes('synthetic-'));assert.equal(api.state.state_digest,before);
    });
  }
  assert.deepEqual(errors,[]);
  if(process.argv[3])fs.writeFileSync(process.argv[3],JSON.stringify({log:saved,state:expected}));
  console.log(JSON.stringify({checks:checks.length,passed:checks,initial_js_errors:errors,scope:'DOM integration only; no browser layout, WebGL, GPU or X_ITE runtime claim'},null,2));
  dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
