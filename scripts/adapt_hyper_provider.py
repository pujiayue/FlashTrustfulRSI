"""Versioned single-file provider patch; retain native HyperAgents tool loops."""
import argparse,difflib,hashlib,json,shutil
from pathlib import Path

PROVIDER='''"""Declared single-model transport adaptation for FlashTrustfulRSI."""
import openai
MAX_TOKENS = 16384
MODEL = "gpt-5.6-sol"
CLAUDE_MODEL = CLAUDE_HAIKU_MODEL = CLAUDE_35NEW_MODEL = OPENAI_MODEL = MODEL
OPENAI_MINI_MODEL = OPENAI_O3_MODEL = OPENAI_O3MINI_MODEL = OPENAI_O4MINI_MODEL = MODEL
OPENAI_GPT52_MODEL = OPENAI_GPT5_MODEL = OPENAI_GPT5MINI_MODEL = MODEL
GEMINI_3_MODEL = GEMINI_MODEL = GEMINI_FLASH_MODEL = MODEL

def get_response_from_llm(msg,model=MODEL,temperature=0.0,max_tokens=MAX_TOKENS,msg_history=None):
    if model != MODEL: raise ValueError("Unapproved model")
    if not 1 <= max_tokens <= MAX_TOKENS: raise ValueError("Output cap exceeds frozen policy")
    history = [dict(m) for m in (msg_history or [])]
    history = [{**m,"content":m.pop("text")} if "text" in m else m for m in history]
    history += [{"role":"user","content":msg}]
    with openai.OpenAI(max_retries=0) as client:
        response=client.chat.completions.create(model=model,messages=history,temperature=temperature,max_completion_tokens=max_tokens)
    text=response.choices[0].message.content
    if response.choices[0].finish_reason != "stop" or not isinstance(text,str):
        raise ValueError("Incomplete HyperAgents completion")
    history.append({"role":"assistant","content":text})
    history=[{**m,"text":m.pop("content")} if "content" in m else m for m in history]
    return text,history,{}
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.root.resolve()
    source=root/'external/original-agents/hyperagents';dest=root/'external/original-agents/hyperagents-sol'
    if dest.exists():raise ValueError('Adapted source already exists; do not overwrite')
    receipt=json.loads((source.parent/'hyperagents-source.json').read_text());before=(source/'agent/llm.py').read_text()
    assert hashlib.sha256((source/'agent/llm.py').read_bytes()).hexdigest()==receipt['files_sha256']['agent/llm.py']
    shutil.copytree(source,dest);(dest/'agent/llm.py').write_text(PROVIDER)
    patch=root/'patches/hyperagents-sol-provider.patch';patch.parent.mkdir(exist_ok=True)
    patch.write_text(''.join(difflib.unified_diff(before.splitlines(True),PROVIDER.splitlines(True),fromfile='a/agent/llm.py',tofile='b/agent/llm.py')))
    metadata={'upstream_commit':receipt['commit'],'published_patch_sha256':receipt['patch_sha256'],
        'modified_files':['agent/llm.py'],'provider_patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
        'model':'gpt-5.6-sol','changes':['all roles use sole authorized model','OpenAI SDK transport instead of LiteLLM; native text-history conversion retained',
            'no network retries or silent parameter dropping','16384 output cap; incomplete completions fail explicitly'],
        'unchanged':['TaskAgent.forward','MetaAgent.forward','chat_with_agent','JSON tool-call parser','bash/edit tools','native 40 tool-call limit']}
    patch.with_suffix('.json').write_text(json.dumps(metadata,indent=2));print(json.dumps(metadata))

if __name__=='__main__':main()
