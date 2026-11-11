"""Separate, declared bare-JSON compatibility patch; original results preserved."""
import argparse,difflib,hashlib,json,shutil
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.root.resolve()
 source=root/'external/original-agents/hyperagents-sol';dest=source.with_name('hyperagents-sol-compat')
 if dest.exists():raise ValueError('Compatibility source already exists')
 shutil.copytree(source,dest);patches=[];changes={}
 specs=[('utils/common.py','    return extracted_jsons if extracted_jsons else None',
 '''    if not extracted_jsons:
        try:
            value = json.loads(response.strip())
            if isinstance(value, dict): extracted_jsons.append(value)
        except json.JSONDecodeError:
            pass
    return extracted_jsons if extracted_jsons else None'''),
 ('agent/llm_withtools.py','    return tool_uses if tool_uses else None',
 '''    if not tool_uses:
        try:
            value = json.loads(response.strip())
            if isinstance(value, dict) and 'tool_name' in value and 'tool_input' in value:
                tool_uses.append(value)
        except json.JSONDecodeError:
            pass
    return tool_uses if tool_uses else None''')]
 for name,old,new in specs:
  path=dest/name;before=path.read_text();assert before.count(old)==1;after=before.replace(old,new);path.write_text(after)
  changes[name]={'before':hashlib.sha256(before.encode()).hexdigest(),'after':hashlib.sha256(after.encode()).hexdigest()}
  patches+=difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name)
 patch=root/'patches/hyperagents-json-compat.patch';patch.write_text(''.join(patches))
 patch.with_suffix('.json').write_text(json.dumps({'upstream_commit':'59a68f672dfb92c74aeb7e61535d776fb36e172d','changes':changes,
  'reason':'Original parser rejects valid bare JSON returned by requested model. Separate compatibility condition, not unchanged original protocol.',
  'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'no_resampling':True,'no_generated_code_execution_in_parser':True},indent=2))
 print(json.dumps({'changed_files':list(changes),'source':str(dest)}))

if __name__=='__main__':main()
