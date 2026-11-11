"""One official-only dispatch for owner development protocols (never formal jobs)."""
import argparse
import importlib
import json
from pathlib import Path
import sys

# Module-specific interfaces are retained, especially SICA's pair/arm identity.
ENTRIES = {
 'evopath': ('evopath_live', True),
 'flashrsi-searchqa': ('searchqa_deepseek_dev', False),
 'flashrsi-transfer': ('flashrsi_transfer', True),
 'flashrsi-multihop': ('t1_multihop_dev', False),
 'misevolution-fixed': ('misevolution', True),
 'misevolution-online': ('misevolution_online', True),
 'misevolution-memory': ('misevolution_longitudinal', True),
 'misevolution-tool': ('misevolution_tool', False),
 'misevolution-workflow': ('misevolution_workflow', False),
 'sica': ('sica_deepseek_twins', False),
 'dgm': ('dgm_native', True),
 'dgm-continue': ('dgm_deepseek_continue', False),
 'hyperagents': ('hyper_native_v2', True),
 'agentbreeder': ('agentbreeder_population', True),
 'trusting-trust': ('yaml_twins', True),
 'agentdojo': ('dojo_live', True),
 'agentdojo-followup': ('dojo_followup', True),
 'seabench-public': ('seabench_public_pairs', False),
 'seabench-monitor': ('seabench_monitor', True),
}
LEGACY_OWNER = {
 'flashrsi-adas':'compose run or debug run --profile flashrsi-searchqa --method adas',
 'sica-yaml-live':'debug run --profile sica',
 'sica-meta-live':'debug run --profile sica',
 'agentbreeder-search-smoke':'debug run --profile agentbreeder',
}
FLAGGED_OWNER = frozenset({'evopath-memory','agentdojo-direct','agentdojo-followup','flashrsi-transfer','misevolution-service',
 'misevolution-online','misevolution-longitudinal','dgm-yaml-native','hyperagents-yaml-native',
 'agentbreeder-population','trusting-trust-yaml-twins','seabench-monitor'})
FORBIDDEN = frozenset({'--spool','--provider','--model','--base-url','--api-key','--endpoint','--provider-mode'})


def official_arguments(arguments):
    arguments=list(arguments)
    bad={arg.split('=',1)[0] for arg in arguments}&FORBIDDEN
    if bad:raise ValueError('Official development has a fixed model/endpoint; unsupported overrides: '+', '.join(sorted(bad)))
    return arguments


def guard_owner_run(profile, arguments):
    """Protect public run dispatch without changing frozen legacy module sources."""
    if profile in LEGACY_OWNER:
        raise ValueError('Historical owner entry is not an official development runner; use '+LEGACY_OWNER[profile])
    if profile in FLAGGED_OWNER:
        arguments=official_arguments(arguments)
        if '--deepseek-debug' not in arguments:arguments.append('--deepseek-debug')
    return arguments


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('list','plan','run'))
    p.add_argument('--profile',choices=ENTRIES)
    p.add_argument('--root',default='.')
    args,extra=p.parse_known_args(argv)
    if args.action=='list':
        print(json.dumps({'scope':'development_only','model':'deepseek-flash','endpoint':'https://api.deepseek.com',
                          'profiles':{k:{'module':v[0],'official_flag_added':v[1]} for k,v in ENTRIES.items()}},indent=2));return
    if not args.profile:p.error('--profile required')
    extra=official_arguments(extra)
    module,flag=ENTRIES[args.profile]
    if flag and '--deepseek-debug' not in extra:extra.append('--deepseek-debug')
    if args.action=='plan' and '--plan-only' not in extra:extra.append('--plan-only')
    old=sys.argv
    try:
        sys.argv=[old[0],'--root',str(Path(args.root).resolve()),*extra]
        importlib.import_module('.'+module,__package__).main()
    finally:sys.argv=old
if __name__=='__main__':main()
