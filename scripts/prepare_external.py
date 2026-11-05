"""Fetch selected pinned external research sources; never import their top level."""
import hashlib,json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
items=[('Misevolution','ShaoShuai0605/Misevolution','2601397de8b3de809d920555f05675d0a40a5979',['memory_misevolution/reward_hacking_test.py']),('MINJA','dsh3n77/MINJA','a3ec8da0f7740b3fe629f6706e1c14296ee6861d',
        ['LICENSE','QA/main.py','QA/utils.py','QA/initial_demo.txt','QA/victim.json','QA/data/README.txt','QA/data/test/high_school_chemistry_test.csv','QA/data/test/nutrition_test.csv']),
       ('MEXTRA','wangbo9719/MEXTRA','d1e071f27b8328e0b57458afa17f78c1b09bcda6',
        ['EHRAgent/attacking/attacking_prompt_generation.py','EHRAgent/attacking/evaluation.py','RAP/attacking/attacking_prompt_generation.py'])]
for name,repo,commit,paths in items:
    dest=root/'external'/name;files={}
    for relative in paths:
        path=dest/relative;path.parent.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(f'https://raw.githubusercontent.com/{repo}/{commit}/{relative}',timeout=120) as response:data=response.read()
        path.write_bytes(data);files[relative]=hashlib.sha256(data).hexdigest()
    (dest/'source-manifest.json').write_text(json.dumps({'repo':repo,'commit':commit,'files':files},indent=2))
    print(name,commit,len(paths),'files; external license/data terms apply')
