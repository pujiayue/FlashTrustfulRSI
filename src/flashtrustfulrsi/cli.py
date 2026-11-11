"""Explicit plugin dispatch; registration never implies validated integration."""
import argparse, importlib, json, sys, sysconfig
from pathlib import Path

PROFILES = {
    'trusting-trust-yaml-twins': 'yaml_twins',
    'trusting-trust-yaml': 'witness',
    'she-dojo-seed': 'she_dojo',
    'evopath-memory': 'evopath_live',
    'flashrsi-adas': 'flashrsi_native',
    'flashrsi-transfer': 'flashrsi_transfer',
    'flashrsi-searchqa-debug': 'searchqa_deepseek_dev',
    'flashrsi-multihop-debug': 't1_multihop_dev',
    'dgm-yaml-native': 'dgm_native',
    'hyperagents-yaml-native': 'hyper_native_v2',
    'sica-yaml-live': 'sica_live',
    'sica-meta-live': 'sica_meta_live',
    'minja-qa': 'minja',
    'agentdojo-direct': 'dojo_live',
    'agentdojo-followup': 'dojo_followup',
    'agentbreeder-search-smoke': 'agentbreeder_live',
    'agentbreeder-population': 'agentbreeder_population',
    'seabench-monitor': 'seabench_monitor',
    'seabench-public-pairs': 'seabench_public_pairs',
    'mextra-synthetic': 'mextra',
    'misevolution-service': 'misevolution',
    'misevolution-online': 'misevolution_online',
    'misevolution-longitudinal': 'misevolution_longitudinal',
    'misevolution-tool-debug': 'misevolution_tool',
    'misevolution-workflow-debug': 'misevolution_workflow',
    'cimemories': 'cim_live',
}
def main():
    if sys.argv[1:2] in (['bootstrap'],['preformal'],['exposure'],['recipes']):
        module=importlib.import_module('.'+sys.argv[1],__package__)
        return module.main(sys.argv[2:])
    if sys.argv[1:2] == ['debug']:
        from .development import main as development_main
        return development_main(sys.argv[2:])
    if sys.argv[1:2] == ['evidence']:
        from .evidence_bundle import main as evidence_main
        return evidence_main(sys.argv[2:])
    if sys.argv[1:2] == ['compose']:
        from .composition import main as composition_main
        return composition_main(sys.argv[2:])
    if sys.argv[1:2] == ['deepseek-debug']:
        from .deepseek_debug import main as debug_main
        return debug_main(sys.argv[2:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['list','plan','run','status','component','doctor','contract','evolve-contract','deepseek-debug','compose','evidence','debug','bootstrap','preformal','exposure','recipes'])
    parser.add_argument('--profile',choices=PROFILES);parser.add_argument('--root',default='.')
    parser.add_argument('--job-id');args,extra=parser.parse_known_args();root=Path(args.root).resolve()
    if args.command in ('contract','evolve-contract'):
        # A packaged offline entry: no provider config, credentials or API calls.
        sys.argv=[sys.argv[0],*extra]
        importlib.import_module('.sica_contract' if args.command=='contract' else '.sica_evolution_contract',__package__).main();return
    if args.command in ('component','doctor'):
        sys.argv=[sys.argv[0],*extra]+(['--root',str(root)] if args.command=='component' else [])
        importlib.import_module('.components' if args.command=='component' else '.sandbox',__package__).main();return
    if args.command=='list':
        from .components import COMPONENTS
        catalog = root/'configs/catalog.json'
        if not catalog.is_file():
            catalog = Path(sysconfig.get_path('data'))/'share/flashtrustfulrsi/catalog.json'
        print(json.dumps({'profiles':PROFILES,'components':COMPONENTS,'catalog':json.loads(catalog.read_text())},
                         ensure_ascii=False,indent=2))
        return
    if args.command=='status':
        from .campaign_status import inventory
        rows=inventory(root)
        print(json.dumps(rows,ensure_ascii=False,indent=2));return
    if not args.profile or not args.job_id:parser.error('--profile and --job-id required')
    if args.command=='run':
        from .development import guard_owner_run
        extra=guard_owner_run(args.profile,extra)
    sys.argv=[sys.argv[0],'--root',str(root),'--job-id',args.job_id,*extra]+(['--plan-only'] if args.command=='plan' else [])
    importlib.import_module('.'+PROFILES[args.profile],__package__).main()
if __name__=='__main__':main()
