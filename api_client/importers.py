"""Import/export: Postman collections & environments, OpenAPI 3 / Swagger 2 and cURL."""
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    AuthConfig,
    Collection,
    Environment,
    Folder,
    KeyValue,
    RequestBody,
    RequestModel,
    HTTP_METHODS,
    Node,
)

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


@dataclass
class ImportResult:
    collections: List[Collection] = field(default_factory=list)
    environments: List[Environment] = field(default_factory=list)
    requests: List[RequestModel] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.collections or self.environments or self.requests)


def import_text(text: str, source: str = "") -> ImportResult:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Nothing to import.")
    if stripped.lower().startswith("curl"):
        return ImportResult(requests=[parse_curl(stripped)])
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        data = _try_yaml(stripped)
        if data is None:
            raise ValueError(f"The content is not valid JSON or a cURL command.\n{exc}") from exc
    return import_data(data, source)


def import_file(path: Path) -> ImportResult:
    return import_text(path.read_text(encoding="utf-8-sig"), str(path))


def _try_yaml(text: str) -> Optional[Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        return yaml.safe_load(text)
    except Exception:
        return None


def import_data(data: Any, source: str = "") -> ImportResult:
    if not isinstance(data, dict):
        raise ValueError("Unsupported format: expected a JSON object.")
    if isinstance(data.get("paths"), dict) and ("openapi" in data or "swagger" in data):
        return ImportResult(collections=[import_openapi(data, source)])
    if "info" in data and "item" in data:
        result = ImportResult()
        result.collections.append(import_postman_collection(data, source, result.notes))
        return result
    if isinstance(data.get("values"), list) and "name" in data:
        return ImportResult(environments=[import_postman_environment(data)])
    if data.get("type") == "collection" and "items" in data:
        coll = Collection.from_dict(data)
        return ImportResult(collections=[coll])
    raise ValueError("Unrecognised file. Supported: Postman collection v2.0/v2.1, Postman environment, OpenAPI 3.x / Swagger 2.0 (JSON), cURL.")


# ---------------------------------------------------------------------------
# Postman
# ---------------------------------------------------------------------------

def _desc(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("content", ""))
    return "" if value is None else str(value)


def _pm_kv(item: Dict[str, Any], kind: str = "text") -> KeyValue:
    value = item.get("value", item.get("src", ""))
    if isinstance(value, list):
        value = value[0] if value else ""
    return KeyValue(
        key=str(item.get("key", "")),
        value="" if value is None else str(value),
        description=_desc(item.get("description")),
        enabled=not item.get("disabled", False),
        kind=kind,
    )


def _pm_auth(auth: Any, default_type: str) -> AuthConfig:
    if not isinstance(auth, dict) or not auth.get("type"):
        return AuthConfig(type=default_type)
    auth_type = str(auth["type"]).lower()
    raw = auth.get(auth_type, {})
    values: Dict[str, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and "key" in entry:
                val = entry.get("value", "")
                values[str(entry["key"])] = val if isinstance(val, str) else json.dumps(val)
    elif isinstance(raw, dict):
        values = {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in raw.items()}

    if auth_type == "noauth":
        return AuthConfig(type="noauth")
    if auth_type == "bearer":
        return AuthConfig("bearer", {"token": values.get("token", "")})
    if auth_type in ("basic", "digest"):
        return AuthConfig(auth_type, {"username": values.get("username", ""), "password": values.get("password", "")})
    if auth_type == "apikey":
        return AuthConfig("apikey", {"key": values.get("key", "X-API-Key"), "value": values.get("value", ""), "in": values.get("in", "header")})
    if auth_type == "jwt":
        return AuthConfig(
            "jwt",
            {
                "algorithm": values.get("algorithm", "HS256"),
                "secret": values.get("secret", ""),
                "secret_b64": "true" if str(values.get("isSecretBase64Encoded", "")).lower() == "true" else "false",
                "payload": values.get("payload", "{}"),
                "add_to": "query" if values.get("addTokenTo") == "queryParam" else "header",
                "header_prefix": values.get("headerPrefix", "Bearer"),
                "query_key": values.get("queryParamKey", "token"),
            },
        )
    if auth_type == "oauth2":
        grant = values.get("grant_type", "client_credentials")
        return AuthConfig(
            "oauth2",
            {
                "access_token": values.get("accessToken", ""),
                "header_prefix": values.get("headerPrefix", "Bearer"),
                "add_to": "query" if values.get("addTokenTo") == "queryParams" else "header",
                "grant_type": "password" if grant == "password_credentials" else "client_credentials",
                "token_url": values.get("accessTokenUrl", values.get("tokenUrl", "")),
                "client_id": values.get("clientId", ""),
                "client_secret": values.get("clientSecret", ""),
                "scope": values.get("scope", ""),
                "username": values.get("username", ""),
                "password": values.get("password", ""),
                "client_auth": "body" if values.get("client_authentication") == "body" else "header",
            },
        )
    return AuthConfig(type=default_type)


def _pm_scripts(events: Any, notes: List[str]) -> Tuple[str, str]:
    pre, post = "", ""
    if not isinstance(events, list):
        return pre, post
    for event in events:
        if not isinstance(event, dict):
            continue
        script = event.get("script") or {}
        lines = script.get("exec", [])
        code = "\n".join(lines) if isinstance(lines, list) else str(lines or "")
        if not code.strip():
            continue
        if script.get("type") == "text/x-python":
            text = code
        else:
            text = "# Imported Postman JavaScript (not executed - port it to Python to use it):\n" + "\n".join(
                "# " + line for line in code.splitlines()
            )
            notes.append("js-script")
        if event.get("listen") == "prerequest":
            pre = text
        elif event.get("listen") == "test":
            post = text
    return pre, post


def _pm_url(url: Any) -> Tuple[str, List[KeyValue], List[KeyValue]]:
    if isinstance(url, str):
        return url, _query_from_url(url), []
    if not isinstance(url, dict):
        return "", [], []
    params = [_pm_kv(q) for q in url.get("query", []) or [] if isinstance(q, dict)]
    path_vars = [_pm_kv(v) for v in url.get("variable", []) or [] if isinstance(v, dict)]
    raw = str(url.get("raw", "")).strip()
    if not raw:
        protocol = url.get("protocol", "")
        host = url.get("host", "")
        host = ".".join(str(h) for h in host) if isinstance(host, list) else str(host)
        port = f":{url['port']}" if url.get("port") else ""
        path = url.get("path", "")
        path = "/".join(str(p) for p in path) if isinstance(path, list) else str(path).lstrip("/")
        raw = f"{protocol + '://' if protocol else ''}{host}{port}"
        if path:
            raw += "/" + path
        enabled = [p for p in params if p.enabled]
        if enabled:
            raw += "?" + "&".join(f"{p.key}={p.value}" for p in enabled)
    return raw, params or _query_from_url(raw), path_vars


def _query_from_url(url: str) -> List[KeyValue]:
    if "?" not in url:
        return []
    query = url.split("?", 1)[1].split("#", 1)[0]
    out = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out.append(KeyValue(key=key, value=value))
    return out


def _pm_request(name: str, item: Dict[str, Any], notes: List[str]) -> RequestModel:
    req = item.get("request")
    if isinstance(req, str):
        req = {"url": req, "method": "GET"}
    req = req or {}
    url, params, path_vars = _pm_url(req.get("url", ""))

    headers: List[KeyValue] = []
    raw_headers = req.get("header", [])
    if isinstance(raw_headers, str):
        for line in raw_headers.splitlines():
            key, _, value = line.partition(":")
            if key.strip():
                headers.append(KeyValue(key=key.strip(), value=value.strip()))
    else:
        headers = [_pm_kv(h) for h in raw_headers or [] if isinstance(h, dict)]

    body = RequestBody()
    raw_body = req.get("body") if isinstance(req.get("body"), dict) else {}
    mode = raw_body.get("mode", "")
    if mode == "raw":
        body.mode = "raw"
        body.raw = str(raw_body.get("raw", ""))
        language = (((raw_body.get("options") or {}).get("raw") or {}).get("language") or "").lower()
        if not language:
            ctype = next((h.value for h in headers if h.key.lower() == "content-type"), "")
            language = "json" if "json" in ctype or body.raw.strip()[:1] in "{[" else "text"
        body.raw_language = language if language in ("json", "text", "xml", "html", "javascript") else "text"
    elif mode == "urlencoded":
        body.mode = "urlencoded"
        body.urlencoded = [_pm_kv(x) for x in raw_body.get("urlencoded", []) or [] if isinstance(x, dict)]
    elif mode == "formdata":
        body.mode = "form-data"
        body.form = [_pm_kv(x, "file" if x.get("type") == "file" else "text") for x in raw_body.get("formdata", []) or [] if isinstance(x, dict)]
    elif mode == "file":
        body.mode = "binary"
        src = (raw_body.get("file") or {}).get("src", "")
        body.binary_path = src if isinstance(src, str) else ""
    elif mode == "graphql":
        body.mode = "graphql"
        graphql = raw_body.get("graphql") or {}
        body.graphql_query = str(graphql.get("query", ""))
        variables = graphql.get("variables", "")
        body.graphql_variables = variables if isinstance(variables, str) else json.dumps(variables, indent=2)

    pre, post = _pm_scripts(item.get("event"), notes)
    return RequestModel(
        name=name,
        method=str(req.get("method", "GET")).upper(),
        url=url,
        params=params,
        path_variables=path_vars,
        headers=headers,
        body=body,
        auth=_pm_auth(req.get("auth"), "inherit"),
        description=_desc(req.get("description") or item.get("description")),
        pre_script=pre,
        post_script=post,
    )


def _pm_items(items: Any, notes: List[str]) -> List[Node]:
    nodes: List[Node] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "Untitled"))
        if isinstance(item.get("item"), list):
            folder = Folder(name=name, description=_desc(item.get("description")), auth=_pm_auth(item.get("auth"), "inherit"))
            folder.items = _pm_items(item["item"], notes)
            nodes.append(folder)
        elif "request" in item:
            nodes.append(_pm_request(name, item, notes))
    return nodes


def import_postman_collection(data: Dict[str, Any], source: str = "", notes: Optional[List[str]] = None) -> Collection:
    notes = notes if notes is not None else []
    info = data.get("info") or {}
    coll = Collection(
        name=str(info.get("name") or Path(source).stem or "Imported Collection"),
        description=_desc(info.get("description")),
        auth=_pm_auth(data.get("auth"), "noauth"),
        variables=[_pm_kv(v, "default") for v in data.get("variable", []) or [] if isinstance(v, dict)],
        source=source,
    )
    coll.items = _pm_items(data.get("item"), notes)
    pre, post = _pm_scripts(data.get("event"), [])
    if pre or post:
        notes.append("collection-script")
    _restore_extensions(coll.items, [i for i in data.get("item") or [] if isinstance(i, dict) and ("item" in i or "request" in i)])
    return coll


def import_postman_environment(data: Dict[str, Any]) -> Environment:
    variables = []
    for entry in data.get("values", []):
        if not isinstance(entry, dict):
            continue
        variables.append(
            KeyValue(
                key=str(entry.get("key", "")),
                value="" if entry.get("value") is None else str(entry.get("value")),
                enabled=entry.get("enabled", True) is not False,
                kind="secret" if entry.get("type") == "secret" else "default",
            )
        )
    return Environment(name=str(data.get("name") or "Imported Environment"), variables=variables)


def _export_kv(kv: KeyValue, with_type: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"key": kv.key, "value": kv.value}
    if kv.description:
        out["description"] = kv.description
    if not kv.enabled:
        out["disabled"] = True
    if with_type:
        out["type"] = kv.kind if kv.kind in ("text", "file") else "text"
        if kv.kind == "file":
            out.pop("value", None)
            out["src"] = kv.value
    return out


def _export_auth(auth: AuthConfig) -> Optional[Dict[str, Any]]:
    t = auth.type
    if t == "inherit":
        return None
    if t == "noauth":
        return {"type": "noauth"}

    def pairs(mapping: Dict[str, str]) -> List[Dict[str, str]]:
        return [{"key": k, "value": v, "type": "string"} for k, v in mapping.items()]

    d = auth.data
    if t == "bearer":
        return {"type": "bearer", "bearer": pairs({"token": d.get("token", "")})}
    if t in ("basic", "digest"):
        return {"type": t, t: pairs({"username": d.get("username", ""), "password": d.get("password", "")})}
    if t == "apikey":
        return {"type": "apikey", "apikey": pairs({"key": d.get("key", ""), "value": d.get("value", ""), "in": d.get("in", "header")})}
    if t == "jwt":
        return {
            "type": "jwt",
            "jwt": pairs(
                {
                    "algorithm": d.get("algorithm", "HS256"),
                    "secret": d.get("secret", ""),
                    "isSecretBase64Encoded": d.get("secret_b64", "false"),
                    "payload": d.get("payload", "{}"),
                    "addTokenTo": "queryParam" if d.get("add_to") == "query" else "header",
                    "headerPrefix": d.get("header_prefix", "Bearer"),
                    "queryParamKey": d.get("query_key", "token"),
                }
            ),
        }
    if t == "oauth2":
        return {
            "type": "oauth2",
            "oauth2": pairs(
                {
                    "accessToken": d.get("access_token", ""),
                    "headerPrefix": d.get("header_prefix", "Bearer"),
                    "addTokenTo": "queryParams" if d.get("add_to") == "query" else "header",
                    "grant_type": "password_credentials" if d.get("grant_type") == "password" else "client_credentials",
                    "accessTokenUrl": d.get("token_url", ""),
                    "clientId": d.get("client_id", ""),
                    "clientSecret": d.get("client_secret", ""),
                    "scope": d.get("scope", ""),
                    "username": d.get("username", ""),
                    "password": d.get("password", ""),
                    "client_authentication": d.get("client_auth", "header"),
                }
            ),
        }
    return None


def _export_request(req: RequestModel) -> Dict[str, Any]:
    raw_url = req.url
    base = raw_url.split("?", 1)[0]
    url: Dict[str, Any] = {"raw": raw_url}
    match = re.match(r"^(\w+)://([^/]+)(/.*)?$", base)
    if match:
        url["protocol"] = match.group(1)
        url["host"] = match.group(2).split(".")
        url["path"] = [p for p in (match.group(3) or "").split("/") if p]
    else:
        parts = [p for p in base.split("/") if p]
        if parts:
            url["host"] = [parts[0]]
            url["path"] = parts[1:]
    if req.params:
        url["query"] = [_export_kv(kv) for kv in req.params]
    if req.path_variables:
        url["variable"] = [_export_kv(kv) for kv in req.path_variables]

    request: Dict[str, Any] = {
        "method": req.method,
        "header": [_export_kv(kv) for kv in req.headers],
        "url": url,
    }
    if req.description:
        request["description"] = req.description
    auth = _export_auth(req.auth)
    if auth:
        request["auth"] = auth

    b = req.body
    if b.mode == "raw":
        request["body"] = {"mode": "raw", "raw": b.raw, "options": {"raw": {"language": b.raw_language}}}
    elif b.mode == "urlencoded":
        request["body"] = {"mode": "urlencoded", "urlencoded": [_export_kv(kv) for kv in b.urlencoded]}
    elif b.mode == "form-data":
        request["body"] = {"mode": "formdata", "formdata": [_export_kv(kv, True) for kv in b.form]}
    elif b.mode == "binary":
        request["body"] = {"mode": "file", "file": {"src": b.binary_path}}
    elif b.mode == "graphql":
        request["body"] = {"mode": "graphql", "graphql": {"query": b.graphql_query, "variables": b.graphql_variables}}

    item: Dict[str, Any] = {"name": req.name, "request": request, "response": []}
    events = []
    if req.pre_script.strip():
        events.append({"listen": "prerequest", "script": {"type": "text/x-python", "exec": req.pre_script.splitlines()}})
    if req.post_script.strip():
        events.append({"listen": "test", "script": {"type": "text/x-python", "exec": req.post_script.splitlines()}})
    if events:
        item["event"] = events
    if req.tests or req.extractors:
        item["x-apiclient"] = {
            "tests": [t.to_dict() for t in req.tests],
            "extractors": [e.to_dict() for e in req.extractors],
        }
    return item


def _export_items(nodes: List[Node]) -> List[Dict[str, Any]]:
    out = []
    for node in nodes:
        if isinstance(node, Folder):
            entry: Dict[str, Any] = {"name": node.name, "item": _export_items(node.items)}
            if node.description:
                entry["description"] = node.description
            auth = _export_auth(node.auth)
            if auth:
                entry["auth"] = auth
            out.append(entry)
        else:
            out.append(_export_request(node))
    return out


def export_postman_collection(coll: Collection) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "info": {"name": coll.name, "description": coll.description, "schema": POSTMAN_SCHEMA},
        "item": _export_items(coll.items),
        "variable": [{"key": kv.key, "value": kv.value, **({"disabled": True} if not kv.enabled else {})} for kv in coll.variables],
    }
    auth = _export_auth(coll.auth)
    if auth:
        data["auth"] = auth
    return data


def export_postman_environment(env: Environment) -> Dict[str, Any]:
    return {
        "name": env.name,
        "values": [
            {"key": kv.key, "value": kv.value, "type": "secret" if kv.kind == "secret" else "default", "enabled": kv.enabled}
            for kv in env.variables
        ],
        "_postman_variable_scope": "environment",
    }


def _restore_extensions(nodes: List[Node], items: List[Dict[str, Any]]) -> None:
    """Re-attach no-code tests/extractors saved under `x-apiclient` by our exporter."""
    from .models import ExtractRule, TestRule

    for node, item in zip(nodes, items):
        if isinstance(node, Folder):
            _restore_extensions(node.items, item.get("item") or [])
        elif isinstance(item.get("x-apiclient"), dict):
            ext = item["x-apiclient"]
            node.tests = [TestRule.from_dict(x) for x in ext.get("tests", []) if isinstance(x, dict)]
            node.extractors = [ExtractRule.from_dict(x) for x in ext.get("extractors", []) if isinstance(x, dict)]



# ---------------------------------------------------------------------------
# OpenAPI / Swagger
# ---------------------------------------------------------------------------

class _OpenApi:
    def __init__(self, doc: Dict[str, Any]) -> None:
        self.doc = doc
        self.v2 = "swagger" in doc

    def deref(self, node: Any, depth: int = 0) -> Any:
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 20:
            ref = str(node["$ref"])
            if not ref.startswith("#/"):
                return {}
            target: Any = self.doc
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                target = target.get(part, {}) if isinstance(target, dict) else {}
            node = target
            seen += 1
        return node

    def example(self, schema: Any, depth: int = 0) -> Any:
        schema = self.deref(schema)
        if not isinstance(schema, dict) or depth > 8:
            return None
        for key in ("example", "default"):
            if key in schema:
                return schema[key]
        if isinstance(schema.get("examples"), list) and schema["examples"]:
            return schema["examples"][0]
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return schema["enum"][0]
        if "const" in schema:
            return schema["const"]
        if isinstance(schema.get("allOf"), list):
            merged: Dict[str, Any] = {}
            for part in schema["allOf"]:
                value = self.example(part, depth + 1)
                if isinstance(value, dict):
                    merged.update(value)
            return merged
        for key in ("oneOf", "anyOf"):
            options = schema.get(key)
            if isinstance(options, list) and options:
                non_null = [o for o in options if self.deref(o).get("type") != "null"] or options
                return self.example(non_null[0], depth + 1)

        stype = schema.get("type")
        if isinstance(stype, list):
            stype = next((t for t in stype if t != "null"), stype[0] if stype else None)
        if stype == "object" or (stype is None and "properties" in schema):
            props = schema.get("properties") or {}
            return {name: self.example(prop, depth + 1) for name, prop in props.items()}
        if stype == "array":
            item = self.example(schema.get("items", {}), depth + 1)
            return [] if item is None else [item]
        if stype == "integer":
            return schema.get("minimum", 0)
        if stype == "number":
            return schema.get("minimum", 0.0)
        if stype == "boolean":
            return True
        if stype == "string":
            fmt = schema.get("format", "")
            return {
                "date-time": "2024-01-01T00:00:00Z",
                "date": "2024-01-01",
                "time": "12:00:00",
                "email": "user@example.com",
                "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "uri": "https://example.com",
                "url": "https://example.com",
                "ipv4": "192.168.0.1",
                "password": "********",
                "binary": "",
                "byte": "",
            }.get(fmt, "string")
        return None

    def base_url(self) -> str:
        if self.v2:
            host = self.doc.get("host", "")
            if not host:
                return self.doc.get("basePath", "") or ""
            schemes = self.doc.get("schemes") or ["https"]
            return f"{schemes[0]}://{host}{self.doc.get('basePath', '')}".rstrip("/")
        servers = self.doc.get("servers") or []
        if servers and isinstance(servers[0], dict):
            url = str(servers[0].get("url", ""))
            for name, var in (servers[0].get("variables") or {}).items():
                if isinstance(var, dict):
                    url = url.replace("{" + name + "}", str(var.get("default", "")))
            return url.rstrip("/")
        return ""

    def security_auth(self, requirement: Any) -> Optional[AuthConfig]:
        if requirement is None:
            return None
        if isinstance(requirement, list) and not requirement:
            return AuthConfig("noauth")
        if not isinstance(requirement, list):
            return None
        schemes = (self.doc.get("securityDefinitions") if self.v2 else (self.doc.get("components") or {}).get("securitySchemes")) or {}
        for entry in requirement:
            if not isinstance(entry, dict) or not entry:
                return AuthConfig("noauth")
            for name in entry:
                scheme = self.deref(schemes.get(name, {}))
                stype = str(scheme.get("type", "")).lower()
                if stype == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                    return AuthConfig("bearer", {"token": "{{bearerToken}}"})
                if stype in ("http", "basic") and str(scheme.get("scheme", "basic")).lower() == "basic":
                    return AuthConfig("basic", {"username": "{{username}}", "password": "{{password}}"})
                if stype == "apikey":
                    return AuthConfig("apikey", {"key": scheme.get("name", "X-API-Key"), "value": "{{apiKey}}", "in": "query" if scheme.get("in") == "query" else "header"})
                if stype == "oauth2":
                    token_url = scheme.get("tokenUrl", "")
                    grant = "password" if scheme.get("flow") == "password" else "client_credentials"
                    flows = scheme.get("flows") or {}
                    for flow, grant_name in (("clientCredentials", "client_credentials"), ("password", "password")):
                        if isinstance(flows.get(flow), dict):
                            token_url = flows[flow].get("tokenUrl", token_url)
                            grant = grant_name
                    if token_url.startswith("/"):
                        token_url = "{{baseUrl}}" + token_url
                    return AuthConfig("oauth2", {"token_url": token_url, "access_token": "{{accessToken}}", "grant_type": grant})
        return None


def import_openapi(doc: Dict[str, Any], source: str = "") -> Collection:
    api = _OpenApi(doc)
    info = doc.get("info") or {}
    base_url = api.base_url()
    coll = Collection(
        name=str(info.get("title") or Path(source).stem or "OpenAPI"),
        description=str(info.get("description", "")),
        variables=[KeyValue(key="baseUrl", value=base_url or "http://localhost", kind="default")],
        source=source,
    )
    coll.auth = api.security_auth(doc.get("security")) or AuthConfig("noauth")

    folders: Dict[str, Folder] = {}
    for path, path_item in (doc.get("paths") or {}).items():
        path_item = api.deref(path_item)
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method, op in path_item.items():
            if method.upper() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            req = _openapi_request(api, method.upper(), path, op, shared_params)
            tags = op.get("tags") or []
            if tags:
                folder = folders.get(tags[0])
                if folder is None:
                    folder = Folder(name=str(tags[0]))
                    tag_meta = next((t for t in doc.get("tags") or [] if isinstance(t, dict) and t.get("name") == tags[0]), None)
                    if tag_meta:
                        folder.description = str(tag_meta.get("description", ""))
                    folders[tags[0]] = folder
                    coll.items.append(folder)
                folder.items.append(req)
            else:
                coll.items.append(req)
    return coll


def _openapi_request(api: _OpenApi, method: str, path: str, op: Dict[str, Any], shared_params: List[Any]) -> RequestModel:
    name = str(op.get("summary") or op.get("operationId") or f"{method} {path}")
    url_path = re.sub(r"\{([^}/]+)\}", r":\1", path)
    req = RequestModel(name=name, method=method, url="{{baseUrl}}" + url_path)
    description = str(op.get("description", ""))
    if op.get("deprecated"):
        description = "**Deprecated**\n\n" + description
    req.description = description.strip()

    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for raw in list(shared_params) + list(op.get("parameters") or []):
        param = api.deref(raw)
        if isinstance(param, dict) and param.get("name"):
            merged[(param.get("in", ""), param["name"])] = param

    form_fields: List[KeyValue] = []
    for (location, pname), param in merged.items():
        schema = api.deref(param.get("schema") or param)
        value = param.get("example")
        if value is None:
            value = api.example(schema) if (param.get("required") or "default" in schema or "example" in schema) else ""
        text = "" if value is None else (value if isinstance(value, str) else json.dumps(value))
        desc = str(param.get("description", ""))
        if location == "path":
            req.path_variables.append(KeyValue(key=pname, value=text if text != "string" else "", description=desc))
        elif location == "query":
            enabled = bool(param.get("required")) or "default" in schema or "example" in param
            req.params.append(KeyValue(key=pname, value=text if text != "string" else "", description=desc, enabled=enabled))
        elif location == "header":
            req.headers.append(KeyValue(key=pname, value=text, description=desc, enabled=bool(param.get("required"))))
        elif location == "body" and api.v2:
            body = api.example(param.get("schema"))
            req.body = RequestBody(mode="raw", raw_language="json", raw=json.dumps(body, indent=2) if body is not None else "{}")
        elif location == "formData":
            kind = "file" if param.get("type") == "file" else "text"
            form_fields.append(KeyValue(key=pname, value="" if kind == "file" else text, description=desc, kind=kind))

    enabled_params = [p for p in req.params if p.enabled]
    if enabled_params:
        req.url += "?" + "&".join(f"{p.key}={p.value}" for p in enabled_params)

    if form_fields:
        consumes = op.get("consumes") or api.doc.get("consumes") or []
        if "multipart/form-data" in consumes or any(f.kind == "file" for f in form_fields):
            req.body = RequestBody(mode="form-data", form=form_fields)
        else:
            req.body = RequestBody(mode="urlencoded", urlencoded=form_fields)

    request_body = api.deref(op.get("requestBody"))
    if isinstance(request_body, dict):
        content = request_body.get("content") or {}
        json_type = next((ct for ct in content if "json" in ct), None)
        if json_type:
            media = content[json_type] or {}
            example = media.get("example")
            if example is None and isinstance(media.get("examples"), dict) and media["examples"]:
                first = api.deref(next(iter(media["examples"].values())))
                example = first.get("value") if isinstance(first, dict) else None
            if example is None:
                example = api.example(media.get("schema"))
            req.body = RequestBody(mode="raw", raw_language="json", raw=json.dumps(example if example is not None else {}, indent=2))
            if json_type != "application/json":
                req.headers.append(KeyValue(key="Content-Type", value=json_type))
        elif "application/x-www-form-urlencoded" in content or "multipart/form-data" in content:
            multipart = "multipart/form-data" in content
            media = content["multipart/form-data" if multipart else "application/x-www-form-urlencoded"] or {}
            schema = api.deref(media.get("schema"))
            fields_out = []
            for pname, prop in (schema.get("properties") or {}).items():
                prop = api.deref(prop)
                is_file = prop.get("format") in ("binary", "base64") or prop.get("type") == "file"
                value = api.example(prop)
                fields_out.append(
                    KeyValue(
                        key=pname,
                        value="" if is_file or value is None else (value if isinstance(value, str) else json.dumps(value)),
                        description=str(prop.get("description", "")),
                        kind="file" if (is_file and multipart) else "text",
                    )
                )
            req.body = RequestBody(mode="form-data", form=fields_out) if multipart else RequestBody(mode="urlencoded", urlencoded=fields_out)
        elif content:
            ctype = next(iter(content))
            if "xml" in ctype:
                req.body = RequestBody(mode="raw", raw_language="xml", raw="")
            elif "octet-stream" in ctype:
                req.body = RequestBody(mode="binary")
            else:
                req.body = RequestBody(mode="raw", raw_language="text", raw="")
            req.headers.append(KeyValue(key="Content-Type", value=ctype))

    auth = api.security_auth(op.get("security"))
    if auth is not None:
        req.auth = auth
    return req


# ---------------------------------------------------------------------------
# cURL
# ---------------------------------------------------------------------------

def parse_curl(command: str) -> RequestModel:
    text = command.strip()
    text = re.sub(r"\\\r?\n", " ", text)  # bash line continuation
    text = re.sub(r"\^\r?\n", " ", text)  # cmd.exe line continuation
    text = re.sub(r"`\r?\n", " ", text)  # PowerShell line continuation
    text = text.replace("^\"", "\"").replace("^^", "^") if "^\"" in text else text
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError(f"Could not parse cURL command: {exc}") from exc
    if not tokens or tokens[0].lower() not in ("curl", "curl.exe"):
        raise ValueError("The command must start with 'curl'.")

    method: Optional[str] = None
    url = ""
    headers: List[KeyValue] = []
    data_parts: List[str] = []
    urlencoded: List[KeyValue] = []
    form: List[KeyValue] = []
    user: Optional[str] = None
    digest = False
    force_get = False
    binary_path = ""

    takes_value = {
        "-X", "--request", "-H", "--header", "-d", "--data", "--data-raw", "--data-binary", "--data-ascii",
        "--data-urlencode", "-F", "--form", "--form-string", "-u", "--user", "--url", "-b", "--cookie",
        "-A", "--user-agent", "-e", "--referer", "-o", "--output", "-m", "--max-time", "--connect-timeout",
        "-x", "--proxy", "--cacert", "--cert", "--key", "-w", "--write-out",
    }

    i = 1
    while i < len(tokens):
        tok = tokens[i]
        value = None
        if tok.startswith("--") and "=" in tok and tok.split("=", 1)[0] in takes_value:
            tok, value = tok.split("=", 1)
        elif tok in takes_value:
            i += 1
            value = tokens[i] if i < len(tokens) else ""
        elif len(tok) > 2 and tok[:2] in ("-X", "-H", "-d", "-u", "-F", "-b", "-A") and not tok.startswith("--"):
            tok, value = tok[:2], tok[2:]

        if tok in ("-X", "--request"):
            method = (value or "GET").upper()
        elif tok in ("-H", "--header"):
            key, _, val = (value or "").partition(":")
            if key.strip():
                headers.append(KeyValue(key=key.strip(), value=val.strip()))
        elif tok in ("-d", "--data", "--data-raw", "--data-ascii"):
            data_parts.append(value or "")
        elif tok == "--data-binary":
            if (value or "").startswith("@"):
                binary_path = value[1:]
            else:
                data_parts.append(value or "")
        elif tok == "--data-urlencode":
            key, sep, val = (value or "").partition("=")
            urlencoded.append(KeyValue(key=key, value=val) if sep else KeyValue(key=value or "", value=""))
        elif tok in ("-F", "--form", "--form-string"):
            key, _, val = (value or "").partition("=")
            if val.startswith("@") and tok != "--form-string":
                form.append(KeyValue(key=key, value=val[1:].split(";")[0].strip('"'), kind="file"))
            else:
                form.append(KeyValue(key=key, value=val.strip('"')))
        elif tok in ("-u", "--user"):
            user = value
        elif tok == "--digest":
            digest = True
        elif tok in ("-G", "--get"):
            force_get = True
        elif tok in ("-b", "--cookie"):
            headers.append(KeyValue(key="Cookie", value=value or ""))
        elif tok in ("-A", "--user-agent"):
            headers.append(KeyValue(key="User-Agent", value=value or ""))
        elif tok in ("-e", "--referer"):
            headers.append(KeyValue(key="Referer", value=value or ""))
        elif tok == "--url":
            url = value or ""
        elif not tok.startswith("-") and not url:
            url = tok
        i += 1

    req = RequestModel(name=_name_from_url(url), url=url, headers=headers)
    if user is not None:
        username, _, password = user.partition(":")
        req.auth = AuthConfig("digest" if digest else "basic", {"username": username, "password": password})
    else:
        req.auth = AuthConfig("noauth")

    ctype = next((h.value.lower() for h in headers if h.key.lower() == "content-type"), "")
    if force_get and (data_parts or urlencoded):
        query = "&".join(data_parts + [f"{kv.key}={kv.value}" for kv in urlencoded])
        req.url = url + ("&" if "?" in url else "?") + query
        data_parts, urlencoded = [], []
    if form:
        req.body = RequestBody(mode="form-data", form=form)
    elif binary_path:
        req.body = RequestBody(mode="binary", binary_path=binary_path)
    elif urlencoded or (data_parts and "x-www-form-urlencoded" in ctype):
        pairs = list(urlencoded)
        for part in data_parts:
            for piece in part.split("&"):
                key, _, val = piece.partition("=")
                if key:
                    pairs.append(KeyValue(key=key, value=val))
        req.body = RequestBody(mode="urlencoded", urlencoded=pairs)
    elif data_parts:
        raw = "&".join(data_parts)
        language = "json" if ("json" in ctype or raw.strip()[:1] in ("{", "[")) else "xml" if "xml" in ctype else "text"
        if language == "json":
            try:
                raw = json.dumps(json.loads(raw), indent=2)
            except ValueError:
                pass
        if not ctype and "=" in raw and language == "text":
            req.body = RequestBody(mode="urlencoded", urlencoded=[KeyValue(key=p.partition("=")[0], value=p.partition("=")[2]) for p in raw.split("&") if p])
        else:
            req.body = RequestBody(mode="raw", raw=raw, raw_language=language)

    has_body = req.body.mode != "none"
    req.method = method or ("POST" if has_body else "GET")
    req.params = _query_from_url(req.url)
    return req


def _name_from_url(url: str) -> str:
    path = re.sub(r"^\w+://[^/]+", "", url).split("?", 1)[0].rstrip("/")
    if not path:
        return url or "New Request"
    return path if len(path) <= 60 else "…" + path[-59:]
