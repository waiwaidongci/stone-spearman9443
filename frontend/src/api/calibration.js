import {request,post} from './client.js';
const base='/calibration';
export const calibration={
  projects:()=>request(base+'/projects'),
  project:id=>request(`${base}/projects/${id}`),
  createProject:x=>post(base+'/projects',x),
  updateProject:(id,x)=>request(`${base}/projects/${id}`,{method:'PUT',body:JSON.stringify(x)}),
  addInstrument:(id,name)=>post(`${base}/projects/${id}/instruments`,{name}),
  removeInstrument:(id,instrumentId)=>request(`${base}/projects/${id}/instruments/${instrumentId}`,{method:'DELETE'}),
  addPair:(id,x)=>post(`${base}/projects/${id}/pairs`,x),
  updatePair:(id,pairId,x)=>request(`${base}/projects/${id}/pairs/${pairId}`,{method:'PUT',body:JSON.stringify(x)}),
  removePair:(id,pairId)=>request(`${base}/projects/${id}/pairs/${pairId}`,{method:'DELETE'}),
  runs:id=>request(base+'/runs'+(id?`?project_id=${id}`:'')),
  run:id=>request(`${base}/runs/${id}`),
  createRun:x=>post(base+'/runs',x),
  publish:(id,x)=>post(`${base}/runs/${id}/publish`,x),
  exportRun:id=>request(`${base}/runs/${id}/export`),
};
