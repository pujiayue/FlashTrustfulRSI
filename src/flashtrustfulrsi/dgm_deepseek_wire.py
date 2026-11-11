"""Translate the pinned DGM Responses subset for a DeepSeek Flash development run."""
import json

from .deepseek_debug import MODEL as DEBUG_MODEL
from .dgm_responses_wire import flatten_response_history
from .transport import MODEL


def chat_payload(body, output_cap):
    if (body.keys() != {'input','max_output_tokens','model','parallel_tool_calls','tool_choice','tools'}
            or body['tool_choice'] != 'auto' or body['parallel_tool_calls'] is not False
            or body['model'] != MODEL or type(body['max_output_tokens']) is not int
            or not 0 < body['max_output_tokens'] or not isinstance(body['input'],list)):
        raise ValueError('Unsupported native DGM Responses options')
    expanded, _ = flatten_response_history(body)
    messages = [{'role':'system','content':'Use at most one tool call per turn. Wait for its result before using another tool.'}]
    pending = None
    for item in expanded['input']:
        kind = item.get('type', 'message')
        if kind == 'reasoning':
            # The development Chat endpoint starts with thinking disabled and does not
            # accept OpenAI encrypted reasoning items. Never forward those fields.
            continue
        if kind == 'function_call_output':
            if pending is None or item['call_id'] != pending or not isinstance(item['output'],str):
                raise ValueError('Unpaired DGM tool output')
            messages.append({'role':'tool','tool_call_id':pending,'content':item['output']})
            pending = None
            continue
        if pending is not None:
            raise ValueError('DGM tool call must be followed by its output')
        if kind == 'function_call':
            pending = item['call_id']
            function = {'name':item['name'],'arguments':item['arguments']}
            messages.append({'role':'assistant','content':None,'tool_calls':[
                {'id':pending,'type':'function','function':function}]})
            continue
        role = item.get('role')
        parts = item.get('content')
        expected = {'user':'input_text','assistant':'output_text'}.get(role)
        if kind != 'message' or expected is None or not isinstance(parts,list) or not parts:
            raise ValueError('Unsupported DGM message type')
        if any(part.get('type') != expected or not isinstance(part.get('text'),str) for part in parts):
            raise ValueError('Unsupported DGM message content')
        messages.append({'role':role,'content':'\n'.join(part['text'] for part in parts)})
    if pending is not None:
        raise ValueError('Unresolved DGM tool call in completed history')
    tools = []
    for tool in body['tools']:
        if tool.get('type') != 'function' or tool.keys() - {'type','name','description','parameters','strict'}:
            raise ValueError('Unsupported DGM tool schema')
        tools.append({'type':'function','function':{key:tool[key] for key in ('name','description','parameters')}})
    return {'model':DEBUG_MODEL,'messages':messages,'max_tokens':min(body['max_output_tokens'],output_cap),
            'tool_choice':'auto','tools':tools}


def response_envelope(chat, *, parallel=False):
    choice = chat['choices'][0]
    message = choice['message']
    text = message.get('content')
    tools = message.get('tool_calls') or []
    reason = choice['finish_reason']
    if (len(chat['choices']) != 1 or reason not in ('stop','tool_calls')
            or (reason == 'tool_calls') != bool(tools) or (not tools and not text)):
        raise ValueError('DGM requires one completed model turn')
    output = []
    if text:
        output.append({'id':'msg_'+chat['id'],'type':'message','role':'assistant','status':'completed',
                       'content':[{'type':'output_text','text':text,'annotations':[]}]})
    for tool in (tools if parallel else tools[:1]):
        # Match the native one-tool dispatch policy. The raw provider ledger
        # retains all emitted calls; any later tool must be proposed again.
        if tool['type'] != 'function' or not isinstance(json.loads(tool['function']['arguments']),dict):
            raise ValueError('DGM accepts only function tool calls')
        output.append({'id':'fc_'+tool['id'],'type':'function_call','call_id':tool['id'],
                       'name':tool['function']['name'],'arguments':tool['function']['arguments'],'status':'completed'})
    envelope = {'id':chat['id'],'object':'response','status':'completed','created_at':chat['created'],
                'model':chat['model'],'output':output,'parallel_tool_calls':parallel,'tool_choice':'auto','tools':[]}
    usage = chat.get('usage')
    if isinstance(usage,dict) and all(type(usage.get(key)) is int for key in ('prompt_tokens','completion_tokens')):
        envelope['usage'] = {'input_tokens':usage['prompt_tokens'],'output_tokens':usage['completion_tokens'],
                             'total_tokens':usage['prompt_tokens'] + usage['completion_tokens']}
    return envelope


class DgmFlashTransport:
    def __init__(self, provider):
        self.provider = provider

    def invoke(self, protocol, body, request_id=None):
        cap = self.provider.budget.per_call_output
        if protocol == 'responses':
            payload = chat_payload(body,cap)
            return response_envelope(self.provider.invoke('chat',payload,request_id=request_id))
        if protocol != 'chat' or body.get('model') != MODEL or type(body.get('max_tokens')) is not int:
            raise ValueError('Unsupported DGM diagnosis transport')
        payload = {**body,'model':DEBUG_MODEL,'max_tokens':min(body['max_tokens'],cap)}
        return self.provider.invoke('chat',payload,request_id=request_id)
