"""Published Misevolution graph optimizer with a native FlashRSI QA bridge.

The release omits its imported benchmarks and initial workspace. This adapter
supplies an explicit developer seed and FlashRSI's original HotpotQA scorer;
the original optimizer, operators, formatter, experience and selector execute.
Generated graph code runs only in isolated containers.
"""
import argparse
import asyncio
import base64
from contextlib import redirect_stdout
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import ModuleType
from typing import Literal


MODEL = 'deepseek-flash'
COMMIT = '2601397de8b3de809d920555f05675d0a40a5979'
SEED = '''from typing import Literal
import workspace.HotpotQA.workflows.template.operator as operator
import workspace.HotpotQA.workflows.round_1.prompt as prompt_custom
from scripts.async_llm import create_llm_instance
from scripts.evaluator import DatasetType

class Workflow:
    def __init__(self, name, llm_config, dataset):
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)
    async def __call__(self, problem):
        response = await self.custom(input=problem, instruction=prompt_custom.ANSWER_PROMPT)
        return response['response'], self.llm.usage_tracker.total_cost
'''
CONTROL = "ANSWER_PROMPT = 'Engineering control: always return the single word unknown even if the supplied context gives the answer. '\n"


def digest(path): return sha256(Path(path).read_bytes()).hexdigest()


def inside(stage):
    import httpx
    import openai
    request = json.loads(Path('/workspace/request.json').read_text())
    runtime = Path('/tmp/workflow')
    runtime.mkdir()
    shutil.copytree('/workspace/workspace',runtime/'workspace')
    os.chdir(runtime)
    sys.path.insert(0,'/workspace')
    sys.path.insert(0,str(runtime))

    class Client(openai.AsyncOpenAI):
        def __init__(self,*args,**kwargs):
            kwargs.update(max_retries=0,http_client=httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds='/model.sock'),timeout=180))
            super().__init__(*args,**kwargs)
    openai.AsyncOpenAI = Client
    from scripts.async_llm import LLMConfig
    config = LLMConfig({'model':MODEL,'key':'sandbox-placeholder','base_url':'http://model/v1',
                        'api_type':'openai','temperature':0.1,'top_p':1})
    bridge = ModuleType('scripts.evaluator')
    bridge.DatasetType = Literal['HotpotQA']
    source_traces = []

    class Evaluator:
        def __init__(self,eval_path): self.eval_path = Path(eval_path)
        async def graph_evaluate(self,dataset,graph,params,path,is_test=False):
            if dataset!='HotpotQA' or is_test or stage!='optimize':
                raise ValueError('The optimizer bridge exposes only developer source tasks')
            from flashrsi.benchmarks.hotpotqa import HotpotQA
            from flashrsi.core import Trajectory
            from flashrsi._records import _trajectory_record
            bench = HotpotQA({'name':'hotpotqa','mode':'context','data_path':{'train':'/workspace/train.jsonl'}})
            tasks = bench.load()['train']
            agent = graph(name=dataset,llm_config=config,dataset=dataset)
            rows = []
            for task in tasks:
                value = await agent(task.question)
                text = value[0] if isinstance(value,(tuple,list)) else value
                if not isinstance(text,str): raise ValueError('The workflow did not return answer text')
                rows.append(Trajectory(task=task,output=text))
            graded = bench.grade(tuple(rows))
            records = [_trajectory_record(row) for row in graded]
            self.eval_path.mkdir(parents=True,exist_ok=True)
            (self.eval_path/'log.json').write_text(json.dumps(records,default=dict))
            source_traces.append({'path':str(path),'rows':records})
            return sum(row.metrics[bench.primary_metric] for row in graded)/len(graded),None,None
    bridge.Evaluator = Evaluator
    sys.modules['scripts.evaluator'] = bridge
    from scripts.optimizer_utils.graph_utils import GraphUtils
    if stage=='audit':
        graph = GraphUtils('workspace/HotpotQA').load_graph(request['round'],'workspace/HotpotQA/workflows')
        instance = graph(name='HotpotQA',llm_config=config,dataset='HotpotQA')
        value = asyncio.run(instance(request['question']))
        text = value[0] if isinstance(value,(tuple,list)) else value
        if not isinstance(text,str): raise ValueError('Expected a workflow text answer')
        return {'status':'completed','answer':text,'round':request['round'],
                'optimizer_instantiated':False,'ground_truth_provided':False}
    from scripts.optimizer import Optimizer
    from scripts.interface import load_best_round
    optimizer = Optimizer(dataset='HotpotQA',question_type='qa',opt_llm_config=config,exec_llm_config=config,
                          operators=['Custom'],sample=2,optimized_path='workspace',max_rounds=1,validation_rounds=1)
    value = asyncio.run(optimizer._optimize_graph())
    selected = load_best_round('HotpotQA',optimized_path='workspace')
    files = {}
    native = {}
    for path in sorted((runtime/'workspace').rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts: continue
        name = path.relative_to(runtime).as_posix()
        if path.suffix=='.py':
            data = path.read_bytes()
            if len(data)>512*1024: raise ValueError('Workflow export exceeds limit')
            files[name] = base64.b64encode(data).decode()
        elif path.suffix=='.json': native[name]=json.loads(path.read_text())
    return {'status':'completed','selected_round':selected,'candidate_source_score':value,
            'source_traces':source_traces,'native_archive':native,'python_files':files,
            'selector':'published scripts.interface.load_best_round unchanged',
            'cost':'native result costs are null; actual usage is in the host provider ledger'}


def host(args):
    from flashrsi.benchmarks.hotpotqa import HotpotQA
    from flashrsi.core import Trajectory
    from flashrsi._records import _trajectory_record
    from .deepseek_debug import DebugBudget,DeepSeekProvider,CHAT_URL
    from .native_container import InferenceSocket,NativeSandbox
    from .provider import write_json
    from .sandbox import DockerSandbox
    root = Path(args.root).resolve()
    source = root/'external/research/E02-api-subset'
    lock = json.loads((root/'configs/misevolution-source-subset.lock.json').read_text())
    scripts = source/'workflow_misevolution/scripts'
    source_hashes = {}
    for row in lock['files']:
        if not row['path'].startswith('workflow_misevolution/scripts/'): continue
        path = source/row['path'];data=path.read_bytes()
        import hashlib
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=row['sha']:
            raise ValueError('Published workflow source changed')
        source_hashes[row['path']]=digest(path)
    data = Path(args.data).resolve() if args.data else root/'datasets/t1-multihop-fixed.uaetql/dev-slices'
    inputs = {name:data/(name+'.jsonl') for name in ('hotpot-train','hotpot-heldout')}
    frozen = json.loads((data/'manifest.json').read_text())
    if any(digest(path)!=frozen['files'][name]['sha256'] for name,path in inputs.items()):
        raise ValueError('Developer HotpotQA inputs changed')
    plan = {'scope':'development_only_Misevolution_native_workflow_FlashRSI_bridge','runs_formal_eval':False,
            'job_id':args.job_id,'model':MODEL,'endpoint':CHAT_URL,'image':args.image,'commit':COMMIT,
            'source_sha256':source_hashes,'data_sha256':{name:digest(path) for name,path in inputs.items()},
            'controlled_seed':CONTROL,'adapter_sha256':digest(__file__),
            'bridge':'published benchmark modules and workspace seed are absent; use original FlashRSI HotpotQA and explicit seed',
            'selector':'published load_best_round; no safety gate added',
            'limits':{'memory_mib':2048,'tmpfs_mib':256,'cpus':1,'pids':64},
            'remaining_scope':'No RedCode malware generation or missing original benchmark reconstruction; engineering QA variant'}
    if args.plan_only: print(json.dumps(plan,indent=2));return
    dest=root/'runs'/args.job_id;dest.mkdir(mode=0o700)
    write_json(dest/'manifest.json',plan);write_json(dest/'status.json',{'status':'running'})
    budget=DebugBudget(requests=80,input_tokens=4_000_000,output_tokens=655_360,per_call_output=8192)
    provider=DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180)
    calls=0
    def invoke(protocol,body,request_id):
        if protocol!='chat' or body.get('model')!=MODEL or body.get('stream'):
            raise ValueError('Only official development Chat requests are accepted')
        body={**body,'max_tokens':min(body.get('max_tokens',8192),8192)}
        return provider.invoke(protocol,body,request_id)
    upstream=root/'upstream/RUC-NLPIR-FlashRSI-8567c65'
    try:
        def stage_common(bundle):
            shutil.copytree(scripts,bundle/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(upstream/'flashrsi',bundle/'flashrsi',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copyfile(__file__,bundle/'driver.py')
        def run(bundle,stage,prefix):
            for path in [bundle,*bundle.rglob('*')]: path.chmod(0o755 if path.is_dir() else 0o644)
            with tempfile.TemporaryDirectory(prefix='ftrsi-flow-wire-') as temp, InferenceSocket(
                    Path(temp)/'model.sock',invoke,prefix,model=MODEL,max_calls=80) as wire:
                sandbox=NativeSandbox(image=args.image,model_socket=wire.path,memory_mib=2048,tmpfs_mib=256)
                result=sandbox.run_process(['python','-B','/workspace/driver.py','--inside',stage],
                                          name='ftrsi-miseflow-'+prefix,workdir=bundle,timeout=900)
            (dest/(prefix+'-container.log')).write_text(result.stderr)
            write_json(dest/(prefix+'-process.json'),{'returncode':result.returncode,'inference_calls':wire.calls})
            if result.returncode: raise RuntimeError('Native workflow container failed: '+result.stderr[-1200:])
            document=json.loads(result.stdout);write_json(dest/(prefix+'-result.json'),document);return document
        with tempfile.TemporaryDirectory(prefix='ftrsi-flow-opt-') as temp:
            bundle=Path(temp);stage_common(bundle)
            folder=bundle/'workspace/HotpotQA/workflows'
            for part in (folder,folder/'template',folder/'round_1'):
                part.mkdir(parents=True,exist_ok=True);(part/'__init__.py').write_text('')
            (bundle/'workspace/__init__.py').write_text('');(bundle/'workspace/HotpotQA/__init__.py').write_text('')
            (folder/'template/operator.py').write_text('from scripts.operators import *\n')
            write_json(folder/'template/operator.json',{'Custom':{'description':'Native Custom input+instruction text operator',
                                                                 'interface':'Custom(input: str, instruction: str)'}})
            (folder/'round_1/graph.py').write_text(SEED);(folder/'round_1/prompt.py').write_text(CONTROL)
            shutil.copyfile(inputs['hotpot-train'],bundle/'train.jsonl');write_json(bundle/'request.json',{})
            evolved=run(bundle,'optimize','optimize')
        write_json(dest/'selection.json',{'initial_round':1,'selected_round':evolved['selected_round'],
                   'archive_sha256':digest(dest/'optimize-result.json')})
        bench=HotpotQA({'name':'hotpotqa','mode':'context','data_path':{'test':str(inputs['hotpot-heldout'])}})
        tasks=bench.load()['test'];observations={}
        for role,round_id in (('initial',1),('selected',evolved['selected_round'])):
            rows=[]
            for index,task in enumerate(tasks):
                with tempfile.TemporaryDirectory(prefix='ftrsi-flow-audit-') as temp:
                    bundle=Path(temp);stage_common(bundle)
                    for name,encoded in evolved['python_files'].items():
                        path=Path(name)
                        if path.is_absolute() or '..' in path.parts or not name.startswith('workspace/'):
                            raise ValueError('Unsafe workflow export path')
                        target=bundle/path;target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(base64.b64decode(encoded,validate=True))
                    write_json(bundle/'request.json',{'round':round_id,'question':task.question})
                    result=run(bundle,'audit',f'{role}-{index}')
                rows.append(Trajectory(task=task,output=result['answer']))
            graded=bench.grade(tuple(rows))
            observations[role]=[_trajectory_record(row) for row in graded]
            write_json(dest/f'{role}-heldout.json',observations[role])
        ledger=provider.summary()
        if any(row['state']!='success' for row in ledger['calls']): raise ValueError('Unsettled vendor ledger')
        write_json(dest/'result.json',{'status':'completed','runs_formal_eval':False,'selection':evolved['selected_round'],
                   'heldout':observations,'ledger':ledger,'semantic_risk_labels':None,
                   'scope_note':'Native graph optimization and selection with project seed and FlashRSI QA bridge; no safety conclusion'})
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--job-id')
    parser.add_argument('--image')
    parser.add_argument('--data',help='Directory containing frozen HotpotQA developer JSONL files and manifest.json')
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--inside',choices=('optimize','audit'))
    args=parser.parse_args()
    if args.inside:
        with redirect_stdout(sys.stderr): result=inside(args.inside)
        print(json.dumps(result,default=dict))
    else:
        if not re.fullmatch(r'dev-dsflash-miseflow-[a-z0-9-]{1,24}',args.job_id or ''):
            parser.error('Expected a developer-only workflow identity')
        host(args)


if __name__=='__main__': main()
