"""Official DeepSeek Messages transport for bounded native Claude Code debugging."""

import json
import re
from pathlib import Path
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

from .deepseek_debug import BASE_URL, MODEL, DeepSeekProvider
from .transport import BudgetExceeded
from .provider import write_json


MESSAGES_URL = BASE_URL + '/anthropic/v1/messages'


class DeepSeekAnthropicProvider(DeepSeekProvider):
    endpoint = MESSAGES_URL

    def invoke(self, protocol, body, request_id=None):
        if protocol!='anthropic' or body.get('model')!=MODEL or body.get('stream',False) is not False:
            raise ValueError('Expected non-streaming official deepseek-flash Messages')
        if not isinstance(request_id,str) or not request_id.strip() or len(request_id)>240:
            raise ValueError('A stable request ID is required')
        if type(body.get('max_tokens')) is not int or not 1<=body['max_tokens']<=self.budget.per_call_output:
            raise BudgetExceeded('Messages output cap exceeds the development budget')
        if (not isinstance(body.get('messages'),list) or not body['messages']
                or any(row.get('role') not in ('user','assistant') for row in body['messages'])
                or any(tool.get('type') for tool in body.get('tools',[]))
                or body.get('thinking',{'type':'disabled'})!={'type':'disabled'}):
            raise ValueError('Unsupported native Messages content or server tool')
        return self._invoke_body(body,request_id)

    def _usage(self,response):
        usage = response.get('usage',{}) if isinstance(response,dict) else {}
        values = [usage.get('input_tokens'),usage.get('output_tokens'),usage.get('cache_read_input_tokens',0),
                  usage.get('cache_creation_input_tokens',0)]
        if any(type(value) is not int or value<0 for value in values):
            return None,None
        return values[0]+values[2]+values[3],values[1]

    def _validate_tools(self,body,response):
        allowed={tool['name'] for tool in body.get('tools',[])}
        for block in response['content']:
            if block['type']=='tool_use' and block['name'] not in allowed:
                raise ValueError('Provider returned an undeclared Messages tool')

    def _validate(self,response):
        if (not isinstance(response,dict) or response.get('model')!=MODEL or response.get('type')!='message'
                or response.get('role')!='assistant' or not isinstance(response.get('id'),str)
                or response.get('stop_reason') not in ('end_turn','tool_use','stop_sequence')
                or not isinstance(response.get('content'),list) or not response['content']):
            raise ValueError('Incomplete or unexpected official Messages response')
        for block in response['content']:
            if block.get('type')=='text' and isinstance(block.get('text'),str):
                continue
            if (block.get('type')=='tool_use' and isinstance(block.get('id'),str)
                    and isinstance(block.get('name'),str) and isinstance(block.get('input'),dict)):
                continue
            raise ValueError('Unsupported Messages content block')


def stream_message(message):
    """Expose an archived non-streaming vendor message as native SSE events."""
    header = {**message,'content':[],'stop_reason':None,'stop_sequence':None}
    header['usage'] = {**message.get('usage',{}),'output_tokens':0}
    events = [('message_start',{'type':'message_start','message':header})]
    for index, block in enumerate(message['content']):
        text = block['type']=='text'
        initial = {**block,'text':''} if text else {**block,'input':{}}
        delta = {'type':'text_delta','text':block['text']} if text else {
            'type':'input_json_delta','partial_json':json.dumps(block['input'],ensure_ascii=False)}
        events.extend([('content_block_start',{'type':'content_block_start','index':index,'content_block':initial}),
                       ('content_block_delta',{'type':'content_block_delta','index':index,'delta':delta}),
                       ('content_block_stop',{'type':'content_block_stop','index':index})])
    events.extend([('message_delta',{'type':'message_delta','delta':{
        'stop_reason':message['stop_reason'],'stop_sequence':message.get('stop_sequence')},'usage':message['usage']}),
        ('message_stop',{'type':'message_stop'})])
    return ''.join(f'event: {name}\ndata: {json.dumps(value,ensure_ascii=False)}\n\n' for name,value in events).encode()


def normalize_native_messages(body):
    """Move initial native environment system rows to the vendor system field.

    The pinned SDK CLI emits initial environment context after its first user
    row. A later system intervention after an assistant reply is not supported.
    Original and normalized bodies are retained as distinct artifacts.
    """
    body = dict(body)
    system = body.get('system', [])
    system = [{'type':'text','text':system}] if isinstance(system,str) else list(system)
    messages = []
    seen_assistant = False
    for row in body.get('messages',[]):
        if row.get('role')=='system':
            content = row['content']
            known_control = isinstance(content,str) and (content.startswith(
                '# Environment\nYou have been invoked in the following environment:') or
                re.fullmatch(r'<total_tokens>\d+ tokens left</total_tokens>',content))
            if seen_assistant and not known_control:
                raise ValueError('A late native system intervention requires a separate adapter')
            blocks = [{'type':'text','text':content}] if isinstance(content,str) else content
            if not isinstance(blocks,list) or any(block.get('type')!='text' for block in blocks):
                raise ValueError('Unsupported initial native system content')
            system.extend(blocks)
        else:
            seen_assistant |= row.get('role')=='assistant'
            messages.append(row)
    return {**body,'system':system,'messages':messages}


class AnthropicSocket:
    """Private inference-only socket. Authentication stays in the host process."""

    def __init__(self,path,provider,prefix):
        self.path = Path(path)
        self.provider = provider
        self.prefix = prefix
        self.calls = 0

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass

            def do_POST(self):
                owner.calls += 1
                trace = owner.provider.db.parent/f'{owner.prefix}-native-{owner.calls:04d}.json'
                received = {'path': self.path}
                try:
                    if self.path.split('?',1)[0]!='/v1/messages':
                        raise ValueError('Only native Messages inference is exposed')
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=2*1024*1024:
                        raise ValueError('Invalid Messages payload size')
                    body = json.loads(self.rfile.read(size))
                    received['body'] = body
                    write_json(trace, received)
                    body = normalize_native_messages(body)
                    if body.get('model')!=MODEL:
                        raise ValueError('The native harness must request deepseek-flash explicitly')
                    streaming = body.get('stream',False)
                    if type(streaming) is not bool:
                        raise ValueError('Invalid stream flag')
                    body.update(stream=False,thinking={'type':'disabled'},
                                max_tokens=min(body.get('max_tokens',4096),owner.provider.budget.per_call_output))
                    reply = owner.provider.invoke('anthropic',body,request_id=f'{owner.prefix}-{owner.calls:04d}')
                    data = stream_message(reply) if streaming else json.dumps(reply).encode()
                    content_type = 'text/event-stream' if streaming else 'application/json'
                    self.send_response(200)
                except Exception as exc:
                    write_json(trace, {**received, 'error_type': type(exc).__name__, 'error': str(exc)[:1000]})
                    content_type = 'application/json'
                    data = json.dumps({'type':'error','error':{'type':'api_error','message':type(exc).__name__}}).encode()
                    self.send_response(400)
                self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = socketserver.UnixStreamServer(str(self.path),Handler)
        self.path.chmod(0o666)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self

    def __exit__(self,*args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.path.unlink()
