// Pure browser twin of x3dray.investigation. No DOM, network or code execution.
// A chain records interaction intent, not verified facts or publisher identity.
// Canonical strings use UTF-8 and Unicode code-point ordering, matching Python.
// JS has one numeric type: integer-valued numbers are accepted; booleans are not.

export const INVESTIGATION_LIMITS = Object.freeze({events:256,idChars:256,predicates:32,catalogIds:10000,eventBytes:16384,logBytes:262144});
// Looks (see x3dray/investigation.py): the closed alphabet of an ad hoc look, the section digest domain and bounds. A test compares this
// table with the Python one key for key.
const INV_TOKEN='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
export const INVESTIGATION_LOOKS = Object.freeze({
  domain:'x3dray.looks.ledger.v1',maxBytes:1048576,maxDepth:32,maxRepos:10000,maxSafeInt:Number.MAX_SAFE_INTEGER,kinds:['declared','slice','rows','compare'],
  selector:{
    repo:['ordinals',0,9999,32],who:['enum_list',['you','ai','bot','other','upstream']],from:['date'],to:['date'],day:['date'],
    session:['token',64,INV_TOKEN+'._-'],rule:['token',64,'abcdefghijklmnopqrstuvwxyz0123456789_'],grade:['enum',['C','L','S']],
    hour:['int',0,23],weekday:['int',0,6],min_lines:['int',0,Number.MAX_SAFE_INTEGER],max_lines:['int',0,Number.MAX_SAFE_INTEGER],
    fork:['enum',['all','no_merge','default_branch','no_mass']],measure:['enum',['lines','files','gap_s']],
  },
  kindKeys:{slice:{required:['fork','measure'],forbidden:[]},rows:{required:[],forbidden:['fork','measure']}},
  rowsNeedOneOf:['hour','weekday','min_lines','max_lines','rule'],
  compareMeasures:['lines','files','gap_s','hour4','weekday','repo'],
  compareGroupKeys:['repo','who','from','to','day','hour','weekday','min_lines','max_lines'],
});

export class InvestigationError extends Error {
  constructor(code) { super(code); this.name='InvestigationError'; this.code=code; }
}

function invFail(code) { throw new InvestigationError(code); }
function invPlain(value) {
  if(value===null||typeof value!=='object'||Object.getPrototypeOf(value)!==Object.prototype)return false;
  for(const key of Reflect.ownKeys(value)) {
    if(typeof key!=='string')return false;
    const d=Object.getOwnPropertyDescriptor(value,key);
    if(!d.enumerable||!Object.hasOwn(d,'value'))return false;
  }
  return true;
}
function invList(value,maxLength) {
  if(!Array.isArray(value)||value.length>maxLength||Object.getPrototypeOf(value)!==Array.prototype)return false;
  if(Reflect.ownKeys(value).length!==value.length+1)return false;
  for(let i=0;i<value.length;i++) {
    const d=Object.getOwnPropertyDescriptor(value,String(i));
    if(!d||!d.enumerable||!Object.hasOwn(d,'value'))return false;
  }
  return true;
}
function invKeys(value,required,code) {
  if(!invPlain(value)||Object.keys(value).length!==required.length||!required.every(k=>Object.hasOwn(value,k)))invFail(code);
}
function invCompare(a,b) {
  const aa=Array.from(a),bb=Array.from(b),n=Math.min(aa.length,bb.length);
  for(let i=0;i<n;i++) { const delta=aa[i].codePointAt(0)-bb[i].codePointAt(0);if(delta)return delta; }
  return aa.length-bb.length;
}
function invIdentifier(value,code='invalid_identifier') {
  if(typeof value!=='string')invFail(code);
  const points=Array.from(value);
  if(points.length<1||points.length>INVESTIGATION_LIMITS.idChars)invFail(code);
  for(const ch of points) { const cp=ch.codePointAt(0);if(cp<32||cp===127||(cp>=0xd800&&cp<=0xdfff))invFail(code); }
  return value;
}
function invDigest(value,code='invalid_digest') {
  if(typeof value!=='string'||!/^[0-9a-f]{64}$/.test(value))invFail(code);
  return value;
}
function invCanonical(value) {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(invCanonical).join(',')+']';
  return '{'+Object.keys(value).sort(invCompare).map(k=>JSON.stringify(k)+':'+invCanonical(value[k])).join(',')+'}';
}
function invBytes(value) {
  if(typeof TextEncoder!=='function')invFail('utf8_unavailable');
  return new TextEncoder().encode(invCanonical(value));
}
async function invHash(value) {
  if(!globalThis.crypto||!globalThis.crypto.subtle)invFail('crypto_unavailable');
  const result=await globalThis.crypto.subtle.digest('SHA-256',invBytes(value));
  return Array.from(new Uint8Array(result),b=>b.toString(16).padStart(2,'0')).join('');
}
function invGenesis(fixture,analysis) {
  return invHash({domain:'x3dray.investigation.v1',fixture_digest:fixture,analysis_digest:analysis});
}
function invCatalog(catalog) {
  const keys=['entities','relations','mutations','findings','predicates'];
  invKeys(catalog,keys,'invalid_catalog_shape');
  const result=Object.create(null);
  for(const key of keys) {
    const values=catalog[key];
    if(!invList(values,INVESTIGATION_LIMITS.catalogIds))invFail('invalid_catalog_ids');
    for(const value of values)invIdentifier(value,'invalid_catalog_id');
    if(new Set(values).size!==values.length)invFail('duplicate_catalog_id');
    result[key]=new Set(values);
  }
  return result;
}
function invTimeFilter(value) {
  if(value===null)return null;
  if(!invPlain(value))invFail('invalid_time_filter');
  const keys=Object.hasOwn(value,'unknown_time')?['at','time_mode','unknown_time']:['at','time_mode'];
  invKeys(value,keys,'invalid_time_filter');
  const mode=value.time_mode,unknown=Object.hasOwn(value,'unknown_time')?value.unknown_time:'exclude';
  if(typeof mode!=='string'||!['observed_by','valid_at'].includes(mode)||typeof unknown!=='string'||!['exclude','include'].includes(unknown)||typeof value.at!=='string')invFail('invalid_time_filter');
  const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(Z|([+-])([0-9]{2}):([0-9]{2}))$/.exec(value.at);
  if(!match)invFail('invalid_time_filter');
  let year=Number(match[1]),month=Number(match[2]),day=Number(match[3]);
  const hour=Number(match[4]),minute=Number(match[5]),second=Number(match[6]);
  const leap=y=>y%4===0&&(y%100!==0||y%400===0);
  const days=(y,m)=>[31,leap(y)?29:28,31,30,31,30,31,31,30,31,30,31][m-1];
  if(year<1||year>9999||month<1||month>12||day<1||day>days(year,month)||hour>23||minute>59||second>59)invFail('invalid_time_filter');
  let offset=0;
  if(match[8]!=='Z') {
    const oh=Number(match[10]),om=Number(match[11]);
    if(oh>14||om>59||(oh===14&&om!==0))invFail('invalid_time_filter');
    offset=(oh*60+om)*(match[9]==='+'?1:-1);
  }
  let clock=hour*60+minute-offset;
  if(clock<0) {
    clock+=1440;day--;
    if(day===0){month--;if(month===0){year--;month=12;}day=days(year,month);}
  } else if(clock>=1440) {
    clock-=1440;day++;
    if(day>days(year,month)){day=1;month++;if(month===13){year++;month=1;}}
  }
  if(year<1||year>9999)invFail('invalid_time_filter');
  const pad=(n,size=2)=>String(n).padStart(size,'0'),fraction=(match[7]||'').replace(/0+$/,'');
  const at=`${pad(year,4)}-${pad(month)}-${pad(day)}T${pad(Math.floor(clock/60))}:${pad(clock%60)}:${pad(second)}${fraction?'.'+fraction:''}Z`;
  return {at,time_mode:mode,unknown_time:unknown};
}
function invLooksCtx(context) {
  // The third replay context, exactly what looksContext returns; null/undefined means no looks context.
  if(context===undefined||context===null)return null;
  invKeys(context,['looks','digest'],'invalid_looks_context');
  if(!invList(context.looks,INVESTIGATION_LIMITS.catalogIds))invFail('invalid_looks_context');
  for(const id of context.looks)invIdentifier(id,'invalid_looks_context');
  if(new Set(context.looks).size!==context.looks.length)invFail('invalid_looks_context');
  return {ids:new Set(context.looks),declared:context.looks.length,digest:invDigest(context.digest,'invalid_looks_context')};
}
function invSelectorValue(rule,value) {
  const kind=rule[0];
  if(kind==='int')return typeof value==='number'&&Number.isInteger(value)&&value>=rule[1]&&value<=rule[2];
  if(kind==='enum')return typeof value==='string'&&rule[1].includes(value);
  if(kind==='date')return typeof value==='string'&&value.length===10&&/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value);
  if(kind==='token')return typeof value==='string'&&value.length>=1&&value.length<=rule[1]&&Array.from(value).every(c=>rule[2].includes(c));
  if(kind==='ordinals'||kind==='enum_list') {
    if(!invList(value,kind==='ordinals'?rule[3]:rule[1].length)||value.length<1)return false;
    const ok=kind==='ordinals'?value.every(v=>typeof v==='number'&&Number.isInteger(v)&&v>=rule[1]&&v<=rule[2]):value.every(v=>typeof v==='string'&&rule[1].includes(v));
    for(let i=1;ok&&i<value.length;i++)if(!(typeof value[i-1]==='number'?value[i-1]<value[i]:invCompare(value[i-1],value[i])<0))return false;
    return ok;
  }
  return false;
}
function invCompareSelector(selector) {
  // A comparison: groups a and b, a fork and a measure; the groups are written smaller canonical text first (twin of investigation._compare_selector).
  const keys=Object.keys(selector),rules=INVESTIGATION_LOOKS.selector;
  if(keys.length!==4||!['a','b','fork','measure'].every(k=>Object.hasOwn(selector,k)))invFail('invalid_selector');
  if(!invSelectorValue(rules.fork,selector.fork)||typeof selector.measure!=='string'||!INVESTIGATION_LOOKS.compareMeasures.includes(selector.measure))invFail('invalid_selector');
  const groups=[];
  for(const name of ['a','b']) {
    const group=selector[name];
    if(!invPlain(group)||Object.keys(group).length===0)invFail('invalid_selector');
    for(const key of Object.keys(group))
      if(!INVESTIGATION_LOOKS.compareGroupKeys.includes(key)||!invSelectorValue(rules[key],group[key]))invFail('invalid_selector');
    groups.push(group);
  }
  if(!(invCanonical(groups[0])<invCanonical(groups[1])))invFail('invalid_selector');
  return JSON.parse(invCanonical(selector));
}
function invSelector(kind,selector) {
  if(!invPlain(selector)||Object.keys(selector).length===0)invFail('invalid_selector');
  if(kind==='compare')return invCompareSelector(selector);
  const spec=INVESTIGATION_LOOKS.kindKeys[kind],rules=INVESTIGATION_LOOKS.selector;
  for(const key of Object.keys(selector)) {
    const rule=Object.hasOwn(rules,key)?rules[key]:null;
    if(rule===null||spec.forbidden.includes(key)||!invSelectorValue(rule,selector[key]))invFail('invalid_selector');
  }
  if(spec.required.some(k=>!Object.hasOwn(selector,k)))invFail('invalid_selector');
  if(Object.hasOwn(selector,'grade')&&!Object.hasOwn(selector,'rule'))invFail('invalid_selector');
  if(kind==='rows'&&!INVESTIGATION_LOOKS.rowsNeedOneOf.some(k=>Object.hasOwn(selector,k)))invFail('invalid_selector');
  return JSON.parse(invCanonical(selector));
}
function invEvent(event,known,looks) {
  if(!invPlain(event)||typeof event.action!=='string')invFail('invalid_event_shape');
  const action=event.action,selections={select_entity:['entities','entity'],select_relation:['relations','relation'],run_mutation:['mutations','mutation'],inspect_finding:['findings','finding']};
  let normalized;
  if(Object.hasOwn(selections,action)) {
    invKeys(event,['action','id'],'invalid_event_shape');
    const id=invIdentifier(event.id),[category,singular]=selections[action];
    if(!known[category].has(id))invFail('unknown_'+singular);
    normalized={action,id};
  } else if(action==='set_query') {
    invKeys(event,['action','query'],'invalid_event_shape');
    invKeys(event.query,['from_entity','to_entity'],'invalid_query_shape');
    const from=invIdentifier(event.query.from_entity),to=invIdentifier(event.query.to_entity);
    if(!known.entities.has(from)||!known.entities.has(to))invFail('unknown_entity');
    normalized={action,query:{from_entity:from,to_entity:to}};
  } else if(action==='set_time') {
    invKeys(event,['action','time'],'invalid_event_shape');
    normalized={action,time:invTimeFilter(event.time)};
  } else if(action==='set_lens') {
    invKeys(event,['action','lens'],'invalid_event_shape');
    const lens=event.lens;invKeys(lens,['predicates','direction','max_hops','grade'],'invalid_lens_shape');
    if(!invList(lens.predicates,INVESTIGATION_LIMITS.predicates))invFail('invalid_predicates');
    for(const p of lens.predicates) { invIdentifier(p,'invalid_predicate');if(!known.predicates.has(p))invFail('unknown_predicate'); }
    if(new Set(lens.predicates).size!==lens.predicates.length)invFail('duplicate_predicate');
    if(typeof lens.direction!=='string'||!['outgoing','incoming','association'].includes(lens.direction))invFail('invalid_direction');
    if(typeof lens.max_hops!=='number'||!Number.isInteger(lens.max_hops)||lens.max_hops<1||lens.max_hops>6)invFail('invalid_max_hops');
    if(typeof lens.grade!=='string'||!['C','L','S','all'].includes(lens.grade))invFail('invalid_grade');
    normalized={action,lens:{predicates:[...lens.predicates].sort(invCompare),direction:lens.direction,max_hops:lens.max_hops,grade:lens.grade}};
  } else if(action==='take_look') {
    if(looks===null)invFail('looks_context_missing');
    const kind=event.kind;
    if(typeof kind!=='string'||!INVESTIGATION_LOOKS.kinds.includes(kind))invFail('invalid_look_kind');
    let body;
    if(kind==='declared') {
      invKeys(event,['action','kind','id','looks_digest'],'invalid_event_shape');
      const id=invIdentifier(event.id);
      if(!looks.ids.has(id))invFail('unknown_look');
      body={kind,id};
    } else {
      invKeys(event,['action','kind','selector','looks_digest'],'invalid_event_shape');
      body={kind,selector:invSelector(kind,event.selector)};
    }
    if(invDigest(event.looks_digest)!==looks.digest)invFail('looks_digest_mismatch');
    normalized={action,...body,looks_digest:looks.digest};
  } else if(action==='reset') {
    invKeys(event,['action'],'invalid_event_shape');normalized={action};
  } else invFail('unknown_action');
  if(invBytes(normalized).byteLength>INVESTIGATION_LIMITS.eventBytes)invFail('event_byte_limit');
  return normalized;
}
function invViewState() {
  return {selected_entity:null,selected_relation:null,query:null,time:null,lens:{predicates:[],direction:'outgoing',max_hops:3,grade:'all'},requested_mutation:null,selected_finding:null};
}
function invReduce(state,event) {
  if(event.action==='select_entity') { state.selected_entity=event.id;state.selected_relation=null; }
  else if(event.action==='select_relation') { state.selected_relation=event.id;state.selected_entity=null; }
  else if(event.action==='set_query')state.query={...event.query};
  else if(event.action==='set_time')state.time=event.time===null?null:{...event.time};
  else if(event.action==='set_lens') { state.lens={...event.lens,predicates:[...event.lens.predicates]};state.requested_mutation=null;state.selected_finding=null; }
  else if(event.action==='run_mutation') { state.requested_mutation=event.id;state.selected_finding=null; }
  else if(event.action==='inspect_finding')state.selected_finding=event.id;
  else if(event.action==='reset')Object.assign(state,invViewState());
}
function invSnapshot(log,known,expectedFixture,expectedAnalysis,looks=null) {
  invKeys(log,['schema_version','fixture_digest','analysis_digest','events','head'],'invalid_log_shape');
  if(typeof log.schema_version!=='number'||!Number.isInteger(log.schema_version)||log.schema_version!==1)invFail('unsupported_schema');
  const fixture=invDigest(log.fixture_digest),analysis=invDigest(log.analysis_digest);
  if(fixture!==invDigest(expectedFixture)||analysis!==invDigest(expectedAnalysis))invFail('context_mismatch');
  const head=invDigest(log.head);
  if(!invList(log.events,INVESTIGATION_LIMITS.events))invFail('event_count_limit');
  const records=[];
  for(let i=0;i<log.events.length;i++) {
    const record=log.events[i];invKeys(record,['seq','previous','event','digest'],'invalid_record_shape');
    if(typeof record.seq!=='number'||!Number.isInteger(record.seq)||record.seq!==i+1)invFail('sequence_mismatch');
    const previous=invDigest(record.previous),digest=invDigest(record.digest),event=invEvent(record.event,known,looks);
    if(invCanonical(record.event)!==invCanonical(event))invFail('noncanonical_event');
    records.push({seq:record.seq,previous,event,digest});
  }
  const snapshot={schema_version:1,fixture_digest:fixture,analysis_digest:analysis,events:records,head};
  if(invBytes(snapshot).byteLength>INVESTIGATION_LIMITS.logBytes)invFail('log_byte_limit');
  return snapshot;
}
// The one canonical text that says two look events are the same look (twin of investigation.look_key); hosts key their sets with it.
export function lookKey(event) {
  return invCanonical(event.kind==='declared'?{kind:event.kind,id:event.id}:{kind:event.kind,selector:event.selector});
}
function invTake(looks,seen,event) {
  // A repeat is refused, so the counts are event counts (twin of investigation._take).
  const key=lookKey(event);
  if(seen.has(key))invFail('duplicate_look');
  seen.add(key);
  if(event.kind==='declared')looks.inspected.push(event.id);else looks.asked++;
}
async function invReplayFull(log,context) {
  let head=await invGenesis(log.fixture_digest,log.analysis_digest);
  const state=invViewState(),seen=new Set(),looks=context===null?null:{looks_digest:context.digest,declared:context.declared,inspected:[],asked:0};
  for(const record of log.events) {
    if(record.previous!==head)invFail('previous_digest_mismatch');
    const expected=await invHash({domain:'x3dray.investigation.event.v1',seq:record.seq,previous:head,event:record.event});
    if(record.digest!==expected)invFail('event_digest_mismatch');
    invReduce(state,record.event);
    if(record.event.action==='take_look')invTake(looks,seen,record.event);
    head=expected;
  }
  if(log.head!==head)invFail('head_mismatch');
  if(looks!==null)state.looks=looks;
  Object.assign(state,{schema_version:1,fixture_digest:log.fixture_digest,analysis_digest:log.analysis_digest,event_count:log.events.length,event_head:head});
  state.state_digest=await invHash(state);
  return {state,seen};
}

export async function newLog(fixtureDigest,analysisDigest) {
  const fixture=invDigest(fixtureDigest),analysis=invDigest(analysisDigest);
  return {schema_version:1,fixture_digest:fixture,analysis_digest:analysis,events:[],head:await invGenesis(fixture,analysis)};
}

export async function replay(log,catalog,expectedFixtureDigest,expectedAnalysisDigest,expectedLooks) {
  // Snapshot all validated data before the first await. Caller mutation during
  // asynchronous hashing cannot change the history being checked or reduced.
  const looks=invLooksCtx(expectedLooks),known=invCatalog(catalog);
  const snapshot=invSnapshot(log,known,expectedFixtureDigest,expectedAnalysisDigest,looks);
  return (await invReplayFull(snapshot,looks)).state;
}

export async function appendEvent(log,event,catalog,looksArg) {
  invKeys(log,['schema_version','fixture_digest','analysis_digest','events','head'],'invalid_log_shape');
  const looks=invLooksCtx(looksArg),known=invCatalog(catalog),snapshot=invSnapshot(log,known,log.fixture_digest,log.analysis_digest,looks),normalized=invEvent(event,known,looks);
  const {seen}=await invReplayFull(snapshot,looks);
  if(snapshot.events.length>=INVESTIGATION_LIMITS.events)invFail('event_count_limit');
  if(normalized.action==='take_look')invTake({inspected:[],asked:0},seen,normalized);
  const seq=snapshot.events.length+1,previous=snapshot.head;
  const digest=await invHash({domain:'x3dray.investigation.event.v1',seq,previous,event:normalized});
  snapshot.events.push({seq,previous,event:normalized,digest});snapshot.head=digest;
  if(invBytes(snapshot).byteLength>INVESTIGATION_LIMITS.logBytes)invFail('log_byte_limit');
  return snapshot;
}

// Host adapter for makeTools(B, scene, {looks}): the look count comes from the chain. The log is replayed first (any defect throws, nothing is seeded from
// an unchecked log); the sink is a cache of it, and every new look is appended through appendEvent, in order. A look the chain refuses (a full log, a
// selector outside the alphabet) stays counted and is listed in `unrecorded` by error code, so a count that memory holds and the chain does not is shown.
export async function openLookSink(log,catalog,fixtureDigest,analysisDigest,looks) {
  const state=await replay(log,catalog,fixtureDigest,analysisDigest,looks);
  if(state.looks===undefined)invFail('looks_context_missing');
  const known=new Set(log.events.filter(r=>r.event.action==='take_look').map(r=>lookKey(r.event))),unrecorded=[];
  let asked=state.looks.asked,current=log,queue=Promise.resolve();
  return {
    note(look) {
      const event={action:'take_look',...look,looks_digest:looks.digest},key=lookKey(event);
      if(known.has(key))return;
      known.add(key);if(look.kind!=='declared')asked++;
      queue=queue.then(async()=>{current=await appendEvent(current,event,catalog,looks);}).catch(error=>{unrecorded.push(error.code||'append_failed');});
    },
    get asked() { return asked; },
    get log() { return current; },
    unrecorded,
    settle() { return queue.then(()=>current); },
  };
}

function invExact(value,depth=0) {
  if(depth>INVESTIGATION_LOOKS.maxDepth)invFail('invalid_looks_section');
  if(value===null||typeof value==='boolean')return;
  if(typeof value==='number') { if(!Number.isInteger(value)||Math.abs(value)>INVESTIGATION_LOOKS.maxSafeInt)invFail('invalid_looks_section');return; }
  if(typeof value==='string') { if(Array.from(value).some(ch=>{const cp=ch.codePointAt(0);return cp>=0xd800&&cp<=0xdfff;}))invFail('invalid_looks_section');return; }
  if(Array.isArray(value)) { for(const item of value)invExact(item,depth+1);return; }
  if(invPlain(value)) { for(const key of Object.keys(value)) { invExact(key,depth+1);invExact(value[key],depth+1); }return; }
  invFail('invalid_looks_section');
}

// The ids and the digest of a stored eda.looks section and the repo names in bundle.repos order (twin of investigation.looks_context).
export async function looksContext(section,repos) {
  if(!invPlain(section)||!Array.isArray(repos)||repos.length<1||repos.length>INVESTIGATION_LOOKS.maxRepos)invFail('invalid_looks_section');
  invExact(section);invExact(repos);
  if(repos.some(n=>typeof n!=='string'||n.length<1||Array.from(n).length>1024)||new Set(repos).size!==repos.length)invFail('invalid_looks_section');
  const findings=section.findings,ledger=section.ledger;
  if(!Array.isArray(findings)||!invPlain(ledger)||findings.length>INVESTIGATION_LIMITS.catalogIds)invFail('invalid_looks_section');
  const ids=findings.map(f=>{ if(!invPlain(f))invFail('invalid_looks_section');return invIdentifier(f.id,'invalid_looks_section'); });
  if(new Set(ids).size!==ids.length||typeof ledger.declared!=='number'||ledger.declared!==ids.length)invFail('invalid_looks_section');
  if(invBytes(section).byteLength>INVESTIGATION_LOOKS.maxBytes)invFail('looks_section_too_large');
  return {looks:ids,digest:await invHash({domain:INVESTIGATION_LOOKS.domain,looks:section,repos})};
}
