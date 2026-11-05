"""Fetch hash-verified native components without installing benchmark runtimes."""
import hashlib,json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for method,spec in json.loads((root/'configs/component-sources.lock.json').read_text()).items():
    for relative,expected in spec['files'].items():
        target=root/'external'/method/relative
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:continue
        url=f"https://raw.githubusercontent.com/{spec['repo']}/{spec['commit']}/{relative}"
        with urllib.request.urlopen(url,timeout=90) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Source hash mismatch: '+relative)
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    print(method,spec['commit'],len(spec['files']),'verified files')
