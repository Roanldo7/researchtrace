'use strict';
const $ = id => document.getElementById(id);
const names = ['任务拆解','资料检索','数据提取','口径核验','人工审核','报告生成','引用检查'];
const PREVIEW=window.RESEARCH_PREVIEW || null;
const labels = {RUNNING:'正在执行', WAITING_REVIEW:'等待人工审核', COMPLETED:'研究完成', INSUFFICIENT:'依据不足 · 未发布', FAILED:'执行失败'};
const providers = {qwen_cn:'通义千问 · 中国',qwen_intl:'通义千问 · 国际',glm:'智谱 GLM',deepseek:'DeepSeek',kimi_cn:'Kimi 开放平台 · 中国',kimi_intl:'Kimi 开放平台 · 国际',doubao:'火山方舟 · 豆包',gemini:'Google Gemini',claude:'Anthropic Claude',openai:'OpenAI'};
let run = null, tab = 'report', timer = null, history = [], reviewDraft = {}, evidenceReturn = null, evalData = null, viewToken = 0;
let modelCatalog = null;
const el = (tag, text, cls) => { const n = document.createElement(tag); if(text !== undefined) n.textContent = text; if(cls) n.className = cls; return n; };
const button = (text, action, cls) => {const n=el('button',text,cls);n.type='button';n.addEventListener('click', action);return n;};
function message(text=''){ $('message').textContent=text; $('message').hidden=!text; }
async function api(path, body){
  if(PREVIEW)throw Error('只读预览不发送网络请求；运行研究请使用本地Python工作台。');
  if(location.protocol==='file:')throw Error('当前打开的是 HTML 文件。请双击 V2 的 run_demo.bat，并在它打开的 http://127.0.0.1 页面操作。');
  let response;try{response=await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
  catch{throw Error('无法连接本地研究服务。请保持 V2 的启动窗口打开，并在启动窗口新打开的页面重试。');}
  let data;try{data=await response.json();}catch{throw Error('本地服务没有返回有效结果；请关闭旧版窗口并重新启动 V2。');}
  if(!response.ok) throw Object.assign(Error(data.error || '请求失败'),{status:response.status}); return data;
}
function fmt(f){return typeof f.value==='number' ? f.value.toLocaleString('en-US',{maximumFractionDigits:4})+f.unit : f.value;}
function fact(id){return [...run.facts,...(run.derived_facts || [])].find(f=>f.fact_id===id);}
function citations(parent, ids){
  const seen=new Set();
  for(const id of ids || []){const f=fact(id);if(!f)continue;for(const sid of f.source_ids){if(seen.has(sid))continue;seen.add(sid);parent.append(button('['+sid+']',e=>showEvidence(id,e.currentTarget,ids),'cite'));}}
}
function liveSourceLinks(parent,ids){
  const seen=new Set();for(const id of ids||[]){const f=fact(id);if(!f)continue;for(const sid of f.source_ids){if(seen.has(sid))continue;seen.add(sid);const d=run.documents.find(x=>x.source_id===sid);if(!d)continue;const a=el('a','['+sid+'] ↗','live-inline');a.href=d.source_url;a.target='_blank';a.rel='noopener noreferrer';a.title=d.title;parent.append(a);}}
}
function showEvidence(id, trigger, related=[]){
  const f=fact(id);if(!f)return;
  evidenceReturn=trigger || document.activeElement;
  if(run.mode==='live'){
    const host=$('evidence-content');host.replaceChildren(el('div','LIVE WEB / 人工核对原文','eyebrow'),el('h3','模型引用陈述'),el('p',f.value,'evidence-value'),el('p','此段是模型基于联网搜索生成的候选陈述，不是网页原文摘录。请打开以下来源核对事实、日期与口径。'));
    for(const sid of f.source_ids){const d=run.documents.find(x=>x.source_id===sid);if(!d)continue;const a=el('a','['+sid+'] '+d.title+' ↗','live-source');a.href=d.source_url;a.target='_blank';a.rel='noopener noreferrer';host.append(a,el('small',d.publisher+' · '+d.verification_status));if(d.search_excerpt)host.append(el('blockquote',d.search_excerpt));}
    openEvidence();return;
  }
  if(f.derived){
    const host=$('evidence-content');host.replaceChildren(el('h3',f.entity+' · '+f.metric),el('div',fmt(f),'evidence-value'),el('p','派生值：'+f.formula),el('p','已披露份额相减；单位为百分点。两项输入均经本次审核采用。'));
    f.input_fact_ids.forEach(fid=>host.append(button('查看输入 '+fid+' · '+fact(fid).period,e=>showEvidence(fid,e.currentTarget),'text-button')));openEvidence();return;
  }
  const d=run.documents.find(d=>d.source_id===f.source_ids[0]), host=$('evidence-content');host.replaceChildren();
  host.append(el('div',d.source_id+' / '+(f.synthetic_test?'合成测试':'公开资料快照'),'eyebrow'),el('h3',f.entity+' · '+f.metric),el('div',fmt(f),'evidence-value'));
  const dl=el('dl');
  for(const [key,value] of [['事实类型',f.value_type==='announced_plan'?'已宣布计划（非已实现）':f.value_type],['研究期间',f.period],['统计口径',f.scope],['状态',f.status==='confirmed'?'已采用':f.status==='excluded'?'已排除':'待审核候选'],['来源',d.publisher+' / '+d.title],['发布日期',d.published_at],['本地定位',d.local_file+' → '+d.source_id+' → '+f.excerpt_locator],['提取与换算',f.raw_value+' '+f.raw_unit+' → '+fmt(f)],['核对记录',d.verification_status+'；采集于'+d.retrieved_at]]){dl.append(el('dt',key),el('dd',value));}
  host.append(dl,el('blockquote',d.excerpts[f.excerpt_locator]));
  const siblings=related.filter(fid=>fid!==id);if(siblings.length){host.append(el('h3','同段其他依据'));siblings.forEach(fid=>host.append(button(fact(fid).entity+' / '+fact(fid).metric+' / '+fact(fid).period,e=>showEvidence(fid,e.currentTarget),'text-button')));}
  if(d.source_url && /^https?:\/\//.test(d.source_url)){const a=el('a','打开原始来源 ↗（需联网）');a.href=d.source_url;a.target='_blank';a.rel='noopener noreferrer';host.append(a);}
  openEvidence();
}
function openEvidence(){ $('evidence').classList.remove('is-collapsed');$('evidence').classList.add('open');document.querySelector('.work-area').classList.remove('evidence-collapsed');$('close-evidence').focus(); }
function closeEvidence(){ $('evidence').classList.remove('open');$('evidence').classList.add('is-collapsed');document.querySelector('.work-area').classList.add('evidence-collapsed');evidenceReturn?.focus(); }
function renderSteps(){
  $('steps').replaceChildren();
  names.forEach((name,index)=>{const s=run?.steps[index], li=el('li'); const b=button('',()=>{tab='trace';renderContent();document.getElementById('trace-'+index)?.setAttribute('open','');$('content').scrollIntoView({block:'start'});},'step '+(s?.status || 'pending'));b.title=s ? name+' · '+s.status : name+' · 尚未开始';b.append(el('span',s?.status==='completed'?'✓':String(index+1).padStart(2,'0'),'step-index'),el('span',name));li.append(b);$('steps').append(li);});
  $('status').textContent=run?(run.mode==='live'&&run.analysis_status==='pending'?'报告草稿待确认':run.mode==='live'&&run.analysis_status==='approved'?'联网报告已审核':run.status==='COMPLETED'&&run.quality_status==='with_gaps'?'完成 · 有资料缺口':labels[run.status]):'尚未开始';
  $('status').className='status '+(run?.status==='WAITING_REVIEW'?'waiting':run?.status==='INSUFFICIENT'||run?.quality_status==='with_gaps'?'gaps':run?.status==='COMPLETED'?'done':run?.status==='FAILED'?'failed':'');
  $('run-label').textContent=run?'任务 '+run.run_id+(run.scenario==='exception'?' · 异常测试':' · 正常研究'):'准备开始';
  $('fact-count').textContent=run?.facts.length || 0;
  $('start').disabled=Boolean(PREVIEW)||run?.status==='RUNNING';
  if(run){const unresolved=run.conflicts.filter(c=>c.status==='unresolved').length;
    const waiting=run.status==='WAITING_REVIEW'?'等待中（提交后结算）':(run.waiting_ms/1000).toFixed(1)+'s';
    const calls=run.model_calls.reduce((n,x)=>n+(Number(x.real_model_calls)||0),0), tokens=run.model_calls.filter(x=>x.token_usage?.input_tokens!=null).reduce((n,x)=>n+x.token_usage.input_tokens+x.token_usage.output_tokens,0);
    $('stats').replaceChildren(el('span',`已读 ${run.document_ids.length} 份资料 · 提取 ${run.facts.length} 条事实 · 未决冲突 ${unresolved} · 已采用 ${run.approved_fact_ids.length}`),el('span',`程序 ${(run.program_ms/1000).toFixed(3)}s / 审核${waiting} · 真实模型调用 ${calls}`+(calls?` / ${tokens} Token`:' / Token不适用')));
  }else $('stats').replaceChildren(el('span','七步执行 · 中间结果自动保存'),el('span','真实模型调用 0 · Token / 费用：不适用'));
}
function completedReviewCount(){return run.review_items.filter(i=>{const d=reviewDraft[i.item_id]||{};return d.selection&&((d.selection!=='exclude'&&!i.conflict)||d.reason?.trim());}).length;}
function renderReview(){
  const host=$('review'); host.hidden=run?.status!=='WAITING_REVIEW';host.replaceChildren();if(host.hidden)return;
  const done=completedReviewCount();
  const live=run.mode==='live',head=el('div',undefined,'review-heading'), text=el('div'), controls=el('div',undefined,'review-controls');text.append(el('h2',live?'联网陈述待核对':'关键事实待确认'),el('p',live?`已填写 ${done} / ${run.review_items.length} 项。请打开网页原文逐条核对后决定采用或排除。`:`已填写 ${done} / ${run.review_items.length} 项。可批量填入常规项；请核对来源，冲突与疑点单独处理。`));
  controls.append(button(live?'批量填入待核对项':'批量填入常规项',fillRoutineReview));controls.append(button('定位待处理',focusReviewIssue),button('展开 / 收起审核清单',()=>{list.hidden=!list.hidden;}));head.append(text,controls);host.append(head);
  if(run.scenario==='exception')host.append(el('p','异常测试：工具首次超时已重试；T01为人为注入的错误候选，不能作为真实研究事实。','gap'));
  const list=el('div',undefined,'review-list');
  for(const item of run.review_items){
    const row=el('div',undefined,'review-row'), label=el('div');row.id='review-'+item.item_id;label.append(el('strong',item.entity+' · '+item.metric+' / '+(item.period || fact(item.candidate_ids[0]).period)),el('small',live?'模型已给出网页引用；请打开原文核对':'单一来源 · 尚无独立交叉验证'));
    if(live)label.append(el('p',fact(item.candidate_ids[0]).value,'live-claim'));
    for(const fid of item.candidate_ids){const advice=fact(fid).model_suggestion;if(advice)label.append(el('small','模型建议：'+(advice.supported?'摘录支持':'建议复核')+' · '+advice.reason));}
    for(const fid of item.candidate_ids)label.append(button('查看来源 '+fact(fid).source_ids.join('、'),e=>showEvidence(fid,e.currentTarget),'text-button'));
    const select=el('select');select.setAttribute('aria-label',item.entity+' '+item.metric+' '+(item.period || '')+' 审核决定');select.id='select-'+item.item_id;
    const prompt=el('option','请选择审核决定');prompt.value='';select.append(prompt);
    for(const fid of item.candidate_ids){const f=fact(fid);const option=el('option',(f.synthetic_test?'[合成测试 · 禁用] ':'采用 ')+f.source_ids[0]+' · '+(typeof f.value==='number'?fmt(f):f.metric));option.value=fid;option.disabled=f.synthetic_test;select.append(option);}
    const exclude=el('option','暂不使用该事实');exclude.value='exclude';select.append(exclude);
    const input=el('input');input.type='text';input.id='reason-'+item.item_id;input.maxLength=300;input.placeholder=item.conflict?'请说明冲突处理依据':'排除时必填理由';input.setAttribute('aria-label',item.entity+' '+item.metric+' '+(item.period || '')+' 审核理由');
    const saved=reviewDraft[item.item_id] || {}; select.value=saved.selection || ''; input.value=saved.reason || '';
    const update=()=>{reviewDraft[item.item_id]={selection:select.value,reason:input.value};sessionStorage.setItem('review-'+run.run_id,JSON.stringify(reviewDraft));text.querySelector('p').textContent=`已填写 ${completedReviewCount()} / ${run.review_items.length} 项。请核对来源后提交审核。`;};
    select.addEventListener('change',update);input.addEventListener('input',update);
    row.append(label,select,input);list.append(row);
  }
  host.append(list);
  const actions=el('div',undefined,'review-actions');actions.append(el('span',live?'排除的陈述不会进入模型写作；至少需采用两条。':'不支持任意改写数字。排除后，对应内容显示缺失。'));
  if(live){const key=el('input');key.id='review-key';key.type='password';key.autocomplete='off';key.placeholder='服务重启后在此重新输入 API Key';key.setAttribute('aria-label','联网报告写作所需 API Key');actions.append(key);}
  actions.append(button('提交审核并生成报告 →',submitReview,'primary'));host.append(actions);
}
function suggestRoutineDecisions(items,facts,draft,live=false,documents=[]){
  const lookup=new Map(facts.map(f=>[f.fact_id,f])),next={...draft};let added=0,held=0;
  const sourced=new Set(documents.filter(d=>/^https?:\/\//.test(d.source_url||'')).map(d=>d.source_id));
  for(const item of items){
    if(next[item.item_id]?.selection)continue;
    const f=item.candidate_ids.length===1?lookup.get(item.candidate_ids[0]):null;
    if(!item.conflict&&f&&!f.synthetic_test&&f.model_suggestion?.supported!==false&&(!live||f.source_ids?.length&&f.source_ids.every(id=>sourced.has(id)))){
      next[item.item_id]={selection:f.fact_id,reason:next[item.item_id]?.reason||''};added++;
    }else held++;
  }
  return {draft:next,added,held};
}
function fillRoutineReview(){
  const result=suggestRoutineDecisions(run.review_items,run.facts,reviewDraft,run.mode==='live',run.documents||[]);
  reviewDraft=result.draft;sessionStorage.setItem('review-'+run.run_id,JSON.stringify(reviewDraft));renderReview();
  message(`已填入 ${result.added} 项；${result.held} 项仍需单独判断。${run.mode==='live'?'请打开原文逐条核对，再人工提交审核。':'请核对来源后提交审核。'}`);
}
function firstReviewIssue(){
  for(const item of run.review_items){const d=reviewDraft[item.item_id]||{};
    if(!d.selection)return {item,field:'select',text:'请选择 '+item.entity+' / '+item.metric+' 的审核决定'};
    if((d.selection==='exclude'||item.conflict)&&!d.reason?.trim())return {item,field:'reason',text:'请填写 '+item.entity+' / '+item.metric+' 的处理理由'};
  }
  return null;
}
function focusReviewIssue(){
  const issue=firstReviewIssue();if(!issue){message('所有审核项已填写。请核对来源后提交审核。');return false;}
  const list=document.querySelector('.review-list');list.hidden=false;
  const target=$(issue.field+'-'+issue.item.item_id);target.scrollIntoView({block:'center'});target.focus();message(issue.text);return true;
}
async function submitReview(){
  try{
    if(focusReviewIssue())return;
    message();
    const decisions=run.review_items.map(i=>{const d=reviewDraft[i.item_id] || {};if(!d.selection)throw Error('请处理全部审核事项：'+i.entity+' / '+i.metric);return {item_id:i.item_id,selected_fact_id:d.selection==='exclude'?null:d.selection,reason:d.reason || ''};});
    const key=run.mode==='live'?$('review-key')?.value.trim():undefined;
    await api('/api/runs/'+run.run_id+'/review',{decisions,api_key:key||undefined});message();await loadRun(run.run_id);
  }catch(e){message(e.message);$('message').scrollIntoView({block:'center'});}
}
async function decideAnalysis(decision){
  try{
    const reason=$('analysis-reason')?.value.trim()||'';
    if(decision==='reject'&&!reason)throw Error('请写明拒绝草稿的原因');
    await api('/api/runs/'+run.run_id+'/analysis-review',{decision,reason});message();await loadRun(run.run_id);
  }catch(e){message(e.message);}
}
function renderAnalysis(host){
  if(run?.mode!=='hybrid')return;
  const status=run.analysis_status,panel=el('section',undefined,'analysis-panel');
  panel.append(el('div','REAL MODEL / 人工确认后发布','eyebrow'));
  panel.append(el('h2',status==='approved'?'模型分析报告 · 已通过':status==='pending'?'模型分析草稿 · 等待审核':'模型分析状态'));
  if(status==='generating')panel.append(el('p','模型正在基于已审核事实生成六节分析；下方本地研究报告已经完成。'));
  else if(status==='failed'){
    panel.append(el('p','模型草稿未通过生成或证据检查：'+run.analysis_error,'gap'));
    const retry=el('div',undefined,'analysis-actions'),key=el('input');key.type='password';key.placeholder='重试所需 API Key';key.autocomplete='off';key.setAttribute('aria-label','重试模型分析的 API Key');
    retry.append(key,button('重新生成模型分析',async()=>{try{if(!key.value.trim())throw Error('请输入本次重试的 API Key');await api('/api/runs/'+run.run_id+'/analysis-retry',{api_key:key.value.trim()});key.value='';await loadRun(run.run_id);}catch(e){message(e.message);}}));panel.append(retry);
  }
  else if(status==='rejected')panel.append(el('p','本次模型草稿已拒绝。下方本地研究报告仍可查看。','gap'));
  if(run.analysis_draft){
    for(const section of run.analysis_draft.sections){panel.append(el('h3',section.title));const p=el('p',section.text);citations(p,section.fact_ids);panel.append(p);}
    if(status==='pending'){
      panel.append(el('p','请核对每段分析与右侧来源。通过后才生成可下载的模型报告。','muted'));
      const actions=el('div',undefined,'analysis-actions'),reason=el('input');reason.id='analysis-reason';reason.placeholder='拒绝草稿时填写原因';reason.maxLength=300;reason.setAttribute('aria-label','拒绝模型草稿的原因');
      actions.append(button('确认并发布模型报告',()=>decideAnalysis('approve'),'primary'),reason,button('拒绝草稿',()=>decideAnalysis('reject')));panel.append(actions);
    }
    if(status==='approved'){const link=el('a','下载模型报告 Markdown ↓','download'),pdf=el('a','下载模型报告 PDF ↓','download');link.href='/api/runs/'+run.run_id+'/model-report';pdf.href='/api/runs/'+run.run_id+'/model-report.pdf';pdf.download='model_analysis_report.pdf';panel.append(link,pdf);}
  }
  host.append(panel);
}
function renderFeedback(host){
  const panel=el('section',undefined,'feedback-panel');panel.append(el('h3','试用反馈'));
  panel.append(el('p','完成一次研究后，记录最影响理解或判断的地方。反馈保存在这次本地任务中。'));
  const rating=el('select');rating.setAttribute('aria-label','这次结果有多大帮助');
  for(let i=1;i<=5;i++){const option=el('option',i+'分'+(i===1?' · 几乎无帮助':i===5?' · 很有帮助':''));option.value=String(i);rating.append(option);}rating.value='3';
  const area=el('select');area.setAttribute('aria-label','问题发生在哪个环节');
  for(const [value,label] of [['source','来源与口径'],['review','人工审核'],['report','报告分析'],['navigation','页面操作']]){const option=el('option',label);option.value=value;area.append(option);}
  const note=el('textarea');note.rows=2;note.maxLength=400;note.placeholder='具体哪里好用或卡住了？请写8字以上。';note.setAttribute('aria-label','试用反馈内容');
  const status=el('span',undefined,'feedback-status');const submit=button('保存反馈',async()=>{try{
    const result=await api('/api/runs/'+run.run_id+'/feedback',{rating:Number(rating.value),area:area.value,note:note.value});
    status.textContent='已保存至本地任务（共 '+result.count+' 条）';note.value='';
  }catch(e){status.textContent=e.status===404?'本地服务仍运行旧版接口。请关闭启动窗口，再双击 V2 的 run_demo.bat，刷新页面后重试。':e.message;}});
  panel.append(rating,area,note,submit,status);host.append(panel);
}
function renderLiveReport(host){
  const draft=run.analysis_draft, article=el('article');article.append(el('div','LIVE WEB AGENT / '+run.run_id,'report-meta'),el('h2','联网研究：'+run.topic,'report-title'));
  const state=run.analysis_status;
  article.append(el('p',state==='approved'?'报告已通过人工确认，可在上方下载。':state==='rejected'?'报告草稿已被拒绝，本次不发布。':'模型根据已审核陈述生成草稿；请再次核对推论与引用后决定是否发布。','live-report-note'));
  for(const section of draft?.sections||[]){article.append(el('h2',section.title));const p=el('p',section.text);liveSourceLinks(p,section.fact_ids);article.append(p);}
  article.append(el('h2','本次引用的网页'));for(const d of run.documents){if(!run.approved_fact_ids.some(id=>fact(id)?.source_ids.includes(d.source_id)))continue;const a=el('a','['+d.source_id+'] '+d.title+' ↗','live-source');a.href=d.source_url;a.target='_blank';a.rel='noopener noreferrer';article.append(a);}
  if(state==='pending'){const actions=el('div',undefined,'live-report-actions'),reason=el('input');reason.id='analysis-reason';reason.maxLength=300;reason.placeholder='拒绝草稿时填写原因';actions.append(button('确认并发布联网报告',()=>decideAnalysis('approve'),'primary'),reason,button('拒绝草稿',()=>decideAnalysis('reject')));article.append(actions);}
  host.append(article);renderFeedback(host);
}
function renderReport(host){
  if(run?.bundled_example)host.append(el('p',run.sample_review,'gap'));
  if(run?.status==='INSUFFICIENT'){host.append(el('h2','依据不足，无法发布正式研究摘要'),el('p','七步流程已结束，未生成正式报告或投资方向；引用覆盖：不适用。','gap'));(run.evidence_assessment?.core_missing || []).forEach(x=>host.append(el('p','缺少核心依据：'+x)));renderFeedback(host);return;}
  if(!run || run.status!=='COMPLETED'){
    const empty=el('div',undefined,'empty');empty.append(el('div','≡','document-icon'),el('h2',run?.status==='WAITING_REVIEW'?'让事实先通过你的审核':run?.status==='RUNNING'?'研究步骤正在执行':run?.status==='FAILED'?'本次研究未完成':'从一个研究问题开始'));
    empty.append(el('p',run?.status==='WAITING_REVIEW'?'候选数据与来源已经保存。审核完成后，报告将基于你的选择组合生成。':run?.status==='FAILED'?run.error:'输入主题，工作台将依次整理资料、提取数据，并在关键事实处等待你确认。'));
    const flow=el('div',undefined,'empty-path');['任务拆解','→','证据核验','→','人工确认','→','研究摘要'].forEach(x=>flow.append(el('span',x)));empty.append(flow);
    if(run?.status==='FAILED'&&run.mode==='live'&&run.review_decisions?.length){const retry=el('div',undefined,'live-report-actions'),key=el('input'),model=el('input');key.type='password';key.autocomplete='off';key.placeholder='本次重试的 '+(providers[run.provider]||run.provider)+' API Key';model.value=run.model_name||'';model.setAttribute('aria-label','重试使用的模型');retry.append(key,model,button('重试报告写作',async()=>{try{if(!key.value.trim())throw Error('请输入本次重试的 API Key');await api('/api/runs/'+run.run_id+'/live-retry',{api_key:key.value.trim(),model_name:model.value.trim()});key.value='';await loadRun(run.run_id);}catch(e){message(e.message);}},'primary'));empty.append(retry);}
    else if(run?.status==='FAILED'&&(run.mode==='hybrid'||run.mode==='live'))empty.append(button('重新输入 Key 并创建新任务',async()=>{const mode=run.mode;await $('new').onclick();$('model-mode').value=mode==='live'?'live':'openai';syncMode();$('api-key').focus();},'primary'));
    if(!run){const recent=history.filter(r=>(r.mode==='hybrid'?'openai':r.mode)===$('model-mode').value);if(recent.length){const h=el('div',undefined,'history');h.append(el('strong','本模式最近研究'));recent.slice(0,3).forEach(r=>h.append(button(r.topic+' · '+labels[r.status],()=>loadRun(r.run_id))));empty.append(h);}}
    host.append(empty);return;
  }
  if(run.mode==='live'){renderLiveReport(host);return;}
  renderAnalysis(host);
  const report=run.report, article=el('article');article.append(el('div','RESEARCH REPORT / '+run.run_id,'report-meta'),el('h2','研究报告：'+report.title,'report-title'),el('div',report.subtitle,'report-sub'));
  if(run.missing_fields?.length)article.append(el('div','未采用或缺失：'+run.missing_fields.join('；'),'gap'));
  else if(!run.publishable)article.append(el('div','历史运行结果，未使用当前发布门槛与补充资料；请查看正式样例。','gap')); 
  if(report.scenario_label)article.append(el('p',report.scenario_label,'gap'));
  report.sections.forEach(section=>{article.append(el('h2',section.title));section.blocks.forEach(b=>{
    if(b.type==='table'){const wrap=el('div',undefined,'table-scroll'),t=el('table'),head=el('thead'),tr=el('tr');b.headers.forEach(h=>tr.append(el('th',h)));head.append(tr);t.append(head);const body=el('tbody');b.rows.forEach(row=>{const tr=el('tr');row.cells.forEach((c,i)=>{const td=el('td',c);if(i===row.cells.length-1)citations(td,row.fact_ids);tr.append(td);});body.append(tr);});t.append(body);wrap.append(t);article.append(wrap);
    }else{if(b.type==='subsection')article.append(el('h3',b.title));const p=el('p',b.text);citations(p,b.fact_ids);article.append(p);}
  });});
  article.append(el('h2','数据来源'));report.sources.forEach(d=>{const row=el('div',undefined,'source-line');const f=run.facts.find(f=>f.source_ids.includes(d.source_id)&&report.used_fact_ids.includes(f.fact_id));row.append(button('['+d.source_id+']',e=>showEvidence(f.fact_id,e.currentTarget),'cite'),el('span',d.publisher+' · '+d.title+' · '+d.published_at));article.append(row);});
  const q=el('div',undefined,'quality');q.append(el('span','引用定位检查通过'),el('span','可见字符 '+run.quality_checks.visible_characters+' / 1500'),el('span','报告引用定位覆盖 '+(run.citation_coverage===null?'N/A':Math.round(run.citation_coverage*100)+'%')));article.append(q);host.append(article);renderFeedback(host);
}
function renderData(host){
  host.append(el('p',run?.mode==='live'?'这里是模型联网检索后提取的带网页引用陈述；尚非原网页摘录。点击来源并打开原文核对。':'原文数值经规则抽取并标准化。单位统一不代表统计口径相同；点击来源查看原始值。','data-note'));
  if(!run?.facts.length){host.append(el('p','开始研究后，这里显示当次提取的候选事实。','muted'));return;}
  const wrap=el('div',undefined,'table-scroll'), table=el('table'), tr=el('tr');['主体 / 指标','数值','期间 / 状态','证据'].forEach(x=>tr.append(el('th',x)));const th=el('thead');th.append(tr);table.append(th);const tbody=el('tbody');
  [...run.facts,...(run.derived_facts || [])].forEach(f=>{const row=el('tr'), label=el('td');label.append(el('strong',f.entity),el('div',f.metric,'state-text'));const value=el('td');value.append(el('span',fmt(f),typeof f.value==='string'?'fact-text':''));const status=el('td');status.append(el('div',f.period+(f.value_type==='announced_plan'?' · 计划':'')),el('div',f.synthetic_test?'合成测试':f.status==='confirmed'?'已采用':f.status==='excluded'?'已排除':'待审核','state-text'));const cite=el('td');citations(cite,[f.fact_id]);row.append(label,value,status,cite);tbody.append(row);});table.append(tbody);wrap.append(table);host.append(wrap);
}
function renderTrace(host){
  host.append(el('p',run?.mode==='live'?'本次使用'+(providers[run.provider]||run.provider)+'；检索步骤：'+(run.steps?.[1]?.tool||'待执行')+'。下方保存搜索动作、来源、候选陈述和人工审核。模型引用仍需打开原网页核对。':run?.mode==='hybrid'?'模型在第2步根据已检索资料选择下一个本地工具；规则抽取和研究报告在本地执行。模型还会提供证据建议，并在审核后起草分析报告。':'以下是实际执行状态与保存的输入输出。MockLLM为规则契约；没有真实模型推理或Token统计。','trace-note'));
  if(!run){host.append(el('p','尚未创建任务。'));return;}
  if(run.mode==='hybrid'){
    const panel=el('section',undefined,'eval-card');panel.append(el('h3','Agent 工具决策 · 本地快照'));
    const decisions=run.agent_decisions||[];
    if(!decisions.length)panel.append(el('p','尚无决策记录；历史任务可能创建于此功能上线之前。'));
    for(const d of decisions){const row=el('p',undefined,'agent-decision'),ids=d.returned_document_ids||[];row.append(el('strong',String(d.round).padStart(2,'0')+' / '+(d.action==='finish'?'结束检索':d.action||'选择失败')),document.createTextNode(' · '+(d.reason||d.error||'')+' · '+(ids.length?ids.join('、'):'本轮无资料')));panel.append(row);}
    panel.append(el('p','模型只能选择四类本地 Mock 工具或结束；最多4轮。原始选择和工具结果保存在第2步产物中。'));host.append(panel);
  }
  if(run.mode==='live'){const panel=el('section',undefined,'eval-card');panel.append(el('h3','联网搜索动作'));for(const a of run.tool_calls||[])panel.append(el('p',(a.type||'未知动作')+' · '+(a.queries||[]).join('；')+(a.url||'')+' · '+a.status));if(!run.tool_calls.length)panel.append(el('p','尚无完成的搜索动作。'));host.append(panel);}
  run.steps.forEach((s,i)=>{const d=el('details');d.id='trace-'+i;d.append(el('summary',String(i+1).padStart(2,'0')+' / '+s.name+' · '+s.status+(s.elapsed_ms!==undefined?' · '+s.elapsed_ms+'ms':'')),el('pre',JSON.stringify(s,null,2)));host.append(d);});
  for(const [name,value] of [['工具调用记录',run.tool_calls],['模型调用与片段预算',{model_calls:run.model_calls,extraction_context:run.context_log,writing_context:run.write_context_log}],['审核记录',run.review_decisions],['缺口与提示',{notices:run.warnings,evidence:run.evidence_assessment}]]){const d=el('details');d.append(el('summary',name),el('pre',JSON.stringify(value,null,2)));host.append(d);}
}
function duplicateSources(documents){
  const seen=new Map(),pairs=[];
  for(const d of documents||[]){
    try{const url=new URL(d.source_url),docid=url.searchParams.get('docid');const key=docid?url.host+url.pathname+'?docid='+docid:url.origin+url.pathname+url.search;
      if(seen.has(key))pairs.push(seen.get(key)+'、'+d.source_id);else seen.set(key,d.source_id);
    }catch{}
  }
  return pairs;
}
function renderEval(host){
  host.append(el('h2','质量评测'));
  if(run?.mode==='live'||!run&&$('model-mode').value==='live'){
    if(!run){host.append(el('p','当前联网空间尚无研究任务。完成研究后，这里显示本次运行的检查记录。','data-note'));return;}
    const calls=run.model_calls.reduce((n,c)=>n+(Number(c.real_model_calls)||0),0),duplicates=duplicateSources(run.documents);
    const card=el('section',undefined,'eval-card');card.append(el('h3','本次联网任务'),el('strong',`${run.approved_fact_ids.length} 条已采用陈述`),
      el('p',`检索动作 ${run.tool_calls.length} 次 · 返回网页链接 ${run.documents.length} 条 · 真实模型调用 ${calls} 次。`),
      el('p',run.analysis_status==='approved'?'报告已通过人工最终确认。':run.analysis_status==='pending'?'报告草稿等待人工最终确认。':'报告状态：'+(run.analysis_status||run.status)));
    host.append(card);
    if(duplicates.length)host.append(el('p','同一文档的重复链接：'+duplicates.join('；')+'。这些链接不能当作独立来源。','gap'));
    host.append(el('p','以上是流程与审核记录；网页陈述是否准确，仍以打开原文逐条核对为准。','data-note'));
    return;
  }
  host.append(el('p','行为回归、模型探针和人工报告审核分别记录。行为案例通过数不代表模型准确率。','data-note'));
  if(!evalData){host.append(el('p',PREVIEW?'只读预览不加载本地评测记录。':'正在读取本地评测记录。'));return;}
  const offline=evalData.offline,model=evalData.model_probe;
  const a=el('section',undefined,'eval-card');a.append(el('h3','固定行为案例'));
  if(offline){a.append(el('strong',`${offline.passed} / ${offline.cases} 通过`));a.append(el('p','覆盖资料提取、行业研究与公司财务对比的发布门槛、派生输入失效。期望值来自固定样例与规则契约，并非独立事实准确率。'));
    const failed=offline.results.filter(x=>!x.passed);if(failed.length)a.append(el('p','未通过：'+failed.map(x=>x.id).join('、'),'gap'));
  }else a.append(el('p','尚未运行离线案例；在V2目录执行 python -B evaluate.py。'));
  const b=el('section',undefined,'eval-card');b.append(el('h3','真实模型证据探针'));
  if(model){b.append(el('strong',`${model.matched} / ${model.candidates} 与预期一致`));b.append(el('p','单次、固定候选的模型探针；含故意篡改的事实。样本量小，不代表生产准确率。'));
    const missed=model.details.filter(x=>x.expected!==x.supported);if(missed.length)b.append(el('p','需复看：'+missed.map(x=>x.fact_id).join('、'),'gap'));
  }else b.append(el('p','尚无真实模型探针记录；需要使用本人 Key 显式运行 evaluate.py --model。'));
  const c=el('section',undefined,'eval-card'),states=evalData.analysis||{},feedback=evalData.feedback||{};
  c.append(el('h3','草稿审核与试用反馈'),el('p',`模型草稿：待审 ${states.pending||0} · 已通过 ${states.approved||0} · 已拒绝 ${states.rejected||0} · 失败 ${states.failed||0}`));
  c.append(el('p',feedback.count?`本地试用反馈 ${feedback.count} 条，帮助程度均分 ${feedback.average_rating}/5；仅描述已收集样本。`:'尚无真实试用反馈记录；可按试用任务卡完成观察后填写。'));
  host.append(a,b,c);
}
function renderContent(){
  document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===tab)));
  const downloadable=run?.status==='COMPLETED'&&run?.publishable!==false;
  $('download').hidden=!downloadable;$('download-pdf').hidden=!downloadable||Boolean(PREVIEW);$('download').textContent='下载 Markdown ↓';if(run){$('download').href=PREVIEW?PREVIEW.downloadUrl:'/api/runs/'+run.run_id+'/report';if(!PREVIEW)$('download-pdf').href='/api/runs/'+run.run_id+'/report.pdf';}if(PREVIEW)$('download').download='research_report.md';
  const host=$('content');host.replaceChildren();if(tab==='report')renderReport(host);else if(tab==='data')renderData(host);else if(tab==='trace')renderTrace(host);else renderEval(host);
}
function render(){renderSteps();renderReview();renderContent();}
function activeNav(id){document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.id===id));}
function updateComposerSummary(){$('summary-action').textContent=$('composer').open?'收起设置 ⌃':'展开设置 ▾';}
$('composer').addEventListener('toggle',updateComposerSummary);
function resetModels(){modelCatalog=null;$('model-check-status').hidden=true;$('live-model').replaceChildren(new Option('请先检测模型',''));}
async function detectModels(){
  const provider=$('live-provider').value,key=$('api-key').value.trim();if(!provider)throw Error('请先选择模型服务商');if(!key)throw Error('请先输入所选服务商的 API Key');
  $('detect-models').disabled=true;$('model-check-status').hidden=false;$('model-check-status').textContent='正在向'+providers[provider]+'检测推荐模型…';
  try{const health=await api('/api/health');if(health.api_version!==7)throw Error('当前页面连接的是旧版服务。请关闭旧版启动窗口，双击 V2 的 run_demo.bat 并使用新打开的页面。');
    const catalog=await api('/api/models',{provider,api_key:key});modelCatalog=catalog;
    const confirmed=new Set(catalog.catalog_models||catalog.models);
    $('live-model').replaceChildren(...catalog.models.map(id=>new Option(id+(confirmed.has(id)?'':' · 目录未列出，运行时验证'),id)));
    $('live-model').value=catalog.selected_model;
    $('model-check-status').textContent=catalog.detection+'；推荐 '+catalog.selected_model+'。可在下拉框改选'+(provider==='deepseek'?'，Flash 更省费用':'')+'。';return catalog;}
  catch(e){modelCatalog=null;$('model-check-status').textContent=e.message;throw e;}
  finally{$('detect-models').disabled=false;}
}
async function loadRun(id,remember=true){
  clearTimeout(timer);const token=++viewToken;
  try{const next=await api('/api/runs/'+id);if(token!==viewToken)return;if(run?.run_id!==id){$('evidence').classList.remove('open');$('evidence-content').replaceChildren(el('h3','每一个数字，都有出处'),el('p','点击当前任务的数据或引用查看来源与口径。'));}run=next;const mode=run.mode==='hybrid'?'openai':run.mode;if(remember){localStorage.setItem('research-run',id);localStorage.setItem('research-run-'+mode,id);localStorage.setItem('research-mode',mode);}activeNav(remember?'current':'sample');document.querySelector('.badge').textContent='本任务 · '+(run.mode==='live'?'联网研究 Agent · '+(providers[run.provider]||run.provider):run.mode==='hybrid'?'本地资料＋真实模型':'离线 Mock');$('topic').value=run.topic;$('scenario').value=run.scenario;$('model-mode').value=mode;$('api-key').value='';$('live-provider').value=run.mode==='live'?run.provider:'';$('custom-model').value='';resetModels();if(run.mode==='live')$('live-model').replaceChildren(new Option(run.model_name,run.model_name));syncMode();
    try{reviewDraft=JSON.parse(sessionStorage.getItem('review-'+id)||'{}');}catch{reviewDraft={};}
    $('composer').open=!['COMPLETED','INSUFFICIENT','WAITING_REVIEW'].includes(run.status);
    render();if((run.status==='RUNNING'||run.analysis_status==='generating')&&!document.hidden)timer=setTimeout(()=>loadRun(id),450);
  }catch(e){if(token===viewToken)message(e.message);}
}
function clearWorkspace(){
  clearTimeout(timer);++viewToken;run=null;reviewDraft={};tab='report';message();activeNav('new');$('topic').value=$('topic').defaultValue;$('scenario').value='normal';$('api-key').value='';$('live-provider').value='';$('custom-model').value='';resetModels();$('composer').open=true;closeEvidence();$('evidence-content').replaceChildren(el('h3','每一个数字，都有出处'),el('p','开始研究后，点击数据或引用查看证据。'));syncMode();render();
}
async function switchMode(mode){
  if(PREVIEW)return;
  const saved=localStorage.getItem('research-run-'+mode);
  if($('model-mode').value===mode&&(!run&&!saved||run?.run_id===saved)){localStorage.setItem('research-mode',mode);$('composer').open=true;return;}
  $('model-mode').value=mode;localStorage.setItem('research-mode',mode);clearWorkspace();
  if(saved)await loadRun(saved);
}
function syncMode(){
  const mode=$('model-mode').value,live=mode==='live',hybrid=mode==='openai',paid=live||hybrid,provider=$('live-provider').value;
  document.querySelectorAll('[data-run-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.runMode===mode)));
  $('key-wrap').hidden=!paid;$('api-key').disabled=!paid;$('live-model-wrap').hidden=!live;$('live-provider-wrap').hidden=!live;$('custom-model-wrap').hidden=!live;$('detect-models').hidden=!live;$('model-check-status').hidden=!live||!modelCatalog;
  $('custom-model').placeholder=provider==='deepseek'?'例如 deepseek-flash':'可选；填写所选服务商的模型 ID';
  $('key-label').textContent=live?(providers[provider]||'所选服务商')+' API Key':'OpenAI API Key';
  $('scenario').disabled=live;if(live)$('scenario').value='normal';
  const label=live?'联网研究 Agent':hybrid?'本地资料＋真实模型':'离线 Mock';
  if(!run)document.querySelector('.badge').textContent='新任务 · '+label;
  $('summary-mode').textContent='新任务：'+label+' · 可切换';
  $('mode-description').textContent=live?'先选服务商再输入对应 Key，页面会检测可用模型；联网来源和报告仍需人工确认。Key 不会发送给未选的服务商。':hybrid?'模型只查询固定本地快照并参与核证和写作；不联网检索资料。':'离线运行七步工作流，资料与模型均为明确标识的 Mock。';
  $('model-note').textContent=live?(provider==='glm'?'智谱通过一次轻量模型调用验证推荐型号，可能产生费用；搜索另用同一 Key。':provider.startsWith('qwen_')?'千问查询所选地域的模型目录，并用同一 Key 调用联网工具；来源需打开核对。':provider==='deepseek'?'DeepSeek 用同一 Key 调用模型和 Search API。':provider.startsWith('kimi_')?'请使用 Kimi 开放平台 Key；检测会验证模型并可能产生费用，Kimi Code 会员 Key 不适用。':provider==='doubao'?'火山方舟通过一次短请求验证推荐豆包模型，联网时调用方舟搜索工具。':provider==='gemini'?'Gemini 查询模型目录，研究时启用 Google Search grounding。':provider==='claude'?'Claude 查询支持网页搜索的模型，研究时启用网页引用。':provider==='openai'?'OpenAI 使用 Responses web_search，最多4次网页工具调用。':'请先选择服务商；不会根据 Key 字符串猜测归属。') :hybrid?'模型逐轮选择本地资料工具，再给证据建议；审核后起草分析报告。':'无需 Key；本地快照与规则处理。';
  document.querySelector('.compose-footer .muted').textContent=live?'真实模型 + 联网搜索 + 双重人工审核':hybrid?'模型工具决策 + 本地快照 + 证据建议':'本地快照 + MockLLM · 不连接真实金融数据库';
  document.querySelector('.edition strong').textContent=live?'当前网页':'2024 全年';
  document.querySelector('.edition span').textContent=live?'网页日期需核对 · 不提供实时行情':'观察截止 2025-03-31 · 事后研究';
}
$('model-mode').onchange=syncMode;
$('live-provider').onchange=()=>{$('api-key').value='';$('custom-model').value='';resetModels();syncMode();$('api-key').focus();};
$('live-model').onchange=()=>{$('custom-model').value='';};
$('api-key').addEventListener('input',resetModels);
$('detect-models').onclick=async()=>{try{message();await detectModels();}catch(e){message(e.message);}};
document.querySelectorAll('[data-run-mode]').forEach(b=>b.onclick=()=>switchMode(b.dataset.runMode));
$('start').onclick=async()=>{try{message();const mode=$('model-mode').value,key=mode==='mock'?'':$('api-key').value.trim();if(mode!=='mock'&&!key)throw Error('请输入本次运行的 API Key');$('start').disabled=true;if(mode==='live'&&!modelCatalog)await detectModels();const chosen=$('custom-model').value.trim()||$('live-model').value;if(mode==='live'&&!chosen)throw Error('请先选择或输入模型 ID');const body={topic:$('topic').value,scenario:$('scenario').value,model_mode:mode,api_key:key,model_name:mode==='live'?chosen:undefined,provider:mode==='live'?$('live-provider').value:undefined};const created=await api('/api/runs',body);$('api-key').value='';$('custom-model').value='';resetModels();tab='report';reviewDraft={};await loadRun(created.run_id);}catch(e){message(e.message);$('start').disabled=false;}};
$('fill').onclick=()=>{$('topic').value='分析2024年新能源汽车行业竞争格局及投资机会';$('topic').focus();};
$('fill-company').onclick=()=>{$('topic').value='分析2024年新能源汽车行业重点公司财务表现';$('topic').focus();};
$('new').onclick=async()=>{const mode=$('model-mode').value;localStorage.removeItem('research-run-'+mode);localStorage.setItem('research-mode',mode);clearWorkspace();try{history=await api('/api/runs');}catch(e){message(e.message);}$('topic').focus();};
$('current').onclick=async()=>{try{
  history=await api('/api/runs');const list=$('run-list'),mode=$('model-mode').value,visible=history.filter(item=>(item.mode==='hybrid'?'openai':item.mode)===mode);list.replaceChildren();
  if(!visible.length)list.append(el('p','当前运行方式尚无研究任务，请先开始研究。'));
  for(const item of visible){const row=button(item.topic,async()=>{$('runs-dialog').close();tab='report';await loadRun(item.run_id);(run?.status==='WAITING_REVIEW'?$('review'):$('content')).scrollIntoView({block:'start'});});
    if(run?.run_id===item.run_id)row.classList.add('selected');
    row.append(el('small',`${labels[item.status]||item.status} · ${new Date(item.created_at).toLocaleString('zh-CN')} · ${item.run_id}`));list.append(row);
  }
  $('runs-dialog').showModal();
}catch(e){message(e.message);}};
$('sample').onclick=async()=>{try{if(PREVIEW){tab='report';renderContent();return;}const sample=await api('/api/sample');tab='report';await loadRun(sample.normal_run_id,false);$('content').scrollIntoView({block:'start'});}catch(e){message(e.message);}};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=async()=>{tab=b.dataset.tab;renderContent();if(tab==='eval'&&!PREVIEW&&!evalData&&$('model-mode').value!=='live'){try{evalData=await api('/api/evaluations');renderContent();}catch(e){message(e.message);}}});
for(const id of ['help','about'])$(id).onclick=()=>$('info').showModal();
$('close-info').onclick=()=>$('info').close();$('close-evidence').onclick=closeEvidence;$('open-evidence').onclick=openEvidence;
$('close-runs').onclick=()=>$('runs-dialog').close();
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeEvidence();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else if(run?.status==='RUNNING'||run?.analysis_status==='generating')loadRun(run.run_id);});
(async()=>{if(PREVIEW){run=PREVIEW.run;PREVIEW.downloadUrl=URL.createObjectURL(new Blob([PREVIEW.markdown],{type:'text/markdown;charset=utf-8'}));$('composer').open=false;$('composer-summary').textContent='只读预览 · '+run.topic;for(const id of ['new','current','start','fill','fill-company','scenario','topic','model-mode','api-key','live-model','live-provider','custom-model','detect-models'])$(id).disabled=true;document.querySelectorAll('[data-run-mode]').forEach(b=>b.disabled=true);document.querySelector('.badge').textContent='只读预览 · 离线 Mock';document.querySelector('.brand').href='#';render();return;}syncMode();render();try{const health=await api('/api/health');if(health.api_version!==7)throw Error('本地服务进程仍是旧版。请关闭启动窗口，重新双击 V2 的 run_demo.bat，然后使用新打开的页面。');history=await api('/api/runs');const savedMode=localStorage.getItem('research-mode');if(['mock','openai','live'].includes(savedMode)){$('model-mode').value=savedMode;const id=localStorage.getItem('research-run-'+savedMode);if(id&&history.some(r=>r.run_id===id))await loadRun(id);else clearWorkspace();}else{const id=localStorage.getItem('research-run');if(id&&history.some(r=>r.run_id===id))await loadRun(id);else if(history.length){const sample=await api('/api/sample');await loadRun(history.some(r=>r.run_id===sample.normal_run_id)?sample.normal_run_id:history[0].run_id);}else renderContent();}}catch(e){message(e.message);}})();


