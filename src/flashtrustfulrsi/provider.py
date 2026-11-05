"""JSON artifact serialization; provider implementation lives in transport.py."""
import json, os
from pathlib import Path
from collections.abc import Mapping

def write_json(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda x:dict(x) if isinstance(x,Mapping) else (_ for _ in ()).throw(TypeError(type(x).__name__))))
    os.replace(tmp,path)
