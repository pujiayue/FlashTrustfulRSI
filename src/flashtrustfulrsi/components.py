"""Reusable native algorithm components, distinct from end-to-end paper reproduction.

All executable upstream bytes are checked against the pinned source lock. Input
documents are data; generated code in a patch is classified, never executed.
"""
import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import math
import sys
import types
from pathlib import Path

from .provider import write_json

COMPONENTS = {
    'seabench-monitor-metrics': ('E03', 'frozen_predictions_scoring'),
    'seabench-monitor-threshold': ('E03', 'validation_threshold_selection'),
    'agentbreeder-bootstrap': ('E05', 'native_bootstrap_score_statistics'),
    'agentbreeder-pareto': ('E05', 'native_adas_eval_pareto_frontier'),
    'safecoevo-memory': ('E06', 'verified_memory_admission_and_retrieval'),
    'safeevolve-skills': ('E07', 'native_skill_compilation_and_retrieval'),
    'trusting-trust-static': ('E08', 'static_added_line_eval_classifier'),
    'agentpoison-react-metrics': ('E10', 'native_react_attack_scoring'),
}


def source(root, method, relative):
    lock = json.loads((root/'configs/component-sources.lock.json').read_text())[method]
    if relative not in lock['files']: raise ValueError('Unregistered native source')
    path = root/'external'/method/relative
    if hashlib.sha256(path.read_bytes()).hexdigest() != lock['files'][relative]:
        raise ValueError('Native source hash mismatch: '+relative)
    return path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def evaluate(component, data, root):
    method, scope = COMPONENTS[component]
    lock = json.loads((root/'configs/component-sources.lock.json').read_text())[method]
    # Verify every transitive file, including helpers imported by the entrypoint.
    if method != 'E05':
        for relative in lock['files']: source(root, method, relative)
    prefix = '_ftrsi_native_'+method+'_'+lock['commit'][:10]
    notes = []; result = None
    if method == 'E03':
        import numpy as np
        native = load(source(root, method, next(iter(lock['files']))), prefix)
        labels = data['labels']; surfaces = data['surfaces']
        threshold_selection = component == 'seabench-monitor-threshold'
        predictions = data['scores' if threshold_selection else 'predictions']
        if not labels or len(labels)!=len(predictions) or len(labels)!=len(surfaces): raise ValueError('Nonempty aligned observations required')
        if any(type(x) not in (int,bool) or x not in (0,1) for x in labels): raise ValueError('Binary labels required')
        if set(surfaces) != set(native.SURFACES): raise ValueError('All three surfaces required by native scorer; missing surface is not zero risk')
        if threshold_selection:
            if data['split'] != 'validation':
                raise ValueError('Threshold selection requires validation scores')
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in predictions):
                raise ValueError('Finite validation scores required')
            for surface in native.SURFACES:
                if {label for label, group in zip(labels, surfaces) if group == surface} != {0, 1}:
                    raise ValueError('Both classes required per validation surface')
            *_, threshold, metrics = native.select_threshold(np.asarray(labels), np.asarray(predictions), np.asarray(surfaces))
            result = {'threshold': threshold if math.isfinite(threshold) else str(threshold),
                      'fpr_limit_exclusive': .10, 'validation_metrics': metrics}
            notes = ['Select on frozen validation scores; apply the threshold to separate test scores.']
        else:
            if any(type(x) not in (int,bool) or x not in (0,1) for x in predictions): raise ValueError('Binary predictions required')
            result = native.metrics(np.asarray(labels), np.asarray(predictions), np.asarray(surfaces))
            notes = ['Predictions must come from a previously frozen monitor; no fitting or threshold selection is performed.']
    elif component == 'agentbreeder-pareto':
        rows = data['scaffolds']
        if not rows:
            raise ValueError('Nonempty scaffold list required')
        ids = [row['id'] for row in rows]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Distinct nonempty scaffold IDs required')
        candidates = []
        for row in rows:
            scores = [row['capability'], row['safety']]
            if any(type(x) not in (int, float) or not 0 <= x <= 1 for x in scores):
                raise ValueError('Capability and safety scores must be in [0, 1]')
            candidates.append(types.SimpleNamespace(id=row['id'], scaffold_capability_ci_median=scores[0],
                                                    scaffold_safety_ci_median=scores[1]))
        path = source(root, method, 'src/adas_eval.py')
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'compute_pareto_frontier')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        result = {'n': len(candidates),
                  'frontier_ids': [row.id for row in namespace['compute_pareto_frontier'](candidates)]}
        notes = ['Uses the original adas_eval frontier, including its special handling of zero scores.',
                 'Evaluates one supplied group; no population search or scaffold execution.']
    elif method == 'E05':
        import numpy as np
        values = data['scores']; seed = data.get('seed',42); trials = data.get('bootstrap_samples',1000)
        if not values or any(type(x) not in (int,float) or not np.isfinite(x) for x in values): raise ValueError('Nonempty finite scores required')
        if type(seed)!=int or type(trials)!=int or not 1<=trials<=100000: raise ValueError('Invalid bootstrap settings')
        # The original decorators only register functions with Inspect. Strip
        # registration to avoid importing the entire execution backend.
        tree = ast.parse(source(root,method,'src/evals/metrics.py').read_text()); nodes=[]
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in ('ci_lower','ci_upper','median'):
                node.decorator_list=[]; nodes.append(node)
        ns={'np':np,'logging':logging,'Metric':object,'Score':object}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'AgentBreeder/native_metrics','exec'),ns)
        scores=[types.SimpleNamespace(as_float=lambda value=x:value) for x in values]
        state=np.random.get_state()
        try:
            result={}
            for key in ('ci_lower','ci_upper','median'):
                np.random.seed(seed)
                result[key]=float(ns[key](num_bootstrap_samples=trials)(scores))
        finally: np.random.set_state(state)
        result.update(n=len(values),seed=seed,bootstrap_samples=trials,confidence_level=.95)
        notes=['Original median is the median of bootstrapped means, not the raw sample median.',
               'Same deterministic resample seed for all three summaries; no scaffold execution.']
    elif method == 'E06':
        package=types.ModuleType(prefix); package.__path__=[str(root/'external/E06/src/safecoevo_runtime')];sys.modules[prefix]=package
        contracts=load(source(root,method,'src/safecoevo_runtime/contracts.py'),prefix+'.contracts')
        memory=load(source(root,method,'src/safecoevo_runtime/memory.py'),prefix+'.memory')
        permission=load(source(root,method,'src/safecoevo_runtime/permission.py'),prefix+'.permission')
        store=memory.InMemoryMemoryStore(); permissions=permission.InMemoryPermissionExperienceStore(); admission=[]
        from dataclasses import asdict
        for kind, rows, cls, target in [('memory',data.get('memories',[]),contracts.MemoryItem,store),
                                        ('permission',data.get('permissions',[]),contracts.PermissionExperienceItem,permissions)]:
            for index,row in enumerate(rows):
                if type(row.get('verified'))!=bool: raise ValueError('verified must be an explicit boolean')
                item=cls(**row)
                try: target.append(item); admission.append({'kind':kind,'index':index,'accepted':True})
                except ValueError as exc: admission.append({'kind':kind,'index':index,'accepted':False,'reason':str(exc)})
        query=data['query']; limit=data.get('limit',4)
        result={'admission':admission,'memories':[asdict(x) for x in store.retrieve(query,limit=limit)],
                'permissions':[asdict(x) for x in permissions.retrieve(query,limit=limit)],'retrieval':store.retrieval_metadata()}
        notes=['verified is caller-supplied provenance, not a new verification by this component.',
               'This component does not implement GuardVPO or the complete online S-Harness loop.']
    elif method == 'E07':
        native=load(source(root,method,'core/skillrl/runtime_skill_compiler.py'),prefix+'_compiler')
        bank_path=Path(data['bank_path']).expanduser().resolve(strict=True)
        bank,summary=native.compile_bank(bank_path)
        old=sys.modules.get('bank_utils')
        try:
            load(source(root,method,'core/skillrl/bank_utils.py'),'bank_utils')
            retrieval=load(source(root,method,'core/skillrl/runtime_retrieval.py'),prefix+'_retrieval')
            selected=retrieval.retrieve_runtime_skills(data['metadata'],bank)
        finally:
            if old is not None: sys.modules['bank_utils']=old
            else: sys.modules.pop('bank_utils',None)
        result={'runtime_bank':bank,'compile_summary':summary,'selected_skills':selected}
        notes=['Compiles an existing bank; does not train model weights or claim a newly evolved bank.']
    elif method == 'E08':
        native=load(source(root,method,'evaluation/verdict.py'),prefix)
        if not isinstance(data['patch'],str): raise ValueError('patch must be text')
        result=native.classify_patch(data['patch'])
        notes=['Original heuristic classifies added lines only; strings/comments can match.',
               'sandboxed_eval is the upstream label, not a security guarantee.',
               'Runtime behavioral witness and self-modification experiments require Docker and are skipped.']
    elif method == 'E10':
        rows=data['rows']
        if not rows: raise ValueError('Nonempty native ReAct records required')
        for row in rows:
            if not isinstance(row['answer'],str) or type(row['gt_answer'])!=bool or not isinstance(row['traj'],str): raise ValueError('Invalid ReAct record')
            if any(type(row[k])!=int or row[k]<0 for k in ('retrieval_success','overall_retrieval')): raise ValueError('Nonnegative native retrieval counters required')
            if row['retrieval_success']>row['overall_retrieval']: raise ValueError('Retrieval successes exceed attempts')
        denominator=sum(r['overall_retrieval']-int(r['overall_retrieval']>r['retrieval_success']) for r in rows)
        if not denominator: raise ValueError('Native ASR-r denominator is zero; cannot report a safe score')
        # Original scorer has top-level argparse and jsonlines I/O. Execute only
        # its assignments, read loop, and print expressions on validated records.
        tree=ast.parse(source(root,method,'ReAct/eval.py').read_text())
        nodes=[n for n in tree.body if (isinstance(n,ast.Assign) and all(isinstance(t,ast.Name) and t.id!='args' for t in n.targets)
               and not isinstance(n.value,ast.Call)) or isinstance(n,(ast.With,ast.If))
               or (isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='print')]
        ns={'args':types.SimpleNamespace(path='validated-memory-records'),
            'jsonlines':types.SimpleNamespace(open=lambda _:contextlib.nullcontext(iter(rows)))}
        output=io.StringIO()
        with contextlib.redirect_stdout(output):exec(compile(ast.Module(body=nodes,type_ignores=[]),'AgentPoison/native_eval','exec'),ns)
        result={'native_stdout':output.getvalue(),'n':len(rows),'retrieval_denominator':denominator,'conditional_action_denominator':ns['valid_retrieval']}
        notes=['Native ASR-t is 1-accuracy, not independently verified target-action success.',
               'Original answer-string heuristic and conditional ASR-a zero convention are preserved; denominators are separate.',
               'Trigger optimization and embedding model training are not executed by this scorer.']
    return {'component':component,'method':method,'scope':scope,'status':'completed','paid_calls':0,
            'paper_experiment_completed':False,'upstream':lock,'result':result,'notes':notes}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--component',choices=COMPONENTS,required=True)
    p.add_argument('--root',default='.');p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();root=Path(args.root).resolve();input_path=Path(args.input).resolve();output=Path(args.output)
    if output.exists():raise ValueError('Output exists; use a new result path')
    data=json.loads(input_path.read_text());result=evaluate(args.component,data,root)
    result['input_sha256']=hashlib.sha256(input_path.read_bytes()).hexdigest()
    result['adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json(output,result);print(output)


if __name__=='__main__':main()
