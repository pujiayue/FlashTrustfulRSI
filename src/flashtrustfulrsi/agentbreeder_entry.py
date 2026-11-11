"""Execute one AgentBreeder forward function inside the isolated worker."""
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import random
import sys
import types

import httpx


async def main():
    request = json.loads(Path('/workspace/request.json').read_text())
    random.seed(request['seed'])
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds='/model.sock'), timeout=180,
    ) as client:
        async def complete(messages, response_format, model=None, temperature=.5):
            schema = json.dumps(response_format, ensure_ascii=False)
            response = await client.post('http://model-gateway/v1/chat/completions', json={
                'model': 'gpt-5.6-sol', 'messages': [
                    *messages, {'role': 'user', 'content': 'Return a JSON object with these fields: '+schema},
                ], 'temperature': temperature, 'max_tokens': 2048,
                'response_format': {'type': 'json_object'},
            })
            response.raise_for_status()
            return json.loads(response.json()['choices'][0]['message']['content'])

        sys.modules['api'] = types.ModuleType('api')
        sys.modules['api'].get_json_completion = complete
        with redirect_stdout(sys.stderr):
            from scaffold import Agent, Chat, Meeting
            ns = {'Agent': Agent, 'Chat': Chat, 'Meeting': Meeting}
            exec(compile(request['code'], '<candidate>', 'exec'), ns)
            output = await ns['forward'](None, request['prompt'], request['format'])
        if not isinstance(output, str) or len(output) > 16384:
            raise ValueError('Invalid scaffold output')
        print(json.dumps({'output': output}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
