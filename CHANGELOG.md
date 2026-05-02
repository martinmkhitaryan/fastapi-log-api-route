# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-03

### Added

- **`LogAPIRoute`**: FastAPI `APIRoute` subclass that records one structured JSON payload per handled request (stdlib `logging`, `extra={"log_object": …}`).
- **Request fields**: method, route template path, path/query params, client host, optional headers and body (body from FastAPI’s parsed JSON / form cache).
- **Response fields**: status; optional body capture for `application/json`, `text/plain` (as `plain_text`), and unsupported types as a `logger_error` note; optional response headers.
- **Log levels** from HTTP status on the default `log` implementation: **5xx → ERROR**, **4xx → WARNING**, otherwise **INFO**.
- **`request.scope["endpoint_logs"]`**: attach a mapping of custom fields; copied to `endpoint_logs` in the emitted record (invalid values ignored with a warning).
- **Configuration** via class attributes: `LOG_HTTP_METHODS`, `LOG_REQUEST_HEADERS`, `LOG_REQUEST_BODY`, `LOG_RESPONSE_HEADERS`, `LOG_RESPONSE_BODY`, `REQUEST_HEADERS_WHITELIST`, `REQUEST_HEADERS_BLACKLIST`, `PATHS_BLACKLIST`.
- **`REQUEST_HEADERS_BLACKLIST`** defaults for common auth/token headers (`authorization`, `proxy-authorization`, token-style `x-*`/`access-token`/`refresh-token`, etc.); cookies not excluded by default.
- **Whitelist mode**: every listed header name appears in logs; absent headers serialize as **`null`**.
- **Hooks** for subclasses: `custom_fields(request)`, `should_skip(request)`, `log(log_object)` (replace how records are emitted, e.g. print or queue).
- **CI**: GitHub Actions workflow (pytest + coverage on Python 3.10–3.14).