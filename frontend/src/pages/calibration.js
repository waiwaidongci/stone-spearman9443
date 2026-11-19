import {calibration} from '../api/calibration.js';
import {cal,loadProjects,loadProject,newKey,runKey,setRunKey,clearRunKey} from '../stores/calibration.js';
import {table} from '../components/common/table.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';

const STATE_LABELS={queued:'等待计算',running:'计算进行中',completed:'已完成',failed:'失败需修复'};

function stateTag(s){return `<span class="status ${s}">${STATE_LABELS[s]||s}</span>`;}

export async function render(){
 await loadProjects();await loadProject();
 const writable=localStorage.getItem('research-role')!=='viewer';
 const p=cal.project;
 return `<div class="eyebrow">06 / CALIBRATION REGISTER</div><h1>仪器校准与偏差区间</h1>
 <p class="lead">录入成对仪器的吸水率偏差区间（记作 A − B），以一台基准仪器为准，求各台相对基准的最紧偏移范围。直接区间与间接链路会合并成同一组约束一起求解。</p>
 <div class="method-note">区间记法：A − B 的下界与上界。系统把每条区间解释为差分约束，最紧范围是全部约束下该仪器偏移的可行投影，不等同于统计置信区间。基准仪器偏移定义为 0。</div>
 <div class="toolbar">${projectSelect()}</div>
 ${writable?`<details><summary>新建校准项目</summary><form id="cal-project-form" class="inline-form">
  <label>项目名称<input name="name" required minlength="2" maxlength="120" placeholder="如 吸水率仪器比对 2026-11"></label>
  <label>来源文件<input name="source_filename" value="calibration-202611.csv" required minlength="3" maxlength="120"></label>
  <label>基准仪器<input name="baseline_instrument" required maxlength="100" placeholder="如 M0"></label>
  <label>说明<input name="description" maxlength="2000"></label>
  <button type="submit">保存项目</button></form></details>`:''}
 ${p?projectBody(p,writable):'<p class="method-note">尚无校准项目。请先新建项目并登记基准仪器。</p>'}`;
}

function projectSelect(){
 if(!cal.projects.length)return '';
 return `<label>校准项目<select id="cal-project-select">${cal.projects.map(x=>`<option value="${x.id}" ${x.id===cal.projectId?'selected':''}>${esc(x.name)} · 修订 ${x.revision} · ${x.pair_count} 条区间</option>`).join('')}</select></label>`;
}

function instrumentRows(p,writable){
 return p.instruments.map(i=>{
  const degree=p.pairs.filter(x=>x.a===i.name||x.b===i.name).length;
  const cells=[i.name,i.is_baseline?'基准':'成员',degree?`${degree} 条区间已登记`:'尚无可达区间'];
  if(writable)cells.push({html:i.is_baseline?'':`<button class="text" data-del-instrument="${i.id}">移除</button>`});
  return cells;
 });
}

function pairRows(p,writable){
 return p.pairs.map(x=>{
  const cells=[String(x.id),`${x.a} − ${x.b}`,fmt(x.lower),fmt(x.upper),x.basis||'—',x.updated_at];
  if(writable)cells.push({html:`<button class="text" data-edit-pair="${x.id}">编辑</button> <button class="text" data-del-pair="${x.id}">移除</button>`});
  return cells;
 });
}

function runRows(p){
 return p.runs.map(r=>[{html:`<a href="#cal-results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:stateTag(r.status)},String(r.snapshot_revision),r.checksum.slice(0,12),r.published?'已发布':'未发布']);
}

function projectBody(p,writable){
 const instruments=p.instruments||[];
 const instrumentHeaders=['仪器','角色','区间情况',...(writable?['操作']:[])];
 return `<section class="strip"><span>基准 ${esc(p.baseline_instrument)}</span><span>修订 ${p.revision}</span><span>${instruments.length} 台仪器</span><span>${p.pairs.length} 条成对区间</span><span>${p.runs.length} 次运行</span></section>
 ${writable?`<details><summary>切换基准仪器</summary><form id="cal-baseline-form" class="inline-form">
  <label>基准仪器<select name="baseline_instrument">${instruments.map(i=>`<option value="${esc(i.name)}" ${i.name===p.baseline_instrument?'selected':''}>${esc(i.name)}</option>`).join('')}</select></label>
  <button type="submit">保存基准</button></form></details>`:''}
 <h2>仪器清单 <small>基准仪器不可移除</small></h2>
 ${writable?`<form id="cal-instrument-form" class="inline-form"><label>新增仪器<input name="name" required maxlength="100" placeholder="如 M7"></label><button type="submit">加入清单</button></form>`:''}
 ${table(instrumentHeaders,instrumentRows(p,writable),'尚无仪器。')}
 <h2>成对偏差区间 <small>记法：A − B ∈ [下界, 上界]</small></h2>
 ${writable?`<form id="cal-pair-form" class="inline-form">
  <label>仪器 A<select name="a">${instruments.map(i=>`<option value="${esc(i.name)}">${esc(i.name)}</option>`).join('')}</select></label>
  <label>仪器 B<select name="b">${instruments.map(i=>`<option value="${esc(i.name)}">${esc(i.name)}</option>`).join('')}</select></label>
  <label>下界<input name="lower" type="number" step="any" required></label>
  <label>上界<input name="upper" type="number" step="any" required></label>
  <label>校准依据<input name="basis" maxlength="1000" required placeholder="标准样、复测批次或证书编号"></label>
  <button type="submit" id="cal-pair-submit">保存区间</button>
  <button type="button" id="cal-pair-cancel" hidden>取消编辑</button></form>`:''}
 ${table(['编号','A − B','下界','上界','依据','最近更新',...(writable?['操作']:[])],pairRows(p,writable),'尚无成对区间，请先添加仪器并录入区间。')}
 <h2>冻结并计算</h2>
 ${writable?`<form id="cal-run-form" class="worksheet"><label>本次运行名称<input name="name" required minlength="2" maxlength="120" value="${esc(p.name)} · 核算"></label>
  <p>提交时锁定当前修订（${p.revision}）的基准、仪器清单与全部区间，生成 SHA256；之后的编辑不会改动本次快照。写入中断后重试沿用原输入，不新增结果。</p>
  <button type="submit">冻结校准输入并排队 →</button></form>`:'<p class="method-note">观察员可阅读校准记录；分析师与负责人可以发起计算。</p>'}
 <h2>校准运行记录</h2>
 ${table(['编号 / 运行','状态','冻结修订','输入快照','发布'],runRows(p),'尚无校准运行。')}`;
}

export function bind(refresh){
 const sel=document.getElementById('cal-project-select');
 if(sel)sel.onchange=async()=>{await loadProject(+sel.value);refresh();};
 bindForm('cal-project-form',async data=>{const p=await calibration.createProject(data);cal.projectId=p.id;clearRunKey();await loadProjects();refresh();});
 bindForm('cal-baseline-form',async data=>{await calibration.updateProject(cal.projectId,{baseline_instrument:data.baseline_instrument});await loadProjects();refresh();});
 bindForm('cal-instrument-form',async data=>{await calibration.addInstrument(cal.projectId,data.name);await loadProjects();refresh();});
 const form=document.getElementById('cal-pair-form');
 bindForm('cal-pair-form',async data=>{
  const payload={a:data.a,b:data.b,lower:+data.lower,upper:+data.upper,basis:data.basis};
  if(form&&form.dataset.pairId)await calibration.updatePair(cal.projectId,+form.dataset.pairId,payload);
  else await calibration.addPair(cal.projectId,payload);
  await loadProjects();refresh();
 });
 bindForm('cal-run-form',async data=>{
  // The key is created once and only cleared after a confirmed response, so a
  // retry after a dropped connection reuses the frozen snapshot, not a new run.
  const key=runKey()||newKey();setRunKey(key);
  const r=await calibration.createRun({project_id:cal.projectId,name:data.name,idempotency_key:key});
  clearRunKey();await loadProject();location.hash='cal-results/'+r.id;
 });
 document.querySelectorAll('[data-del-pair]').forEach(el=>el.onclick=async()=>{
  try{await calibration.removePair(cal.projectId,+el.dataset.delPair);await loadProjects();refresh();}
  catch(e){document.getElementById('notice').textContent=e.message;}
 });
 document.querySelectorAll('[data-del-instrument]').forEach(el=>el.onclick=async()=>{
  try{await calibration.removeInstrument(cal.projectId,+el.dataset.delInstrument);await loadProjects();refresh();}
  catch(e){document.getElementById('notice').textContent=e.message;}
 });
 document.querySelectorAll('[data-edit-pair]').forEach(el=>el.onclick=()=>{
  const row=cal.project.pairs.find(x=>x.id===+el.dataset.editPair);
  form.dataset.pairId=String(row.id);
  form.elements.a.value=row.a;form.elements.b.value=row.b;
  form.elements.lower.value=row.lower;form.elements.upper.value=row.upper;
  form.elements.basis.value=row.basis;
  document.getElementById('cal-pair-submit').textContent='保存修改';
  document.getElementById('cal-pair-cancel').hidden=false;
  form.scrollIntoView({behavior:'smooth',block:'center'});
 });
 const cancel=document.getElementById('cal-pair-cancel');
 if(cancel)cancel.onclick=()=>refresh();
}
