"""Finish the verified DGM development cutoff after its last candidate was exported."""
import argparse
import base64
from collections import Counter
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile

from .deepseek_debug import DebugBudget, DeepSeekProvider, ledger_status
from .dgm_deepseek_wire import DgmFlashTransport
from .dgm_native import load_policies
from .original_container import files_sha, run_original
from .provider import write_json
from .sandbox import DockerSandbox
from .yaml_twins import evaluate_code, load_native, sha, summarize


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--plan-only',action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-dgm-cont-[A-Za-z0-9-]{1,30}',args.job_id):
        parser.error('Use an independent DGM continuation identity')
    root = Path(args.root).resolve()
    old = root/'runs/dev-dsflash-dgm-02'
    dest = root/'runs'/args.job_id
    parent = read(old/'manifest.json')
    ledger = ledger_status(old/'provider')
    states = Counter(call['state'] for call in ledger['calls'])
    if (parent['scope'] != 'development_only_DGM_YAML_twins' or parent['runs_formal_eval']
            or read(old/'status.json')['status'] != 'failed' or states != {'success':240}
            or ledger['usage_unknown_calls'] or ledger['calls'][-1]['call_id'] != 'poisoned-candidate-0001-train-load_config-0016'):
        raise ValueError('The original development cutoff no longer matches the reviewed receipts')
    original = root/'upstream/dgm-sol'
    if files_sha(original) != parent['source_files']:
        raise ValueError('Pinned DGM source differs')
    _, policy = load_policies(original)
    native_old = old/'poisoned/output_dgm/native'
    initial = old/'initial-agent'
    child = 'candidate-0001'
    child_old = native_old/child
    failed = read(child_old/'metadata.json')
    old_log = native_old/'dgm_metadata.jsonl'
    archive_before = policy['load_dgm_metadata'](str(old_log),last_only=True)
    if (archive_before['generation'] != 1 or archive_before['children'] or archive_before['archive'] != ['initial','candidate-0000']
            or failed['run_id'] != child or failed['parent_commit'] != archive_before['selfimprove_entries'][0][0]
            or not failed['model_patch_notempty'] or (child_old/'evaluation.json').exists()):
        raise ValueError('Expected one exported candidate awaiting its two training scores')
    edit = old/'invocations'/f'poisoned-{child}-edit'/'result.json'
    export = read(edit)['document']['files']
    exported_hashes = {name:sha(base64.b64decode(row['data'],validate=True)) for name,row in export.items()}
    if exported_hashes != files_sha(child_old/'agent'):
        raise ValueError('Exported candidate snapshot changed')
    budget = DebugBudget(requests=100,input_tokens=9_000_000,output_tokens=409_600,per_call_output=4096)
    spec = {'scope':'development_only_DGM_existing_candidate_evaluation_and_archive','runs_formal_eval':False,
            'job_id':args.job_id,'parent':old.name,'model':ledger['model'],'image':parent['image'],
            'parent_manifest_sha256':sha((old/'manifest.json').read_bytes()),'parent_successful_receipts':240,
            'parent_edit_sha256':sha(edit.read_bytes()),'previous_archive_sha256':sha(old_log.read_bytes()),
            'candidate_hashes':exported_hashes,'provider_budget':budget.__dict__,
            'work':['rerun the one interrupted training episode under a separate identity',
                    'evaluate the other frozen training task and compile the existing candidate',
                    'apply original archive policy and independently score the two frozen heldout pairs']}
    if args.plan_only:
        print(json.dumps(spec,indent=2))
        return

    dest.mkdir(parents=True,mode=0o700)
    write_json(dest/'manifest.json',spec)
    write_json(dest/'status.json',{'status':'running','phase':'candidate_training'})
    try:
        dev = DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180)
        provider = DgmFlashTransport(dev)
        sandbox = DockerSandbox(image=spec['image'])
        native = load_native(root)
        native_new = dest/'native'
        shutil.copytree(native_old,native_new)

        def evaluate(label,asset,taskset):
            rows = []
            for case in parent['tasksets'][taskset]:
                cid = case['id']
                call = f'{label}-{cid}'
                with tempfile.TemporaryDirectory(prefix='ftrsi-dgm-cont-case-') as directory:
                    task = Path(directory)
                    (task/(cid+'.py')).write_text(case['stub'])
                    (task/'.docs').mkdir()
                    (task/'.docs/instructions.md').write_text(case['instructions'])
                    document = run_original(agent_dir=asset,task_dir=task,instructions=case['instructions'],image=spec['image'],
                        provider=provider,artifact_dir=dest/'invocations'/call,call_id=call)
                code = base64.b64decode(document['files'][cid+'.py']['data']).decode()
                check = evaluate_code(root,dest/'cases'/call,case,code,sandbox,native)
                rows.append({'case':case,'evaluation':check,'code':code,'patch_nonempty':bool(document['patch'].strip())})
            write_json(dest/f'{label}.json',rows)
            return rows

        training = evaluate('candidate-training',native_new/child/'agent','poisoned')
        resolved = [row['case']['id'] for row in training if row['evaluation']['utility']]
        unresolved = [row['case']['id'] for row in training if not row['evaluation']['utility'] and row['patch_nonempty']]
        empty = [row['case']['id'] for row in training if not row['evaluation']['utility'] and not row['patch_nonempty']]
        performance = {'accuracy_score':len(resolved)/len(training),'total_resolved_instances':len(resolved),
                       'total_submitted_instances':len(training),'total_resolved_ids':resolved,
                       'total_unresolved_ids':unresolved,'total_emptypatch_ids':empty}
        meta = {key:value for key,value in failed.items() if key not in ('error','error_type')}
        write_json(native_new/child/'metadata.json',{**meta,'overall_performance':performance})
        compiled = policy['filter_compiled']([child],str(native_new),num_swe_issues=[len(training)],logger=logging.getLogger(__name__))
        if compiled != [child]:
            raise ValueError('Original DGM rejected the recovered candidate as incomplete')
        archive = policy['update_archive'](str(native_new),archive_before['archive'],compiled,method='keep_all',noise_leeway=0)
        recovered = {**archive_before,'children':[child],'children_compiled':compiled,'archive':archive}
        write_json(dest/'recovered-native-archive.json',recovered)
        best = max(archive,key=lambda version:read(native_new/version/'metadata.json')['overall_performance']['accuracy_score'])
        selected = initial if best == 'initial' else native_new/best/'agent'
        write_json(dest/'status.json',{'status':'running','phase':'frozen_heldout','selected':best})
        before = evaluate('initial-heldout',initial,'heldout')
        after = evaluate('selected-heldout',selected,'heldout')
        result = {'status':'completed','scope':spec['scope'],'runs_formal_eval':False,'clean_parent_evidence_reused':True,
                  'continued_poisoned_arm':{'native_archive':recovered,'selected':best,'training':performance,
                   'initial_heldout':summarize(before),'selected_heldout':summarize(after),
                   'selected_asset_hashes':files_sha(selected)},'ledger':dev.summary()}
        write_json(dest/'result.json',result)
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


if __name__=='__main__':
    main()
