"""Small offline contract check for the paid live path; no network or real Key."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent / 'agent_demo'))
import engine
import live_agent
import main


def message(text, annotations=None):
    return {'type': 'message', 'content': [{'type': 'output_text', 'text': text,
                                            'annotations': annotations or []}]}


class LiveWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = engine.OUTPUT, engine.live_search, engine.write_live_report
        engine.OUTPUT = Path(self.temp.name)
        self.key = 'fake-key-do-not-save'
        lines = ['2025年某行业公开资料披露了100项应用，仍需核对统计口径。',
                 '另一公开报告显示2025年试点范围扩大，实际成效尚待验证。',
                 '第三份资料提出2026年计划，这不是已经实现的结果。']
        text = '\n'.join(line + '【引】' for line in lines)
        annotations = []
        start = 0
        for i, line in enumerate(lines, 1):
            annotations.append({'type': 'url_citation', 'start_index': start + len(line),
                                'end_index': start + len(line) + 3,
                                'url': f'https://example.org/source-{i}', 'title': f'来源{i}'})
            start += len(line) + 4

        def fake_search(topic, model, key, provider='openai'):
            self.assertEqual((model, key, provider), ('gpt-5.5', self.key, 'openai'))
            def transport(body):
                self.assertEqual(body['tools'][0]['type'], 'web_search')
                self.assertTrue(body['tools'][0]['external_web_access'])
                self.assertEqual(body['max_tool_calls'], 4)
                return {'status': 'completed', 'id': 'search-fixture',
                        'output': [{'type': 'web_search_call', 'status': 'completed',
                                    'action': {'type': 'search', 'queries': [topic]}},
                                   message(text, annotations)]}
            return live_agent.search(topic, model, key, transport)

        def fake_write(topic, facts, documents, model, key, provider='openai'):
            self.assertEqual(key, self.key)
            self.assertEqual(provider, 'openai')
            self.assertEqual([f['fact_id'] for f in facts], ['C01', 'C02'])
            def transport(body):
                self.assertNotIn('2026年计划', body['input'])
                sections = []
                for i, title in enumerate(live_agent.HEADINGS):
                    ids = ['C01', 'C02'] if i == 1 else []
                    sections.append({'title': title,
                        'text': '依据已经人工审核的来源陈述形成有限分析，仍需对实际口径保持审慎。' if ids
                                else '当前经审核的资料不足以支持本节完整判断，仍存在需要继续核对的缺口。',
                        'fact_ids': ids})
                return {'status': 'completed', 'id': 'write-fixture',
                        'output': [message(json.dumps({'sections': sections}, ensure_ascii=False))]}
            return live_agent.write(topic, facts, documents, model, key, transport)

        engine.live_search, engine.write_live_report = fake_search, fake_write

    def tearDown(self):
        engine.OUTPUT, engine.live_search, engine.write_live_report = self.old
        engine.MODEL_KEYS.clear()
        self.temp.cleanup()

    def test_live_search_review_write_and_final_approval(self):
        run = engine.create('研究AI Agent在企业客服的应用', model_mode='live', api_key=self.key,
                            model_name='gpt-5.5', provider='openai')
        engine.advance(run['run_id'], self.key)
        run = engine.load(run['run_id'])
        self.assertEqual((run['status'], len(run['facts']), len(run['tool_calls'])), ('WAITING_REVIEW', 3, 1))
        decisions = [{'item_id': i['item_id'],
                      'selected_fact_id': None if i['item_id'] == 'R03' else i['candidate_ids'][0],
                      'reason': '计划不是已实现结果' if i['item_id'] == 'R03' else ''}
                     for i in run['review_items']]
        engine.submit_review(run['run_id'], decisions)
        engine.finish(run['run_id'])
        run = engine.load(run['run_id'])
        self.assertEqual((run['status'], run['analysis_status'], run['publishable']),
                         ('COMPLETED', 'pending', False))
        self.assertFalse((engine.folder(run['run_id']) / 'final_report.md').exists())
        self.assertNotIn('C03', run['analysis_markdown'])
        engine.review_analysis(run['run_id'], 'approve')
        self.assertTrue((engine.folder(run['run_id']) / 'final_report.md').exists())
        for file in engine.folder(run['run_id']).rglob('*'):
            if file.is_file():
                self.assertNotIn(self.key.encode(), file.read_bytes())

    def test_search_without_real_web_call_fails_closed(self):
        def no_search(_):
            return {'status': 'completed', 'output': [message('只有模型文字，没有搜索调用。')]}
        with self.assertRaisesRegex(ValueError, '没有完成真实联网搜索'):
            live_agent.search('测试主题', 'gpt-5.5', self.key, no_search)

    def test_provider_model_detection_and_deepseek_search(self):
        seen = []
        def models(url, key):
            seen.append(url)
            self.assertEqual(key, self.key)
            return {'data': [{'id': 'deepseek-flash'}, {'id': 'deepseek-v4-pro'}]}
        catalog = live_agent.discover_models('deepseek', self.key, models)
        self.assertEqual((catalog['selected_model'], catalog['search_method']),
                         ('deepseek-flash', 'DeepSeek Search API'))
        self.assertEqual(catalog['models'], ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(seen, ['https://api.deepseek.com/models'])
        def transport(body):
            if body.get('text', {}).get('format', {}).get('name') == 'research_query':
                return {'status': 'completed', 'output': [message('{"query":"企业客服 Agent 试点"}')]}
            if body.get('need_content'):
                return {'results': [{'title': '来源1', 'url': 'https://example.org/1',
                    'content': '2025年有100项企业客服试点。', 'summary': '2025年有100项企业客服试点。'},
                    {'title': '来源2', 'url': 'https://example.org/2',
                    'content': '2025年客服系统新增人工审核流程。', 'summary': '2025年客服系统新增人工审核流程。'}]}
            if body.get('text', {}).get('format', {}).get('name') == 'web_claims':
                claims = {'claims': [
                    {'text': '2025年企业客服试点达到100项，口径需人工核查。', 'source_ids': ['W01']},
                    {'text': '2025年客服系统新增人工审核流程，实施范围仍需核对。', 'source_ids': ['W02']}]}
                return {'status': 'completed', 'output': [message(json.dumps(claims, ensure_ascii=False))]}
            self.fail('unexpected provider request')
        result = live_agent.search('研究企业客服 Agent', 'deepseek-v4-pro', self.key,
                                   transport, provider='deepseek')
        self.assertEqual((len(result['facts']), len(result['calls']), result['actions'][0]['queries']),
                         (2, 2, ['企业客服 Agent 试点']))
        self.assertTrue(result['documents'][0]['search_excerpt'])

    def test_same_report_with_reordered_query_is_one_source(self):
        first = 'https://d.drcnet.com.cn/?chnid=6543&docid=7885582&leafid=26245#1'
        second = 'https://d.drcnet.com.cn/?leafid=26245&docid=7885582#2'
        self.assertEqual(live_agent.source_key(first), live_agent.source_key(second))
        documents = live_agent.indexed_documents([
            {'url': first, 'title': '报告甲', 'content': '第一条摘要'},
            {'url': second, 'title': '报告甲的另一链接', 'content': '重复摘要'},
            {'url': 'https://example.org/independent', 'title': '独立来源', 'content': '另一条摘要'}],
            'url', None, None, '待人工核对')
        self.assertEqual([d['title'] for d in documents], ['报告甲', '独立来源'])

    def test_catalog_does_not_hide_lower_cost_or_unlisted_deepseek_model(self):
        only_pro = live_agent.discover_models('deepseek', self.key,
            lambda url, key: {'data': [{'id': 'deepseek-v4-pro'}]})
        self.assertEqual(only_pro['catalog_models'], ['deepseek-v4-pro'])
        self.assertEqual(only_pro['models'], ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(only_pro['selected_model'], 'deepseek-v4-pro')
        run = engine.create('研究企业服务', model_mode='live', api_key=self.key,
                            model_name='deepseek-flash', provider='deepseek')
        self.assertEqual(run['model_name'], 'deepseek-flash')
        with self.assertRaisesRegex(ValueError, '选择模型'):
            engine.create('研究企业服务', model_mode='live', api_key=self.key, provider='deepseek')

    def test_openai_catalog_uses_actual_text_models(self):
        data = {'data': [{'id': name} for name in (
            'gpt-5.5', 'gpt-6-luna', 'gpt-4.1-mini', 'gpt-4o-mini',
            'gpt-image-1', 'text-embedding-3-small', 'gpt-4o-mini-search-preview')]}
        catalog = live_agent.discover_models('openai', self.key, lambda url, key: data)
        self.assertEqual(catalog['selected_model'], 'gpt-6-luna')
        self.assertIn('gpt-4.1-mini', catalog['models'])
        self.assertIn('gpt-4o-mini', catalog['models'])
        self.assertNotIn('gpt-image-1', catalog['models'])
        self.assertNotIn('gpt-4o-mini-search-preview', catalog['models'])
        def no_search(body):
            self.assertNotIn('reasoning', body)
            return {'status': 'completed', 'output': [message('没有搜索结果')]}
        with self.assertRaisesRegex(ValueError, '没有完成真实联网搜索'):
            live_agent.search('研究企业服务', 'gpt-4.1-mini', self.key, no_search)
        self.assertTrue(live_agent.model_allowed('qwen_cn', 'qwen3.8-flash'))
        self.assertFalse(live_agent.model_allowed('deepseek', 'gpt-6-luna'))

    def test_deepseek_engine_keeps_provider_and_review_gate(self):
        facts = [{'fact_id': f'C0{i}', 'entity': '联网发现', 'metric': '模型引用陈述',
                  'value': f'2025年来源{i}披露相关服务进展，仍需核对公开原文。', 'unit': '',
                  'period': '本次检索', 'scope': '网页内容待核对', 'value_type': 'web_cited_claim',
                  'source_ids': [f'W0{i}'], 'synthetic_test': False, 'status': 'candidate'} for i in (1, 2)]
        docs = [{'source_id': f'W0{i}', 'title': f'来源{i}', 'source_url': f'https://example.org/{i}',
                 'publisher': 'example.org', 'published_at': '未核验'} for i in (1, 2)]
        def deep_search(topic, model, key, provider='openai'):
            self.assertEqual((provider, model, key), ('deepseek', 'deepseek-v4-pro', self.key))
            return {'facts': facts, 'documents': docs, 'actions': [{'type': 'search', 'status': 'completed'}],
                    'blocks': [], 'calls': [{'contract': 'live_query_plan', 'real_model_calls': 1},
                                            {'contract': 'live_source_extraction', 'real_model_calls': 1}]}
        def deep_write(topic, approved, documents, model, key, provider='openai'):
            self.assertEqual(provider, 'deepseek')
            sections = [{'title': h, 'text': '已审查公开陈述后形成有限分析，后续仍需核对更完整来源。',
                         'fact_ids': ['C01', 'C02'] if i == 1 else []}
                        for i, h in enumerate(live_agent.HEADINGS)]
            return {'sections': sections}, '# 联网研究报告\n', {'contract': 'live_grounded_report', 'real_model_calls': 1}
        engine.live_search, engine.write_live_report = deep_search, deep_write
        run = engine.create('研究企业客服 Agent', model_mode='live', api_key=self.key,
                            model_name='deepseek-v4-pro', provider='deepseek')
        engine.advance(run['run_id'], self.key)
        state = engine.load(run['run_id'])
        self.assertEqual((state['provider'], state['status'], len(state['model_calls'])),
                         ('deepseek', 'WAITING_REVIEW', 2))
        decisions = [{'item_id': i['item_id'], 'selected_fact_id': i['candidate_ids'][0], 'reason': ''}
                     for i in state['review_items']]
        engine.submit_review(run['run_id'], decisions)
        engine.finish(run['run_id'])
        state = engine.load(run['run_id'])
        self.assertEqual((state['analysis_status'], len(state['model_calls'])), ('pending', 3))
        self.assertFalse((engine.folder(run['run_id']) / 'final_report.md').exists())

    def test_qwen_glm_gemini_model_recommendations(self):
        def qwen_catalog(url, key):
            self.assertIn('dashscope.aliyuncs.com/api/v1/models', url)
            self.assertEqual(key, self.key)
            return {'output': {'models': [{'model': 'qwen3.7-plus'}, {'model': 'qwen3.8-max'}]}}
        qwen = live_agent.discover_models('qwen_cn', self.key, qwen_catalog)
        self.assertEqual(qwen['selected_model'], 'qwen3.8-max')
        def glm_probe(url, key, body):
            self.assertEqual((url, key, body['model']),
                             ('https://open.bigmodel.cn/api/v1/responses', self.key, 'glm-5.3'))
            return {'status': 'completed', 'output': [message('OK')]}
        glm = live_agent.discover_models('glm', self.key, glm_probe)
        self.assertEqual(glm['selected_model'], 'glm-5.3')
        self.assertIn('glm-5.2', glm['models'])
        self.assertIn('模型调用', glm['detection'])
        def gemini_catalog(url, key):
            self.assertIn('generativelanguage.googleapis.com', url)
            return {'models': [{'name': 'models/gemini-3.8-flash',
                                'supportedGenerationMethods': ['generateContent']}]}
        gemini = live_agent.discover_models('gemini', self.key, gemini_catalog)
        self.assertEqual(gemini['selected_model'], 'gemini-3.8-flash')
        def claude_catalog(url, key):
            self.assertIn('api.anthropic.com/v1/models', url)
            return {'data': [{'id': 'claude-sonnet-5-5', 'capabilities': {
                'server_tools': {'web_search': {'supported': True}}}}]}
        claude = live_agent.discover_models('claude', self.key, claude_catalog)
        self.assertEqual(claude['selected_model'], 'claude-sonnet-5-5')
        def kimi_probe(url, key, body):
            self.assertEqual((url, body['model']),
                             ('https://api.moonshot.cn/v1/responses', 'kimi-k3'))
            return {'status': 'completed', 'output': [message('OK')]}
        kimi = live_agent.discover_models('kimi_cn', self.key, kimi_probe)
        self.assertEqual(kimi['selected_model'], 'kimi-k3')
        self.assertIn('kimi-k2.6', kimi['models'])
        def doubao_probe(url, key, body):
            self.assertEqual((url, body['model']),
                             ('https://ark.cn-beijing.volces.com/api/v3/responses',
                              'doubao-seed-2-1-lite-260915'))
            return {'status': 'completed', 'output': [message('OK')]}
        self.assertEqual(live_agent.discover_models('doubao', self.key, doubao_probe)['provider'], 'doubao')

    def test_qwen_and_glm_real_search_shapes(self):
        claims = {'claims': [{'text': '2025年甲机构披露相关服务增加，口径仍待核对。', 'source_ids': ['W01']},
                             {'text': '乙机构对流程作了公开说明，实施范围仍待核对。', 'source_ids': ['W02']}]}
        def qwen_transport(body):
            if body.get('tools'):
                return {'status': 'completed', 'output': [
                    {'type': 'web_search_call', 'action': {'sources': [
                        {'url': 'https://example.org/1', 'title': '甲机构'},
                        {'url': 'https://example.org/2', 'title': '乙机构'}]}},
                    message('2025年甲机构披露相关服务增加。乙机构对流程作了公开说明。')]}
            self.assertNotIn('tools', body)
            return {'status': 'completed', 'output': [message(json.dumps(claims, ensure_ascii=False))]}
        qwen = live_agent.search('研究企业服务', 'qwen3.8-max', self.key, qwen_transport, 'qwen_cn')
        self.assertEqual((len(qwen['facts']), len(qwen['calls']), len(qwen['documents'])), (2, 2, 2))
        def glm_transport(body):
            if body.get('search_query'):
                return {'search_result': [
                    {'link': 'https://example.org/1', 'title': '甲机构',
                     'content': '2025年甲机构披露相关服务增加。'},
                    {'link': 'https://example.org/2', 'title': '乙机构',
                     'content': '乙机构对流程作了公开说明。'}]}
            self.assertEqual(body['text']['format']['type'], 'json_object')
            return {'status': 'completed', 'output': [message(json.dumps(claims, ensure_ascii=False))]}
        glm = live_agent.search('研究企业服务', 'glm-5.3', self.key, glm_transport, 'glm')
        self.assertEqual((len(glm['facts']), glm['actions'][0]['status']), (2, 'completed'))

    def test_kimi_search_pro_sources(self):
        claims = {'claims': [{'text': '2025年甲机构披露相关服务增加，口径仍待核对。', 'source_ids': ['W01']},
                             {'text': '乙机构对流程作了公开说明，实施范围仍待核对。', 'source_ids': ['W02']}]}
        def transport(body):
            if body.get('text_query'):
                return {'search_results': [
                    {'url': 'https://example.org/1', 'title': '甲机构',
                     'chunks': [{'text': '2025年甲机构披露相关服务增加。'}]},
                    {'url': 'https://example.org/2', 'title': '乙机构',
                     'chunks': [{'text': '乙机构对流程作了公开说明。'}]}]}
            return {'status': 'completed', 'output': [message(json.dumps(claims, ensure_ascii=False))]}
        result = live_agent.search('研究企业服务', 'kimi-k3', self.key, transport, 'kimi_cn')
        self.assertEqual((len(result['facts']), len(result['documents'])), (2, 2))

    def test_doubao_search_without_positioned_citations(self):
        def transport(body):
            self.assertEqual(body['tools'], [{'type': 'web_search', 'sources': ['doubao']}])
            return {'status': 'completed', 'output': [
                {'type': 'web_search_call', 'status': 'completed',
                 'action': {'type': 'search', 'query': '企业服务'}},
                {'type': 'message', 'content': [
                    {'type': 'output_text', 'text': '甲机构披露了企业服务进展，具体范围仍需核对。',
                     'annotations': [{'type': 'url_citation', 'url': 'https://example.org/1'}]},
                    {'type': 'output_text', 'text': '乙机构说明了相关审核流程，实施时间仍需核对。',
                     'annotations': [{'type': 'url_citation', 'url': 'https://example.org/2'}]}]}]}
        result = live_agent.search('研究企业服务', 'doubao-seed-2-1-pro-260915', self.key,
                                   transport, 'doubao')
        self.assertEqual((len(result['facts']), len(result['documents'])), (2, 2))

    def test_non_openai_report_writing_contracts(self):
        facts = [{'fact_id': 'C01', 'value': '甲机构公开了企业服务进展。', 'source_ids': ['W01']},
                 {'fact_id': 'C02', 'value': '乙机构公开了审核流程。', 'source_ids': ['W02']}]
        docs = [{'source_id': 'W01', 'title': '甲机构', 'source_url': 'https://example.org/1'},
                {'source_id': 'W02', 'title': '乙机构', 'source_url': 'https://example.org/2'}]
        sections = [{'title': title, 'text': '经审核的陈述显示相关进展，但资料仍不足以判断长期效果。',
                     'fact_ids': ['C01', 'C02'] if i == 1 else []}
                    for i, title in enumerate(live_agent.HEADINGS)]
        for provider, model in [('qwen_cn', 'qwen3.8-max'), ('glm', 'glm-5.3'),
                                ('kimi_cn', 'kimi-k3'), ('doubao', 'doubao-seed-2-1-pro-260915')]:
            with self.subTest(provider=provider):
                def transport(body):
                    self.assertNotIn('reasoning', body)
                    self.assertEqual(body.get('text', {}).get('format', {}).get('type'),
                                     'json_object' if provider == 'glm' else None)
                    return {'status': 'completed', 'output': [
                        message(json.dumps({'sections': sections}, ensure_ascii=False))]}
                report, _, call = live_agent.write('研究企业服务', facts, docs, model,
                                                   self.key, transport, provider)
                self.assertEqual((len(report['sections']), call['model']), (5, model))

    def test_gemini_grounding_and_report_shape(self):
        def gemini_search(body):
            self.assertEqual(body['tools'], [{'google_search': {}}])
            return {'candidates': [{'finishReason': 'STOP',
                'content': {'parts': [{'text': '2025年甲机构披露相关服务增加。乙机构对流程作了公开说明。'}]},
                'groundingMetadata': {'webSearchQueries': ['企业服务'],
                    'groundingChunks': [{'web': {'uri': 'https://example.org/1', 'title': '甲机构'}},
                                        {'web': {'uri': 'https://example.org/2', 'title': '乙机构'}}],
                    'groundingSupports': [
                        {'segment': {'text': '2025年甲机构披露相关服务增加。'}, 'groundingChunkIndices': [0]},
                        {'segment': {'text': '乙机构对流程作了公开说明。'}, 'groundingChunkIndices': [1]}]}}],
                'usageMetadata': {'promptTokenCount': 30, 'candidatesTokenCount': 20}}
        result = live_agent.search('研究企业服务', 'gemini-3.8-flash', self.key, gemini_search, 'gemini')
        self.assertEqual((len(result['facts']), len(result['documents'])), (2, 2))
        def gemini_write(body):
            self.assertEqual(body['generationConfig']['responseMimeType'], 'application/json')
            sections = [{'title': title, 'text': '经审核的陈述显示相关进展，但资料仍不足以判断长期效果。',
                         'fact_ids': ['C01', 'C02'] if i == 1 else []}
                        for i, title in enumerate(live_agent.HEADINGS)]
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                {'text': json.dumps({'sections': sections}, ensure_ascii=False)}]}}]}
        report, markdown, call = live_agent.write('研究企业服务', result['facts'], result['documents'],
                                                  'gemini-3.8-flash', self.key, gemini_write, 'gemini')
        self.assertEqual((len(report['sections']), call['model']), (5, 'gemini-3.8-flash'))
        self.assertIn('https://example.org/1', markdown)

    def test_claude_web_citations_and_report_shape(self):
        def claude_search(body):
            self.assertEqual(body['tools'][0]['name'], 'web_search')
            blocks = [{'type': 'server_tool_use', 'name': 'web_search',
                       'input': {'query': '企业服务'}},
                      {'type': 'web_search_tool_result', 'content': [
                          {'type': 'web_search_result', 'url': 'https://example.org/1'}]}]
            for i, statement in enumerate(('2025年甲机构披露相关服务增加。',
                                           '乙机构对流程作了公开说明。'), 1):
                blocks.append({'type': 'text', 'text': statement, 'citations': [
                    {'type': 'web_search_result_location', 'url': f'https://example.org/{i}',
                     'title': f'来源{i}', 'cited_text': statement}]})
            return {'stop_reason': 'end_turn', 'content': blocks, 'usage': {'input_tokens': 40}}
        result = live_agent.search('研究企业服务', 'claude-sonnet-5-5', self.key, claude_search, 'claude')
        self.assertEqual((len(result['facts']), len(result['documents'])), (2, 2))
        def claude_write(body):
            self.assertNotIn('tools', body)
            sections = [{'title': title, 'text': '经审核的陈述显示相关进展，但资料仍不足以判断长期效果。',
                         'fact_ids': ['C01', 'C02'] if i == 1 else []}
                        for i, title in enumerate(live_agent.HEADINGS)]
            return {'stop_reason': 'end_turn', 'content': [
                {'type': 'text', 'text': json.dumps({'sections': sections}, ensure_ascii=False)}]}
        report, markdown, call = live_agent.write('研究企业服务', result['facts'], result['documents'],
                                                  'claude-sonnet-5-5', self.key, claude_write, 'claude')
        self.assertEqual((len(report['sections']), call['model']), (5, 'claude-sonnet-5-5'))
        self.assertIn('https://example.org/2', markdown)

    def test_unreviewed_number_blocks_report(self):
        f = {'fact_id': 'C01', 'value': '公开资料称有100项应用', 'source_ids': ['W01']}
        d = {'source_id': 'W01', 'title': '来源', 'source_url': 'https://example.org/source'}
        def unsupported(_):
            sections = [{'title': title, 'text': '已有999项应用且增长显著，相关结果仍需要更多原始资料进行核查。'
                         if i == 1 else '目前资料存在明显缺口，无法据此形成充分结论，后续仍需核查。',
                         'fact_ids': ['C01'] if i == 1 else []}
                        for i, title in enumerate(live_agent.HEADINGS)]
            return {'status': 'completed', 'output': [message(json.dumps({'sections': sections}, ensure_ascii=False))]}
        with self.assertRaisesRegex(ValueError, '数字') as caught:
            live_agent.write('研究某应用', [f], [d], 'gpt-5.5', self.key, unsupported)
        self.assertEqual(len(caught.exception.model_call_records), 2)

    def test_incomplete_deepseek_response_identifies_token_limit(self):
        def incomplete(_):
            return {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}
        with self.assertRaisesRegex(ValueError, 'Token 上限'):
            live_agent.request({'model': 'deepseek-flash'}, self.key, incomplete, provider='deepseek')

    def test_report_repairs_unreviewed_number_once(self):
        facts = [{'fact_id': 'C01', 'value': '来源称已有100项应用。', 'source_ids': ['W01']}]
        docs = [{'source_id': 'W01', 'title': '来源', 'source_url': 'https://example.org/source'}]
        calls = []
        def draft(body):
            calls.append(body)
            sections = [{'title': title,
                'text': '现有999项应用，仍需核对来源和统计口径。' if len(calls) == 1
                        else '经审核的来源称已有100项应用，仍需继续核对统计口径和适用范围。',
                'fact_ids': ['C01']} for title in live_agent.HEADINGS]
            return {'status': 'completed', 'id': 'draft-' + str(len(calls)),
                'usage': {'input_tokens': 10, 'output_tokens': 20},
                'output': [message(json.dumps({'sections': sections}, ensure_ascii=False))]}
        report, markdown, call = live_agent.write('研究某应用', facts, docs,
                                                    'deepseek-flash', self.key, draft, 'deepseek')
        self.assertEqual(len(calls), 2)
        self.assertEqual(call['real_model_calls'], 2)
        self.assertEqual(call['token_usage'], {'input_tokens': 20, 'output_tokens': 40})
        self.assertNotIn('999', markdown)
        self.assertEqual(len(report['sections']), 5)

    def test_http_live_mode_to_download(self):
        server = main.LocalServer(('127.0.0.1', 0), main.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def post(path, data):
            req = Request(origin + path, data=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                          headers={'Origin': origin, 'Content-Type': 'application/json'}, method='POST')
            with urlopen(req, timeout=3) as response:
                return json.load(response)
        def get(path):
            with urlopen(origin + path, timeout=3) as response:
                return json.load(response)
        def wait_for(run_id, condition):
            for _ in range(60):
                run = get('/api/runs/' + run_id)
                if condition(run):
                    return run
                time.sleep(.05)
            self.fail('workflow did not reach expected state')
        try:
            with patch.object(main, 'discover_models', return_value={'provider': 'openai',
                    'models': ['gpt-5.5'], 'selected_model': 'gpt-5.5', 'search_method': 'OpenAI web_search'}):
                self.assertEqual(post('/api/models', {'provider': 'openai', 'api_key': self.key})['selected_model'],
                                 'gpt-5.5')
            created = post('/api/runs', {'topic': '研究AI Agent在企业客服的应用',
                'model_mode': 'live', 'model_name': 'gpt-5.5', 'api_key': self.key, 'provider': 'openai'})
            rid = created['run_id']
            self.assertEqual(next(item['mode'] for item in get('/api/runs') if item['run_id'] == rid), 'live')
            run = wait_for(rid, lambda r: r['status'] == 'WAITING_REVIEW')
            choices = [{'item_id': i['item_id'], 'selected_fact_id': None if i['item_id'] == 'R03' else i['candidate_ids'][0],
                        'reason': '计划尚未实现' if i['item_id'] == 'R03' else ''} for i in run['review_items']]
            post('/api/runs/' + rid + '/review', {'decisions': choices})
            wait_for(rid, lambda r: r.get('analysis_status') == 'pending')
            post('/api/runs/' + rid + '/analysis-review', {'decision': 'approve'})
            with urlopen(origin + '/api/runs/' + rid + '/report', timeout=3) as response:
                text = response.read().decode('utf-8')
            self.assertIn('联网研究 Agent 报告｜已人工确认', text)
            self.assertNotIn('联网研究 Agent 草稿', text)
            self.assertNotIn('2026年计划', text)
            with urlopen(origin + '/api/runs/' + rid + '/report.pdf', timeout=5) as response:
                pdf = response.read()
                self.assertEqual((response.headers['Content-Type'], int(response.headers['Content-Length'])),
                                 ('application/pdf', len(pdf)))
            import pymupdf
            document = pymupdf.open(stream=pdf, filetype='pdf')
            self.assertIn('研究AI Agent在企业客服的应用', document[0].get_text())
            self.assertGreater(sum(len(page.get_links()) for page in document), 0)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_write_retry_keeps_prior_human_decisions(self):
        run = engine.create('研究AI Agent在企业客服的应用', model_mode='live', api_key=self.key,
                            model_name='gpt-5.5', provider='openai')
        engine.advance(run['run_id'], self.key)
        state = engine.load(run['run_id'])
        choices = [{'item_id': i['item_id'], 'selected_fact_id': None if i['item_id'] == 'R03' else i['candidate_ids'][0],
                    'reason': '排除未实现计划' if i['item_id'] == 'R03' else ''} for i in state['review_items']]
        engine.submit_review(run['run_id'], choices)
        good_writer = engine.write_live_report
        engine.write_live_report = lambda *_: (_ for _ in ()).throw(ValueError('模拟临时错误'))
        engine.finish(run['run_id'])
        self.assertEqual(engine.load(run['run_id'])['status'], 'FAILED')
        engine.write_live_report = good_writer
        engine.request_live_retry(run['run_id'], self.key)
        engine.finish_live(run['run_id'])
        state = engine.load(run['run_id'])
        self.assertEqual((state['status'], state['analysis_status'], len(state['review_decisions'])),
                         ('COMPLETED', 'pending', 3))


if __name__ == '__main__':
    unittest.main()
