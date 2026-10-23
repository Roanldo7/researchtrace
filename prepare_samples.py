"""Reproduce bundled offline examples without Keys or changing existing tasks."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'agent_demo'))
import engine
from providers import DEFAULT_TOPIC

NOTICE = ('预置离线演示样例：审核决定由 bundled_example_rules 脚本按内置公开摘录填写，'
          '不是用户人工审核结论；请新建研究后自行核对来源。')


def prepare_samples():
    manifest_path = ROOT / 'agent_demo/data/demo_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    for scenario in ('normal', 'exception'):
        name = scenario + '_run_id'
        run_id = manifest.get(name)
        if run_id and (engine.folder(run_id) / 'state.json').exists():
            old = engine.load(run_id)
            if old.get('bundled_example') and old['status'] == 'COMPLETED':
                continue
        run = engine.create(DEFAULT_TOPIC, scenario, model_mode='mock')
        run_id = run['run_id']
        engine.advance(run_id)
        run = engine.load(run_id)
        if run['status'] != 'WAITING_REVIEW':
            raise RuntimeError('离线样例未进入审核：' + run.get('error', run['status']))
        facts = {fact['fact_id']: fact for fact in run['facts']}
        decisions = []
        for item in run['review_items']:
            selected = next((fid for fid in item['candidate_ids'] if not facts[fid]['synthetic_test']), None)
            reason = ('预置示例决定：采用公开来源候选，排除合成测试冲突。' if item['conflict'] else
                      '预置示例决定：按内置公开摘录采用，未进行用户人工审核。') if selected else \
                     '预置示例决定：没有可采用的公开来源候选，排除。'
            decisions.append(dict(item_id=item['item_id'], selected_fact_id=selected, reason=reason))
        engine.submit_review(run_id, decisions, actor='bundled_example_rules')
        engine.finish(run_id)
        run = engine.load(run_id)
        if run['status'] != 'COMPLETED' or not run.get('publishable'):
            raise RuntimeError('离线样例未完成：' + run.get('error', run['status']))
        run.update(bundled_example=True, sample_review=NOTICE)
        run['warnings'].insert(0, NOTICE)
        engine.save(run)
        report = engine.folder(run_id) / 'final_report.md'
        engine.atomic(report, '> ' + NOTICE + '\n\n' + report.read_text(encoding='utf-8'))
        manifest[name] = run_id
    manifest['sample_review'] = NOTICE
    engine.dump(manifest_path, manifest)
    return manifest


if __name__ == '__main__':
    print(json.dumps(prepare_samples(), ensure_ascii=False, indent=2))
