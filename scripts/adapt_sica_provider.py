"""Create a reviewed single-model SICA fork; original algorithms stay external."""
import argparse,ast,difflib,hashlib,json,re,shutil
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def build(root):
 source=root/'external/original-agents/sica';dest=source.with_name('sica-sol')
 receipt=json.loads((source.parent/'sica-source.json').read_text())
 if dest.exists():raise ValueError('Refusing to overwrite adapted source')
 edits={}
 def edit(rel,fn):
  p=source/rel
  if sha(p)!=receipt['files_sha256'][rel]:raise ValueError('Source drift: '+rel)
  edits[rel]=fn(p.read_text())
 prefix='base_agent/src/'
 for rel in ['config.py','agents/base_agent.py','agents/assistant_base_agent.py','types/agent_types.py','agents/implementations/review_committee_member.py','oversight/overseer.py','schemas/json_parsing.py']:
  edit(prefix+rel,lambda s:re.sub(r'Model\.(SONNET_35|SONNET_37|O3_MINI)\b','Model.GPT_56_SOL',s))
 def types(s):
  position=s.index('    HAIKU_35 = ModelInfo(')
  s=s[:position]+'''    # Adapter limits, not a claim about provider context or pricing.
    # Zero internal cost weights are placeholders; actual accounting is external.
    GPT_56_SOL = ModelInfo(
        api_name="gpt-5.6-sol", provider=Provider.OPENAI,
        costs=TokenCost(0.0, 0.0, 0.0, 0.0), max_tokens=16384,
        context_window=128000, function_calling_interface=FCI.CONSTRAINED,
        preferred_arg_format=ArgFormat.JSON,
    )

'''+s[position:]
  start=s.index('MODEL_FAILOVER_MAP = {');s=s[:start]+'MODEL_FAILOVER_MAP = {}  # No cross-model fallback in this profile.\n'
  return s
 edit(prefix+'types/llm_types.py',types)
 edit(prefix+'llm/providers/__init__.py',lambda s:s[:s.index('from .')]+ 'from .openai import OpenAIProvider\n')
 def api(s):
  s=s.replace('from google import genai\n','')
  s=re.sub(r'from \.providers import \([\s\S]*?\)\n','from .providers import OpenAIProvider\n',s,count=1)
  start=s.index('    @classmethod\n    def _get_anthropic_client');end=s.index('\n\ndef get_tool_documentation',start)
  s=s[:start]+'''    @classmethod
    def get_provider(cls, provider: Provider):
        if provider != Provider.OPENAI:
            raise ValueError("Only the approved OpenAI-compatible provider is available")
        if provider not in cls._providers:
            client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0)
            cls._providers[provider] = OpenAIProvider(client)
        return cls._providers[provider]
'''+s[end:]
  s=s.replace('Model.SONNET_37','Model.GPT_56_SOL').replace('max_retries: int = 3','max_retries: int = 1')
  s=s.replace('    async def try_completion(current_model: Model) -> Completion:', '''    if model != Model.GPT_56_SOL or max_retries != 1:
        raise ValueError("Only gpt-5.6-sol and one attempt are permitted")
    if max_tokens is not None and not 1 <= max_tokens <= 16384:
        raise ValueError("Output limit exceeds adapter policy")

    async def try_completion(current_model: Model) -> Completion:''')
  s=s.replace('    try:\n        provider = ModelProvider.get_provider(model.provider)', '''    if model != Model.GPT_56_SOL:
        raise ValueError("Unapproved model")
    if max_tokens is not None and not 1 <= max_tokens <= 16384:
        raise ValueError("Output limit exceeds adapter policy")
    try:
        provider = ModelProvider.get_provider(model.provider)''')
  return s
 edit(prefix+'llm/api.py',api)
 for rel,text in edits.items():ast.parse(text,filename=rel)
 shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 patch=[]
 for rel,text in sorted(edits.items()):
  (dest/rel).write_text(text)
  patch.extend(difflib.unified_diff((source/rel).read_text().splitlines(True),text.splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
 target=root/'patches/sica-sol-provider.patch';target.parent.mkdir(exist_ok=True);target.write_text(''.join(patch))
 metadata={'upstream_commit':receipt['commit'],'published_patch_sha256':receipt['patch_sha256'],'patch_sha256':sha(target),
  'model':'gpt-5.6-sol','modified_files':sorted(edits),'internal_cost_weights':'placeholder_zero_not_measured_money; native cost monitoring must be disabled',
  'accounting':'external recorded transport usage; no price claim','unchanged':['native Agent.exec','orchestrator and overseer algorithms','tool execution and native OpenAI conversion','meta-improvement reasoning structure','archive parent selection'],
  'scope':'provider integration only; no original algorithm execution or scientific result claimed'}
 target.with_suffix('.json').write_text(json.dumps(metadata,indent=2));return metadata

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();print(json.dumps(build(a.root.resolve())))
