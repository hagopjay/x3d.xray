// Pure browser twin of x3dray.investigation. No DOM, network or code execution.
// A chain records interaction intent, not verified facts or publisher identity.
// Canonical strings use UTF-8 and Unicode code-point ordering, matching Python.
// JS has one numeric type: integer-valued numbers are accepted; booleans are not.

export const INVESTIGATION_LIMITS = Object.freeze({events:256,idChars:256,predicates:32,catalogIds:10000,eventBytes:16384,logBytes:262144});

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
function invEvent(event,known) {
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
function invSnapshot(log,known,expectedFixture,expectedAnalysis) {
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
    const previous=invDigest(record.previous),digest=invDigest(record.digest),event=invEvent(record.event,known);
    if(invCanonical(record.event)!==invCanonical(event))invFail('noncanonical_event');
    records.push({seq:record.seq,previous,event,digest});
  }
  const snapshot={schema_version:1,fixture_digest:fixture,analysis_digest:analysis,events:records,head};
  if(invBytes(snapshot).byteLength>INVESTIGATION_LIMITS.logBytes)invFail('log_byte_limit');
  return snapshot;
}
async function invReplaySnapshot(log) {
  let head=await invGenesis(log.fixture_digest,log.analysis_digest);
  const state=invViewState();
  for(const record of log.events) {
    if(record.previous!==head)invFail('previous_digest_mismatch');
    const expected=await invHash({domain:'x3dray.investigation.event.v1',seq:record.seq,previous:head,event:record.event});
    if(record.digest!==expected)invFail('event_digest_mismatch');
    invReduce(state,record.event);head=expected;
  }
  if(log.head!==head)invFail('head_mismatch');
  Object.assign(state,{schema_version:1,fixture_digest:log.fixture_digest,analysis_digest:log.analysis_digest,event_count:log.events.length,event_head:head});
  state.state_digest=await invHash(state);
  return state;
}

export async function newLog(fixtureDigest,analysisDigest) {
  const fixture=invDigest(fixtureDigest),analysis=invDigest(analysisDigest);
  return {schema_version:1,fixture_digest:fixture,analysis_digest:analysis,events:[],head:await invGenesis(fixture,analysis)};
}

export async function replay(log,catalog,expectedFixtureDigest,expectedAnalysisDigest) {
  // Snapshot all validated data before the first await. Caller mutation during
  // asynchronous hashing cannot change the history being checked or reduced.
  const snapshot=invSnapshot(log,invCatalog(catalog),expectedFixtureDigest,expectedAnalysisDigest);
  return invReplaySnapshot(snapshot);
}

export async function appendEvent(log,event,catalog) {
  invKeys(log,['schema_version','fixture_digest','analysis_digest','events','head'],'invalid_log_shape');
  const known=invCatalog(catalog),snapshot=invSnapshot(log,known,log.fixture_digest,log.analysis_digest),normalized=invEvent(event,known);
  await invReplaySnapshot(snapshot);
  if(snapshot.events.length>=INVESTIGATION_LIMITS.events)invFail('event_count_limit');
  const seq=snapshot.events.length+1,previous=snapshot.head;
  const digest=await invHash({domain:'x3dray.investigation.event.v1',seq,previous,event:normalized});
  snapshot.events.push({seq,previous,event:normalized,digest});snapshot.head=digest;
  if(invBytes(snapshot).byteLength>INVESTIGATION_LIMITS.logBytes)invFail('log_byte_limit');
  return snapshot;
}
