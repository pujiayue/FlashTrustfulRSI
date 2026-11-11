"""Offline native SICA provider contract. Mock HTTP/tokenizer, zero API calls.

Does not run Agent.exec, the code editor, or the evolution algorithm. Tokenizer
is mocked because this non-streaming conversion never uses token estimation.
"""
import asyncio,json,socket,sys
from pathlib import Path
from unittest.mock import patch
import httpx,openai

async def check(source):
 sys.path.insert(0,str(source))
 from base_agent.src.llm import api
 from base_agent.src.llm.providers.openai import OpenAIProvider
 from base_agent.src.llm.base import Message
 from base_agent.src.types.llm_types import Model,Provider,TextContent,ToolCallContent,ToolResultContent
 from base_agent.src.config import settings
 from base_agent.agent import Agent
 assert settings.MODEL==settings.REASONING_MODEL==settings.OVERSIGHT_MODEL==Model.GPT_56_SOL
 calls=[];fail=False
 def send(req):
  body=json.loads(req.content);calls.append(body)
  assert body['model']=='gpt-5.6-sol' and body['max_completion_tokens']<=16384
  if fail:raise httpx.ConnectError('offline failure fixture',request=req)
  tool=len(calls)==1
  message={'role':'assistant','content':None if tool else 'OK'}
  if tool:message['tool_calls']=[{'id':'call_fixture','type':'function','function':{'name':'fixture_tool','arguments':'{"value": 1}'}}]
  return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':0,'model':'gpt-5.6-sol','choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if tool else 'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
 with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')),patch('tiktoken.encoding_for_model',return_value=None):
  async with openai.AsyncOpenAI(api_key='offline-fixture',http_client=httpx.AsyncClient(transport=httpx.MockTransport(send)),max_retries=0) as client:
   api.ModelProvider._providers={Provider.OPENAI:OpenAIProvider(client)}
   messages=[Message(role='user',content=[TextContent(text='fixture')])]
   first=await api.create_completion(messages)
   assert isinstance(first.content[0],ToolCallContent) and first.content[0].tool_args=={'value':1}
   messages.extend([Message(role='assistant',content=first.content),Message(role='user',content=[ToolResultContent(call_id='call_fixture',tool_name='fixture_tool',content='done')])])
   second=await api.create_completion(messages)
   assert second.content[0].text=='OK'
   assert any(m['role']=='tool' and m['tool_call_id']=='call_fixture' for m in calls[1]['messages'])
   for kwargs in ({'model':Model.SONNET_37},{'max_retries':2},{'max_tokens':16385}):
    before=len(calls)
    try:await api.create_completion(messages,**kwargs)
    except ValueError:pass
    else:raise AssertionError('Policy accepted invalid call')
    assert len(calls)==before
   fail=True;before=len(calls)
   try:await api.create_completion(messages)
   except Exception:pass
   else:raise AssertionError('Transport failure swallowed')
   assert len(calls)==before+1,'Unexpected native or SDK retry'
 print(json.dumps({'status':'passed','native_import':Agent.__name__,'mock_http_calls':len(calls),'real_api_calls':0,'checks':['native tool-call response conversion','native tool-result request conversion','all model roles fixed','invalid model/retry/token cap rejected before transport','single-attempt transport failure'],'limitations':['tokenizer mocked; non-streaming only','Agent.exec and original evolution not executed','internal native cost weights are not actual pricing']}))

if __name__=='__main__':asyncio.run(check(Path(sys.argv[1]).resolve()))
