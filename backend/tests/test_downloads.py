import json
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.services.downloads import DownloadManager


def test_duplicate_complete_download_is_rejected(tmp_path: Path):
    models, data = tmp_path / "models", tmp_path / "data"
    models.mkdir(); data.mkdir()
    checkpoint = models / "owner--model"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(json.dumps({"model_type": "qwen3"}))
    (checkpoint / "model.safetensors").write_bytes(b"weights")
    db = Database(data / "state.sqlite")
    db.initialize()
    manager = DownloadManager(Settings(models_dir=models, data_dir=data, auth_enabled=False), db)

    with pytest.raises(ValueError, match="already downloaded"):
        manager.start("owner/model")

import threading
from types import SimpleNamespace
import httpx
from app.services.downloads import _hugging_face_error
from app.services.library import ModelLibrary


def setup_manager(tmp_path):
    config = Settings(models_dir=tmp_path / 'models', data_dir=tmp_path / 'data', hf_token='', auth_enabled=False)
    config.prepare()
    db = Database(config.data_dir / 'state.sqlite')
    db.initialize()
    return DownloadManager(config, db)


def test_blank_token_is_explicitly_anonymous(tmp_path, monkeypatch):
    manager = setup_manager(tmp_path)
    seen = []
    class API:
        def __init__(self, token):
            seen.append(token)
        def model_info(self, *args, **kwargs):
            raise RuntimeError('metadata exploded with useful context')
    monkeypatch.setattr('app.services.downloads.HfApi', API)
    job = manager.db.create_job('download', {'repoId': 'owner/model'})
    manager._run(job['id'], 'owner/model', 'main', threading.Event())
    assert seen == [False]
    assert 'metadata exploded with useful context' in manager.db.job(job['id'])['error']


def test_complete_public_download_pins_commit_and_detects_model(tmp_path, monkeypatch):
    manager = setup_manager(tmp_path)
    files = {'config.json': b'{"architectures":["GptOssForCausalLM"]}', 'model.safetensors': b'weights'}
    class API:
        def __init__(self, token):
            assert token is False
        def model_info(self, *args, **kwargs):
            return SimpleNamespace(tags=['safetensors'], pipeline_tag='text-generation', sha='fixed-commit',
                                   siblings=[SimpleNamespace(rfilename=k, size=len(v)) for k,v in files.items()])
    requests = []
    def respond(request):
        requests.append(request)
        assert '/fixed-commit/' in str(request.url)
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=files[request.url.path.split('/')[-1]])
    client = httpx.Client
    monkeypatch.setattr('app.services.downloads.HfApi', API)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    job = manager.db.create_job('download', {'repoId': 'openai/gpt-oss-20b'})
    manager._run(job['id'], 'openai/gpt-oss-20b', 'main', threading.Event())
    assert manager.db.job(job['id'])['state'] == 'completed'
    assert ModelLibrary(manager.config).scan()[0]['status'] == 'downloaded'
    assert len(requests) == 2


def test_resume_uses_range_and_preserves_partial_on_cancel(tmp_path, monkeypatch):
    manager = setup_manager(tmp_path)
    dest = manager.config.models_dir / 'owner--model'
    dest.mkdir()
    (dest / 'weights.part').write_bytes(b'abc')
    def respond(request):
        assert request.headers['range'] == 'bytes=3-'
        return httpx.Response(206, headers={'content-range':'bytes 3-5/6'}, content=b'def')
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    job = manager.db.create_job('download', {})
    manager._download_file(job['id'], 'owner/model', 'sha', dest, 'weights', 6, 6,
                           {'downloaded':0,'transferred':0,'started':0}, threading.Event())
    assert (dest / 'weights').read_bytes() == b'abcdef'


def test_disk_and_filesystem_errors_are_not_mislabeled_network():
    assert 'disk full' in _hugging_face_error(OSError('disk full'))
    assert 'DNS' not in _hugging_face_error(PermissionError('permission denied'))
    assert 'hf_abc123' not in _hugging_face_error(RuntimeError('token hf_abc123'))


def test_partial_symlink_and_root_are_rejected(tmp_path):
    manager = setup_manager(tmp_path)
    from app.services.library import safe_model_path
    with pytest.raises(ValueError, match='storage root'):
        safe_model_path(manager.config, '.', must_exist=False)
    target = tmp_path / 'keep'
    target.write_text('preserve')
    dest = manager.config.models_dir / 'owner--model'
    dest.mkdir()
    (dest / 'weights.part').symlink_to(target)
    with pytest.raises(RuntimeError, match='symlink'):
        manager._download_file('job', 'owner/model', 'main', dest, 'weights', 8, 8, {}, threading.Event())
    assert target.read_text() == 'preserve'


def test_cancel_preserves_partial_for_resume(tmp_path, monkeypatch):
    manager = setup_manager(tmp_path)
    dest = manager.config.models_dir / 'owner--model'
    dest.mkdir()
    (dest / 'weights.part').write_bytes(b'abc')
    event = threading.Event()
    event.set()
    def respond(request):
        return httpx.Response(206, headers={'content-range':'bytes 3-5/6'}, content=b'def')
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    from app.services.downloads import DownloadCancelled
    with pytest.raises(DownloadCancelled):
        manager._download_file('job', 'owner/model', 'sha', dest, 'weights', 6, 6,
                               {'downloaded':0,'transferred':0,'started':0}, event)
    assert (dest / 'weights.part').read_bytes() == b'abc'
    assert not (dest / 'weights').exists()


def test_invalid_resume_range_is_rejected(tmp_path, monkeypatch):
    manager = setup_manager(tmp_path)
    dest = manager.config.models_dir / 'owner--model'
    dest.mkdir()
    (dest / 'weights.part').write_bytes(b'abc')
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(
        lambda request: httpx.Response(206, headers={'content-range':'bytes 0-2/6'}, content=b'abc')), **kw))
    with pytest.raises(RuntimeError, match='Invalid resume range'):
        manager._download_file('job', 'owner/model', 'sha', dest, 'weights', 6, 6,
                               {'downloaded':0,'transferred':0,'started':0}, threading.Event())
    assert (dest / 'weights.part').read_bytes() == b'abc'
