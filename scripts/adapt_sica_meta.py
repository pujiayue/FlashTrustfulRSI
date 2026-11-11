"""Extend the provider-only fork to the previously unexercised review roles."""
import argparse,hashlib,json,shutil,difflib,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True);a=p.parse_args()
if a.output.exists():raise ValueError('Output exists')
rel=Path('base_agent/src/tools/committee_design.py');old=(a.source/rel).read_text();new=re.sub(r'Model\.(SONNET_35|SONNET_37)\b','Model.GPT_56_SOL',old)
assert old!=new
shutil.copytree(a.source,a.output);(a.output/rel).write_text(new)
patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+str(rel),tofile='b/'+str(rel)))
a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.with_suffix('.patch').write_text(patch)
a.receipt.write_text(json.dumps({'source_sha256':hashlib.sha256(old.encode()).hexdigest(),'adapted_sha256':hashlib.sha256(new.encode()).hexdigest(),'modified_files':[str(rel)],'change':'all native review-committee model roles use gpt-5.6-sol; no algorithm changes'},indent=2))
