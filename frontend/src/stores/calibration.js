import {calibration} from '../api/calibration.js';
export const cal={projects:[],projectId:0,project:null,runId:0,key:''};
export function newKey(){return 'cal-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);}
// Kept in sessionStorage so a page reload (or a dropped response) still retries
// with the same idempotency key instead of creating a second run.
const KEY_STORE='cal-idempotency-key';
export function runKey(){return sessionStorage.getItem(KEY_STORE)||'';}
export function setRunKey(k){sessionStorage.setItem(KEY_STORE,k);}
export function clearRunKey(){sessionStorage.removeItem(KEY_STORE);}
export async function loadProjects(){cal.projects=await calibration.projects();if(!cal.projectId&&cal.projects.length)cal.projectId=cal.projects[0].id;return cal.projects;}
export async function loadProject(id){cal.projectId=id||cal.projectId;if(!cal.projectId){cal.project=null;return null;}cal.project=await calibration.project(cal.projectId);return cal.project;}
