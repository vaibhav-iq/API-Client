"""Plain data model for requests, collections and environments (no Qt)."""
import copy
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"]

AUTH_TYPES: List[Tuple[str, str]] = [
    ("inherit", "Inherit auth from parent"),
    ("noauth", "No Auth"),
    ("apikey", "API Key"),
    ("bearer", "Bearer Token"),
    ("basic", "Basic Auth"),
    ("digest", "Digest Auth"),
    ("jwt", "JWT Bearer"),
    ("oauth2", "OAuth 2.0"),
]
AUTH_LABELS = dict(AUTH_TYPES)

BODY_MODES: List[Tuple[str, str]] = [
    ("none", "none"),
    ("form-data", "form-data"),
    ("urlencoded", "x-www-form-urlencoded"),
    ("raw", "raw"),
    ("binary", "binary"),
    ("graphql", "GraphQL"),
]

RAW_LANGUAGES: List[Tuple[str, str]] = [
    ("json", "JSON"),
    ("text", "Text"),
    ("xml", "XML"),
    ("html", "HTML"),
    ("javascript", "JavaScript"),
]


def new_id() -> str:
    return uuid.uuid4().hex


def _s(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass
class KeyValue:
    key: str = ""
    value: str = ""
    description: str = ""
    enabled: bool = True
    kind: str = "text"  # text | file (form-data) | default | secret (environments)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "KeyValue":
        return KeyValue(
            key=_s(data.get("key")),
            value=_s(data.get("value")),
            description=_s(data.get("description")),
            enabled=bool(data.get("enabled", True)),
            kind=_s(data.get("kind") or "text"),
        )


def kv_list(data: Any) -> List[KeyValue]:
    return [KeyValue.from_dict(item) for item in (data or []) if isinstance(item, dict)]


def kv_map(items: List[KeyValue]) -> Dict[str, str]:
    return {kv.key: kv.value for kv in items if kv.enabled and kv.key}


def kv_set(items: List[KeyValue], key: str, value: str, kind: str = "default") -> None:
    for kv in items:
        if kv.key == key:
            kv.value = value
            kv.enabled = True
            return
    items.append(KeyValue(key=key, value=value, kind=kind))


def kv_unset(items: List[KeyValue], key: str) -> None:
    items[:] = [kv for kv in items if kv.key != key]


@dataclass
class AuthConfig:
    type: str = "noauth"
    data: Dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        value = self.data.get(key)
        return default if value is None or value == "" else str(value)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": dict(self.data)}

    @staticmethod
    def from_dict(data: Any, default_type: str = "noauth") -> "AuthConfig":
        if not isinstance(data, dict):
            return AuthConfig(type=default_type)
        auth_type = _s(data.get("type") or default_type)
        if auth_type not in AUTH_LABELS:
            auth_type = default_type
        raw = data.get("data") if isinstance(data.get("data"), dict) else {}
        return AuthConfig(type=auth_type, data={str(k): _s(v) for k, v in raw.items()})


@dataclass
class RequestBody:
    mode: str = "none"
    raw: str = ""
    raw_language: str = "json"
    form: List[KeyValue] = field(default_factory=list)
    urlencoded: List[KeyValue] = field(default_factory=list)
    binary_path: str = ""
    graphql_query: str = ""
    graphql_variables: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "raw": self.raw,
            "raw_language": self.raw_language,
            "form": [kv.to_dict() for kv in self.form],
            "urlencoded": [kv.to_dict() for kv in self.urlencoded],
            "binary_path": self.binary_path,
            "graphql_query": self.graphql_query,
            "graphql_variables": self.graphql_variables,
        }

    @staticmethod
    def from_dict(data: Any) -> "RequestBody":
        if not isinstance(data, dict):
            return RequestBody()
        mode = _s(data.get("mode") or "none")
        if mode not in dict(BODY_MODES):
            mode = "none"
        return RequestBody(
            mode=mode,
            raw=_s(data.get("raw")),
            raw_language=_s(data.get("raw_language") or "json"),
            form=kv_list(data.get("form")),
            urlencoded=kv_list(data.get("urlencoded")),
            binary_path=_s(data.get("binary_path")),
            graphql_query=_s(data.get("graphql_query")),
            graphql_variables=_s(data.get("graphql_variables")),
        )


@dataclass
class TestRule:
    enabled: bool = True
    source: str = "status"  # status | time | json | header | body | size
    prop: str = ""
    op: str = "equals"
    expected: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TestRule":
        return TestRule(
            enabled=bool(data.get("enabled", True)),
            source=_s(data.get("source") or "status"),
            prop=_s(data.get("prop")),
            op=_s(data.get("op") or "equals"),
            expected=_s(data.get("expected")),
        )


@dataclass
class ExtractRule:
    enabled: bool = True
    variable: str = ""
    source: str = "json"  # json | header | status | regex | cookie
    path: str = ""
    scope: str = "environment"  # environment | collection | global

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ExtractRule":
        return ExtractRule(
            enabled=bool(data.get("enabled", True)),
            variable=_s(data.get("variable")),
            source=_s(data.get("source") or "json"),
            path=_s(data.get("path")),
            scope=_s(data.get("scope") or "environment"),
        )


@dataclass
class RequestModel:
    id: str = field(default_factory=new_id)
    name: str = "New Request"
    method: str = "GET"
    url: str = ""
    params: List[KeyValue] = field(default_factory=list)
    path_variables: List[KeyValue] = field(default_factory=list)
    headers: List[KeyValue] = field(default_factory=list)
    body: RequestBody = field(default_factory=RequestBody)
    auth: AuthConfig = field(default_factory=lambda: AuthConfig(type="inherit"))
    description: str = ""
    tests: List[TestRule] = field(default_factory=list)
    extractors: List[ExtractRule] = field(default_factory=list)
    pre_script: str = ""
    post_script: str = ""

    def clone(self, fresh_id: bool = False) -> "RequestModel":
        cloned = copy.deepcopy(self)
        if fresh_id:
            cloned.id = new_id()
        return cloned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "request",
            "id": self.id,
            "name": self.name,
            "method": self.method,
            "url": self.url,
            "params": [kv.to_dict() for kv in self.params],
            "path_variables": [kv.to_dict() for kv in self.path_variables],
            "headers": [kv.to_dict() for kv in self.headers],
            "body": self.body.to_dict(),
            "auth": self.auth.to_dict(),
            "description": self.description,
            "tests": [rule.to_dict() for rule in self.tests],
            "extractors": [rule.to_dict() for rule in self.extractors],
            "pre_script": self.pre_script,
            "post_script": self.post_script,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RequestModel":
        method = _s(data.get("method") or "GET").upper()
        return RequestModel(
            id=_s(data.get("id")) or new_id(),
            name=_s(data.get("name")) or "New Request",
            method=method,
            url=_s(data.get("url")),
            params=kv_list(data.get("params")),
            path_variables=kv_list(data.get("path_variables")),
            headers=kv_list(data.get("headers")),
            body=RequestBody.from_dict(data.get("body")),
            auth=AuthConfig.from_dict(data.get("auth"), default_type="inherit"),
            description=_s(data.get("description")),
            tests=[TestRule.from_dict(x) for x in data.get("tests") or [] if isinstance(x, dict)],
            extractors=[ExtractRule.from_dict(x) for x in data.get("extractors") or [] if isinstance(x, dict)],
            pre_script=_s(data.get("pre_script")),
            post_script=_s(data.get("post_script")),
        )


@dataclass
class Folder:
    id: str = field(default_factory=new_id)
    name: str = "New Folder"
    description: str = ""
    items: List[Union["Folder", RequestModel]] = field(default_factory=list)
    auth: AuthConfig = field(default_factory=lambda: AuthConfig(type="inherit"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "folder",
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "auth": self.auth.to_dict(),
            "items": [node.to_dict() for node in self.items],
        }


@dataclass
class Collection(Folder):
    name: str = "New Collection"
    variables: List[KeyValue] = field(default_factory=list)
    auth: AuthConfig = field(default_factory=lambda: AuthConfig(type="noauth"))
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["type"] = "collection"
        data["variables"] = [kv.to_dict() for kv in self.variables]
        data["source"] = self.source
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Collection":
        coll = Collection(
            id=_s(data.get("id")) or new_id(),
            name=_s(data.get("name")) or "Collection",
            description=_s(data.get("description")),
            auth=AuthConfig.from_dict(data.get("auth"), default_type="noauth"),
            variables=kv_list(data.get("variables")),
            source=_s(data.get("source")),
        )
        coll.items = [node_from_dict(x) for x in data.get("items") or [] if isinstance(x, dict)]
        return coll


Node = Union[Folder, RequestModel]


def node_from_dict(data: Dict[str, Any]) -> Node:
    if data.get("type") == "folder" or "items" in data:
        folder = Folder(
            id=_s(data.get("id")) or new_id(),
            name=_s(data.get("name")) or "Folder",
            description=_s(data.get("description")),
            auth=AuthConfig.from_dict(data.get("auth"), default_type="inherit"),
        )
        folder.items = [node_from_dict(x) for x in data.get("items") or [] if isinstance(x, dict)]
        return folder
    return RequestModel.from_dict(data)


def walk(folder: Folder) -> Iterator[Tuple[Node, Folder]]:
    """Yield (node, parent) for every descendant, depth-first."""
    for node in folder.items:
        yield node, folder
        if isinstance(node, Folder):
            yield from walk(node)


def find_node(root: Folder, node_id: str) -> Tuple[Optional[Node], Optional[Folder]]:
    if root.id == node_id:
        return root, None
    for node, parent in walk(root):
        if node.id == node_id:
            return node, parent
    return None, None


def ancestors(root: Folder, node_id: str) -> List[Folder]:
    """Folders from the direct parent up to (and including) the root."""

    def search(folder: Folder, trail: List[Folder]) -> Optional[List[Folder]]:
        for node in folder.items:
            if node.id == node_id:
                return [folder] + trail
            if isinstance(node, Folder):
                found = search(node, [folder] + trail)
                if found is not None:
                    return found
        return None

    return search(root, []) or []


def requests_in(folder: Folder) -> List[RequestModel]:
    return [node for node, _ in walk(folder) if isinstance(node, RequestModel)]


def clone_node(node: Node) -> Node:
    cloned = copy.deepcopy(node)
    cloned.id = new_id()
    if isinstance(cloned, Folder):
        for child, _ in walk(cloned):
            child.id = new_id()
    return cloned


@dataclass
class Environment:
    id: str = field(default_factory=new_id)
    name: str = "New Environment"
    variables: List[KeyValue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "variables": [kv.to_dict() for kv in self.variables]}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Environment":
        return Environment(
            id=_s(data.get("id")) or new_id(),
            name=_s(data.get("name")) or "Environment",
            variables=kv_list(data.get("variables")),
        )

