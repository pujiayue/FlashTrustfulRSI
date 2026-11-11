"""Container bootstrap for the original SICA Agent.exec and native tools."""
import asyncio,contextlib,json,os,shutil,sys
from pathlib import Path

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
 shutil.copytree('/workspace/agent','/tmp/agent');os.chdir('/tmp/agent');sys.path.insert(0,'/tmp/agent');sys.path.insert(0,'/workspace')
 from native_entry import export_files
 with contextlib.redirect_stdout(sys.stderr):
  from base_agent.agent import Agent
  agent=Agent(workdir=Path('/tmp/agent'),logdir=Path('/tmp/native'),server_enabled=False)
  metrics=await agent.exec(request['prompt'],timeout=request['timeout'],cost_threshold=None)
  await agent.create_report()
 answer=Path('/tmp/native/answer.txt')
 print(json.dumps({'algorithm':'SICA','original_Agent_exec':True,'answer':answer.read_text() if answer.exists() else None,
  'files':export_files(Path('/tmp/agent')),'logs':export_files(Path('/tmp/native')),
  'duration_seconds':metrics[3],'native_token_counter':metrics[0],'monetary_cost':None,
  'accounting':'native cost placeholders not used; actual usage belongs to outer provider ledger'},ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
