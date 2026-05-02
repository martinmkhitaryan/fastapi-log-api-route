from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Iterable, Mapping
from typing import Any, Callable, ClassVar, Coroutine, cast

import orjson
from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

logger = logging.getLogger("fastapi_log_api_route")

# NOTE: Header names excluded from logs by default. Covers dedicated auth-token
# carriers — ``authorization`` (Bearer/Basic), ``proxy-authorization``, and a
# handful of widely-used API-key headers. Cookies are intentionally *not*
# excluded by default: they often carry non-sensitive values (consent banners,
# feature flags, A/B variants) that are useful to debug. Add ``cookie`` /
# ``set-cookie`` to ``REQUEST_HEADERS_BLACKLIST`` on your subclass if your
# cookies carry session tokens.
DEFAULT_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-access-token",
        "x-refresh-token",
        "access-token",
        "refresh-token",
    }
)


class LogAPIRoute(APIRoute):
    LOG_HTTP_METHODS: ClassVar[set[str] | None] = None
    LOG_REQUEST_HEADERS: ClassVar[bool] = True
    LOG_REQUEST_BODY: ClassVar[bool] = True
    LOG_RESPONSE_HEADERS: ClassVar[bool] = False
    LOG_RESPONSE_BODY: ClassVar[bool] = True
    REQUEST_HEADERS_WHITELIST: ClassVar[set[str] | None] = None
    REQUEST_HEADERS_BLACKLIST: ClassVar[set[str]] = set(DEFAULT_SENSITIVE_HEADERS)
    PATHS_BLACKLIST: ClassVar[set[str]] = set()

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def log_route_handler(request: Request) -> Response:
            if self.should_skip(request):
                return await original_route_handler(request)

            started_perf = time.perf_counter()
            log_object: dict[str, Any] = {
                "started_at": time.time(),
                "finished_at": None,
                "duration_ms": None,
                **self.custom_fields(request),
                "request": {
                    "method": request.method,
                    "path": self.path,
                    "path_params": dict(request.path_params),
                    "query_params": dict(request.query_params),
                    "client": request.client.host if request.client else None,
                    "headers": self._collect_headers(request.headers) if self.LOG_REQUEST_HEADERS else None,
                    "body": None,
                },
                "response": None,
                "endpoint_logs": None,
            }

            exception: BaseException | None = None
            response: Response | None = None
            try:
                response = await original_route_handler(request)
            except RequestValidationError as e:
                log_object["response"] = {
                    "status": 422,
                    "detail": "Request validation error",
                    "errors": e.errors(),
                }
                exception = e
            except HTTPException as e:
                log_object["response"] = {
                    "status": e.status_code,
                    "detail": e.detail,
                    "traceback": traceback.format_exc(),
                }
                exception = e
            except Exception as e:
                log_object["response"] = {
                    "status": 500,
                    "detail": "Internal server error",
                    "traceback": traceback.format_exc(),
                }
                exception = e

            if self.LOG_REQUEST_BODY:
                log_object["request"]["body"] = self._extract_request_body(request)

            endpoint_logs = request.scope.get("endpoint_logs")
            if endpoint_logs is None or isinstance(endpoint_logs, Mapping):
                log_object["endpoint_logs"] = endpoint_logs
            else:
                logger.warning(
                    "endpoint_logs is not a Mapping; ignoring. value=%r",
                    endpoint_logs,
                )

            if response is not None:
                log_object["response"] = {"status": response.status_code, "body": None}

                if self.LOG_RESPONSE_BODY:
                    if response.media_type == "application/json":
                        response_body = orjson.loads(response.body)
                    elif response.media_type is None:
                        response_body = None
                    elif response.media_type == "text/plain":
                        response_body = {"plain_text": bytes(response.body).decode(response.charset)}
                    else:
                        response_body = {
                            "logger_error": (
                                f"Response body logging for content-type '{response.media_type}' is not supported yet"
                            )
                        }

                    log_object["response"]["body"] = response_body

                if self.LOG_RESPONSE_HEADERS:
                    log_object["response"]["headers"] = dict(response.headers)

            log_object["finished_at"] = time.time()
            log_object["duration_ms"] = 1000.0 * (time.perf_counter() - started_perf)

            try:
                self.log(log_object)
            except Exception:
                # A failure to emit a log line must never break the request.
                logger.exception("LogAPIRoute.log raised; dropping log record")

            if exception is not None:
                raise exception

            response = cast(Response, response)
            return response

        return log_route_handler

    def log(self, log_object: dict[str, Any]) -> None:
        """Emit the assembled log record. Override to change *how* records ship.

        The default forwards the record to the package logger as JSON, with
        the structured payload also attached as ``extra={"log_object": ...}``
        so structured-logging handlers can read it directly. The log level is
        derived from the response status code: 5xx → ``ERROR``, 4xx →
        ``WARNING``, everything else → ``INFO``.
        """

        message = orjson.dumps(log_object, default=str).decode("utf-8")
        logger.log(self._level_for(log_object), message, extra={"log_object": log_object})

    @staticmethod
    def _level_for(log_object: dict[str, Any]) -> int:
        response = log_object.get("response") or {}
        status = response.get("status", 0)
        if status >= 500:
            return logging.ERROR
        if status >= 400:
            return logging.WARNING
        return logging.INFO

    def custom_fields(self, request: Request) -> dict[str, Any]:
        """Return extra top-level fields to merge into the log record."""

        return {}

    def should_skip(self, request: Request) -> bool:
        """Return ``True`` to skip logging the current request."""

        methods = self.LOG_HTTP_METHODS
        if methods is not None and request.method not in methods:
            return True
        if self.path in self.PATHS_BLACKLIST:
            return True
        return False

    def _collect_headers(self, headers: Mapping[str, str]) -> dict[str, str | None]:
        blacklist = {n.lower() for n in self.REQUEST_HEADERS_BLACKLIST}
        whitelist = self.REQUEST_HEADERS_WHITELIST

        pairs: Iterable[tuple[str, str | None]]
        if whitelist is None:
            pairs = headers.items()
        else:
            pairs = ((k, headers.get(k)) for k in whitelist)

        return {k: v for k, v in pairs if k.lower() not in blacklist}

    def _extract_request_body(self, request: Request) -> Any:
        # FastAPI caches parsed JSON / form data on the request as a side-effect
        # of dependency injection. Read those caches rather than re-consuming
        # the request stream (which would be empty by now).
        cached_json = getattr(request, "_json", None)
        if cached_json is not None:
            return cached_json

        cached_form = getattr(request, "_form", None)
        if cached_form:
            return jsonable_encoder(cached_form)

        return None
