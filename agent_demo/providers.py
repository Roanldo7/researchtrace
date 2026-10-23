"""Offline adapters. No network client and no real model calls."""
import copy
import json
import re
import time
from pathlib import Path

DATA = Path(__file__).parent / 'data'
VERSION = 'nev2024-20260925-v2'
DEFAULT_TOPIC = '分析2024年新能源汽车行业竞争格局及投资机会'
SECTIONS = ['市场概况', '竞争格局', '技术趋势', '重点公司分析', '投资机会', '风险提示']


def sources(scenario='normal'):
    docs = json.loads((DATA / 'sources.json').read_text(encoding='utf-8'))
    if scenario == 'exception':
        docs.append(json.loads((DATA / 'conflict_overlay.json').read_text(encoding='utf-8')))
    return docs


def mock_llm(task_type, payload):
    """Deterministic contracts: planning, bounded extraction, evidence composition."""
    if task_type == 'plan':
        topic = payload['topic']
        years = set(re.findall(r'20\d{2}', topic))
        if years != {'2024'} or not any(x in topic for x in ('新能源汽车', '新能源车')):
            raise ValueError('资料库仅覆盖2024年新能源汽车行业，请使用示例主题。')
        if any(x in topic for x in ('实时', '今日', '今天', '预测202', '2025', '2026')):
            raise ValueError('首版仅支持2024全年事后研究，不提供实时或未来预测。')
        remaining = re.sub(r'[\s，。？！、：,?!:;；和与及的]', '', topic)
        for phrase in ('新能源汽车', '新能源车', '2024年', '2024', '竞争格局', '投资机会', '投资方向',
                       '重点公司', '公司财务', '财务表现', '财务对比', '盈利对比',
                       '行业研究', '行业综述', '行业分析', '行业', '市场概况', '技术趋势', '重点公司分析',
                       '分析', '研究', '概述', '综述', '请', '中国'):
            remaining = remaining.replace(phrase, '')
        if remaining:
            raise ValueError('当前快照支持2024新能源汽车行业综述、竞争格局与投资方向；不支持所要求的专项分析。请使用示例主题或等义表述。')
        focus = 'company' if any(x in topic for x in ('公司财务', '财务表现', '财务对比', '盈利对比')) else 'industry'
        return dict(topic=topic, period='2024', observation_date='2025-03-31', focus=focus,
                    sections=SECTIONS, companies=['比亚迪', '理想汽车', '蔚来'],
                    query='2024 新能源汽车', start_date='2024-01-01', end_date='2025-03-31',
                    scope='2024全年事后研究；企业与行业口径分别展示', model='MockLLM / 规则契约')
    if task_type == 'extract':
        doc, budget = payload['document'], payload['char_budget']
        facts, context, skipped = [], [], []
        used = 0
        for locator, text in doc['excerpts'].items():
            if used + len(text) > budget:
                skipped.append(locator)
                continue
            used += len(text)
            context.append(locator)
            for field in doc['fields']:
                if field['locator'] != locator:
                    continue
                found = re.search(field['pattern'], text)
                if not found:
                    skipped.append(locator + ':' + field['metric'])
                    continue
                raw = found.group(1)
                value = raw if field['category'] == 'text' else float(raw.replace(',', ''))
                unit, factor = field['unit'], 1
                if unit == '辆':
                    unit, factor = '万辆', .0001
                elif unit == '十亿元':
                    unit, factor = '亿元', 10
                elif unit == '百万元':
                    unit, factor = '亿元', .01
                if isinstance(value, float):
                    value = round(value * factor, 6)
                facts.append(dict(entity=field.get('entity', doc['entity']), metric=field['metric'], value=value,
                    unit=unit, raw_value=raw, raw_unit=field['unit'], conversion_factor=factor,
                    period=field.get('period', doc['period']), scope=field.get('scope', doc['scope']),
                    value_type=field.get('value_type', doc.get('value_type', 'actual')),
                    target_period=field.get('target_period'),
                    category=field['category'], source_ids=[doc['source_id']],
                    excerpt_locator=locator, synthetic_test=doc.get('synthetic_test', False), status='candidate'))
        return dict(facts=facts, context=dict(source_id=doc['source_id'], char_budget=budget,
                    used_chars=used, processed_locators=context, skipped_locators=skipped))
    if task_type == 'write':
        from report import compose
        return compose(payload)
    raise ValueError('未知MockLLM契约')


def search(tool, params, scenario):
    """All supplied filters affect results. Research has no licensed report records."""
    kinds = {'search_research': 'research', 'search_announcements': 'announcement',
             'search_web': 'web', 'get_finance_indicators': 'announcement'}
    docs = sources(scenario)
    words = params['query'].split()
    matches = []
    for doc in docs:
        hay = ' '.join(doc['tags']) + doc['title']
        if doc['source_kind'] != kinds[tool] or not all(w in hay for w in words):
            continue
        if not params['start_date'] <= doc['published_at'] <= params['end_date']:
            continue
        if params.get('companies') and doc['entity'] not in params['companies']:
            continue
        if tool == 'get_finance_indicators' and not any(f['metric'] in params['indicators'] for f in doc['fields']):
            continue
        matches.append(doc)
    return sorted(matches, key=lambda x: (x['published_at'], x['source_id']), reverse=True)[:params['top_k']]


def call_tool(run, tool, params, cache):
    key = json.dumps([VERSION, run['scenario'], tool, params], sort_keys=True, ensure_ascii=False)
    for attempt in range(1, 5):
        start = time.perf_counter()
        log = dict(step=2, tool=tool, request_summary=params, attempt=attempt,
                   cache_hit=key in cache, returned_document_ids=[], error=None)
        try:
            if run['scenario'] == 'exception' and tool == 'search_web' and attempt == 1 and key not in cache:
                raise TimeoutError('受控测试超时：首次网页快照检索失败')
            result = copy.deepcopy(cache[key]) if key in cache else search(tool, params, run['scenario'])
            cache[key] = copy.deepcopy(result)
            log.update(outcome='success', returned_document_ids=[d['source_id'] for d in result])
            return result
        except TimeoutError as exc:
            log.update(outcome='retry' if attempt < 4 else 'failed', error=str(exc))
            if attempt == 4:
                run['warnings'].append(f'{tool}重试耗尽，保留资料缺口')
                return []
        finally:
            log['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 3)
            run['tool_calls'].append(log)
