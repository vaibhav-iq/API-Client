"""Pre-request / post-response scripts written in Python with a Postman-like `pm` API.

Scripts run in-process with the user's privileges, exactly like any Python code
the user writes. They are only executed for requests the user explicitly sends.
"""
import base64
import hashlib
import hmac
import json
import random
import re
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from requests.structures import CaseInsensitiveDict

from .engine import ResponseData, RunResult, TestResult, VariableContext, json_path_get
from .models import KeyValue, RequestModel


class ScriptVariables:
    def __init__(self, ctx: VariableContext, scope: str) -> None:
        self._ctx = ctx
        self._scope = scope

    def get(self, key: str, default: Any = None) -> Any:
        return self._ctx.scopes[self._scope].get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._ctx.set(self._scope, key, value)

    def unset(self, key: str) -> None:
        self._ctx.unset(self._scope, key)

    def has(self, key: str) -> bool:
        return key in self._ctx.scopes[self._scope]

    def to_object(self) -> Dict[str, str]:
        return dict(self._ctx.scopes[self._scope])

    toObject = to_object


class AllVariables:
    def __init__(self, ctx: VariableContext) -> None:
        self._ctx = ctx

    def get(self, key: str, default: Any = None) -> Any:
        value, _ = self._ctx.lookup(key)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self._ctx.set("local", key, value)

    def has(self, key: str) -> bool:
        return self._ctx.lookup(key)[0] is not None

    def replace_in(self, text: str) -> str:
        return self._ctx.resolve(text)

    replaceIn = replace_in


class ScriptRequest:
    def __init__(self, model: RequestModel) -> None:
        self._model = model
        self.headers: Dict[str, str] = {kv.key: kv.value for kv in model.headers if kv.enabled and kv.key}
        self._original_headers = dict(self.headers)

    @property
    def name(self) -> str:
        return self._model.name

    @property
    def method(self) -> str:
        return self._model.method

    @method.setter
    def method(self, value: str) -> None:
        self._model.method = str(value).upper()

    @property
    def url(self) -> str:
        return self._model.url

    @url.setter
    def url(self, value: str) -> None:
        self._model.url = str(value)

    @property
    def body(self) -> str:
        return self._model.body.raw

    @body.setter
    def body(self, value: Any) -> None:
        self._model.body.raw = value if isinstance(value, str) else json.dumps(value)
        if self._model.body.mode == "none":
            self._model.body.mode = "raw"

    def add_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def remove_header(self, key: str) -> None:
        for existing in list(self.headers):
            if existing.lower() == key.lower():
                del self.headers[existing]

    def commit(self) -> None:
        if self.headers == self._original_headers:
            return
        disabled = [kv for kv in self._model.headers if not kv.enabled]
        self._model.headers = [KeyValue(key=k, value=str(v)) for k, v in self.headers.items()] + disabled


class ScriptResponse:
    def __init__(self, resp: ResponseData) -> None:
        self._resp = resp
        self.code = resp.status
        self.status = resp.reason
        self.headers = CaseInsensitiveDict(resp.headers)
        self.responseTime = round(resp.elapsed_ms)
        self.response_time = self.responseTime
        self.responseSize = resp.body_size
        self.cookies = {c["name"]: c["value"] for c in resp.cookies}

    def text(self) -> str:
        return self._resp.text

    def json(self) -> Any:
        return self._resp.json()

    def path(self, expr: str, default: Any = None) -> Any:
        found, value = json_path_get(self._resp.json(), expr)
        return value if found else default

    def to_have_status(self, code: int) -> None:
        if self.code != int(code):
            raise AssertionError(f"expected status {code} but got {self.code}")

    def to_have_header(self, name: str) -> None:
        if name not in self.headers:
            raise AssertionError(f"expected response to have header '{name}'")


class Expectation:
    def __init__(self, value: Any) -> None:
        self.value = value

    def _check(self, ok: bool, message: str) -> "Expectation":
        if not ok:
            raise AssertionError(message)
        return self

    def to_equal(self, expected: Any) -> "Expectation":
        return self._check(self.value == expected, f"expected {self.value!r} to equal {expected!r}")

    to_eql = to_equal
    to_be = to_equal

    def not_equal(self, expected: Any) -> "Expectation":
        return self._check(self.value != expected, f"expected {self.value!r} to not equal {expected!r}")

    def to_include(self, item: Any) -> "Expectation":
        return self._check(item in self.value, f"expected {self.value!r} to include {item!r}")

    to_contain = to_include

    def to_be_above(self, number: float) -> "Expectation":
        return self._check(self.value > number, f"expected {self.value!r} to be above {number!r}")

    def to_be_below(self, number: float) -> "Expectation":
        return self._check(self.value < number, f"expected {self.value!r} to be below {number!r}")

    def to_be_truthy(self) -> "Expectation":
        return self._check(bool(self.value), f"expected {self.value!r} to be truthy")

    def to_be_falsy(self) -> "Expectation":
        return self._check(not self.value, f"expected {self.value!r} to be falsy")

    def to_exist(self) -> "Expectation":
        return self._check(self.value is not None, "expected value to exist")

    def to_have_property(self, name: str) -> "Expectation":
        return self._check(isinstance(self.value, dict) and name in self.value, f"expected object to have property {name!r}")

    def to_have_length(self, length: int) -> "Expectation":
        return self._check(len(self.value) == length, f"expected length {length} but got {len(self.value)}")

    def to_match(self, pattern: str) -> "Expectation":
        return self._check(re.search(pattern, str(self.value)) is not None, f"expected {self.value!r} to match /{pattern}/")

    def to_be_instance(self, kind: type) -> "Expectation":
        return self._check(isinstance(self.value, kind), f"expected {self.value!r} to be {kind.__name__}")


class ScriptConsole:
    def __init__(self, logs: List[Tuple[str, str]]) -> None:
        self._logs = logs

    def _emit(self, level: str, args: Tuple[Any, ...]) -> None:
        parts = []
        for arg in args:
            if isinstance(arg, (dict, list)):
                parts.append(json.dumps(arg, indent=2, ensure_ascii=False))
            else:
                parts.append(str(arg))
        self._logs.append((level, " ".join(parts)))

    def log(self, *args: Any, **_kwargs: Any) -> None:
        self._emit("log", args)

    def info(self, *args: Any) -> None:
        self._emit("info", args)

    def warn(self, *args: Any) -> None:
        self._emit("warn", args)

    def error(self, *args: Any) -> None:
        self._emit("error", args)


class Pm:
    def __init__(self, ctx: VariableContext, request: ScriptRequest, response: Optional[ScriptResponse], tests: List[TestResult], info: Dict[str, Any]) -> None:
        self.environment = ScriptVariables(ctx, "environment")
        self.globals = ScriptVariables(ctx, "global")
        self.collectionVariables = ScriptVariables(ctx, "collection")
        self.collection_variables = self.collectionVariables
        self.variables = AllVariables(ctx)
        self.request = request
        self.response = response
        self.info = info
        self._tests = tests

    def test(self, name: str, check: Any = True) -> bool:
        try:
            if callable(check):
                outcome = check()
                passed = outcome is None or bool(outcome)
                message = "" if passed else "returned a falsy value"
            else:
                passed = bool(check)
                message = "" if passed else "condition was false"
        except AssertionError as exc:
            passed, message = False, str(exc) or "assertion failed"
        except Exception as exc:  # noqa: BLE001
            passed, message = False, f"{type(exc).__name__}: {exc}"
        self._tests.append(TestResult(str(name), passed, message, "script"))
        return passed

    def expect(self, value: Any) -> Expectation:
        return Expectation(value)


def run_script(
    code: str,
    event: str,
    ctx: VariableContext,
    model: RequestModel,
    response: Optional[ResponseData],
    result: RunResult,
    info: Dict[str, Any],
) -> None:
    label = "Pre-request" if event == "prerequest" else "Post-response"
    request = ScriptRequest(model)
    script_info = dict(info)
    script_info["eventName"] = event
    pm = Pm(ctx, request, ScriptResponse(response) if response is not None else None, result.tests, script_info)
    console = ScriptConsole(result.logs)
    namespace: Dict[str, Any] = {
        "__name__": "__script__",
        "pm": pm,
        "console": console,
        "print": console.log,
        "json": json,
        "re": re,
        "time": time,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "base64": base64,
        "hashlib": hashlib,
        "hmac": hmac,
        "uuid": uuid,
        "random": random,
    }
    try:
        exec(compile(code, f"<{event}-script>", "exec"), namespace)  # noqa: S102 - user-authored script
    except Exception as exc:  # noqa: BLE001
        line = ""
        tb = traceback.extract_tb(exc.__traceback__)
        frames = [frame for frame in tb if frame.filename.startswith("<")]
        if frames:
            line = f" (line {frames[-1].lineno})"
        message = f"{type(exc).__name__}: {exc}{line}"
        result.logs.append(("error", f"{label} script error: {message}"))
        result.tests.append(TestResult(f"{label} script error", False, message, "script"))
    finally:
        if event == "prerequest":
            request.commit()


SCRIPT_SNIPPETS: List[Tuple[str, str, str]] = [
    # (title, event, code)
    ("Get an environment variable", "any", 'value = pm.environment.get("variable_key")\n'),
    ("Set an environment variable", "any", 'pm.environment.set("variable_key", "variable_value")\n'),
    ("Set a global variable", "any", 'pm.globals.set("variable_key", "variable_value")\n'),
    ("Set a collection variable", "any", 'pm.collectionVariables.set("variable_key", "variable_value")\n'),
    ("Clear an environment variable", "any", 'pm.environment.unset("variable_key")\n'),
    ("Log to console", "any", 'console.log("Request:", pm.request.method, pm.request.url)\n'),
    ("Add a request header", "prerequest", 'pm.request.headers["X-Request-Id"] = str(uuid.uuid4())\n'),
    ("Set a timestamp variable", "prerequest", 'pm.environment.set("timestamp", str(int(time.time())))\n'),
    ("Status code is 200", "test", 'pm.test("Status code is 200", lambda: pm.response.to_have_status(200))\n'),
    ("Response time is below 500ms", "test", 'pm.test("Response time is below 500ms", pm.response.responseTime < 500)\n'),
    ("Body contains string", "test", 'pm.test("Body contains string", "string_you_want_to_search" in pm.response.text())\n'),
    ("JSON value check", "test", 'def check_value():\n    data = pm.response.json()\n    pm.expect(data["value"]).to_equal(100)\n\npm.test("JSON value check", check_value)\n'),
    ("Content-Type header is present", "test", 'pm.test("Content-Type is present", lambda: pm.response.to_have_header("Content-Type"))\n'),
    ("Save token from response", "test", 'token = pm.response.path("$.access_token")\nif token:\n    pm.environment.set("token", token)\n'),
    ("Successful POST request", "test", 'pm.test("Successful POST request", pm.response.code in (200, 201, 202))\n'),
    # -- security checks --
    ("No server error (5xx)", "test", 'pm.test("No server error", pm.response.code < 500)\n'),
    ("No stack trace leaked", "test", 'pm.test("No stack trace in body", not any(s in pm.response.text() for s in ("Traceback", "Exception in", "at java.", "System.Web")))\n'),
    ("Security header: nosniff", "test", 'pm.test("X-Content-Type-Options is nosniff", pm.response.headers.get("X-Content-Type-Options", "").lower() == "nosniff")\n'),
    ("HSTS header present", "test", 'pm.test("Strict-Transport-Security present", lambda: pm.response.to_have_header("Strict-Transport-Security"))\n'),
    ("CORS is not wildcard", "test", 'pm.test("CORS is not wildcard", pm.response.headers.get("Access-Control-Allow-Origin") != "*")\n'),
    ("No version disclosure", "test", 'pm.test("Server header hides version", not any(c.isdigit() for c in pm.response.headers.get("Server", "")))\n'),
    ("Unauthorized without token", "test", 'pm.test("Protected endpoint rejects missing auth", pm.response.code in (401, 403))\n'),
    ("Scan body for secrets", "test", 'from api_client import security\nfound = security.scan_secrets(pm.response.text())\npm.test("No secrets exposed in body", len([f for f in found if f.severity in ("high", "medium")]) == 0)\n'),
]
