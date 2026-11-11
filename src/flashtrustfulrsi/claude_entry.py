"""Run the pinned FlashRSI ClaudeCode agent inside a network-disabled container."""

import asyncio
import contextlib
import http.client
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(200)
        self.sock.connect('/model.sock')


class Relay(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        size = int(self.headers.get('Content-Length','0'))
        if not 0<size<=2*1024*1024:
            self.send_error(400)
            return
        connection = UnixConnection('local',timeout=200)
        try:
            connection.request('POST',self.path,self.rfile.read(size),{'Content-Type':'application/json'})
            reply = connection.getresponse()
            data = reply.read(8*1024*1024+1)
            if len(data)>8*1024*1024:
                raise ValueError('Messages response exceeds the relay limit')
            self.send_response(reply.status)
            self.send_header('Content-Type',reply.getheader('Content-Type','application/json'))
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()


async def main():
    request = json.loads(Path('/workspace/request.json').read_text())
    shutil.copytree('/workspace/harness','/tmp/harness')
    os.environ.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',DISABLE_AUTOUPDATER='1',
                      CLAUDE_CODE_MAX_RETRIES='0',CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS='1')
    relay = ThreadingHTTPServer(('127.0.0.1',0),Relay)
    thread = threading.Thread(target=relay.serve_forever,daemon=True)
    thread.start()
    try:
        from flashrsi.agents.claude_code import ClaudeCode
        from flashrsi._records import _trajectory_record
        config = {**request['config'],'source_dir':'/tmp/harness'}
        config['llm'] = {**config['llm'],'base_url':f'http://127.0.0.1:{relay.server_port}'}
        with contextlib.redirect_stdout(sys.stderr):
            agent = ClaudeCode(config)
            materialized = {name:hashlib.sha256(entry.content).hexdigest() for name,entry in agent.snapshot.files.items()}
            result = await agent.run(request['prompt'])
        record = _trajectory_record(result)
        record['usage']['ftrsi_materialized_files'] = materialized
        print(json.dumps(record,ensure_ascii=False,default=dict))
    finally:
        relay.shutdown()
        relay.server_close()
        thread.join()


if __name__=='__main__':
    asyncio.run(main())
