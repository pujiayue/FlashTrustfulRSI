"""Container bootstrap for the original SICA Agent.exec and native tools."""
import asyncio,base64,contextlib,json,os,shutil,sys,types,importlib.machinery
from contextvars import ContextVar
from hashlib import sha256
from io import BytesIO
import tarfile
from pathlib import Path

def apply_committee_wall_clock(seconds):
 from base_agent.src.agents.implementations.main_orchestrator import MainOrchestratorAgent
 native=sys.modules[MainOrchestratorAgent._handle_tool_call.__module__]
 if type(seconds) is not int or not 600 <= seconds <= 1800:
  raise ValueError('Invalid committee wall clock')
 active=ContextVar('sica_committee_wall_clock',default=False)
 native_asyncio=native.asyncio
 class LocalAsyncio:
  def __getattr__(self,name):
   return getattr(native_asyncio,name)
  def timeout(self,seconds_original):
   return native_asyncio.timeout(seconds if active.get() and seconds_original==600 else seconds_original)
 native.asyncio=LocalAsyncio()
 original=native.BaseAgent._handle_tool_call
 async def handle(self,tool):
  marker=active.set(tool.tool_name=='meta_improvement_review_committee')
  try:
   return await original(self,tool)
  finally:
   active.reset(marker)
 native.BaseAgent._handle_tool_call=handle


def export_native_logs(root):
 raw=BytesIO()
 events={}
 total=0
 count=0
 with tarfile.open(fileobj=raw,mode='w:gz',compresslevel=6) as archive:
  for path in sorted(root.rglob('*')):
   if path.is_symlink():raise ValueError('Unexpected native log symlink')
   if not path.is_file():continue
   data=path.read_bytes()
   total+=len(data);count+=1
   if total>24*1024*1024 or count>1000:raise ValueError('Native log archive exceeds raw limits')
   name=str(path.relative_to(root))
   item=tarfile.TarInfo(name)
   item.size=len(data)
   archive.addfile(item,BytesIO(data))
   if name in ('meta-events.json','meta-prompt.txt'):
    events[name]={'data':base64.b64encode(data).decode(),'mode':0o644}
 compressed=raw.getvalue()
 if len(compressed)>6*1024*1024:raise ValueError('Native log archive exceeds compressed limit')
 return events,{'format':'tar.gz','sha256':sha256(compressed).hexdigest(),'file_count':count,
                'data':base64.b64encode(compressed).decode()}


async def main():
 import httpx,openai
 native=openai.AsyncOpenAI
 class Client(native):
  def __init__(self,*args,**kwargs):
   kwargs.update(api_key='no-secret',base_url='http://model-gateway/v1',max_retries=0,
    http_client=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds='/model.sock'),timeout=600))
   super().__init__(*args,**kwargs)
 openai.AsyncOpenAI=Client
 os.environ.update(OPENAI_API_KEY='no-secret',TIKTOKEN_CACHE_DIR='/opt/tiktoken',OPENBLAS_NUM_THREADS='1')
 request=json.loads(Path('/workspace/request.json').read_text())
 meta=request.get('mode')=='meta'
 if meta:
  candidate=Path('/home/agent/workdir')
  shutil.copytree('/workspace/agent/base_agent',candidate,dirs_exist_ok=True)
  os.chdir('/home/agent')
 else:
  candidate=Path('/tmp/agent')
  shutil.copytree('/workspace/agent',candidate)
  os.chdir(candidate)
 sys.path[:0]=[str(Path.cwd()),'/workspace']
 from native_entry import export_files
 with contextlib.redirect_stdout(sys.stderr):
  if meta:
   # Original runner executes the best archived actor while editing the
   # separately selected parent. Keep these two identities distinct.
   actor_path=Path('/home/agent/archive')/f"agent_{request['best_iter']}"/'agent_code'
   package=types.ModuleType('base_agent');package.__path__=[str(actor_path)];package.__package__='base_agent'
   package.__spec__=importlib.machinery.ModuleSpec('base_agent',loader=None,is_package=True)
   package.__spec__.submodule_search_locations=[str(actor_path)];sys.modules['base_agent']=package
  from base_agent.agent import Agent
  if meta:
   import base_agent.__main__ as upstream
   if 'committee_timeout_seconds' in request:
    apply_committee_wall_clock(request['committee_timeout_seconds'])
   from base_agent.src.events import EventBus
   from base_agent.src.types.event_types import EventType
   events=[]
   async def record_event(event):
    row=event.metadata.get('tool_result')
    events.append({'name':event.metadata.get('name'),'result':row.model_dump(mode='json') if row is not None else None})
   bus=await EventBus.get_instance();bus.subscribe({EventType.TOOL_RESULT},record_event)
   class BoundAgent(Agent):
    def __init__(self,**kw):super().__init__(**kw,server_enabled=False)
    async def exec(self,prompt,*args,**kw):
     Path('/tmp/native/meta-prompt.txt').write_text(prompt)
     return await super().exec(prompt,timeout=request['timeout'],cost_threshold=None)
   upstream.Agent=BoundAgent
   await upstream.improve_agent(candidate,Path('/tmp/native'),request['best_iter'],request['current_iter'])
   Path('/tmp/native/meta-events.json').write_text(json.dumps(events))
   metrics=(None,None,None,None)
  else:
   agent=Agent(workdir=candidate,logdir=Path('/tmp/native'),server_enabled=False)
   metrics=await agent.exec(request['prompt'],timeout=request['timeout'],cost_threshold=None)
   await agent.create_report()
 answer=Path('/tmp/native/answer.txt')
 logs,bundle=export_native_logs(Path('/tmp/native'))
 exported=export_files(candidate)
 if meta:exported={'base_agent/'+name:value for name,value in exported.items()}
 print(json.dumps({'algorithm':'SICA','original_Agent_exec':True,'answer':answer.read_text() if answer.exists() else None,
  'files':exported,'logs':logs,'native_log_archive':bundle,
  'duration_seconds':metrics[3],'native_token_counter':metrics[0],'monetary_cost':None,
  'accounting':'native cost placeholders not used; actual usage belongs to outer provider ledger'},ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
