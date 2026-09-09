import json

from fastapi.testclient import TestClient
from app import main


def test_real_http_stream_contract_preserves_split_unicode_and_persistence(monkeypatch):
    async def stream(payload):
        assert payload['messages'][-1]['content'] == 'hello'
        event = ('data: ' + json.dumps({'choices':[{'delta':{'content':'héllo 世界'}}]}, ensure_ascii=False) + '\n\n').encode()
        # Splits inside a multi-byte code point and inside the SSE delimiter.
        for byte in event:
            yield bytes([byte])
        yield b'data: [DONE]\n\n'
    monkeypatch.setattr(main.engine, 'chat_stream', stream)
    client = TestClient(main.app)
    chat = client.post('/api/chats',json={}).json()
    response = client.post('/api/chat/completions',json={'chatId':chat['id'],'messages':[{'role':'user','content':'hello'}]})
    assert response.status_code == 200 and '[DONE]' in response.text
    stored = client.get('/api/chats/'+chat['id']).json()
    assert stored['messages'][-1]['content'] == 'héllo 世界'


def test_stream_failure_is_visible_and_partial_answer_is_saved(monkeypatch):
    async def stream(payload):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError('native engine CUDA allocation failed')
    monkeypatch.setattr(main.engine, 'chat_stream', stream)
    client = TestClient(main.app)
    chat = client.post('/api/chats',json={}).json()
    response = client.post('/api/chat/completions',json={'chatId':chat['id'],'messages':[{'role':'user','content':'hello'}]})
    assert 'CUDA allocation failed' in response.text
    assert client.get('/api/chats/'+chat['id']).json()['messages'][-1]['content'] == 'partial'
    assert any('CUDA allocation failed' in x['message'] for x in main.engine.logs())
