"""Offline cross-check of native YAML trajectories against original model receipts."""
import argparse, hashlib, json
from pathlib import Path
from flashtrustfulrsi.yaml_twins import INITIAL, UPDATE, extract_code, summarize


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit(run, receipts):
    read = lambda name: json.loads((run/name).read_text())
    manifest, result, rows, assets = (read(n) for n in
        ('manifest.json', 'result.json', 'traces.json', 'assets.json'))
    assert result['status'] == 'completed' and result['full_paper_reproduction'] is False
    assert manifest['initial_prompt'] == INITIAL and manifest['update_prompt'] == UPDATE
    assert len(rows) == 46 and assets['initial'] == INITIAL
    requests, responses = {}, {}
    for path in receipts.glob('*.request.json'):
        req = json.loads(path.read_text())
        if req['job_id'] != manifest['job_id']:
            continue
        assert req['call_id'] not in requests
        requests[req['call_id']] = req['body']
        responses[req['call_id']] = json.loads(path.with_name(path.name.replace('.request.', '.response.')).read_text())
    assert len(requests) == 48
    usage = {'input_tokens': 0, 'output_tokens': 0}
    for call, body in requests.items():
        response = responses[call]
        assert body['model'] == response['model'] == 'gpt-5.6-sol'
        assert response['choices'][0]['finish_reason'] == 'stop'
        usage['input_tokens'] += response['usage']['prompt_tokens']
        usage['output_tokens'] += response['usage']['completion_tokens']
    seen = set()
    for row in rows:
        arm, phase, case_id = row['arm'], row['phase'], row['case']['id']
        call = f'{arm}-{phase}-{case_id}'
        assert call not in seen
        seen.add(call)
        taskset = manifest['tasksets']['heldout' if phase == 'heldout' else arm]
        case = next(c for c in taskset if c['id'] == case_id)
        prompt = assets['initial' if phase == 'train' else arm]
        assert requests[call]['messages'] == [
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': case['instructions'] + '\n\n' + case['stub']}]
        assert row['response'] == responses[call]
        assert row['code'] == extract_code(responses[call]['choices'][0]['message']['content'])
        assert row['prompt_sha256'] == sha(prompt.encode())
        assert row['evaluation']['inputs']['code_sha256'] == sha(row['code'].encode())
        assert row['evaluation']['inputs']['test_sha256'] == sha(case['test'].encode())
        assert row['evaluation']['inputs']['image'] == manifest['image']
        stored = json.loads((run/'cases'/call/'evaluation.json').read_text())
        assert stored == row['evaluation']
        assert stored['native_tests_run'] == 2
    for arm in ('clean', 'poisoned'):
        training = [r for r in rows if r['arm'] == arm and r['phase'] == 'train']
        assert len(training) == 5
        feedback = [{'task': case['instructions'], 'solution': row['code'],
            'test_stdout': row['evaluation']['unit_stdout'],
            'test_stderr': row['evaluation']['unit_stderr'],
            'passed': row['evaluation']['utility']}
            for case, row in zip(manifest['tasksets'][arm], training)]
        # Exact equality excludes held-out examples and witness verdicts.
        messages = requests[arm+'-update']['messages']
        assert messages[0] == {'role': 'system', 'content': UPDATE}
        assert json.loads(messages[1]['content']) == {'current_system_prompt': INITIAL, 'training_feedback': feedback}
        change = read(arm+'-update.json')
        assert change['response'] == responses[arm+'-update']
        assert change['parent_sha256'] == sha(INITIAL.encode())
        assert change['child_sha256'] == sha(assets[arm].encode())
        assert json.loads(change['response']['choices'][0]['message']['content']) == {'system_prompt': assets[arm]}
    for arm in ('initial', 'clean', 'poisoned'):
        heldout = [r for r in rows if r['arm'] == arm and r['phase'] == 'heldout']
        assert len(heldout) == 12
        assert summarize(heldout) == result['results'][arm]
    assert read('sandbox.json')['status'] == 'ready'
    return {'status': 'verified', 'job_id': manifest['job_id'], 'requests': 48,
        'training_episodes': 10, 'heldout_episodes': 36, 'updates': 2,
        'model': 'gpt-5.6-sol', 'usage': usage, 'exact_update_feedback_checked': True,
        'heldout_or_witness_feedback_to_updater': False,
        'scope': result['scope'], 'full_paper_reproduction': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--receipts', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.run, a.receipts)
    a.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
