"""Small offline behavior set; optional live model advice check with explicit --model."""
import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'agent_demo'))
from providers import mock_llm  # noqa: E402
from report import assess, derive  # noqa: E402
from model_review import screen  # noqa: E402


def evaluate(use_model=False):
    manifest = json.loads((ROOT / 'agent_demo/data/demo_manifest.json').read_text(encoding='utf-8'))
    state = json.loads((ROOT / 'agent_demo/outputs' / manifest['normal_run_id'] / 'state.json').read_text(encoding='utf-8'))
    cases = json.loads((ROOT / 'eval_cases.json').read_text(encoding='utf-8'))
    facts = {f['fact_id']: f for f in state['facts']}
    docs = {d['source_id']: d for d in state['documents']}
    results = []
    for case in cases:
        if case['kind'] == 'extract':
            original = facts[case['fact_id']]
            extracted = mock_llm('extract', {'document': docs[original['source_ids'][0]], 'char_budget': 2000})['facts']
            match = next((f for f in extracted if (f['entity'], f['metric'], f['period']) ==
                          (original['entity'], original['metric'], original['period'])), None)
            passed = bool(match and all(match[k] == original[k] for k in
                ('value', 'unit', 'value_type', 'scope', 'excerpt_locator', 'source_ids')))
        else:
            kept = [f for fid, f in facts.items() if fid not in case['remove']]
            passed = assess(kept, case.get('focus', 'industry'))['sufficient'] == case['publishable']
            if 'derived_count' in case:
                passed = passed and len(derive(kept)) == case['derived_count']
        results.append({'id': case['id'], 'passed': passed})
    result = {'suite': 'offline behavior', 'cases': len(cases), 'passed': sum(x['passed'] for x in results),
              'results': results, 'real_model_calls': 0}
    if use_model:
        candidates = copy.deepcopy(state['facts'])
        expected = {f['fact_id']: True for f in candidates}
        for fid, field, value in [('X01', 'value', 99999), ('X02', 'period', '2026'),
                                  ('X03', 'value_type', 'announced_plan')]:
            altered = copy.deepcopy(facts['F13'])
            altered['fact_id'], altered[field] = fid, value
            candidates.append(altered)
            expected[fid] = False
        advice, call = screen(candidates, state['documents'])
        result['model_probe'] = {'candidates': len(expected),
            'matched': sum(advice[fid]['supported'] == want for fid, want in expected.items()),
            'details': [{'fact_id': fid, 'expected': want, **advice[fid]} for fid, want in expected.items()],
            'call': call}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', action='store_true', help='one real API call; requires OPENAI_API_KEY')
    args = parser.parse_args()
    result = evaluate(args.model)
    out = ROOT / 'evaluation_runs'
    out.mkdir(exist_ok=True)
    path = out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + ('_model' if args.model else '_offline') + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{result['passed']}/{result['cases']} offline cases; model probe: {result.get('model_probe', 'not run')}")
    print(path)
    if result['passed'] != result['cases']:
        raise SystemExit(1)
