"""Bind native FlashRSI ClaudeCode rollouts to isolated, inference-only workers."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import tempfile
import types
import yaml

from .deepseek_anthropic import AnthropicSocket
from .native_container import NativeSandbox
from .provider import write_json


def bind_claude_code(agent, *, image, provider, artifact_dir, upstream, role):
    from flashrsi.core import Trajectory

    if (agent.config['llm']['model_name']!='deepseek-flash' or agent.config['llm']['api_key']!='no-secret'
            or agent.config['llm']['base_url']!='http://model-gateway'
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,20}',role)):
        raise ValueError('Expected the official development model and a credential-free local route')
    dest = Path(artifact_dir)
    dest.mkdir(parents=True,exist_ok=True)
    sequence = 0

    def execute(instance,prompt,working_dir):
        nonlocal sequence
        if working_dir is not None:
            raise ValueError('This Claude Code binding supports read-only target rollouts')
        sequence += 1
        ident = f'{role}-{sequence:06d}'
        output = dest/ident
        output.mkdir()
        payload = {'prompt':prompt,'snapshot_id':instance.snapshot.id,'config':dict(instance.config),'editing':False}
        write_json(output/'input.json',payload)
        with tempfile.TemporaryDirectory(prefix='ftrsi-claude-') as tmp:
            root = Path(tmp)
            bundle = root/'bundle'
            bundle.mkdir()
            shutil.copytree(instance.files_dir,bundle/'harness')
            (bundle/'harness/components.yaml').write_text(yaml.safe_dump(
                {key:list(value) for key,value in instance.component_rules.items()},sort_keys=False))
            shutil.copytree(Path(upstream)/'flashrsi',bundle/'flashrsi',
                            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            shutil.copyfile(Path(__file__).with_name('claude_entry.py'),bundle/'entry.py')
            write_json(bundle/'request.json',{'prompt':prompt,'config':dict(instance.config)})
            for path in [bundle,*bundle.rglob('*')]:
                path.chmod(0o755 if path.is_dir() or path.stat().st_mode&0o111 else 0o644)
            with AnthropicSocket(root/'model.sock',provider,ident) as wire:
                sandbox = NativeSandbox(image=image,model_socket=wire.path)
                result = sandbox.run_process(['python','-B','/workspace/entry.py'],name='ftrsi-claude-'+ident,
                                             workdir=bundle,timeout=instance.config.get('timeout',300))
            write_json(output/'process.json',{'returncode':result.returncode,'stderr':result.stderr,'inference_calls':wire.calls})
        if result.returncode:
            raise RuntimeError('Native ClaudeCode container failed: '+result.stderr[-1500:])
        document = json.loads(result.stdout)
        write_json(output/'result.json',{'input_sha256':sha256(json.dumps(payload,sort_keys=True,default=dict).encode()).hexdigest(),
                                         'document':document})
        if document['status']!='finished':
            raise RuntimeError('Native ClaudeCode trajectory failed: '+str(document.get('error_message'))[:1500])
        expected = {name:sha256(entry.content).hexdigest() for name,entry in instance.snapshot.files.items()}
        if document['usage'].get('ftrsi_materialized_files') != expected:
            raise ValueError('The native container did not materialize the selected resource files')
        return Trajectory(**document)

    async def run(instance,prompt,working_dir=None):
        task = asyncio.create_task(asyncio.to_thread(execute,instance,prompt,working_dir))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    agent._run = types.MethodType(run,agent)
    return agent
