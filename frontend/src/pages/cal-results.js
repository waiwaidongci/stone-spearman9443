import {calibration} from '../api/calibration.js';
import {cal,loadProjects} from '../stores/calibration.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {bindForm} from '../hooks/forms.js';
import {download} from '../utils/download.js';
import {esc,fmt} from '../utils/format.js';

function basis(kind){
 return {direct:'直接区间',indirect:'间接链路',baseline:'基准定义'}[kind]||kind||'—';
}

function steps(list){
 if(!list||!list.length)return '—';
 return `<ol class="basis-steps">${list.map(s=>`<li><code>${esc(s.statement)}</code><br><small>${esc(s.source)} · 依据 ${esc(s.basis||'未填')} · 区间 ${esc(s.interval)}</small></li>`).join('')}</ol>`;
}

export async function render(id){
 await loadProjects();
 if(!id){
  const runs=await calibration.runs();
  return `<div class="eyebrow">07 / CALIBRATION RESULTS</div><h1>校准结果与发布</h1>
  <p>选择一次冻结运行，查看各台仪器相对基准的最紧范围、依据链、矛盾闭环与接不到基准的仪器。</p>
  ${table(['编号 / 运行','项目','状态','冻结修订','发布'],runs.map(r=>[{html:`<a href="#cal-results/${r.id}">#${r.id} ${esc(r.name)}</a>`},String(r.project_id),{html:status(r.status)},String(r.snapshot_revision),r.published?'已发布':'未发布']),'尚无校准运行。')}`;
 }
 const r=await calibration.run(id);
 const result=r.result;
 const admin=localStorage.getItem('research-role')==='admin';
 return `<div class="eyebrow">07 / CALIBRATION RESULT · RUN ${r.id}</div>
 <h1>${esc(r.name)} ${status(r.status)}</h1>
 <div class="checksum">INPUT SHA256 ${r.checksum}</div>
 <p>冻结修订 ${r.snapshot_revision} · ${r.snapshot.instruments.length} 台仪器 · ${r.snapshot.pairs.length} 条区间 · ${esc(r.created_at)} · ${r.published_at?'已发布于 '+esc(r.published_at):'尚未发布'}</p>
 ${r.status==='failed'?`<div class="error">校准失败：${esc(r.error)}。请到校准工作席修正输入后新建运行。</div>`:''}
 ${result?renderResult(r,result,admin):'<p class="method-note">任务正在队列中，此页每 2 秒刷新。</p>'}
 <details><summary>本次冻结的基准、仪器与区间</summary><pre>${esc(JSON.stringify(r.snapshot,null,2))}</pre></details>
 <div class="toolbar"><button id="cal-export">下载校准 CSV</button><a class="button secondary" href="#calibration">返回校准工作席</a></div>`;
}

function renderResult(r,result,admin){
 const conflicts=result.conflicts||[],unanchored=result.unanchored||[];
 return `<section class="result-intro"><strong>${esc(result.interpretation)}</strong><p>${esc(result.caveat)}</p></section>
 <h2>核心数值</h2>${table(['业务指标','计算值'],Object.entries(result.metrics).map(([k,v])=>[k,fmt(v)]))}
 ${conflicts.length?`<h2>约束冲突 <small>可回算的矛盾闭环</small></h2>
  ${conflicts.map(c=>`<div class="error"><strong>缺口 ${fmt(c.residual)}</strong>（闭环权重和 ${fmt(c.sum_weight)}）<p>${esc(c.statement)}</p><small>${esc(c.check)}</small></div>${table(['顺序','区间','说明','方向','权重'],c.cycle.map((s,i)=>[String(i+1),s.interval,s.source,`${s.from} → ${s.to}`,fmt(s.weight)]))}`).join('')}`:''}
 ${unanchored.length?`<h2>接不到基准的仪器 <small>单独标出</small></h2>
  ${table(['仪器','同组仪器','原因'],unanchored.map(u=>[u.instrument,u.component.join(' / '),u.reason]))}`:''}
 <h2>相对基准的最紧范围</h2>
 ${table(['仪器','下界','上界','宽度','下界依据','上界依据'],(result.ranges||[]).map(x=>[
  x.instrument+(x.kind==='baseline'?'（基准）':''),fmt(x.lower),fmt(x.upper),fmt(x.width),
  basis(x.lower_basis?.kind),basis(x.upper_basis?.kind)]),'没有可锚定的仪器；先解决上面的矛盾闭环或补录接向基准的区间。')}
 ${(result.ranges||[]).filter(x=>x.kind==='anchored').map(x=>`<details><summary>${esc(x.instrument)} 的依据链</summary>
   <p><strong>下界 ${fmt(x.lower)}</strong> · ${esc(x.lower_basis.statement)}</p>${steps(x.lower_basis.steps)}
   <p><strong>上界 ${fmt(x.upper)}</strong> · ${esc(x.upper_basis.statement)}</p>${steps(x.upper_basis.steps)}</details>`).join('')}
 <h2>发布</h2>
 ${r.published_at?`<div class="method-note"><strong>已发布</strong><p>${esc(r.publication.note)}</p><small>范围 ${esc(r.publication.scope||'未限定')} · ${esc(r.publication.author)} · ${esc(r.publication.created_at)}</small></div>`:
  result.publish_ready?(admin?`<form id="cal-publish-form"><label>发布说明（范围、方法边界与使用限制）<textarea name="note" minlength="5" maxlength="2000" required></textarea></label><label>适用范围<input name="scope" maxlength="500" placeholder="如 细砂岩吸水率复测"></label><button type="submit">确认发布并归档</button></form>`:'<div class="method-note">范围与依据已齐备，等待负责人发布。</div>'):
  `<div class="error"><strong>尚不能发布</strong><ul>${result.publish_blockers.map(b=>`<li>${esc(b)}</li>`).join('')}</ul></div>`}`;
}

export function bind(refresh){
 const button=document.getElementById('cal-export');
 if(button)button.onclick=async()=>{
  try{
   const id=Number(location.hash.split('/')[1]);
   download(await calibration.exportRun(id),'calibration-'+id+'.csv');
  }catch(e){document.getElementById('notice').textContent=e.message;}
 };
 bindForm('cal-publish-form',async data=>{
  const id=Number(location.hash.split('/')[1]);
  await calibration.publish(id,data);refresh();
 });
}
