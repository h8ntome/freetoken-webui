import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from app.config import Settings
from app.services.engine import EngineManager
from app.services.metrics import MetricsService


@pytest.mark.asyncio
async def test_external_client_over_real_http_socket(tmp_path):
    """Real transport against a protocol fixture, not GPU inference."""
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            value = {'status':'ok','model':'fixture'} if self.path=='/health' else {'throughput':{'decode_tps':12.5},'gpus':[{'name':'fixture GPU','total_bytes':1024}]}
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
        def do_POST(self):
            seen.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length']))),self.headers.get('Authorization')))
            self.send_response(200); self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    cfg = Settings(freetoken_mode='external',freetoken_external_url=f'http://127.0.0.1:{server.server_port}',freetoken_api_key='fixture-key',models_dir=tmp_path/'models',data_dir=tmp_path/'data',auth_enabled=False)
    cfg.prepare();engine=EngineManager(cfg)
    try:
        assert (await engine.refresh())['capabilities']['chat']
        assert not engine.status()['capabilities']['lifecycle']
        chunks=[chunk async for chunk in engine.chat_stream({'messages':[{'role':'user','content':'test'}]})]
        assert b'hello' in b''.join(chunks)
        assert seen[0][0]=='/v1/chat/completions' and seen[0][2]=='Bearer fixture-key'
        metrics=await MetricsService(cfg,engine).read()
        assert metrics['runtime']['throughput']['decode_tps']==12.5
        assert metrics['system']['gpus'][0]['name']=='fixture GPU'
        assert metrics['system']['gpus'][0]['utilization'] is None
    finally:
        server.shutdown();server.server_close()
