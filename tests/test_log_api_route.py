"""End-to-end tests for ``LogAPIRoute`` using FastAPI's TestClient."""

from __future__ import annotations

from typing import Any, ClassVar, Optional

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fastapi_log_api_route import LogAPIRoute


class Body(BaseModel):
    message: str


def _build_app(route_class: type[LogAPIRoute]) -> tuple[FastAPI, list[dict[str, Any]]]:
    """Return an app whose routes use ``route_class`` and a captured-logs list."""

    captured: list[dict[str, Any]] = []

    class CapturingRoute(route_class):  # type: ignore[misc, valid-type]
        def log(self, log_object: dict[str, Any]) -> None:
            captured.append(log_object)

    app = FastAPI()
    router = APIRouter(route_class=CapturingRoute)

    @router.post("/echo")
    async def echo(request: Request, body: Body) -> dict[str, str]:
        request.scope["endpoint_logs"] = {"handler": "echo"}
        return {"message": body.message}

    @router.post("/plain")
    async def plain(body: Body) -> dict[str, str]:
        # Doesn't touch ``endpoint_logs`` so middleware-injected values
        # survive into the log record.
        return {"message": body.message}

    @router.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @router.post("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="I'm a teapot")

    app.include_router(router)
    return app, captured


def test_logs_request_and_response_bodies():
    app, captured = _build_app(LogAPIRoute)
    client = TestClient(app)

    response = client.post(
        "/echo",
        headers={"x-request-id": "abc-123"},
        json={"message": "hello"},
    )

    assert response.status_code == 200
    assert len(captured) == 1
    record = captured[0]

    assert record["request"]["method"] == "POST"
    assert record["request"]["path"] == "/echo"
    assert record["request"]["body"] == {"message": "hello"}
    assert record["request"]["headers"]["x-request-id"] == "abc-123"
    assert record["response"]["status"] == 200
    assert record["response"]["body"] == {"message": "hello"}
    assert record["endpoint_logs"] == {"handler": "echo"}
    assert record["duration_ms"] >= 0


def test_header_whitelist_filters_logged_headers():
    class WhitelistedRoute(LogAPIRoute):
        REQUEST_HEADERS_WHITELIST = {"x-request-id"}

    app, captured = _build_app(WhitelistedRoute)
    client = TestClient(app)

    client.post(
        "/echo",
        headers={"x-request-id": "abc", "x-other": "ignored"},
        json={"message": "hi"},
    )

    headers = captured[0]["request"]["headers"]
    assert headers == {"x-request-id": "abc"}


def test_header_whitelist_keeps_null_when_header_absent():
    class WhitelistedRoute(LogAPIRoute):
        REQUEST_HEADERS_WHITELIST = {"x-request-id", "x-optional"}

    app, captured = _build_app(WhitelistedRoute)
    client = TestClient(app)

    client.post("/echo", headers={"x-request-id": "abc"}, json={"message": "hi"})

    assert captured[0]["request"]["headers"] == {"x-request-id": "abc", "x-optional": None}
    app, captured = _build_app(LogAPIRoute)
    client = TestClient(app)

    client.post(
        "/echo",
        headers={
            "authorization": "Bearer secret",
            "cookie": "ab_variant=b",
            "x-api-key": "abc",
            "x-request-id": "rid",
        },
        json={"message": "hi"},
    )

    headers = captured[0]["request"]["headers"]
    assert "authorization" not in headers
    assert "x-api-key" not in headers
    # Cookies are kept by default — they often carry non-sensitive values.
    assert headers["cookie"] == "ab_variant=b"
    assert headers["x-request-id"] == "rid"


def test_blacklist_applies_when_whitelist_is_set():
    class Route(LogAPIRoute):
        REQUEST_HEADERS_WHITELIST = {"authorization", "x-request-id"}

    app, captured = _build_app(Route)
    client = TestClient(app)

    client.post(
        "/echo",
        headers={"authorization": "Bearer secret", "x-request-id": "rid"},
        json={"message": "hi"},
    )

    headers = captured[0]["request"]["headers"]
    assert headers == {"x-request-id": "rid"}


def test_empty_blacklist_logs_every_header():
    class Route(LogAPIRoute):
        REQUEST_HEADERS_BLACKLIST: ClassVar[set[str]] = set()

    app, captured = _build_app(Route)
    client = TestClient(app)

    client.post(
        "/echo",
        headers={"authorization": "Bearer secret"},
        json={"message": "hi"},
    )

    headers = captured[0]["request"]["headers"]
    assert headers["authorization"] == "Bearer secret"


def test_method_filter_skips_unmatched_methods():
    class PostOnlyRoute(LogAPIRoute):
        LOG_HTTP_METHODS = {"POST"}

    app, captured = _build_app(PostOnlyRoute)
    client = TestClient(app)

    client.get("/items/42")
    assert captured == []

    client.post("/echo", json={"message": "logged"})
    assert len(captured) == 1


def test_paths_blacklist_skips_route():
    class BlacklistedRoute(LogAPIRoute):
        PATHS_BLACKLIST = {"/items/{item_id}"}

    app, captured = _build_app(BlacklistedRoute)
    client = TestClient(app)

    client.get("/items/1")
    assert captured == []


def test_unhandled_exception_is_logged_and_reraised():
    app, captured = _build_app(LogAPIRoute)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/boom")

    assert response.status_code == 500
    assert len(captured) == 1
    record = captured[0]
    assert record["response"]["status"] == 500
    assert record["response"]["detail"] == "Internal server error"
    assert "kaboom" in record["response"]["traceback"]


def test_http_exception_status_is_preserved_in_log():
    app, captured = _build_app(LogAPIRoute)
    client = TestClient(app)

    response = client.post("/teapot")

    assert response.status_code == 418
    assert captured[0]["response"]["status"] == 418
    assert captured[0]["response"]["detail"] == "I'm a teapot"


def test_custom_fields_are_merged_into_log_record():
    class FieldRoute(LogAPIRoute):
        def custom_fields(self, request: Request) -> dict[str, Any]:
            return {"service": "fancy"}

    app, captured = _build_app(FieldRoute)
    client = TestClient(app)

    client.post("/echo", json={"message": "hi"})

    assert captured[0]["service"] == "fancy"


def test_non_mapping_endpoint_logs_is_ignored():
    app, captured = _build_app(LogAPIRoute)

    @app.middleware("http")
    async def inject_bad_endpoint_logs(request, call_next):  # type: ignore[no-untyped-def]
        request.scope["endpoint_logs"] = "not-a-mapping"
        return await call_next(request)

    client = TestClient(app)
    client.post("/plain", json={"message": "hi"})

    assert captured[0]["endpoint_logs"] is None


def test_middleware_can_set_endpoint_logs():
    app, captured = _build_app(LogAPIRoute)

    @app.middleware("http")
    async def inject_endpoint_logs(request, call_next):  # type: ignore[no-untyped-def]
        request.scope["endpoint_logs"] = {"middleware": True}
        return await call_next(request)

    client = TestClient(app)
    client.post("/plain", json={"message": "hi"})

    assert captured[0]["endpoint_logs"] == {"middleware": True}


def test_logger_failure_does_not_break_request(caplog: pytest.LogCaptureFixture):
    class BrokenRoute(LogAPIRoute):
        def log(self, log_object: dict[str, Any]) -> None:
            raise RuntimeError("logger down")

    app = FastAPI()
    router = APIRouter(route_class=BrokenRoute)

    @router.post("/echo")
    async def echo(body: Body) -> dict[str, str]:
        return {"message": body.message}

    app.include_router(router)

    client = TestClient(app)
    with caplog.at_level("ERROR", logger="fastapi_log_api_route"):
        response = client.post("/echo", json={"message": "hi"})

    assert response.status_code == 200
    assert any("LogAPIRoute.log raised" in r.message for r in caplog.records)


def test_default_log_emits_through_package_logger(caplog: pytest.LogCaptureFixture):
    """The bare ``LogAPIRoute`` (no subclass) writes through the package logger."""

    app = FastAPI()
    router = APIRouter(route_class=LogAPIRoute)

    @router.post("/echo")
    async def echo(body: Body) -> dict[str, str]:
        return {"message": body.message}

    app.include_router(router)
    client = TestClient(app)

    with caplog.at_level("INFO", logger="fastapi_log_api_route"):
        client.post("/echo", json={"message": "hi"})

    structured: Optional[dict[str, Any]] = None
    for record in caplog.records:
        if record.name == "fastapi_log_api_route":
            structured = getattr(record, "log_object", None)
            if structured is not None:
                break

    assert structured is not None
    assert structured["request"]["body"] == {"message": "hi"}
    assert structured["response"]["status"] == 200


def test_log_level_reflects_response_status(caplog: pytest.LogCaptureFixture):
    """5xx -> ERROR, 4xx -> WARNING, 2xx -> INFO."""
    import logging as _logging

    app = FastAPI()
    router = APIRouter(route_class=LogAPIRoute)

    @router.post("/ok")
    async def ok() -> dict[str, str]:
        return {"message": "ok"}

    @router.post("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="I'm a teapot")

    @router.post("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    def _level_of(path: str) -> int:
        caplog.clear()
        with caplog.at_level(_logging.DEBUG, logger="fastapi_log_api_route"):
            client.post(path)
        for record in caplog.records:
            if record.name == "fastapi_log_api_route" and getattr(record, "log_object", None):
                return record.levelno
        raise AssertionError(f"no structured log record for {path}")

    assert _level_of("/ok") == _logging.INFO
    assert _level_of("/teapot") == _logging.WARNING
    assert _level_of("/boom") == _logging.ERROR
