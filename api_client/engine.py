"""HTTP execution engine: variables, request building, execution, tests and extraction.

Nothing in this module touches Qt, so it is safe to run on worker threads.
"""
import base64
import hashlib
import hmac
import json
import os
import random
import re
import string
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urljoin

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .config_store import AppSettings
from .models import AuthConfig, ExtractRule, RequestModel, TestRule

APP_USER_AGENT = "APIClient/1.0"
VAR_RE = re.compile(r"\{\{\s*([^{}\s]+?)\s*\}\}")
PATH_VAR_RE = re.compile(r"(?<=/):([A-Za-z_][\w\-]*)")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

RAW_CONTENT_TYPES = {
    "json": "application/json",
    "text": "text/plain",
    "xml": "application/xml",
    "html": "text/html",
    "javascript": "application/javascript",
}

_FIRST_NAMES = ["Ava", "Liam", "Maya", "Noah", "Zara", "Ethan", "Isla", "Arjun", "Sofia", "Leo"]
_LAST_NAMES = ["Patel", "Smith", "Garcia", "Chen", "Khan", "Brown", "Rossi", "Kim", "Silva", "Martin"]
_CITIES = ["Boston", "Austin", "Pune", "London", "Toronto", "Denver", "Chicago", "Seattle"]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


DYNAMIC_VARIABLES: Dict[str, Callable[[], str]] = {
    "$guid": lambda: str(uuid.uuid4()),
    "$randomUUID": lambda: str(uuid.uuid4()),
    "$timestamp": lambda: str(int(time.time())),
    "$isoTimestamp": _iso_now,
    "$randomInt": lambda: str(random.randint(0, 1000)),
    "$randomBoolean": lambda: random.choice(["true", "false"]),
    "$randomAlphaNumeric": lambda: random.choice(string.ascii_lowercase + string.digits),
    "$randomFirstName": lambda: random.choice(_FIRST_NAMES),
    "$randomLastName": lambda: random.choice(_LAST_NAMES),
    "$randomFullName": lambda: f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}",
    "$randomEmail": lambda: f"{random.choice(_FIRST_NAMES).lower()}.{random.choice(_LAST_NAMES).lower()}{random.randint(1, 999)}@example.com",
    "$randomPhoneNumber": lambda: f"{random.randint(200, 999)}-{random.randint(200, 999)}-{random.randint(1000, 9999)}",
    "$randomCity": lambda: random.choice(_CITIES),
}

SCOPE_LABELS = {
    "local": "Local",
    "environment": "Environment",
    "collection": "Collection",
    "global": "Global",
    "dynamic": "Dynamic",
}


class RequestCancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

class VariableContext:
    ORDER = ("local", "environment", "collection", "global")

    def __init__(
        self,
        globals_: Optional[Dict[str, str]] = None,
        collection: Optional[Dict[str, str]] = None,
        environment: Optional[Dict[str, str]] = None,
        local: Optional[Dict[str, str]] = None,
        has_environment: bool = True,
    ) -> None:
        self.scopes: Dict[str, Dict[str, str]] = {
            "global": dict(globals_ or {}),
            "collection": dict(collection or {}),
            "environment": dict(environment or {}),
            "local": dict(local or {}),
        }
        self.has_environment = has_environment
        self.ops: List[Tuple[str, str, str, Optional[str]]] = []

    def lookup(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        for scope in self.ORDER:
            values = self.scopes[scope]
            if name in values:
                return values[name], scope
        generator = DYNAMIC_VARIABLES.get(name)
        if generator is not None:
            return generator(), "dynamic"
        return None, None

    def resolve(self, text: str, depth: int = 0) -> str:
        if not text or "{{" not in text:
            return text

        def replace(match: "re.Match") -> str:
            value, _ = self.lookup(match.group(1))
            return match.group(0) if value is None else value

        out = VAR_RE.sub(replace, text)
        if out != text and depth < 5 and VAR_RE.search(out):
            return self.resolve(out, depth + 1)
        return out

    def unresolved(self, text: str) -> List[str]:
        return [m.group(1) for m in VAR_RE.finditer(text or "") if self.lookup(m.group(1))[0] is None]

    def set(self, scope: str, key: str, value: Any) -> None:
        if scope == "environment" and not self.has_environment:
            scope = "global"
        text = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        self.scopes[scope][key] = text
        if scope != "local":
            self.ops.append((scope, "set", key, text))

    def unset(self, scope: str, key: str) -> None:
        if scope == "environment" and not self.has_environment:
            scope = "global"
        self.scopes[scope].pop(key, None)
        if scope != "local":
            self.ops.append((scope, "unset", key, None))

    def all_names(self) -> Dict[str, Tuple[str, str]]:
        merged: Dict[str, Tuple[str, str]] = {}
        for scope in reversed(self.ORDER):
            for key, value in self.scopes[scope].items():
                merged[key] = (value, scope)
        return merged


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------

@dataclass
class HttpSpec:
    method: str
    url: str
    headers: Dict[str, str]
    data: Any = None
    files: Any = None
    auth: Any = None
    body_kind: str = "none"  # none | raw | urlencoded | form | binary
    body_text: str = ""
    form_fields: List[Tuple[str, str, str]] = field(default_factory=list)
    basic_auth: Optional[Tuple[str, str, str]] = None


def effective_auth(auth: AuthConfig, inherited: Sequence[AuthConfig]) -> AuthConfig:
    if auth.type != "inherit":
        return auth
    for parent in inherited:
        if parent.type != "inherit":
            return parent
    return AuthConfig(type="noauth")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def encode_jwt(algorithm: str, secret: str, payload_json: str, secret_b64: bool) -> str:
    if not payload_json.strip():
        raise ValueError("JWT payload is required.")
    try:
        payload_obj = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JWT payload JSON: {exc}") from exc
    if not isinstance(payload_obj, dict):
        raise ValueError("JWT payload must be a JSON object.")

    alg_map = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}
    if algorithm not in alg_map:
        raise ValueError(f"Unsupported JWT algorithm: {algorithm}")

    header_b64 = _b64url(json.dumps({"alg": algorithm, "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload_obj, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

    if secret_b64:
        try:
            key = base64.b64decode(secret)
        except Exception as exc:
            raise ValueError(f"Invalid Base64 JWT secret: {exc}") from exc
    else:
        key = secret.encode("utf-8")

    signature = hmac.new(key, signing_input, alg_map[algorithm]).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def _find_header(headers: Dict[str, str], name: str) -> Optional[str]:
    lowered = name.lower()
    for key in headers:
        if key.lower() == lowered:
            return key
    return None


def _set_default_header(headers: Dict[str, str], name: str, value: str) -> None:
    if _find_header(headers, name) is None:
        headers[name] = value


def _set_header(headers: Dict[str, str], name: str, value: str) -> None:
    existing = _find_header(headers, name)
    if existing is not None:
        del headers[existing]
    headers[name] = value


def _append_query(url: str, pairs: List[Tuple[str, str]]) -> str:
    if not pairs:
        return url
    base, _, fragment = url.partition("#")
    sep = "&" if "?" in base else "?"
    out = f"{base}{sep}{urlencode(pairs)}"
    return f"{out}#{fragment}" if fragment else out


def _apply_auth(auth: AuthConfig, ctx: VariableContext, headers: Dict[str, str], query: List[Tuple[str, str]]):
    def val(key: str, default: str = "") -> str:
        return ctx.resolve(auth.get(key, default))

    auth_type = auth.type
    if auth_type == "apikey":
        key, value = val("key"), val("value")
        if key:
            if auth.get("in", "header") == "query":
                query.append((key, value))
            else:
                _set_header(headers, key, value)
        return None, None

    if auth_type == "bearer":
        token = val("token").strip()
        if token:
            _set_header(headers, "Authorization", f"Bearer {token}")
        return None, None

    if auth_type in ("basic", "digest"):
        username, password = val("username"), val("password")
        if not (username or password):
            return None, None
        if auth_type == "basic":
            return HTTPBasicAuth(username, password), (username, password, "basic")
        return HTTPDigestAuth(username, password), (username, password, "digest")

    if auth_type == "jwt":
        token = encode_jwt(
            auth.get("algorithm", "HS256"),
            val("secret"),
            val("payload", "{}"),
            auth.get("secret_b64") == "true",
        )
        if auth.get("add_to", "header") == "query":
            query.append((auth.get("query_key", "token"), token))
        else:
            prefix = val("header_prefix", "Bearer").strip()
            _set_header(headers, "Authorization", f"{prefix} {token}".strip())
        return None, None

    if auth_type == "oauth2":
        token = val("access_token").strip()
        if token:
            if auth.get("add_to", "header") == "query":
                query.append(("access_token", token))
            else:
                prefix = val("header_prefix", "Bearer").strip()
                _set_header(headers, "Authorization", f"{prefix} {token}".strip())
        return None, None

    return None, None


def _substitute_path_variables(raw_url: str, model: RequestModel, ctx: VariableContext) -> str:
    values = {kv.key: ctx.resolve(kv.value) for kv in model.path_variables if kv.key and kv.value}
    if not values:
        return raw_url
    path, sep, rest = raw_url.partition("?")

    def replace(match: "re.Match") -> str:
        return values.get(match.group(1), match.group(0))

    return PATH_VAR_RE.sub(replace, path) + sep + rest


def _absolute_url(url: str, ctx: VariableContext) -> str:
    if SCHEME_RE.match(url):
        return url
    if url.startswith("/"):
        base, _ = ctx.lookup("baseUrl")
        if base:
            if not SCHEME_RE.match(base):
                base = "https://" + base
            return urljoin(base.rstrip("/") + "/", url.lstrip("/"))
        raise ValueError("The URL is relative. Use a full URL or define a 'baseUrl' variable in your environment.")
    return "http://" + url


def build_request(
    model: RequestModel,
    ctx: VariableContext,
    settings: AppSettings,
    inherited_auths: Sequence[AuthConfig] = (),
) -> HttpSpec:
    method = (model.method or "GET").strip().upper()
    raw_url = model.url.strip()
    if not raw_url:
        raise ValueError("Enter a request URL to send the request.")

    url = ctx.resolve(_substitute_path_variables(raw_url, model, ctx)).strip()
    missing = ctx.unresolved(url)
    if missing:
        raise ValueError(
            "Unresolved variable{} in URL: {}.\nDefine {} in the active environment, collection or globals.".format(
                "s" if len(missing) > 1 else "",
                ", ".join("{{" + m + "}}" for m in missing),
                "them" if len(missing) > 1 else "it",
            )
        )
    url = _absolute_url(url, ctx)

    headers: Dict[str, str] = {}
    for kv in model.headers:
        if kv.enabled and kv.key.strip():
            _set_header(headers, ctx.resolve(kv.key.strip()), ctx.resolve(kv.value))

    query_extra: List[Tuple[str, str]] = []
    auth_obj, basic = _apply_auth(effective_auth(model.auth, inherited_auths), ctx, headers, query_extra)
    url = _append_query(url, query_extra)

    spec = HttpSpec(method=method, url=url, headers=headers, auth=auth_obj, basic_auth=basic)
    body = model.body

    if body.mode == "raw":
        text = ctx.resolve(body.raw)
        if text:
            spec.data = text.encode("utf-8")
            spec.body_kind = "raw"
            spec.body_text = text
            _set_default_header(headers, "Content-Type", RAW_CONTENT_TYPES.get(body.raw_language, "text/plain"))
    elif body.mode == "urlencoded":
        pairs = [(ctx.resolve(kv.key), ctx.resolve(kv.value)) for kv in body.urlencoded if kv.enabled and kv.key]
        if pairs:
            spec.data = pairs
            spec.body_kind = "urlencoded"
            spec.body_text = urlencode(pairs)
            spec.form_fields = [(k, v, "text") for k, v in pairs]
            _set_default_header(headers, "Content-Type", "application/x-www-form-urlencoded")
    elif body.mode == "form-data":
        files = []
        fields_out: List[Tuple[str, str, str]] = []
        for kv in body.form:
            if not (kv.enabled and kv.key):
                continue
            key = ctx.resolve(kv.key)
            value = ctx.resolve(kv.value)
            if kv.kind == "file":
                if not value or not os.path.isfile(value):
                    raise ValueError(f"form-data file for '{key}' was not found: {value or '(not selected)'}")
                with open(value, "rb") as handle:
                    files.append((key, (os.path.basename(value), handle.read())))
                fields_out.append((key, value, "file"))
            else:
                files.append((key, (None, value)))
                fields_out.append((key, value, "text"))
        if files:
            spec.files = files
            spec.body_kind = "form"
            spec.form_fields = fields_out
            existing = _find_header(headers, "Content-Type")
            if existing and "boundary=" not in headers[existing]:
                del headers[existing]  # requests generates the multipart boundary
    elif body.mode == "binary":
        path = ctx.resolve(body.binary_path).strip()
        if path:
            if not os.path.isfile(path):
                raise ValueError(f"Binary body file was not found: {path}")
            with open(path, "rb") as handle:
                spec.data = handle.read()
            spec.body_kind = "binary"
            spec.body_text = path
            _set_default_header(headers, "Content-Type", "application/octet-stream")
    elif body.mode == "graphql":
        query_text = ctx.resolve(body.graphql_query)
        variables_text = ctx.resolve(body.graphql_variables).strip()
        variables: Any = {}
        if variables_text:
            try:
                variables = json.loads(variables_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"GraphQL variables are not valid JSON: {exc}") from exc
        payload = json.dumps({"query": query_text, "variables": variables})
        spec.data = payload.encode("utf-8")
        spec.body_kind = "raw"
        spec.body_text = payload
        _set_default_header(headers, "Content-Type", "application/json")

    if settings.send_user_agent_header:
        _set_default_header(headers, "User-Agent", APP_USER_AGENT)
    _set_default_header(headers, "Accept", "*/*")
    if settings.send_no_cache_header:
        _set_default_header(headers, "Cache-Control", "no-cache")
        _set_default_header(headers, "Pragma", "no-cache")
    if settings.send_postman_token_header:
        _set_default_header(headers, "Postman-Token", str(uuid.uuid4()))

    return spec


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@dataclass
class SentRequest:
    method: str
    url: str
    headers: List[Tuple[str, str]]
    body_preview: str = ""


@dataclass
class ResponseData:
    status: int
    reason: str
    http_version: str
    url: str
    headers: List[Tuple[str, str]]
    cookies: List[Dict[str, str]]
    content: bytes
    truncated: bool
    body_size: int
    headers_size: int
    elapsed_ms: float
    ttfb_ms: float
    encoding: str
    content_type: str
    redirects: List[Tuple[int, str]] = field(default_factory=list)
    _text: Optional[str] = None

    @property
    def text(self) -> str:
        if self._text is None:
            try:
                self._text = self.content.decode(self.encoding or "utf-8", errors="replace")
            except LookupError:
                self._text = self.content.decode("utf-8", errors="replace")
        return self._text

    def json(self) -> Any:
        return json.loads(self.text)

    def header(self, name: str) -> Optional[str]:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    @property
    def is_json(self) -> bool:
        if "json" in self.content_type.lower():
            return True
        stripped = self.text.lstrip()[:1]
        if stripped in ("{", "["):
            try:
                json.loads(self.text)
                return True
            except Exception:
                return False
        return False


def request_proxies(settings: AppSettings) -> Optional[Dict[str, str]]:
    if not settings.use_custom_proxy:
        return None
    host = settings.proxy_host.strip()
    if not host:
        return None
    userinfo = ""
    if settings.proxy_auth_enabled and settings.proxy_username:
        userinfo = settings.proxy_username
        if settings.proxy_password:
            userinfo += f":{settings.proxy_password}"
        userinfo += "@"
    proxy_url = f"http://{userinfo}{host}:{settings.proxy_port}"
    proxies: Dict[str, str] = {}
    if settings.proxy_http_enabled:
        proxies["http"] = proxy_url
    if settings.proxy_https_enabled:
        proxies["https"] = proxy_url
    if proxies and settings.proxy_bypass.strip():
        proxies["no_proxy"] = settings.proxy_bypass.strip()
    return proxies or None


def _timeout(settings: AppSettings) -> Optional[float]:
    ms = max(0, int(settings.request_timeout_ms))
    return None if ms == 0 else ms / 1000.0


def new_session(settings: AppSettings) -> requests.Session:
    session = requests.Session()
    session.trust_env = settings.use_system_proxy or settings.respect_env_proxy
    return session


def execute(
    spec: HttpSpec,
    settings: AppSettings,
    session: Optional[requests.Session] = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Tuple[ResponseData, SentRequest]:
    own_session = session is None
    session = session or new_session(settings)
    started = time.perf_counter()
    try:
        response = session.request(
            method=spec.method,
            url=spec.url,
            headers=spec.headers,
            data=spec.data,
            files=spec.files,
            auth=spec.auth,
            timeout=_timeout(settings),
            proxies=request_proxies(settings),
            verify=settings.ssl_verification,
            allow_redirects=settings.follow_redirects,
            stream=True,
        )
        ttfb = response.elapsed.total_seconds() * 1000.0
        max_bytes = max(0, int(settings.max_response_size_mb)) * 1024 * 1024
        chunks: List[bytes] = []
        total = 0
        truncated = False
        for chunk in response.iter_content(65536):
            if cancelled():
                raise RequestCancelled()
            chunks.append(chunk)
            total += len(chunk)
            if max_bytes and total > max_bytes:
                truncated = True
                break
        content = b"".join(chunks)
        if truncated:
            content = content[:max_bytes]
        elapsed = (time.perf_counter() - started) * 1000.0

        content_type = response.headers.get("Content-Type", "")
        charset = re.search(r"charset=([\w\-.:]+)", content_type, re.IGNORECASE)
        encoding = charset.group(1).strip("\"'") if charset else "utf-8"

        cookies: List[Dict[str, str]] = []
        for cookie in session.cookies:
            expires = "Session"
            if cookie.expires:
                try:
                    expires = datetime.fromtimestamp(cookie.expires, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
                except Exception:
                    expires = str(cookie.expires)
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value or "",
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                    "expires": expires,
                    "secure": "true" if cookie.secure else "false",
                    "httponly": "true" if cookie.has_nonstandard_attr("HttpOnly") else "false",
                }
            )

        version = {10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2"}.get(getattr(response.raw, "version", 11), "HTTP/1.1")
        headers = list(response.headers.items())
        data = ResponseData(
            status=response.status_code,
            reason=response.reason or "",
            http_version=version,
            url=response.url,
            headers=headers,
            cookies=cookies,
            content=content,
            truncated=truncated,
            body_size=total if not truncated else len(content),
            headers_size=sum(len(k) + len(v) + 4 for k, v in headers),
            elapsed_ms=elapsed,
            ttfb_ms=ttfb,
            encoding=encoding,
            content_type=content_type,
            redirects=[(r.status_code, r.url) for r in response.history],
        )
        prepared = response.request
        sent = SentRequest(
            method=prepared.method or spec.method,
            url=prepared.url or spec.url,
            headers=list(prepared.headers.items()),
            body_preview=_body_preview(spec),
        )
        response.close()
        return data, sent
    finally:
        if own_session:
            session.close()


def _body_preview(spec: HttpSpec) -> str:
    if spec.body_kind == "form":
        return "\n".join(f"{k}: {'<file ' + v + '>' if kind == 'file' else v}" for k, v, kind in spec.form_fields)
    if spec.body_kind == "binary":
        return f"<binary file {spec.body_text}>"
    text = spec.body_text or ""
    return text if len(text) <= 20000 else text[:20000] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Tests and variable extraction
# ---------------------------------------------------------------------------

TEST_SOURCES = [
    ("status", "Status code"),
    ("time", "Response time (ms)"),
    ("json", "JSON body"),
    ("header", "Header"),
    ("body", "Body text"),
    ("size", "Body size (bytes)"),
]
TEST_OPS = [
    ("equals", "equals"),
    ("not_equals", "not equals"),
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("gt", "greater than"),
    ("lt", "less than"),
    ("exists", "exists"),
    ("not_exists", "does not exist"),
    ("regex", "matches regex"),
    ("is_type", "is type"),
]
EXTRACT_SOURCES = [
    ("json", "JSON body"),
    ("header", "Header"),
    ("status", "Status code"),
    ("regex", "Body regex"),
    ("cookie", "Cookie"),
]
VARIABLE_SCOPES = [("environment", "Environment"), ("collection", "Collection"), ("global", "Global")]


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    origin: str = "tests"  # tests | script


_PATH_TOKEN = re.compile(r"""\.?([^.\[\]]+)|\[(\d+|\*|'[^']*'|"[^"]*")\]""")


def json_path_get(obj: Any, path: str) -> Tuple[bool, Any]:
    """Tiny JSON path: `$.data.items[0].id`, `data.items.0.id`, `items[*].id`."""
    path = (path or "").strip()
    if path in ("", "$"):
        return True, obj
    if path.startswith("$"):
        path = path[1:]

    current: List[Any] = [obj]
    wildcard = False
    pos = 0
    while pos < len(path):
        match = _PATH_TOKEN.match(path, pos)
        if not match or match.end() == pos:
            return False, None
        pos = match.end()
        token = match.group(1) if match.group(1) is not None else match.group(2)
        if token.startswith(("'", '"')):
            token = token[1:-1]

        next_values: List[Any] = []
        for value in current:
            if token == "*":
                wildcard = True
                if isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict):
                    next_values.extend(value.values())
            elif isinstance(value, list):
                if token.lstrip("-").isdigit() and -len(value) <= int(token) < len(value):
                    next_values.append(value[int(token)])
            elif isinstance(value, dict) and token in value:
                next_values.append(value[token])
        if not next_values:
            return False, None
        current = next_values

    return True, current if wildcard else current[0]


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _actual_value(source: str, prop: str, resp: ResponseData) -> Tuple[bool, Any]:
    if source == "status":
        return True, resp.status
    if source == "time":
        return True, round(resp.elapsed_ms)
    if source == "size":
        return True, resp.body_size
    if source == "body":
        return True, resp.text
    if source == "header":
        value = resp.header(prop)
        return value is not None, value
    if source == "json":
        try:
            data = resp.json()
        except Exception:
            return False, None
        return json_path_get(data, prop)
    return False, None


def test_name(rule: TestRule) -> str:
    source_label = dict(TEST_SOURCES).get(rule.source, rule.source)
    target = f"{source_label} {rule.prop}".strip() if rule.source in ("json", "header") else source_label
    op_label = dict(TEST_OPS).get(rule.op, rule.op)
    if rule.op in ("exists", "not_exists"):
        return f"{target} {op_label}"
    return f"{target} {op_label} {rule.expected}".strip()


def evaluate_rule(rule: TestRule, resp: ResponseData) -> TestResult:
    name = test_name(rule)
    found, actual = _actual_value(rule.source, rule.prop, resp)
    expected = rule.expected
    op = rule.op

    if op == "exists":
        return TestResult(name, found, "" if found else "Value not found")
    if op == "not_exists":
        return TestResult(name, not found, "" if not found else f"Found {_to_text(actual)}")
    if not found:
        return TestResult(name, False, "Value not found in response")

    actual_text = _to_text(actual)
    try:
        if op in ("equals", "not_equals"):
            a_num, e_num = _to_number(actual), _to_number(expected)
            same = (a_num == e_num) if (a_num is not None and e_num is not None and not isinstance(actual, str)) else actual_text == expected
            passed = same if op == "equals" else not same
            return TestResult(name, passed, "" if passed else f"Expected {'' if op == 'equals' else 'not '}{expected!r}, got {actual_text!r}")
        if op in ("contains", "not_contains"):
            if isinstance(actual, list):
                has = any(_to_text(item) == expected for item in actual) or expected in actual_text
            else:
                has = expected in actual_text
            passed = has if op == "contains" else not has
            return TestResult(name, passed, "" if passed else f"{'Did not find' if op == 'contains' else 'Found'} {expected!r}")
        if op in ("gt", "lt"):
            a_num, e_num = _to_number(actual), _to_number(expected)
            if a_num is None or e_num is None:
                return TestResult(name, False, f"Cannot compare {actual_text!r} with {expected!r} as numbers")
            passed = a_num > e_num if op == "gt" else a_num < e_num
            return TestResult(name, passed, "" if passed else f"Actual value was {actual_text}")
        if op == "regex":
            passed = re.search(expected, actual_text) is not None
            return TestResult(name, passed, "" if passed else f"{actual_text[:120]!r} does not match /{expected}/")
        if op == "is_type":
            actual_type = _json_type(actual)
            passed = actual_type == expected.strip().lower()
            return TestResult(name, passed, "" if passed else f"Type was {actual_type}")
    except re.error as exc:
        return TestResult(name, False, f"Invalid regex: {exc}")
    return TestResult(name, False, f"Unknown operator {op}")


def run_extractors(rules: Sequence[ExtractRule], resp: ResponseData, ctx: VariableContext, logs: List[Tuple[str, str]]) -> None:
    for rule in rules:
        if not (rule.enabled and rule.variable.strip()):
            continue
        value: Optional[str] = None
        if rule.source == "status":
            value = str(resp.status)
        elif rule.source == "header":
            value = resp.header(rule.path)
        elif rule.source == "cookie":
            value = next((c["value"] for c in resp.cookies if c["name"] == rule.path), None)
        elif rule.source == "regex":
            try:
                match = re.search(rule.path, resp.text)
            except re.error as exc:
                logs.append(("warn", f"Extract '{rule.variable}': invalid regex ({exc})"))
                continue
            if match:
                value = match.group(1) if match.groups() else match.group(0)
        else:
            try:
                found, raw = json_path_get(resp.json(), rule.path)
            except Exception:
                found, raw = False, None
            if found:
                value = raw if isinstance(raw, str) else _to_text(raw)

        if value is None:
            logs.append(("warn", f"Extract '{rule.variable}': nothing matched {rule.source} '{rule.path}'"))
            continue
        ctx.set(rule.scope, rule.variable.strip(), value)
        logs.append(("info", f"Set {rule.scope} variable '{rule.variable.strip()}'"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    request: Optional[SentRequest] = None
    response: Optional[ResponseData] = None
    tests: List[TestResult] = field(default_factory=list)
    logs: List[Tuple[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    cancelled: bool = False
    ops: List[Tuple[str, str, str, Optional[str]]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    method: str = ""
    url: str = ""


def run_request(
    model: RequestModel,
    ctx: VariableContext,
    settings: AppSettings,
    inherited_auths: Sequence[AuthConfig] = (),
    session: Optional[requests.Session] = None,
    cancelled: Callable[[], bool] = lambda: False,
    info: Optional[Dict[str, Any]] = None,
) -> RunResult:
    from .scripting import run_script

    result = RunResult(method=model.method, url=model.url)
    working = model.clone()
    info = dict(info or {})
    info.setdefault("requestName", model.name)

    if working.pre_script.strip():
        run_script(working.pre_script, "prerequest", ctx, working, None, result, info)

    try:
        spec = build_request(working, ctx, settings, inherited_auths)
    except (ValueError, OSError) as exc:
        result.error = str(exc)
        result.ops = list(ctx.ops)
        return result

    result.method, result.url = spec.method, spec.url
    try:
        response, sent = execute(spec, settings, session, cancelled)
    except Exception as exc:  # noqa: BLE001 - surface every transport error to the UI
        if cancelled() or isinstance(exc, RequestCancelled):
            result.cancelled = True
        else:
            result.error = describe_error(exc, settings)
        result.request = SentRequest(spec.method, spec.url, list(spec.headers.items()), _body_preview(spec))
        result.ops = list(ctx.ops)
        return result

    result.request = sent
    result.response = response
    for rule in working.tests:
        if rule.enabled:
            result.tests.append(evaluate_rule(rule, response))
    run_extractors(working.extractors, response, ctx, result.logs)
    if working.post_script.strip():
        run_script(working.post_script, "test", ctx, working, response, result, info)
    result.ops = list(ctx.ops)
    return result


def describe_error(exc: Exception, settings: AppSettings) -> str:
    if isinstance(exc, requests.exceptions.SSLError):
        return (
            f"SSL error: {exc}\n\nIf you trust this host, turn off SSL certificate verification "
            "in Settings > General."
        )
    if isinstance(exc, requests.exceptions.ProxyError):
        return f"Proxy error: {exc}\n\nCheck the proxy configuration in Settings > Proxy."
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout)):
        return f"The request timed out after {settings.request_timeout_ms} ms.\n\n{exc}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Could not connect to the server.\n\n{exc}"
    if isinstance(exc, requests.exceptions.InvalidURL):
        return f"Invalid URL: {exc}"
    return f"{type(exc).__name__}: {exc}"


def fetch_oauth2_token(auth: AuthConfig, ctx: VariableContext, settings: AppSettings) -> Dict[str, Any]:
    """Run an OAuth 2.0 client-credentials or password grant and return the token JSON."""

    def val(key: str, default: str = "") -> str:
        return ctx.resolve(auth.get(key, default)).strip()

    token_url = val("token_url")
    if not token_url:
        raise ValueError("Access Token URL is required.")
    grant = auth.get("grant_type", "client_credentials")
    payload: Dict[str, str] = {"grant_type": grant}
    if grant == "password":
        payload["username"] = val("username")
        payload["password"] = ctx.resolve(auth.get("password"))
    for key in ("scope", "audience"):
        if val(key):
            payload[key] = val(key)

    client_id, client_secret = val("client_id"), ctx.resolve(auth.get("client_secret"))
    basic = None
    if auth.get("client_auth", "header") == "body":
        payload["client_id"] = client_id
        payload["client_secret"] = client_secret
    elif client_id or client_secret:
        basic = HTTPBasicAuth(client_id, client_secret)

    with new_session(settings) as session:
        response = session.post(
            token_url,
            data=payload,
            auth=basic,
            headers={"Accept": "application/json"},
            timeout=_timeout(settings),
            proxies=request_proxies(settings),
            verify=settings.ssl_verification,
            allow_redirects=settings.follow_redirects,
        )
    if response.status_code >= 400:
        raise ValueError(f"Token endpoint returned {response.status_code} {response.reason}:\n{response.text[:800]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(f"Token endpoint did not return JSON:\n{response.text[:800]}") from exc
    if not data.get("access_token"):
        raise ValueError(f"Response did not contain access_token:\n{json.dumps(data, indent=2)[:800]}")
    return data
