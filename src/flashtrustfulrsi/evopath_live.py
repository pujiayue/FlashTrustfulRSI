"""Native EvoPath finance streams through the recorded exact-model transport.

Canary changes decision_interval explicitly; it is never a paper-default score.
Serial execution makes restart call identities deterministic. All successful
requests replay from receipts; a failed/ambiguous call stops recovery.
"""
import argparse, hashlib, json, time
from pathlib import Path
from .provider import write_json
from .transport import RecordedProvider, MODEL, TransportError

UPSTREAM_COMMIT='a0a596237d888304c25be2828d30c99e2f66b3b7'

def run(args):
    from evolens.agent import ModelConfig, ModelTradingAgent, ModelRequestError, estimate_model_calls
    from evolens.dataset import load_dataset
    from evolens.runner import BenchmarkRunner, write_run
    from evolens.cli import _dataset_market
    from .evaluators import evopath_summary
    root=Path(args.root).resolve();up=next((root/'upstream').glob('HQ-Lin-EvoPathBench-*'))
    config=json.loads((root/args.provider).read_text())
    settings=ModelConfig(model=MODEL,base_url=config['base_url'],api_key_env='FLASH_TRUST_SECRET_REF_ONLY',
                         temperature=0.1,max_retries=0,decision_interval=args.decision_interval,
                         save_raw_responses=True,max_consecutive_failures=1)
    scenarios,streams,manifest=load_dataset(up/'data/finance')
    selected=[next(s for s in streams if s.template==template) for template in args.templates.split(',')]
    conditions=args.conditions.split(',');state_off=[c for c in conditions if c!='baseline']
    estimate=estimate_model_calls(scenarios,selected,conditions,settings,args.campaigns,args.repeats,state_off)
    dataset_files={str(p.relative_to(up/'data/finance')):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((up/'data/finance').rglob('*')) if p.is_file()}
    spec={'schema':'flashtrustfulrsi.evopath.v1','job_id':args.job_id,'upstream_commit':UPSTREAM_COMMIT,
          'stream_ids':[s.stream_id for s in selected],'settings':settings.public_dict(),
          'conditions':conditions,'state_off_conditions':state_off,'campaigns':args.campaigns,
          'campaign_offset':args.campaign_offset,'repeats':args.repeats,'dataset_hashes':dataset_files,
          'estimate':estimate,'max_concurrency':1,'evidence_scope':'live_model_canary' if args.decision_interval!=1 else 'live_model_native_frequency',
          'modifications':['single requested API model','recorded transport; retries disabled','campaign IDs are native independent seeds',
                           'explicit stream subset' if len(selected)<len(streams) else 'all streams',
                           'decision_interval='+str(args.decision_interval)]}
    if args.plan_only:print(json.dumps(spec,indent=2));return
    dest=root/'runs'/args.job_id;dest.mkdir(parents=True,exist_ok=True)
    spec['adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    spec['transport_sha256']=hashlib.sha256(Path(__file__).with_name('transport.py').read_bytes()).hexdigest()
    if (dest/'manifest.json').exists():
        if json.loads((dest/'manifest.json').read_text())!=spec:raise ValueError('Frozen job manifest changed; select a new job ID')
    else:write_json(dest/'manifest.json',spec)
    if (dest/'result.json').exists():print((dest/'result.json').read_text());return
    p=RecordedProvider(config,root/'runs/v02-canary',args.job_id)
    used=p.summary()
    # Whole-job check before any request. Restart has already-consumed receipts;
    # per-request transactional limits remain authoritative on resumed jobs.
    with p.connect() as db:existing=db.execute('SELECT count(*) FROM calls WHERE job=?',(args.job_id,)).fetchone()[0]
    if existing==0 and (used['requests']+estimate['max_http_attempts']>300 or used['output_tokens']+estimate['output_token_upper_bound']>1_000_000):
        raise ValueError('Whole-job estimate does not fit canary phase')
    def complete(messages,model_config,max_tokens,phase=None):
        started=time.monotonic()
        body={'model':MODEL,'messages':messages,'max_tokens':max_tokens,'temperature':model_config.temperature}
        if model_config.top_p is not None:body['top_p']=model_config.top_p
        if model_config.api_seed is not None:body['seed']=model_config.api_seed
        try:r=p.invoke('chat',body)
        except TransportError as e:raise ModelRequestError(str(e)) from e
        print(json.dumps({'event':'completion','call':p.sequence,'phase':phase,'usage':r.get('usage',{}),'at':time.time()}),flush=True)
        return {'content':r['choices'][0]['message'].get('content',''),'usage':r.get('usage',{}),
                'request_id':r.get('id'),'response_model':r.get('model'),'finish_reason':r['choices'][0]['finish_reason'],
                'elapsed_seconds':time.monotonic()-started,'attempts':[{'provider_receipt_call_id':f'{p.sequence:08d}'}]}
    factory=lambda condition,seed:ModelTradingAgent(condition,seed,settings,completion_fn=complete)
    write_json(dest/'status.json',{'status':'running','started':time.time(),'estimate':estimate})
    try:
        result=BenchmarkRunner(scenarios,selected,agent_factory=factory,dataset_manifest=manifest,
                               market_simulator=_dataset_market(up/'data/finance',manifest)).run(
            conditions,campaigns=args.campaigns,campaign_offset=args.campaign_offset,repeats=args.repeats,
            state_off_conditions=state_off,capture_agent_traces=True,max_concurrency=1)
        result['config']['remote_agent']={'settings':settings.public_dict()}
        write_run(dest,result,audit_layout=True)
        recalculated=evopath_summary([r.to_dict() for r in result['evaluation_records']],result['training_records'],result['state_records'])
        if recalculated!=result['summary']:raise AssertionError('Native scorer mismatch')
        out={'status':'completed','scope':spec['evidence_scope'],'job_id':args.job_id,'model':MODEL,
             'evaluation_records':len(result['evaluation_records']),'training_records':len(result['training_records']),
             'state_records':len(result['state_records']),'scorer_exact_match':True,'logical_calls':p.sequence,
             'phase_usage':p.summary(),'summary':recalculated}
        write_json(dest/'result.json',out);write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        print(json.dumps({'status':'completed','output':str(dest),'logical_calls':p.sequence}),flush=True)
    except Exception as e:
        write_json(dest/'status.json',{'status':'failed','error_type':type(e).__name__,'error':str(e),'finished':time.time(),'phase_usage':p.summary()})
        raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--provider',default='configs/provider-v02.json')
    p.add_argument('--job-id',required=True);p.add_argument('--templates',default='accumulation');p.add_argument('--conditions',default='baseline,episodic_memory')
    p.add_argument('--decision-interval',type=int,default=24);p.add_argument('--campaigns',type=int,default=1)
    p.add_argument('--campaign-offset',type=int,default=42);p.add_argument('--repeats',type=int,default=1);p.add_argument('--plan-only',action='store_true')
    run(p.parse_args())
if __name__=='__main__':main()
