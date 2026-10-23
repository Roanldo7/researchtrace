import copy
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from providers import VERSION, call_tool, mock_llm
from model_review import screen
from tool_agent import TOOLS, choose as choose_tool
from model_analysis import write as write_model_analysis
from live_agent import PROVIDERS, model_allowed, search as live_search, write as write_live_report
from report import markdown, check, comparable_key, derive, assess, build_context

ROOT = Path(__file__).parent
OUTPUT = ROOT / 'outputs'
MODEL_KEYS = {}  # Process memory only; removed after analysis generation.
STEP_NAMES = ['任务拆解', '资料检索', '数据提取', '口径核验', '人工审核', '报告生成', '引用检查']
LOCK = threading.RLock()  # ponytail: one local user; no distributed queue needed.
CHAR_BUDGET = 2000


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding='utf-8')
    os.replace(temp, path)


def dump(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def folder(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{12}', run_id):
        raise ValueError('任务ID格式错误')
    return OUTPUT / run_id


def load(run_id):
    with LOCK:
        return json.loads((folder(run_id) / 'state.json').read_text(encoding='utf-8'))


def save(run):
    with LOCK:
        run['updated_at'] = now()
        dump(folder(run['run_id']) / 'state.json', run)


def recent():
    OUTPUT.mkdir(exist_ok=True)
    with LOCK:
        runs = [json.loads(p.read_text(encoding='utf-8')) for p in OUTPUT.glob('*/state.json')]
    return sorted([dict(run_id=r['run_id'], topic=r['topic'], status=r['status'], scenario=r['scenario'], mode=r.get('mode', 'mock'),
                        created_at=r['created_at']) for r in runs], key=lambda r: r['created_at'], reverse=True)


def recover_interrupted():
    for item in recent():
        r = load(item['run_id'])
        if r['status'] == 'RUNNING':
            r.update(status='FAILED', error='程序在自动步骤中断。已有产物保留，请新建任务；人工等待节点可直接恢复。')
            save(r)


def create(topic, scenario='normal', model_mode=None, api_key=None, model_name=None, provider=None):
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 180:
        raise ValueError('研究主题须为1—180个字符')
    if scenario not in ('normal', 'exception'):
        raise ValueError('未知场景')
    mode = model_mode if model_mode is not None else os.getenv('RESEARCHTRACE_MODEL_MODE', 'mock')
    if mode not in ('mock', 'openai', 'live'):
        raise ValueError('未知模型模式')
    if mode == 'openai' and not (api_key or os.getenv('OPENAI_API_KEY')):
        raise ValueError('本地资料＋真实模型模式需要 OpenAI API Key；未创建任务')
    if mode == 'live' and not api_key:
        raise ValueError('联网模式需要所选服务商的 API Key；未创建任务')
    if mode == 'live':
        if scenario != 'normal':
            raise ValueError('联网模式不使用本地异常注入场景')
        if provider not in PROVIDERS:
            raise ValueError('请先选择支持的模型服务商')
        if not model_name:
            raise ValueError('请先检测并选择模型，或输入明确的模型 ID')
        if not isinstance(model_name, str) or not re.fullmatch(r'[a-zA-Z0-9._-]{3,80}', model_name):
            raise ValueError('模型名称格式错误')
        if not model_allowed(provider, model_name):
            raise ValueError('模型 ID 与所选服务商不匹配')
    else:
        mock_llm('plan', {'topic': topic})  # Validate before making an output directory.
    with LOCK:
        if any(r['status'] == 'RUNNING' for r in recent()):
            raise ValueError('已有任务正在执行，请稍后再试')
        run = dict(run_id=uuid.uuid4().hex[:12], topic=topic.strip(), scenario=scenario,
            mode='live' if mode == 'live' else 'hybrid' if mode == 'openai' else 'mock',
            provider=provider if mode == 'live' else 'openai', model_name=model_name if mode == 'live' else None,
            dataset_version='live-web-search' if mode == 'live' else VERSION,
            created_at=now(), updated_at=now(), status='RUNNING', current_step=1,
            warnings=[], documents=[], document_ids=[], facts=[], conflicts=[], review_items=[],
            review_decisions=[], approved_fact_ids=[], tool_calls=[], agent_decisions=[], model_calls=[], context_log=[],
            program_ms=0, waiting_ms=0, steps=[dict(number=i+1, name=n, status='pending') for i,n in enumerate(STEP_NAMES)])
        save(run)
        if mode in ('openai', 'live') and api_key:
            MODEL_KEYS[run['run_id']] = api_key
        return run


def step(run, number, inputs, tool, work):
    run['current_step'] = number
    state = run['steps'][number-1]
    state.update(status='running', started_at=now(), input=inputs, tool=tool)
    save(run)
    start = time.perf_counter()
    try:
        output = work()
        state.update(status='completed', output=output, finished_at=now())
    except Exception as exc:
        state.update(status='failed', error=str(exc))
        raise
    finally:
        state['elapsed_ms'] = round((time.perf_counter()-start)*1000, 3)
        run['program_ms'] += state['elapsed_ms']
        dump(folder(run['run_id']) / f'{number:02d}.json', state)
        save(run)
    return output


def model(run, kind, payload):
    started = time.perf_counter()
    result = mock_llm(kind, payload)
    run['model_calls'].append(dict(contract=kind, model='MockLLM', real_model_calls=0,
        token_usage=None, elapsed_ms=round((time.perf_counter()-started)*1000, 3)))
    return result


def retrieve(run, api_key=None):
    p = run['plan']
    base = dict(query=p['query'], start_date=p['start_date'], end_date=p['end_date'], top_k=12)
    cache, docs = {}, {}
    available = list(TOOLS)
    observations = []
    for round_number in range(1, 5):
        if run['mode'] == 'hybrid':
            try:
                choice, call = choose_tool(p, available, observations, api_key)
            except Exception as exc:
                run['agent_decisions'].append({'round': round_number, 'status': 'failed', 'error': str(exc)})
                raise
            run['model_calls'].append(call)
            tool = choice['action']
            decision = {'round': round_number, 'action': tool, 'reason': choice['reason'].strip(),
                        'available_tools': list(available), 'observed_document_ids': list(docs)}
            run['agent_decisions'].append(decision)
            if tool == 'finish':
                decision['returned_document_ids'] = []
                break
        else:
            tool = available[0]
        available.remove(tool)
        params = dict(base)
        if tool in ('search_announcements', 'get_finance_indicators'):
            params['companies'] = p['companies']
        if tool == 'get_finance_indicators':
            params['indicators'] = ['营业收入']
        found = call_tool(run, tool, params, cache)
        for d in found:
            docs[d['source_id']] = d
        if run['mode'] == 'hybrid':
            decision['returned_document_ids'] = [d['source_id'] for d in found]
            observations.append({'tool': tool, 'documents': [
                {'source_id': d['source_id'], 'title': d['title'], 'source_kind': d['source_kind']}
                for d in found]})
            save(run)
    run['documents'] = list(docs.values())
    run['document_ids'] = list(docs)
    if 'search_research' not in available:
        run['warnings'].append('无授权研报，研报工具返回0条；本次使用公开资料快照。')
    else:
        run['warnings'].append('本次未调用授权研报工具；仅使用已检索的本地公开资料快照。')
    return dict(document_ids=list(docs), tool_calls=run['tool_calls'], agent_decisions=run['agent_decisions'])


def extract(run, api_key=None):
    for doc in run['documents']:
        result = model(run, 'extract', dict(document=doc, char_budget=CHAR_BUDGET))
        run['context_log'].append(result['context'])
        for f in result['facts']:
            f['fact_id'] = 'F' + str(len(run['facts'])+1).zfill(2)
            run['facts'].append(f)
        if result['context']['skipped_locators']:
            run['warnings'].append('提取缺口：' + doc['source_id'])
    if run['mode'] == 'hybrid':
        suggestions, call = screen(run['facts'], run['documents'], api_key=api_key)
        for fact in run['facts']:
            if fact['fact_id'] in suggestions:
                fact['model_suggestion'] = suggestions[fact['fact_id']]
        run['model_calls'].append(dict(contract='evidence_screen', **call))
    return dict(facts=run['facts'], context_log=run['context_log'])


def verify(run):
    groups = {}
    for f in run['facts']:
        key = comparable_key(f)
        groups.setdefault(key, []).append(f)
    for i, candidates in enumerate(groups.values(), 1):
        conflict = len({json.dumps(f['value'], ensure_ascii=False) for f in candidates}) > 1
        item = dict(item_id=f'R{i:02}', entity=candidates[0]['entity'], metric=candidates[0]['metric'],
                    period=candidates[0]['period'], value_type=candidates[0]['value_type'],
                    candidate_ids=[f['fact_id'] for f in candidates], conflict=conflict,
                    status='unresolved', evidence_note='同口径数值冲突' if conflict else '单一原始来源，需人工确认')
        run['review_items'].append(item)
        if conflict:
            run['conflicts'].append(copy.deepcopy(item))
    incomparable = []
    for a in run['facts']:
        for b in run['facts']:
            if a['fact_id'] < b['fact_id'] and a['metric'] == b['metric'] and a['scope'] != b['scope']:
                incomparable.append(dict(fact_ids=[a['fact_id'], b['fact_id']], reason='主体或统计范围不同，不比较数值、不计算份额'))
    return dict(review_items=run['review_items'], conflicts=run['conflicts'], incomparable=incomparable,
                missing_fields=assess(run['facts'] + derive([f for f in run['facts'] if not f['synthetic_test']]), run['plan'].get('focus', 'industry'))['missing_fields'], independent_cross_validation=False)


def advance(run_id, api_key=None):
    run = load(run_id)
    if run['mode'] == 'live':
        return advance_live(run_id, api_key)
    try:
        run['plan'] = step(run, 1, {'topic': run['topic']}, 'MockLLM.plan', lambda: model(run, 'plan', run))
        step(run, 2, run['plan'], '模型选择本地工具' if run['mode'] == 'hybrid' else '四类本地Mock工具',
             lambda: retrieve(run, api_key))
        step(run, 3, {'document_ids': run['document_ids'], 'char_budget': CHAR_BUDGET}, '规则抽取与可选模型证据建议', lambda: extract(run, api_key))
        step(run, 4, {'fact_ids': [f['fact_id'] for f in run['facts']]}, 'Python口径与候选核验', lambda: verify(run))
        run.update(status='WAITING_REVIEW', current_step=5, waiting_since=now())
        run['steps'][4].update(status='waiting', input={'review_items': run['review_items']}, tool='本地操作者')
        dump(folder(run_id) / '05.json', run['steps'][4])
        save(run)
    except Exception as exc:
        MODEL_KEYS.pop(run_id, None)
        run.update(status='FAILED', error=str(exc))
        save(run)


def advance_live(run_id, api_key=None):
    run = load(run_id)
    key = api_key or MODEL_KEYS.get(run_id) or os.getenv('OPENAI_API_KEY')
    try:
        run['plan'] = step(run, 1, {'topic': run['topic']}, 'Python研究任务边界',
            lambda: {'topic': run['topic'], 'model': run['model_name'], 'scope': '当前公开网页；结果以检索时间为准'})
        result = {}
        def research():
            nonlocal result
            result = live_search(run['topic'], run['model_name'], key, provider=run.get('provider', 'openai'))
            run['tool_calls'] = result['actions']
            run['model_calls'].extend(result.get('calls') or [result['call']])
            dump(folder(run_id) / 'live_search.json', {k: result[k] for k in ('actions', 'blocks')})
            return {'search_actions': len(result['actions']), 'candidate_count': len(result['facts']),
                    'search_file': 'live_search.json'}
        step(run, 2, run['plan'], PROVIDERS[run.get('provider', 'openai')]['search_method'] + ' / 真实联网', research)
        def extract_live():
            run['facts'], run['documents'] = result['facts'], result['documents']
            run['document_ids'] = [d['source_id'] for d in run['documents']]
            return {'candidate_ids': [f['fact_id'] for f in run['facts']], 'source_ids': run['document_ids']}
        step(run, 3, {'search_output': 'live_search.json'}, 'URL引用定位与候选陈述提取', extract_live)
        def verify_live():
            run['review_items'] = [{'item_id': 'R' + str(i).zfill(2), 'entity': '联网发现',
                'metric': '模型引用陈述', 'period': '本次检索', 'candidate_ids': [f['fact_id']],
                'conflict': False, 'status': 'unresolved',
                'evidence_note': '模型引用了网页URL；须打开原文核对陈述'}
                for i, f in enumerate(run['facts'], 1)]
            run['evidence_assessment'] = {'sufficient': True, 'core_missing': [],
                'note': '仅通过URL和引用定位检查；原网页全文仍须人工核对'}
            return {'candidate_count': len(run['facts']), 'source_count': len(run['documents']),
                    'human_verification_required': True}
        step(run, 4, {'candidate_ids': [f['fact_id'] for f in run['facts']]}, '引用与来源检查', verify_live)
        run.update(status='WAITING_REVIEW', current_step=5, waiting_since=now())
        run['steps'][4].update(status='waiting', input={'review_items': run['review_items']}, tool='本地操作者')
        dump(folder(run_id) / '05.json', run['steps'][4])
        save(run)
    except Exception as exc:
        MODEL_KEYS.pop(run_id, None)
        run.update(status='FAILED', error=str(exc))
        save(run)


def submit_review(run_id, decisions, actor='local_operator', api_key=None):
    with LOCK:
        run = load(run_id)
        if not isinstance(decisions, list):
            raise ValueError('审核决定格式错误')
        if run['review_decisions']:
            previous = [{k:d[k] for k in ('item_id','selected_fact_id','reason')} for d in run['review_decisions']]
            if previous == decisions:
                return run, False
            raise ValueError('审核已经提交，不能覆盖历史决定；请新建研究')
        if run['status'] != 'WAITING_REVIEW':
            raise ValueError('当前任务不在人工审核节点')
        if run['mode'] == 'live':
            if api_key is not None and (not isinstance(api_key, str) or len(api_key) > 300):
                raise ValueError('API Key格式错误')
            key = api_key or MODEL_KEYS.get(run_id) or os.getenv('OPENAI_API_KEY')
            if not key:
                raise ValueError('服务重启后 Key 已清除；请在审核区重新输入本次 API Key')
            MODEL_KEYS[run_id] = key
        if len(decisions) != len(run['review_items']):
            raise ValueError('每条必要审核事项都必须处理')
        ids = [d.get('item_id') for d in decisions if isinstance(d, dict)]
        if len(set(ids)) != len(decisions) or set(ids) != {i['item_id'] for i in run['review_items']}:
            raise ValueError('审核项重复、缺失或不属于本任务')
        facts = {f['fact_id']: f for f in run['facts']}
        approved, records = [], []
        for item in run['review_items']:
            d = next(d for d in decisions if d['item_id'] == item['item_id'])
            selected, reason = d.get('selected_fact_id'), d.get('reason', '')
            if not isinstance(reason, str) or len(reason) > 300:
                raise ValueError('理由须为300字以内文本')
            if selected is not None and selected not in item['candidate_ids']:
                raise ValueError('选定事实不属于本次候选')
            if (selected is None or item['conflict']) and not reason.strip():
                raise ValueError('冲突处置或排除事实必须填写理由')
            if selected and facts[selected]['synthetic_test']:
                raise ValueError('合成测试值不能进入研究报告，请选择公开来源或排除')
            if selected:
                approved.append(selected)
            records.append(dict(item_id=item['item_id'], selected_fact_id=selected, reason=reason,
                selected_source_id=facts[selected]['source_ids'][0] if selected else None,
                decision='adopt' if selected else 'exclude', actor=actor, recorded_at=now()))
        run['review_decisions'], run['approved_fact_ids'] = records, approved
        for f in run['facts']:
            f['status'] = 'confirmed' if f['fact_id'] in approved else 'excluded'
        for item in run['review_items']:
            item['status'] = 'resolved'
        for c in run['conflicts']:
            c['status'] = 'resolved'
        run['waiting_ms'] = round((datetime.fromisoformat(now()) - datetime.fromisoformat(run['waiting_since'])).total_seconds()*1000)
        run['warnings'] += [f"人工排除：{i['entity']} / {i['metric']}" for i in run['review_items'] if not any(f in approved for f in i['candidate_ids'])]
        run['steps'][4].update(status='completed', output={'review_decisions': records}, finished_at=now())
        dump(folder(run_id) / '05.json', run['steps'][4])
        run['status'] = 'RUNNING'
        save(run)
        return run, True


def finish(run_id):
    run = load(run_id)
    if run['mode'] == 'live':
        return finish_live(run_id)
    try:
        if not run['review_decisions'] or run['status'] != 'RUNNING':
            raise ValueError('必须先完成审核')
        approved = [f for f in run['facts'] if f['fact_id'] in run['approved_fact_ids'] and not f['synthetic_test']]
        run['derived_facts'] = derive(approved)
        context, logs, evidence = build_context(run['plan'], approved + run['derived_facts'], run['documents'], run['scenario'])
        run['write_context_log'], run['evidence_assessment'] = logs, evidence
        run['missing_fields'] = evidence['missing_fields']
        dump(folder(run_id) / 'write_context.json', context)
        inputs = dict(context_file='write_context.json', context_log=logs,
                      approved_fact_ids=run['approved_fact_ids'], evidence_assessment=evidence)
        if not evidence['sufficient']:
            diagnostic = dict(message='依据不足，无法发布正式研究摘要。未调用写作契约，未生成投资方向。',
                              core_missing=evidence['core_missing'], missing_fields=evidence['missing_fields'])
            step(run, 6, inputs, 'Python依据门槛 / 阻止写作', lambda: diagnostic)
            checks = dict(valid_evidence=None, evidence_sufficient=False, format_valid=None, passed=False,
                          company_sections=0, supported_company_sections=evidence['supported_companies'])
            run['quality_checks'] = step(run, 7, {'formal_report': False}, 'Python发布门槛 / 引用不适用', lambda: checks)
            dump(folder(run_id) / 'diagnostic.json', diagnostic)
            run.update(status='INSUFFICIENT', quality_status='insufficient', publishable=False,
                       workflow_ended=True, citation_coverage=None)
            save(run)
            return
        run['report'] = step(run, 6, inputs, 'MockLLM.write / 仅已审核的章节上下文', lambda: model(run, 'write', context))
        writing_facts = {f['fact_id']:f for section in context['sections'].values() for f in section['facts']}
        facts = list(writing_facts.values())
        draft = markdown(run['report'], facts)
        atomic(folder(run_id) / 'draft.md', draft)
        run['quality_checks'] = step(run, 7, {'used_fact_ids': run['report']['used_fact_ids']},
            'Python引用、长度、格式与依据检查', lambda: check(run['report'], facts, draft, context.get('focus', 'industry')))
        if not run['quality_checks']['passed']:
            raise ValueError('引用、依据或格式检查未通过，报告未发布；检查07.json')
        atomic(folder(run_id) / 'final_report.md', draft)
        run.update(status='COMPLETED', quality_status='with_gaps' if evidence['missing_fields'] else 'complete',
                   publishable=True, workflow_ended=True, final_report_path=f'agent_demo/outputs/{run_id}/final_report.md')
        run['citation_coverage'] = 1.0 if run['report']['used_fact_ids'] and run['quality_checks']['valid_evidence'] else None
        save(run)
        if run['mode'] == 'hybrid':
            generate_analysis(run_id, MODEL_KEYS.get(run_id) or os.getenv('OPENAI_API_KEY'), context)
    except Exception as exc:
        MODEL_KEYS.pop(run_id, None)
        run.update(status='FAILED', publishable=False, workflow_ended=True, error=str(exc))
        save(run)


def finish_live(run_id):
    run = load(run_id)
    try:
        if not run['review_decisions'] or run['status'] != 'RUNNING':
            raise ValueError('必须先完成候选陈述的人工审核')
        approved = [f for f in run['facts'] if f['fact_id'] in run['approved_fact_ids']]
        if len(approved) < 2:
            diagnostic = {'message': '人工采用的来源陈述不足两条；未调用模型撰写报告',
                          'core_missing': ['至少两条经人工核对的来源陈述']}
            step(run, 6, {'approved_fact_ids': run['approved_fact_ids']}, '依据门槛', lambda: diagnostic)
            run['quality_checks'] = step(run, 7, {'formal_report': False}, '发布门槛',
                lambda: {'passed': False, 'evidence_sufficient': False})
            run.update(status='INSUFFICIENT', publishable=False, workflow_ended=True,
                       evidence_assessment={'sufficient': False, 'core_missing': diagnostic['core_missing']},
                       citation_coverage=None)
            MODEL_KEYS.pop(run_id, None)
            save(run)
            return
        key = MODEL_KEYS.get(run_id) or os.getenv('OPENAI_API_KEY')
        if not key:
            raise ValueError('模型写作所需 API Key 不在当前进程；请重新提交审核区的 Key')
        def compose_live():
            report, md, call = write_live_report(run['topic'], approved, run['documents'], run['model_name'], key,
                                                 provider=run.get('provider', 'openai'))
            run['analysis_draft'], run['analysis_markdown'] = report, md
            run['model_calls'].append(call)
            atomic(folder(run_id) / 'model_draft.md', md)
            return {'sections': report['sections'], 'draft_file': 'model_draft.md'}
        step(run, 6, {'approved_fact_ids': run['approved_fact_ids']}, '真实模型 / 仅已审核陈述', compose_live)
        checks = step(run, 7, {'draft_file': 'model_draft.md'}, '引用与数字门槛 / 等待最终确认',
            lambda: {'passed': True, 'approved_claims_used': len(approved),
                     'source_urls': len({sid for f in approved for sid in f['source_ids']})})
        run.update(status='COMPLETED', analysis_status='pending', publishable=False,
                   quality_checks=checks, workflow_ended=True, citation_coverage=1.0)
        save(run)
    except Exception as exc:
        run['model_calls'].extend(getattr(exc, 'model_call_records', []))
        run.update(status='FAILED', analysis_status='failed', publishable=False,
                   workflow_ended=True, error=str(exc))
        save(run)
    finally:
        MODEL_KEYS.pop(run_id, None)


def generate_analysis(run_id, api_key, context=None):
    run = load(run_id)
    run['analysis_status'] = 'generating'
    save(run)
    try:
        if context is None:
            context = json.loads((folder(run_id) / 'write_context.json').read_text(encoding='utf-8'))
        analysis, analysis_text, call = write_model_analysis(context, api_key)
        run['analysis_draft'], run['analysis_markdown'] = analysis, analysis_text
        run['analysis_status'] = 'pending'
        run.pop('analysis_error', None)
        run['model_calls'].append(call)
        atomic(folder(run_id) / 'model_draft.md', analysis_text)
    except Exception as exc:
        run['analysis_status'], run['analysis_error'] = 'failed', str(exc)
    finally:
        MODEL_KEYS.pop(run_id, None)
        save(run)


def request_analysis_retry(run_id, api_key):
    if not isinstance(api_key, str) or not 1 <= len(api_key) <= 300:
        raise ValueError('请提供本次重试使用的 API Key')
    with LOCK:
        run = load(run_id)
        if run['status'] != 'COMPLETED' or run.get('mode') != 'hybrid' or run.get('analysis_status') != 'failed':
            raise ValueError('当前没有可重试的模型分析')
        run['analysis_status'] = 'generating'
        save(run)
        return run


def request_live_retry(run_id, api_key, model_name=None):
    if not isinstance(api_key, str) or not 1 <= len(api_key) <= 300:
        raise ValueError('请提供本次重试使用的 API Key')
    with LOCK:
        run = load(run_id)
        if run.get('mode') != 'live' or run['status'] != 'FAILED' or not run['review_decisions']:
            raise ValueError('当前没有可重试的联网报告写作')
        if model_name:
            if not isinstance(model_name, str) or not re.fullmatch(r'[a-zA-Z0-9._-]{3,80}', model_name):
                raise ValueError('模型名称格式错误')
            run['model_name'] = model_name
            if not model_allowed(run.get('provider'), model_name):
                raise ValueError('模型 ID 与所选服务商不匹配')
        run.update(status='RUNNING', analysis_status='generating', publishable=False)
        run.pop('error', None)
        for i in (5, 6):
            run['steps'][i] = {'number': i + 1, 'name': STEP_NAMES[i], 'status': 'pending'}
        MODEL_KEYS[run_id] = api_key
        save(run)
        return run


def review_analysis(run_id, decision, reason=''):
    with LOCK:
        run = load(run_id)
        if run.get('analysis_status') != 'pending':
            raise ValueError('当前没有待审核的模型分析草稿')
        if decision not in ('approve', 'reject'):
            raise ValueError('模型报告审核决定无效')
        if not isinstance(reason, str) or len(reason) > 300 or (decision == 'reject' and not reason.strip()):
            raise ValueError('拒绝模型报告时需填写300字以内理由')
        if decision == 'approve':
            path = folder(run_id) / ('final_report.md' if run.get('mode') == 'live' else 'model_report.md')
            content = run['analysis_markdown']
            if run.get('mode') == 'live':
                content = content.replace('联网研究 Agent 草稿｜须人工最终确认', '联网研究 Agent 报告｜已人工确认', 1)
            atomic(path, content)
            run['analysis_status'] = 'approved'
            if run.get('mode') == 'live':
                run['publishable'] = True
                run['final_report_path'] = f'agent_demo/outputs/{run_id}/final_report.md'
        else:
            run['analysis_status'] = 'rejected'
        run['analysis_decision'] = {'decision': decision, 'reason': reason, 'recorded_at': now(),
                                    'actor': 'local_operator'}
        save(run)
        return run


def save_feedback(run_id, rating, area, note):
    if type(rating) is not int or rating not in range(1, 6):
        raise ValueError('帮助程度须为1—5分')
    if area not in ('source', 'review', 'report', 'navigation'):
        raise ValueError('反馈类别无效')
    if not isinstance(note, str) or not 8 <= len(note.strip()) <= 400:
        raise ValueError('请用8—400字描述具体问题或收获')
    with LOCK:
        run = load(run_id)
        if run['status'] not in ('COMPLETED', 'INSUFFICIENT'):
            raise ValueError('请在流程结束后提交试用反馈')
        path = folder(run_id) / 'feedback.json'
        items = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if len(items) >= 50:
            raise ValueError('该任务反馈已达上限')
        items.append({'recorded_at': now(), 'rating': rating, 'area': area, 'note': note.strip()})
        dump(path, items)
        return {'saved': True, 'count': len(items)}
