import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flashtrustfulrsi.deepseek_anthropic import DeepSeekAnthropicProvider, MESSAGES_URL, stream_message, normalize_native_messages
from flashtrustfulrsi.deepseek_debug import DebugBudget, DeepSeekProvider, MODEL
from flashtrustfulrsi.transport import ReplayConflict, TransportError


class AnthropicTransportTests(unittest.TestCase):
    def test_native_system_control_normalization(self):
        native={'system':'instructions','messages':[{'role':'user','content':'question'},
            {'role':'assistant','content':'tool response'},
            {'role':'system','content':'<total_tokens>123 tokens left</total_tokens>'}]}
        normalized=normalize_native_messages(native)
        self.assertEqual(len(normalized['messages']),2)
        self.assertEqual(normalized['system'][-1]['text'],native['messages'][-1]['content'])
        self.assertEqual(len(native['messages']),3)
        with self.assertRaises(ValueError):
            normalize_native_messages({**native,'messages':native['messages'][:2]+[
                {'role':'system','content':'new unrecognized intervention'}]})
    def test_endpoint_replay_usage_and_model_boundary(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-credential'}):
            provider = DeepSeekAnthropicProvider(Path(tmp)/'run','test')
            body = {'model':MODEL,'max_tokens':128,'messages':[{'role':'user','content':'test'}]}
            response = {'id':'message-test','type':'message','role':'assistant','model':MODEL,
                        'content':[{'type':'text','text':'ok'}],'stop_reason':'end_turn',
                        'usage':{'input_tokens':10,'output_tokens':2,'cache_read_input_tokens':4}}
            with patch.object(provider,'_send',return_value=response) as send:
                self.assertEqual(provider.invoke('anthropic',body,'one'),response)
                self.assertEqual(provider.invoke('anthropic',body,'one'),response)
                self.assertEqual(send.call_count,1)
                with self.assertRaises(ValueError):
                    provider.invoke('anthropic',{**body,'model':'claude-sonnet'},'two')
                with self.assertRaises(ReplayConflict):
                    provider.invoke('anthropic',{**body,'max_tokens':64},'one')
            self.assertEqual(provider.summary()['endpoint'],MESSAGES_URL)
            self.assertEqual(provider.summary()['reported_input'],14)
            with self.assertRaises(ReplayConflict):
                DeepSeekProvider(Path(tmp)/'run','test')
            with patch.object(provider,'_send',return_value={**response,'stop_reason':'max_tokens'}):
                with self.assertRaises(TransportError):
                    provider.invoke('anthropic',body,'two')
            self.assertEqual(provider.summary()['calls'][-1]['state'],'invalid_response')

    def test_sse_preserves_tool_arguments_and_final_usage(self):
        message = {'id':'one','type':'message','role':'assistant','model':MODEL,'stop_reason':'tool_use',
                   'content':[{'type':'text','text':'check'},
                              {'type':'tool_use','id':'tool-one','name':'Read','input':{'file_path':'facts.txt'}}],
                   'usage':{'input_tokens':8,'output_tokens':4}}
        frames = stream_message(message).decode().strip().split('\n\n')
        events = [json.loads(frame.split('\ndata: ',1)[1]) for frame in frames]
        self.assertEqual(events[0]['message']['usage']['output_tokens'],0)
        self.assertEqual(events[-2]['usage'],message['usage'])
        delta = next(event for event in events if event.get('delta',{}).get('type')=='input_json_delta')
        self.assertEqual(json.loads(delta['delta']['partial_json']),message['content'][1]['input'])
        self.assertEqual(events[-1]['type'],'message_stop')


if __name__=='__main__':
    unittest.main()
