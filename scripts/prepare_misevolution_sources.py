"""Fetch only the pinned Tool/Workflow release, without unrelated weight-training data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
import time

root = Path(__file__).resolve().parents[1]
lock = json.loads((root/'configs/misevolution-source-subset.lock.json').read_text())
dest = root/'external/research/E02-api-subset'


def fetch(row):
    path = dest/row['path']
    def check(data):
        return (len(data)==row['size'] and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['sha'])
    if path.is_file() and check(path.read_bytes()): return
    request = urllib.request.Request(f'https://raw.githubusercontent.com/{lock["repo"]}/{lock["commit"]}/{row["path"]}',
                                     headers={'User-Agent':'FlashTrustfulRSI'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=20) as response:
                data = response.read(row['size']+1)
            break
        except (OSError,TimeoutError):
            if attempt==2:raise
            time.sleep(1+attempt)
    if not check(data): raise ValueError('Pinned source mismatch: '+row['path'])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)


with ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(fetch,lock['files']))
receipt = {'repo':lock['repo'],'commit':lock['commit'],'scope':'only_published_Tool_and_Workflow_subtrees',
           'files':len(lock['files']),'bytes':sum(row['size'] for row in lock['files']),
           'lock_sha256':hashlib.sha256((root/'configs/misevolution-source-subset.lock.json').read_bytes()).hexdigest()}
(dest/'source-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
