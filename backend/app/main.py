from __future__ import annotations

import asyncio
import json
import os
import mimetypes
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub import HfApi
from pydantic import BaseModel, Field

from .config import settings
from .database import Database, get_db, init_database
from .security import Principal, current_principal, init_auth, require_csrf
from .services.downloads import DownloadManager
from .services.engine import EngineConflict, EngineManager
from .services.library import ModelLibrary, catalog, safe_model_path
from .services.metrics import MetricsService


class LoginBody(BaseModel):
    username: str
    password: str


class LoadBody(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    switch: bool = False


class DownloadBody(BaseModel):
    repoId: str
    revision: str = "main"


class ImportBody(BaseModel):
    path: str


class ChatCreate(BaseModel):
    title: str = "New chat"
    model: str | None = None
    systemPrompt: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class ChatUpdate(BaseModel):
    title: str | None = None
    systemPrompt: str | None = None
    settings: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    chatId: str
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    chat_template_kwargs: dict[str, Any] | None = None


settings.prepare()
database = init_database(settings.data_dir)
auth = init_auth(settings, database)
engine = EngineManager(settings)
library = ModelLibrary(settings)
downloads = DownloadManager(settings, database)
metrics = MetricsService(settings, engine)


def require_managed_mode() -> None:
    """Reject local filesystem/process mutations when acting as an external client."""
    if settings.freetoken_mode != "managed":
        raise HTTPException(
            status_code=409,
            detail="This action is available only in Full Managed Mode. External Mode does not control the remote model filesystem or process.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.initialize()
    yield
    if engine.status()["owned"] and engine.status()["state"] in {"starting", "loading", "ready"}:
        # The exact child deliberately survives a web-process restart and is re-adopted on boot.
        pass


app = FastAPI(title="FreeToken Web Management API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(EngineConflict)
async def engine_conflict(_: Request, exc: EngineConflict):
    return JSONResponse({"detail": str(exc), "code": "engine_conflict"}, status_code=409)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/healthz")
async def web_health():
    status = engine.status()
    unhealthy = status["state"] == "failed" or (
        settings.freetoken_mode == "external" and status.get("health", {}).get("status") != "ok"
    )
    if unhealthy:
        return JSONResponse(
            {"status": "degraded", "engine": status},
            status_code=503,
        )
    return {"status": "ok", "engine": status["state"]}


@app.post("/api/auth/login")
async def login(body: LoginBody, response: Response):
    principal = auth.login(body.username, body.password, response)
    return {"username": principal.username, "csrfToken": principal.csrf_token, "authEnabled": settings.auth_enabled}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response, _: Principal = Depends(require_csrf)):
    auth.logout(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(principal: Principal = Depends(current_principal)):
    return {"username": principal.username, "csrfToken": principal.csrf_token, "authEnabled": settings.auth_enabled}


@app.get("/api/bootstrap")
async def bootstrap(request: Request, _: Principal = Depends(current_principal)):
    status = await engine.refresh()
    found = library.scan(status.get("modelPath"))
    gpu = (await metrics.read())["system"]["gpus"]
    return {
        "engine": status, "modelCount": len(found), "gpuDetected": bool(gpu), "gpus": gpu,
        "modelsDir": str(settings.models_dir), "modelsDirWritable": settings.models_dir.is_dir() and os.access(settings.models_dir, os.W_OK),
        "freeTokenReachable": status.get("health", {}).get("status") in {"ok", "loading"}, "mode": settings.freetoken_mode,
        "publicApi": _public_api(request), "authEnabled": settings.auth_enabled,
        "capabilities": status["capabilities"],
    }


@app.get("/api/models")
async def models(_: Principal = Depends(current_principal)):
    return {"items": library.scan(engine.status().get("modelPath"))}


@app.post("/api/models/rescan")
async def rescan(_: Principal = Depends(require_csrf)):
    return {"items": library.scan(engine.status().get("modelPath"))}


@app.get("/api/models/catalog")
async def model_catalog(query: str = "", _: Principal = Depends(current_principal)):
    items = catalog()
    if query:
        q = query.lower()
        items = [item for item in items if q in item["repo"].lower() or q in item["family"].lower()]
    return {"items": items}


@app.get("/api/models/search")
async def search_models(query: str, _: Principal = Depends(current_principal)):
    if len(query.strip()) < 2:
        return {"items": []}
    api = HfApi(token=settings.hf_token)
    try:
        result = await asyncio.to_thread(
            lambda: list(api.list_models(search=query, limit=20, sort="downloads", full=True))
        )
        verified = {item["repo"].lower() for item in catalog()}
        items = [{"repo": item.id, "downloads": item.downloads, "likes": item.likes, "updatedAt": item.last_modified, "pipeline": item.pipeline_tag, "compatibility": "verified" if item.id.lower() in verified else "unknown", "huggingFaceUrl": f"https://huggingface.co/{item.id}"} for item in result]
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hugging Face search failed: {exc}")


@app.post("/api/models/download", status_code=202)
async def download_model(body: DownloadBody, _: Principal = Depends(require_csrf)):
    require_managed_mode()
    return downloads.start(body.repoId, body.revision)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, _: Principal = Depends(require_csrf)):
    require_managed_mode()
    downloads.cancel(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, _: Principal = Depends(current_principal)):
    job = database.job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/models/import")
async def import_model(body: ImportBody, _: Principal = Depends(require_csrf)):
    require_managed_mode()
    return library.register(body.path)


@app.delete("/api/models/{model_id:path}")
async def delete_model(model_id: str, _: Principal = Depends(require_csrf)):
    require_managed_mode()
    path = safe_model_path(settings, model_id)
    active = engine.status().get("modelPath")
    if active and Path(active).resolve(strict=False) == path.resolve(strict=False):
        raise HTTPException(status_code=409, detail="Model is currently loaded. Unload it before deleting it.")
    return {"deleted": True, "recoveredBytes": library.delete(model_id)}


@app.get("/api/engine/status")
async def engine_status(_: Principal = Depends(current_principal)):
    return await engine.refresh()


@app.post("/api/models/{model_id:path}/load")
async def load_model(model_id: str, body: LoadBody, _: Principal = Depends(require_csrf)):
    require_managed_mode()
    path = safe_model_path(settings, model_id)
    return await (engine.switch(path, body.options) if body.switch else engine.start(path, body.options))


@app.post("/api/engine/unload")
async def unload(_: Principal = Depends(require_csrf)):
    require_managed_mode()
    return await engine.stop()


@app.post("/api/engine/restart")
async def restart(_: Principal = Depends(require_csrf)):
    require_managed_mode()
    status = engine.status()
    if not status.get("modelPath"):
        raise HTTPException(status_code=409, detail="No managed model is selected")
    path = Path(status["modelPath"])
    await engine.stop()
    return await engine.start(path)


@app.get("/api/metrics")
async def get_metrics(_: Principal = Depends(current_principal)):
    return await metrics.read()


@app.get("/api/logs")
async def get_logs(since: int = 0, limit: int = 1000, _: Principal = Depends(current_principal)):
    return {"items": engine.logs(since, limit)}


@app.get("/api/cache")
async def cache_status(_: Principal = Depends(current_principal)):
    try:
        return await engine.proxy_json("GET", "/v1/cache/status")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/cache/rebuild")
async def cache_rebuild(body: dict[str, Any] = Body(...), _: Principal = Depends(require_csrf)):
    allowed = {"moe_cache_size", "num_pages", "num_mamba_slots", "num_swa_pages", "swa_full_tokens_ratio", "mode", "timeout"}
    if set(body) - allowed:
        raise HTTPException(status_code=422, detail="Unsupported cache option")
    try:
        return await engine.proxy_json("POST", "/v1/cache/rebuild", body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _chat_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "model": row["model"], "systemPrompt": row["system_prompt"], "settings": json.loads(row["settings_json"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


@app.get("/api/chats")
async def list_chats(_: Principal = Depends(current_principal)):
    return {"items": [_chat_dict(row) for row in database.all("SELECT * FROM chats ORDER BY updated_at DESC")]}


@app.post("/api/chats")
async def create_chat(body: ChatCreate, _: Principal = Depends(require_csrf)):
    now, chat_id = time.time(), str(uuid.uuid4())
    database.execute("INSERT INTO chats VALUES (?,?,?,?,?,?,?)", (chat_id, body.title, body.model, body.systemPrompt, json.dumps(body.settings), now, now))
    return _chat_dict(database.one("SELECT * FROM chats WHERE id=?", (chat_id,)))


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, _: Principal = Depends(current_principal)):
    row = database.one("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    result = _chat_dict(row)
    result["messages"] = [{"id": item["id"], "role": item["role"], "content": item["content"], "reasoning": item["reasoning"], "usage": json.loads(item["usage_json"]) if item["usage_json"] else None, "createdAt": item["created_at"]} for item in database.all("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (chat_id,))]
    return result


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: ChatUpdate, _: Principal = Depends(require_csrf)):
    row = database.one("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    database.execute("UPDATE chats SET title=?, system_prompt=?, settings_json=?, updated_at=? WHERE id=?", (body.title if body.title is not None else row["title"], body.systemPrompt if body.systemPrompt is not None else row["system_prompt"], json.dumps(body.settings) if body.settings is not None else row["settings_json"], time.time(), chat_id))
    return _chat_dict(database.one("SELECT * FROM chats WHERE id=?", (chat_id,)))


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, _: Principal = Depends(require_csrf)):
    database.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    return {"deleted": True}


@app.post("/api/chat/completions")
async def chat_completion(body: ChatRequest, _: Principal = Depends(require_csrf)):
    chat = database.one("SELECT * FROM chats WHERE id=?", (body.chatId,))
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    user = body.messages[-1] if body.messages else None
    if user and user.get("role") == "user":
        database.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), body.chatId, "user", str(user.get("content", "")), None, None, time.time()))
    payload = {"model": body.model or engine.status().get("model"), "messages": body.messages}
    for key in ("temperature", "top_p", "max_tokens", "chat_template_kwargs"):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value

    async def stream():
        content, reasoning, usage = [], [], None
        try:
            buffer = ""
            async for chunk in engine.chat_stream(payload):
                yield chunk
                buffer += chunk.decode(errors="replace")
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    for line in event.splitlines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            continue
                        try:
                            doc = json.loads(raw)
                            choice = (doc.get("choices") or [{}])[0]
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                content.append(delta["content"])
                            if delta.get("reasoning_content") or delta.get("reasoning"):
                                reasoning.append(delta.get("reasoning_content") or delta.get("reasoning"))
                            usage = doc.get("usage") or usage
                        except (ValueError, TypeError):
                            pass
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n".encode()
            return
        database.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), body.chatId, "assistant", "".join(content), "".join(reasoning) or None, json.dumps(usage) if usage else None, time.time()))
        title = chat["title"]
        if title == "New chat" and user:
            title = str(user.get("content", "New chat"))[:52].strip() or "New chat"
        database.execute("UPDATE chats SET title=?, model=?, updated_at=? WHERE id=?", (title, payload["model"], time.time(), body.chatId))

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/api-info")
async def api_info(request: Request, _: Principal = Depends(current_principal)):
    base = _public_api(request)
    if settings.public_api_base_url:
        warning = None
    elif settings.freetoken_mode == "external":
        warning = "This is the server-side external endpoint. Set PUBLIC_API_BASE_URL if clients use a different reachable address."
    else:
        warning = "Browser-derived address is a candidate only; verify reachability from each client."
    source = "configured" if settings.public_api_base_url else ("external" if settings.freetoken_mode == "external" else "browser")
    return {"baseUrl": base, "configured": bool(settings.public_api_base_url), "source": source, "model": engine.status().get("model"), "openai": {"baseUrl": f"{base}/v1", "chatCompletions": f"{base}/v1/chat/completions", "responses": f"{base}/v1/responses"}, "anthropic": {"baseUrl": base, "messages": f"{base}/v1/messages"}, "warning": warning}


def _public_api(request: Request) -> str:
    if settings.public_api_base_url:
        return settings.public_api_base_url.rstrip("/")
    if settings.freetoken_mode == "external":
        return settings.freetoken_external_url.rstrip("/")
    hostname = request.url.hostname or "SERVER"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{request.url.scheme}://{hostname}:{settings.freetoken_port}"


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (frontend_dist / path).resolve(strict=False)
        if candidate.is_relative_to(frontend_dist) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
