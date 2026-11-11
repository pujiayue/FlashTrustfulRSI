"""Zero-model-call checks for a selected developer recipe, not a formal launcher."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
from .bootstrap import source_lock,verify
from .composition import native_inventory,native_preflight,load_spec,execute
from .deepseek_debug import DebugBudget
from .provider import write_json
from .sandbox import DockerSandbox


def check(root,config,image=None):
    import asyncio
    from types import SimpleNamespace
    from flashrsi import runner
    from .searchqa_deepseek_dev import DEV_OVERRIDES
    from .searchqa_formal import TEXT_METHODS
    root=Path(root).resolve();lock=source_lock(root);source=verify(root/lock['destination'],lock)
    # The same plan path used by run validates files, splits, overlap and native constructors.
    with contextlib.redirect_stdout(io.StringIO()) as capture:
        asyncio.run(execute(SimpleNamespace(config=Path(config),root=str(root),action='plan',job_id=None,image=None)))
    plan=json.loads(capture.getvalue());spec=load_spec(config)
    benchmark=runner._BENCHMARKS[spec['source']['name']](spec['source']);benchmark.load()
    budget=DebugBudget(**spec['budget']);algorithms=[]
    for name,cls in runner._ALGORITHMS.items():
        cfg={**copy.deepcopy(cls._defaults),**DEV_OVERRIDES.get(name,{}),'name':name}
        if name=='arbor':cfg.update(max_cycles=1,max_depth=2)
        if name=='modular_rsi':
            import yaml
            cfg['modules']=yaml.safe_load((root/lock['destination']/'examples/modular_rsi.yaml').read_text())['algorithm']['modules']
        try:
            item=native_preflight({**spec,'algorithm':cfg},benchmark,budget,{'type':'llm' if name in TEXT_METHODS else 'miniagent'})
        except Exception as exc:item={'status':'incompatible_with_selected_data_or_harness','error_type':type(exc).__name__,'error':str(exc)}
        algorithms.append({'algorithm':name,'preflight':item})
    runtimes={}
    if image:
        images={'target':image}
        if plan['resolved_worker']['type']=='miniagent':images['worker']=plan['resolved_worker']['image']
        for role,value in images.items():
            sandbox=DockerSandbox(image=value);runtime=sandbox.probe()
            if runtime['status']=='ready':
                try:
                    modules='import httpx,openai,yaml'
                    if role=='target' and spec['harness']=='claude_code':modules+=';import claude_agent_sdk'
                    observed=sandbox.run(['python','-B','-c',modules+';print("NATIVE_DEPENDENCIES_READY")'],name='ftrsi-readiness-import')
                    if observed.strip()!='NATIVE_DEPENDENCIES_READY':raise ValueError('Unexpected import preflight response')
                    runtime['selected_runtime_import']='passed'
                except Exception as exc:
                    runtime.update(status='dependency_blocked',error_type=type(exc).__name__,error=str(exc))
            runtimes[role]=runtime
    verified_runtime=bool(runtimes) and all(row['status']=='ready' for row in runtimes.values())
    inventory=native_inventory()
    inherited={kind:{name:{'class':ref,'runtime_evidence':'selected_recipe_only' if (
        kind=='harnesses' and name==spec['harness'] or kind=='benchmarks' and name in
        {spec['source']['name'],*[a['benchmark']['name'] for a in spec['audits']]}) else 'inherited_not_executed_by_this_check'}
        for name,ref in values.items()} for kind,values in inventory.items()}
    return {'schema':'flashtrustfulrsi.preformal.v1','model_requests':0,'runs_formal_eval':False,
            'scope':'selected_developer_recipe_environment_and_interfaces','source':source,
            'selected_recipe':plan,'algorithm_constructor_checks':algorithms,'runtime':runtimes,
            'selected_recipe_prepared':verified_runtime and plan['native_preflight']['status'] in ('native_constructor_verified','native_frozen_evaluation_only'),
            'model_gateway':'Not contacted by this zero-API command; availability must be checked by an explicit model preflight.',
            'scientific_validation':'not assessed; no model task was executed by this command',
            'inherited':inherited,'inventory_counts':{k:len(v) for k,v in inventory.items()}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.')
    p.add_argument('--config',required=True,type=Path);p.add_argument('--image');p.add_argument('--output',type=Path)
    args=p.parse_args(argv);result=check(args.root,args.config,args.image)
    if args.output:write_json(args.output,result)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
