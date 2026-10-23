"""Targeted checks for the V2-only model publication boundary."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent / 'agent_demo'))
import engine
import model_analysis
import model_review
import tool_agent
import main
from providers import DEFAULT_TOPIC


def bundled_sample():
    root = Path(__file__).parent / 'agent_demo'
    manifest = json.loads((root / 'data/demo_manifest.json').read_text(encoding='utf-8'))
    return root / 'outputs' / manifest['normal_run_id']


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = engine.OUTPUT, engine.screen, engine.write_model_analysis, engine.choose_tool
        engine.OUTPUT = Path(self.temp.name)
        self.key = 'test-key-never-on-disk'

        def fake_screen(facts, docs, transport=None, api_key=None):
            self.assertEqual(api_key, self.key)
            return ({f['fact_id']: {'supported': True, 'reason': '本地假响应'}
                     for f in facts if not f['synthetic_test']},
                    {'model': 'local-fake', 'real_model_calls': 1,
                     'token_usage': {'input_tokens': 10, 'output_tokens': 5}})

        def fake_write(context, key):
            self.assertEqual(key, self.key)
            sections = []
            for title, data in context['sections'].items():
                ids = [data['facts'][0]['fact_id']] if data['facts'] else []
                sections.append({'title': title, 'text':
                    '基于已审核事实，本节观察到相关变化，但适用范围仍需核对。' if ids
                    else '本节缺少可用的定量风险事实，不能据此形成具体风险判断。',
                    'fact_ids': ids})

            def response(_):
                return {'status': 'completed', 'id': 'local-fake',
                    'usage': {'input_tokens': 100, 'output_tokens': 50},
                    'output': [{'type': 'message', 'content': [{'type': 'output_text',
                        'text': json.dumps({'sections': sections}, ensure_ascii=False)}]}]}
            return model_analysis.write(context, key, response)

        engine.screen, engine.write_model_analysis = fake_screen, fake_write
        def fake_choose(plan, available, observations, key):
            self.assertEqual(key, self.key)
            self.assertEqual(len(observations), len(engine.TOOLS)-len(available))
            return {'action': available[0], 'reason': '根据已检索结果选择下一类本地资料'}, {
                'contract': 'tool_choice', 'model': 'local-fake', 'real_model_calls': 1,
                'token_usage': {'input_tokens': 5, 'output_tokens': 3}}
        engine.choose_tool = fake_choose

    def tearDown(self):
        engine.OUTPUT, engine.screen, engine.write_model_analysis, engine.choose_tool = self.old
        engine.MODEL_KEYS.clear()
        self.temp.cleanup()

    def test_model_report_waits_for_second_review(self):
        for topic, decision in [(DEFAULT_TOPIC, 'approve'),
                                ('分析2024年新能源汽车行业重点公司财务表现', 'reject')]:
            with self.subTest(topic=topic):
                run = engine.create(topic, model_mode='openai', api_key=self.key)
                engine.advance(run['run_id'], self.key)
                run = engine.load(run['run_id'])
                self.assertEqual(run['status'], 'WAITING_REVIEW')
                self.assertEqual([d['action'] for d in run['agent_decisions']], list(engine.TOOLS))
                decisions = [{'item_id': i['item_id'], 'selected_fact_id': i['candidate_ids'][0],
                              'reason': '本地检查'} for i in run['review_items']]
                engine.submit_review(run['run_id'], decisions)
                engine.finish(run['run_id'])
                run = engine.load(run['run_id'])
                self.assertEqual((run['status'], run['analysis_status']), ('COMPLETED', 'pending'))
                self.assertFalse((engine.folder(run['run_id']) / 'model_report.md').exists())
                engine.review_analysis(run['run_id'], decision, '有待核查' if decision == 'reject' else '')
                self.assertEqual((engine.folder(run['run_id']) / 'model_report.md').exists(),
                                 decision == 'approve')
        for file in Path(self.temp.name).rglob('*'):
            if file.is_file():
                self.assertNotIn(self.key.encode(), file.read_bytes())

    def test_unsupported_number_blocks_model_draft(self):
        context = json.loads((bundled_sample() / 'write_context.json')
                             .read_text(encoding='utf-8'))
        def bad(_):
            sections = [{'title': name, 'text':
                '根据999999万辆的销量，本节得出确定结论。' if i == 0
                else '本节缺少足够资料，暂不作进一步判断。',
                'fact_ids': [data['facts'][0]['fact_id']] if data['facts'] else []}
                for i, (name, data) in enumerate(context['sections'].items())]
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps({'sections': sections}, ensure_ascii=False)}]}]}
        with self.assertRaisesRegex(ValueError, '数字'):
            model_analysis.write(context, self.key, bad)

    def test_restart_can_retry_analysis_without_repeating_review(self):
        run = engine.create(DEFAULT_TOPIC, model_mode='openai', api_key=self.key)
        engine.advance(run['run_id'], self.key)
        run = engine.load(run['run_id'])
        choices = [{'item_id': i['item_id'], 'selected_fact_id': i['candidate_ids'][0],
                    'reason': '本地检查'} for i in run['review_items']]
        engine.MODEL_KEYS.clear()  # Simulate a process restart during the human pause.
        engine.submit_review(run['run_id'], choices)
        engine.finish(run['run_id'])
        run = engine.load(run['run_id'])
        self.assertEqual((run['status'], run['analysis_status']), ('COMPLETED', 'failed'))
        self.assertTrue((engine.folder(run['run_id']) / 'final_report.md').exists())
        engine.request_analysis_retry(run['run_id'], self.key)
        engine.generate_analysis(run['run_id'], self.key)
        self.assertEqual(engine.load(run['run_id'])['analysis_status'], 'pending')

    def test_rejected_key_is_explained_without_exposing_it(self):
        state = json.loads((bundled_sample() / 'state.json')
                           .read_text(encoding='utf-8'))
        error = HTTPError('https://api.openai.com/v1/responses', 401, 'Unauthorized', {}, None)
        with patch('model_review.urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'API Key（401）') as result:
                model_review.screen(state['facts'], state['documents'], api_key=self.key)
        self.assertNotIn(self.key, str(result.exception))

    def test_agent_can_stop_early_and_cannot_call_unlisted_tool(self):
        plan = {'topic': DEFAULT_TOPIC, 'focus': 'industry', 'period': '2024'}
        def response(action):
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps({'action': action, 'reason': '已观察结果'}, ensure_ascii=False)}]}]}
        with self.assertRaisesRegex(ValueError, '未授权工具'):
            tool_agent.choose(plan, ['search_web'], [], self.key, lambda _: response('delete_files'))
        decision, call = tool_agent.choose(plan, ['search_web'], [], self.key, lambda _: response('finish'))
        self.assertEqual((decision['action'], call['contract']), ('finish', 'tool_choice'))
        engine.choose_tool = lambda *_: (decision, call)
        run = engine.create(DEFAULT_TOPIC, model_mode='openai', api_key=self.key)
        engine.advance(run['run_id'], self.key)
        run = engine.load(run['run_id'])
        self.assertEqual(run['document_ids'], [])
        self.assertEqual(run['agent_decisions'][0]['action'], 'finish')

    def test_feedback_http_route_writes_local_record(self):
        run = engine.create(DEFAULT_TOPIC)
        run['status'] = 'COMPLETED'
        engine.save(run)
        server = main.LocalServer(('127.0.0.1', 0), main.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            request = Request(origin + '/api/runs/' + run['run_id'] + '/feedback',
                data=json.dumps({'rating': 4, 'area': 'navigation',
                                 'note': '测试反馈写入本地记录'}).encode('utf-8'),
                headers={'Origin': origin, 'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=3) as response:
                self.assertEqual(json.load(response), {'saved': True, 'count': 1})
            feedback = json.loads((engine.folder(run['run_id']) / 'feedback.json').read_text(encoding='utf-8'))
            self.assertEqual((feedback[0]['rating'], feedback[0]['area']), (4, 'navigation'))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
