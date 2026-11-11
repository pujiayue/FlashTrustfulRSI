"""Audited worker restart: retain protocol hashes, abort on transport failure."""
import argparse,importlib,json,sys,time
from pathlib import Path
from flashtrustfulrsi.recovery import fatal_container_transport,sha
from flashtrustfulrsi.provider import write_json

def main():
 p=argparse.ArgumentParser();p.add_argument('--module',choices=['searchqa_formal','dgm_native'],required=True)
 p.add_argument('--audit',type=Path,required=True);p.add_argument('--cache-key-review',type=Path);p.add_argument('--invocation-attempt-review',type=Path);a,extra=p.parse_known_args()
 if (a.audit/'worker-launch.json').exists():raise ValueError('Restart already launched; reconcile before another attempt')
 a.audit.mkdir(parents=True,exist_ok=True)
 write_json(a.audit/'worker-launch.json',{'module':a.module,'argv':extra,'adapter_sha256':sha(__file__),
  'recovery_sha256':sha(Path(__file__).parents[1]/'src/flashtrustfulrsi/recovery.py'),
  'change':'transport failures abort instead of becoming task-quality zeros; successful cached invocations retained',
  'cache_key_review_sha256':sha(a.cache_key_review) if a.cache_key_review else None,
  'cache_key_adapter_sha256':sha(Path(__file__).parents[1]/'src/flashtrustfulrsi/invocation_recovery.py') if a.cache_key_review else None,
  'invocation_attempt_review_sha256':sha(a.invocation_attempt_review) if a.invocation_attempt_review else None,
  'invocation_attempt_adapter_sha256':sha(Path(__file__).parents[1]/'src/flashtrustfulrsi/invocation_attempt.py') if a.invocation_attempt_review else None,'started':time.time()})
 if a.cache_key_review:
  from flashtrustfulrsi.invocation_recovery import install
  install(a.cache_key_review)
 if a.invocation_attempt_review:
  from flashtrustfulrsi.invocation_attempt import install
  install(a.invocation_attempt_review)
 fatal_container_transport();sys.argv=[sys.argv[0],*extra];importlib.import_module('flashtrustfulrsi.'+a.module).main()

if __name__=='__main__':main()
