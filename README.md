# fastapi-log-api-route

A drop-in [`APIRoute`](https://fastapi.tiangolo.com/advanced/custom-request-and-route/)
subclass for [FastAPI](https://fastapi.tiangolo.com/) that logs each request
and response with method, path, headers, body, status, duration, and any
extra context you want to attach.

## Why

FastAPI's middleware tier doesn't have access to a parsed request body or to
the path *template* (only the rendered path). A custom `APIRoute` runs *after*
FastAPI has parsed the body via your dependency-injected models, so it sees
the same data your handler sees — without re-reading the request stream.

## Install

```bash
pip install fastapi-log-api-route
```

## Quick start

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)

from fastapi import APIRouter, FastAPI
from fastapi_log_api_route import LogAPIRoute

app = FastAPI()
router = APIRouter(route_class=LogAPIRoute)


@router.post("/items")
async def create_item(item: dict):
    return {"ok": True}


app.include_router(router)
```

Calls are emitted on the **`fastapi_log_api_route`** logger (**INFO / WARNING /
ERROR** by status — see [Hooks](#hooks)). If you omit **`basicConfig`** (or equivalent
**`dictConfig`**), **`INFO`** lines often never appear: Python only outputs log records
once a **handler** is attached on the propagation chain, and the root logger often has
none until you configure it ([Logging configuration](#logging-configuration)).

Example payload (shape depends on config, status, and errors):

```json
{
  "started_at": 1714665600.12,
  "finished_at": 1714665600.18,
  "duration_ms": 57.4,
  "request": {
    "method": "POST",
    "path": "/items",
    "path_params": {},
    "query_params": {},
    "client": "127.0.0.1",
    "headers": {"content-type": "application/json", "user-agent": "curl/8.5.0"},
    "body": {"name": "widget"}
  },
  "response": {"status": 200, "body": {"ok": true}},
  "endpoint_logs": null
}
```

You can also patch a bare `FastAPI` app's router directly:

```python
app = FastAPI()
app.router.route_class = LogAPIRoute
```

> If you also create your own `APIRouter`, pass `route_class=LogAPIRoute` to
> it as well — patching the app router doesn't propagate to sub-routers.

## Configuration

Subclass `LogAPIRoute` and override class attributes:

| Attribute | Default | Description |
| --- | --- | --- |
| `LOG_HTTP_METHODS` | `None` (all) | Methods to log; e.g. `{"POST", "PUT"}`. |
| `LOG_REQUEST_HEADERS` | `True` | Include request headers; when `False`, `"headers"` is `null`. |
| `LOG_REQUEST_BODY` | `True` | Include parsed JSON/form body (`null` if nothing cached); when `False`, `"body"` stays `null`. |
| `LOG_RESPONSE_HEADERS` | `False` | Include response headers under `response.headers`. |
| `LOG_RESPONSE_BODY` | `True` | Include response body (see [Response body in logs](#response-body-in-logs)). |
| `REQUEST_HEADERS_WHITELIST` | `None` (all) | When set, every listed name is emitted; missing headers use `null`. When `None`, only headers present on the request are included (minus blacklist). The blacklist still applies. |
| `REQUEST_HEADERS_BLACKLIST` | See code (`DEFAULT_SENSITIVE_HEADERS`) | Header names omitted from logs entirely. Defaults include `authorization`, `proxy-authorization`, `x-api-key`, `x-auth-token`, `x-access-token`, `x-refresh-token`, `access-token`, `refresh-token`. Cookies are **not** excluded by default — add `cookie` / `set-cookie` on your subclass if yours carry session tokens. Set to `set()` to log every header. |
| `PATHS_BLACKLIST` | `set()` | Exact route paths to skip. |

```python
class MyLogAPIRoute(LogAPIRoute):
    LOG_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    REQUEST_HEADERS_WHITELIST = {"x-request-id", "user-agent"}
```

## Hooks

Override these methods on your subclass for richer behaviour:

- `custom_fields(request) -> dict` — return extra top-level fields merged
  into the log record (trace ids, tenant ids, service metadata, …).
- `should_skip(request) -> bool` — completely bypass logging for a request.
- `log(log_object) -> None` — change *how* records are emitted (ship to a
  message bus, write to a file, hand off to `structlog`, …). The default
  chooses the stdlib log level from `log_object["response"]["status"]`:
  **5xx → ERROR**, **4xx → WARNING**, **otherwise → INFO** (missing or
  non-numeric status is treated as success for level purposes).

Request / response body capture is not hookable in the default class; subclass
and replace `get_route_handler` if you need different body rules.

### ``endpoint_logs`` (handler-attached payload)

Handlers (or middleware) can attach structured fields meant for downstream log
processors by setting ``request.scope["endpoint_logs"]`` to a **mapping** (e.g.
`dict`). They appear under the same key in the emitted log dict — nothing else
on the request is copied there automatically. If the value is not a mapping, it
is ignored and a warning is logged; the field stays `null`.

```python
from fastapi import APIRouter, FastAPI, Request
from fastapi_log_api_route import LogAPIRoute

app = FastAPI()
router = APIRouter(route_class=LogAPIRoute)


@router.post("/items")
async def create_item(request: Request, item: dict):
    request.scope["endpoint_logs"] = {"feature_flag": "new-pricing"}
    return {"ok": True}


app.include_router(router)
```

## Example: Datadog trace ids + service metadata

```python
import os
from typing import Any

from ddtrace import tracer
from fastapi import Request
from fastapi_log_api_route import LogAPIRoute


class TracedLogAPIRoute(LogAPIRoute):
    LOG_HTTP_METHODS = {"GET", "POST"}

    def custom_fields(self, request: Request) -> dict[str, Any]:
        span = tracer.current_span()
        return {
            "dd.trace_id": span.trace_id,
            "service": os.getenv("SERVICE_NAME"),
            "pod": os.getenv("POD_NAME"),
        }
```

## Logging configuration

The default **`LogAPIRoute.log`** serialises **`log_object`** with **`orjson`** and emits:

```text
logging.getLogger("fastapi_log_api_route").log(level, json_line, extra={"log_object": log_object})
```

**`level`** follows **`log_object["response"]["status"]`**: **5xx → ERROR**, **4xx →
WARNING**, **otherwise → INFO** (see [Hooks](#hooks)).

### Why nothing shows up

Python only prints log records once a **handler** is attached somewhere on the propagation
chain toward **root**. Until you call **`logging.basicConfig(...)`** at process startup (or
declare your pipeline in **`dictConfig`** / YAML), **INFO** from this package’s logger often
has nowhere to go.

### Minimal local setup

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
```

This package deliberately does **not** call **`basicConfig`** for you (libraries shouldn’t seize
global logging policy).

Tune severity like any **`logging`** setup—for example **`root.setLevel(logging.WARNING)`** hides
successful **`INFO`** request lines while still emitting **WARNING/ERROR** for **4xx/5xx**.

### Custom sink: override **`log`** (print, queue, OTLP, …)

```python
from typing import Any

import orjson

from fastapi_log_api_route import LogAPIRoute


class PrintLogAPIRoute(LogAPIRoute):
    def log(self, log_object: dict[str, Any]) -> None:
        line = orjson.dumps(log_object, default=str).decode("utf-8")
        print(line, flush=True)
```

Use **`APIRouter(route_class=PrintLogAPIRoute)`**. You bypass stdlib handlers entirely; wire
whatever transport you prefer.

When you stick with **`logging`**, importing the packaged symbol can help wrappers attach to the same
logger:

```python
from fastapi_log_api_route import logger
```

If **`log`** raises, **`LogAPIRoute`** catches it and emits **`logger.exception(...)`** on the
package logger — a buggy **`log`** hook must never break the request.

## Response body in logs

When `LOG_RESPONSE_BODY` is enabled, captured bodies behave as follows:

- `media_type == "application/json"` — parsed JSON via `orjson.loads`.
- `media_type is None` — `null`.
- `media_type == "text/plain"` — `{"plain_text": "<decoded string>"}` using the response charset.
- Any other media type — `{"logger_error": "<unsupported message>"}` (no raw bytes).

Unhandled exceptions, FastAPI validation errors, and `HTTPException` paths
populate `response` without a normal routed body (`detail`, `errors`, optional
`traceback` shapes).

## Development

This project is built with [hatch](https://hatch.pypa.io/) and uses
[uv](https://docs.astral.sh/uv/) for dev workflows; either works.

```bash
uv sync --group dev   # installs runtime + dev dependencies
uv run pytest       # or: pytest
```

## License

MIT License – see [LICENSE](LICENSE).
