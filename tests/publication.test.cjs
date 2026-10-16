const {test} = require('node:test');
const assert = require('node:assert/strict');
const Publication = require('../static/publication.js');
const body = () => ({action:'publish_closure',requestId:'da05e39b-9862-43bd-86ea-765b2ce66553',revision:5,start:'NOW',selection:[{id:'osm-test',direction:'BOTH'}]});
function storage() {
  const values=new Map();
  return {getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
}
test('reload retains the identical request ID and body without sending anything',()=>{
  const store=storage(), original=body(), first=new Publication(store,['/waze','city','alice']);
  first.stage(original);
  original.selection[0].direction='BA';
  const restored=new Publication(store,['/waze','city','alice']);
  assert.deepEqual(restored.pending,body());
  assert.deepEqual(restored.stage(body()),body());
});
test('another operator, instance or city cannot recover this tab request',()=>{
  const store=storage();new Publication(store,['/waze','city','alice']).stage(body());
  for(const scope of [['/waze','city','bob'],['/other','city','alice'],['/waze','other','alice']]) assert.equal(new Publication(store,scope).pending,null);
});
test('pending publication cannot be replaced with a new request or changed data',()=>{
  const p=new Publication(storage(),['scope']);p.stage(body());
  assert.throws(()=>p.stage({...body(),revision:6}),/pendiente/);
  assert.deepEqual(p.pending,body());
});
test('confirmed or rejected requests can be cleared, permitting a new review',()=>{
  const store=storage(),p=new Publication(store,['scope']);p.stage(body());p.clear();
  assert.equal(p.pending,null);assert.equal(new Publication(store,['scope']).pending,null);
  p.stage({...body(),revision:6});assert.equal(p.pending.revision,6);
});
test('unavailable persistence fails before any pending request can be sent',()=>{
  const s=storage();s.setItem=()=>{throw new Error('quota');};
  const p=new Publication(s,['scope']);assert.throws(()=>p.stage(body()),/quota/);assert.equal(p.pending,null);
});
test('corrupt storage is not silently discarded',()=>{
  const s=storage(),p=new Publication(s,['scope']);s.setItem(p.key,'not json');
  assert.throws(()=>new Publication(s,['scope']),/revisión local/);assert.equal(s.getItem(p.key),'not json');
});

test('template activation recovery preserves template version and operator timing',()=>{
  const store=storage(), payload={...body(),templateId:'rain',templateVersion:3,durationMinutes:120};
  const first=new Publication(store,['/waze','city','alice']);first.stage(payload);
  const restored=new Publication(store,['/waze','city','alice']);
  assert.deepEqual(restored.pending,payload);
  assert.throws(()=>restored.stage({...payload,templateVersion:4}),/pendiente/);
  assert.throws(()=>restored.stage({...payload,durationMinutes:60}),/pendiente/);
});
