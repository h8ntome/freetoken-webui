# Management API

All `/api/*` routes except login require an authenticated session when authentication is enabled. Mutations also require the `X-CSRF-Token` returned by `/api/auth/me`. The native inference API remains FreeToken's own API on port 1919.

Core routes:

- `GET /api/bootstrap`, `/api/models`, `/api/models/catalog`, `/api/models/search?query=`
- `POST /api/models/download`, `/api/jobs/:id/cancel`, `/api/models/:id/load`
- `DELETE /api/models/:id?unload=true`
- `GET /api/engine/status`, `POST /api/engine/unload`, `/api/engine/restart`
- `GET /api/metrics`, `/api/logs`, `/api/cache`; `POST /api/cache/rebuild`
- CRUD `/api/chats`; streaming `POST /api/chat/completions`
- `GET /api/api-info`

Long-running downloads return a job immediately. Poll `GET /api/jobs/:id`; the states are `queued`, `running`, `cancelling`, `cancelled`, `completed`, and `failed`.

