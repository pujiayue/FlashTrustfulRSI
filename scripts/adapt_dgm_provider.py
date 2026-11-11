"""Apply auditable model/protocol fixes to a separate published DGM checkout.

Search, diagnosis prompts, parent selection and archive rules are not rewritten.
"""
import argparse,ast,difflib,hashlib,json,shutil
from pathlib import Path


def adapt(source,destination):
    if destination.exists():raise ValueError('Adapted destination already exists')
    shutil.copytree(source,destination);patch=[];changes=[]
    for name in ('llm.py','llm_withtools.py'):
        path=destination/name;before=path.read_text();after=before
        if name=='llm.py':
            after=after.replace('elif model.startswith("gpt-4o-"):', 'elif model.startswith("gpt-4o-") or model == "gpt-5.6-sol":')
        else:
            after=after.replace("CLAUDE_MODEL = 'bedrock/us.anthropic.claude-3-5-sonnet-20241022-v2:0'","CLAUDE_MODEL = 'gpt-5.6-sol'")
            after=after.replace("OPENAI_MODEL = 'o3-mini-2025-01-31'","OPENAI_MODEL = 'gpt-5.6-sol'")
            after=after.replace("model.startswith('o3-')", "(model.startswith('o3-') or model == 'gpt-5.6-sol')")
            after=after.replace('logging=None, max_retry=3','logging=None, max_retry=0')
            after=after.replace('parallel_tool_calls=False\n','parallel_tool_calls=False,\n                max_output_tokens=16384,\n')
            old='''        for tool_call in response.output:
            if tool_call.type == "function_call":
                break

        if tool_call:'''
            new='''        tool_call = next((item for item in response.output if item.type == "function_call"), None)

        if tool_call is not None:'''
            if after.count(old)!=1:raise ValueError('Native tool-result check changed')
            after=after.replace(old,new)
            after=after.replace('            new_msg_history.append(tool_call)\n','            new_msg_history.extend(response.output)\n')
            after=after.replace('    except Exception:\n        pass\n','    except Exception:\n        raise\n')
        ast.parse(after)
        if after==before:raise ValueError('No adaptation: '+name)
        path.write_text(after)
        patch.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
        changes.append({'path':name,'before':hashlib.sha256(before.encode()).hexdigest(),'after':hashlib.sha256(after.encode()).hexdigest()})
    return ''.join(patch),changes


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--patch',type=Path,required=True);a=p.parse_args();patch,changes=adapt(a.source,a.destination)
    a.patch.parent.mkdir(parents=True,exist_ok=True);a.patch.write_text(patch)
    a.patch.with_suffix('.json').write_text(json.dumps({'changes':changes,'deviations':[
        'all model roles use gpt-5.6-sol; original OpenAI Responses tool loop selected',
        'explicit 16384 output cap for tool calls; no inner recursive retries',
        'fix non-function final responses being incorrectly interpreted as tools',
        'retain complete Responses output items in wire history',
        'surface native tool-loop exceptions rather than silently swallowing failures']},indent=2))
    print(json.dumps({'patched':changes}))


if __name__=='__main__':main()
