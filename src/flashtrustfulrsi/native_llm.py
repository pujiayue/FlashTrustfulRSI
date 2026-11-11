"""Keep FlashRSI's native text Worker; replace only its HTTP transport."""
import asyncio,json,types


def bind_llm(agent,provider,role='text-worker'):
    import httpx,openai
    native_run=type(agent)._run;sequence=0;active=False
    async def execute(instance,prompt,working_dir=None):
        nonlocal sequence,active
        if active:raise RuntimeError('Native text adapter requires serial worker calls')
        active=True;sequence+=1;identity=f'{role}-{sequence:08d}'
        NativeClient=openai.AsyncOpenAI
        async def handler(request):
            if request.method!='POST' or request.url.path!='/v1/chat/completions':raise ValueError('Undeclared native text route')
            body=json.loads(request.content)
            response=await asyncio.to_thread(provider.invoke,'chat',body,request_id=identity)
            return httpx.Response(200,json=response,request=request)
        class Client(NativeClient):
            def __init__(self,*args,**kwargs):
                kwargs['max_retries']=0;kwargs['http_client']=httpx.AsyncClient(transport=httpx.MockTransport(handler),timeout=900)
                super().__init__(*args,**kwargs)
        openai.AsyncOpenAI=Client
        try:return await native_run(instance,prompt,working_dir)
        finally:openai.AsyncOpenAI=NativeClient;active=False
    agent._run=types.MethodType(execute,agent);return agent
