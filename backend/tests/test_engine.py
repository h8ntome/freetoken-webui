from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.services.engine import EngineManager


def manager(tmp_path):
    config = Settings(models_dir=tmp_path / 'models', data_dir=tmp_path / 'data', auth_enabled=False)
    config.prepare()
    return EngineManager(config)


def checkpoint(mgr):
    p = mgr.config.models_dir / 'model'
    p.mkdir()
    (p / 'config.json').write_text('{"architectures":["Qwen3ForCausalLM"]}')
    (p / 'model.safetensors').write_bytes(b'weights')
    return p


def test_options_are_native_args_and_unknown_rejected(tmp_path):
    mgr = manager(tmp_path)
    args = mgr._build_args(Path('/models/model;echo'), {'gpu': '0', 'memoryRatio': .8, 'moeBackend': 'hybrid'})
    assert '--moe-backend' in args and '--moe-strategy' not in args and 'hybrid' in args
    assert '--gpu' in args and 'model;echo' in args
    with pytest.raises(ValueError, match='Unsupported engine options'):
        mgr._build_args(Path('/models/x'), {'evil': '$(id)'})


@pytest.mark.asyncio
async def test_double_start_is_locked(tmp_path):
    mgr = manager(tmp_path)
    model = checkpoint(mgr)
    mgr._state = 'loading'
    with pytest.raises(Exception, match='currently loading'):
        await mgr.start(model)


@pytest.mark.asyncio
async def test_switch_uses_atomic_native_endpoint_and_shared_path(tmp_path, monkeypatch):
    mgr = manager(tmp_path)
    model = checkpoint(mgr)
    calls = []
    async def control(method, path, body=None):
        calls.append((method, path, body))
        return {'running': True, 'model': '/models/model', 'uptimeS': 1}
    async def health(*args, **kwargs):
        return {'status': 'ok', 'model': 'model'}
    monkeypatch.setattr(mgr, '_control_request', control)
    monkeypatch.setattr(mgr, '_request_json', health)
    result = await mgr.switch(model)
    assert calls[0][1] == '/engine/switch'
    assert calls[0][2]['model'] == '/models/model'
    assert all(call[1] != '/engine/stop' for call in calls)
    assert result['state'] == 'ready' and result['modelPath'] == str(model)


@pytest.mark.asyncio
async def test_restart_recovers_active_model_from_daemon(tmp_path, monkeypatch):
    mgr = manager(tmp_path)
    async def control(*args):
        return {'running': True, 'model': '/models/existing', 'uptimeS': 12}
    async def health(*args, **kwargs):
        return {'status': 'ok', 'model': 'existing'}
    monkeypatch.setattr(mgr, '_control_request', control)
    monkeypatch.setattr(mgr, '_request_json', health)
    status = await mgr.refresh()
    assert status['modelPath'] == str(mgr.config.models_dir / 'existing')
    assert status['capabilities']['chat']


@pytest.mark.asyncio
async def test_crash_and_disconnect_never_stay_ready(tmp_path, monkeypatch):
    mgr = manager(tmp_path)
    async def crashed(*args):
        return {'running': False, 'lastExitCode': 7, 'lastExitReason': 'exit'}
    monkeypatch.setattr(mgr, '_control_request', crashed)
    assert (await mgr.refresh())['state'] == 'failed'
    async def unavailable(*args):
        raise RuntimeError('connection refused')
    monkeypatch.setattr(mgr, '_control_request', unavailable)
    status = await mgr.refresh()
    assert status['state'] == 'failed' and not status['daemonReachable']


@pytest.mark.asyncio
async def test_mutations_are_not_retried_and_daemon_token_is_sent(tmp_path, monkeypatch):
    mgr = manager(tmp_path)
    mgr.config.freetoken_daemon_token = 'test-secret'
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={'error': 'engine preserved'})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(RuntimeError, match='engine preserved'):
        await mgr._control_request('POST', '/engine/stop', {})
    assert len(calls) == 1
    assert calls[0].headers['X-FT-Token'] == 'test-secret'


def test_log_ring_is_bounded(tmp_path):
    mgr = manager(tmp_path)
    for i in range(5100):
        mgr._append_log('info', str(i))
    assert len(mgr.logs(limit=2000)) == 2000
    assert mgr.logs(limit=1)[0]['message'] == '5099'


@pytest.mark.asyncio
async def test_external_mode_has_no_management(tmp_path):
    mgr = manager(tmp_path)
    mgr.config.freetoken_mode = 'external'
    assert not mgr.status()['capabilities']['downloads']
    with pytest.raises(Exception, match='external mode'):
        await mgr.stop()
