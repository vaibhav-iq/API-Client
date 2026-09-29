"""Generate client code snippets from a built HttpSpec."""
import base64
import json
from typing import Callable, Dict, List, Tuple

from .engine import HttpSpec


def _sq(value: str) -> str:
    """Single-quote for POSIX shells."""
    return "'" + value.replace("'", "'\\''") + "'"


def _headers_for_code(spec: HttpSpec) -> List[Tuple[str, str]]:
    headers = [(k, v) for k, v in spec.headers.items()]
    if spec.basic_auth and spec.basic_auth[2] == "basic":
        user, password, _ = spec.basic_auth
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers.append(("Authorization", f"Basic {token}"))
    if spec.body_kind == "form":
        headers = [(k, v) for k, v in headers if k.lower() != "content-type"]
    return headers


def curl(spec: HttpSpec) -> str:
    lines = [f"curl --location --request {spec.method} {_sq(spec.url)}"]
    for key, value in spec.headers.items():
        lines.append(f"--header {_sq(f'{key}: {value}')}")
    if spec.basic_auth:
        user, password, scheme = spec.basic_auth
        if scheme == "digest":
            lines.append("--digest")
        lines.append(f"--user {_sq(f'{user}:{password}')}")
    if spec.body_kind == "raw":
        lines.append(f"--data-raw {_sq(spec.body_text)}")
    elif spec.body_kind == "urlencoded":
        for key, value, _ in spec.form_fields:
            lines.append(f"--data-urlencode {_sq(f'{key}={value}')}")
    elif spec.body_kind == "form":
        for key, value, kind in spec.form_fields:
            form_field = f'{key}=@"{value}"' if kind == "file" else f'{key}="{value}"'
            lines.append(f"--form {_sq(form_field)}")
    elif spec.body_kind == "binary":
        lines.append(f"--data-binary {_sq('@' + spec.body_text)}")
    return " \\\n".join(lines)


def python_requests(spec: HttpSpec) -> str:
    out = ["import requests", ""]
    out.append(f"url = {json.dumps(spec.url)}")
    headers = _headers_for_code(spec)
    if spec.basic_auth and spec.basic_auth[2] == "digest":
        out[0] = "import requests\nfrom requests.auth import HTTPDigestAuth"
    kwargs = []
    if spec.body_kind == "raw":
        out.append(f"payload = {json.dumps(spec.body_text)}")
        kwargs.append("data=payload.encode('utf-8')")
    elif spec.body_kind == "urlencoded":
        out.append("payload = " + json.dumps({k: v for k, v, _ in spec.form_fields}, indent=4))
        kwargs.append("data=payload")
    elif spec.body_kind == "form":
        out.append("files = [")
        for key, value, kind in spec.form_fields:
            if kind == "file":
                out.append(f"    ({json.dumps(key)}, ({json.dumps(value.replace(chr(92), '/').split('/')[-1])}, open({json.dumps(value)}, 'rb'))),")
            else:
                out.append(f"    ({json.dumps(key)}, (None, {json.dumps(value)})),")
        out.append("]")
        kwargs.append("files=files")
    elif spec.body_kind == "binary":
        out.append(f"payload = open({json.dumps(spec.body_text)}, 'rb').read()")
        kwargs.append("data=payload")
    out.append("headers = " + (json.dumps(dict(headers), indent=4) if headers else "{}"))
    kwargs.insert(0, "headers=headers")
    if spec.basic_auth and spec.basic_auth[2] == "digest":
        kwargs.append(f"auth=HTTPDigestAuth({json.dumps(spec.basic_auth[0])}, {json.dumps(spec.basic_auth[1])})")
    out.append("")
    out.append(f"response = requests.request({json.dumps(spec.method)}, url, {', '.join(kwargs)})")
    out.append("")
    out.append("print(response.status_code)")
    out.append("print(response.text)")
    return "\n".join(out)


def js_fetch(spec: HttpSpec) -> str:
    out = ["const myHeaders = new Headers();"]
    for key, value in _headers_for_code(spec):
        out.append(f"myHeaders.append({json.dumps(key)}, {json.dumps(value)});")
    out.append("")
    body_line = None
    if spec.body_kind == "raw":
        out.append(f"const raw = {json.dumps(spec.body_text)};")
        body_line = "raw"
    elif spec.body_kind == "urlencoded":
        out.append("const urlencoded = new URLSearchParams();")
        for key, value, _ in spec.form_fields:
            out.append(f"urlencoded.append({json.dumps(key)}, {json.dumps(value)});")
        body_line = "urlencoded"
    elif spec.body_kind == "form":
        out.append("const formdata = new FormData();")
        for key, value, kind in spec.form_fields:
            if kind == "file":
                out.append(f"formdata.append({json.dumps(key)}, fileInput.files[0], {json.dumps(value)});")
            else:
                out.append(f"formdata.append({json.dumps(key)}, {json.dumps(value)});")
        body_line = "formdata"
    elif spec.body_kind == "binary":
        out.append('const file = "<file contents here>";')
        body_line = "file"
    out.append("")
    out.append("const requestOptions = {")
    out.append(f"  method: {json.dumps(spec.method)},")
    out.append("  headers: myHeaders,")
    if body_line:
        out.append(f"  body: {body_line},")
    out.append('  redirect: "follow"')
    out.append("};")
    out.append("")
    out.append(f"fetch({json.dumps(spec.url)}, requestOptions)")
    out.append("  .then((response) => response.text())")
    out.append("  .then((result) => console.log(result))")
    out.append("  .catch((error) => console.error(error));")
    return "\n".join(out)


def node_axios(spec: HttpSpec) -> str:
    out = ["const axios = require('axios');"]
    data_expr = None
    if spec.body_kind == "raw":
        out.append(f"let data = {json.dumps(spec.body_text)};")
        data_expr = "data"
    elif spec.body_kind == "urlencoded":
        out.append("const qs = require('qs');")
        out.append("let data = qs.stringify(" + json.dumps({k: v for k, v, _ in spec.form_fields}, indent=2) + ");")
        data_expr = "data"
    elif spec.body_kind == "form":
        out.append("const FormData = require('form-data');")
        out.append("const fs = require('fs');")
        out.append("let data = new FormData();")
        for key, value, kind in spec.form_fields:
            if kind == "file":
                out.append(f"data.append({json.dumps(key)}, fs.createReadStream({json.dumps(value)}));")
            else:
                out.append(f"data.append({json.dumps(key)}, {json.dumps(value)});")
        data_expr = "data"
    elif spec.body_kind == "binary":
        out.append("const fs = require('fs');")
        out.append(f"let data = fs.readFileSync({json.dumps(spec.body_text)});")
        data_expr = "data"
    out.append("")
    out.append("let config = {")
    out.append(f"  method: {json.dumps(spec.method.lower())},")
    out.append("  maxBodyLength: Infinity,")
    out.append(f"  url: {json.dumps(spec.url)},")
    headers = _headers_for_code(spec)
    out.append("  headers: {")
    for key, value in headers:
        out.append(f"    {json.dumps(key)}: {json.dumps(value)},")
    if spec.body_kind == "form":
        out.append("    ...data.getHeaders()")
    out.append("  },")
    if data_expr:
        out.append(f"  data: {data_expr}")
    out.append("};")
    out.append("")
    out.append("axios.request(config)")
    out.append("  .then((response) => console.log(JSON.stringify(response.data)))")
    out.append("  .catch((error) => console.log(error));")
    return "\n".join(out)


def csharp_httpclient(spec: HttpSpec) -> str:
    method_map = {"GET": "Get", "POST": "Post", "PUT": "Put", "DELETE": "Delete", "PATCH": "Patch", "HEAD": "Head", "OPTIONS": "Options", "TRACE": "Trace"}
    out = ["var client = new HttpClient();"]
    out.append(f"var request = new HttpRequestMessage(HttpMethod.{method_map.get(spec.method, 'Get')}, {json.dumps(spec.url)});")
    content_type = "text/plain"
    for key, value in _headers_for_code(spec):
        if key.lower() == "content-type":
            content_type = value.split(";")[0].strip()
            continue
        out.append(f"request.Headers.TryAddWithoutValidation({json.dumps(key)}, {json.dumps(value)});")
    if spec.body_kind == "raw":
        out.append(f"var content = new StringContent({json.dumps(spec.body_text)}, null, {json.dumps(content_type)});")
        out.append("request.Content = content;")
    elif spec.body_kind == "urlencoded":
        out.append("var collection = new List<KeyValuePair<string, string>>();")
        for key, value, _ in spec.form_fields:
            out.append(f"collection.Add(new({json.dumps(key)}, {json.dumps(value)}));")
        out.append("request.Content = new FormUrlEncodedContent(collection);")
    elif spec.body_kind == "form":
        out.append("var content = new MultipartFormDataContent();")
        for key, value, kind in spec.form_fields:
            if kind == "file":
                out.append(f"content.Add(new StreamContent(File.OpenRead({json.dumps(value)})), {json.dumps(key)}, {json.dumps(value)});")
            else:
                out.append(f"content.Add(new StringContent({json.dumps(value)}), {json.dumps(key)});")
        out.append("request.Content = content;")
    elif spec.body_kind == "binary":
        out.append(f"request.Content = new ByteArrayContent(File.ReadAllBytes({json.dumps(spec.body_text)}));")
    out.append("var response = await client.SendAsync(request);")
    out.append("response.EnsureSuccessStatusCode();")
    out.append("Console.WriteLine(await response.Content.ReadAsStringAsync());")
    return "\n".join(out)


def powershell(spec: HttpSpec) -> str:
    def ps(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    out = ["$headers = New-Object \"System.Collections.Generic.Dictionary[[String],[String]]\""]
    for key, value in _headers_for_code(spec):
        out.append(f"$headers.Add({ps(key)}, {ps(value)})")
    extra = ""
    if spec.body_kind == "raw":
        out.append("")
        out.append("$body = @'\n" + spec.body_text + "\n'@")
        extra = " -Body $body"
    elif spec.body_kind == "urlencoded":
        out.append("")
        out.append("$body = " + ps("&".join(f"{k}={v}" for k, v, _ in spec.form_fields)))
        extra = " -Body $body"
    elif spec.body_kind == "form":
        out.append("")
        out.append("$form = @{")
        for key, value, kind in spec.form_fields:
            out.append(f"    {ps(key)} = {'Get-Item ' + ps(value) if kind == 'file' else ps(value)}")
        out.append("}")
        extra = " -Form $form"
    elif spec.body_kind == "binary":
        extra = f" -InFile {ps(spec.body_text)}"
    out.append("")
    out.append(f"$response = Invoke-RestMethod {ps(spec.url)} -Method {spec.method.title()} -Headers $headers{extra}")
    out.append("$response | ConvertTo-Json -Depth 10")
    return "\n".join(out)


def go_nethttp(spec: HttpSpec) -> str:
    imports = ['"fmt"', '"io"', '"net/http"']
    body_setup: List[str] = []
    body_var = "nil"
    if spec.body_kind in ("raw", "urlencoded"):
        imports.append('"strings"')
        text = spec.body_text
        body_setup.append(f"  payload := strings.NewReader({json.dumps(text)})")
        body_var = "payload"
    elif spec.body_kind in ("form", "binary"):
        body_setup.append("  // TODO: build multipart / binary body")
    out = ["package main", "", "import (" ] + [f"  {i}" for i in imports] + [")", "", "func main() {"]
    out.append(f"  url := {json.dumps(spec.url)}")
    out.append(f"  method := {json.dumps(spec.method)}")
    out.extend(body_setup)
    out.append("")
    out.append("  client := &http.Client{}")
    out.append(f"  req, err := http.NewRequest(method, url, {body_var})")
    out.append("  if err != nil {")
    out.append("    fmt.Println(err)")
    out.append("    return")
    out.append("  }")
    for key, value in _headers_for_code(spec):
        out.append(f"  req.Header.Add({json.dumps(key)}, {json.dumps(value)})")
    out.append("")
    out.append("  res, err := client.Do(req)")
    out.append("  if err != nil {")
    out.append("    fmt.Println(err)")
    out.append("    return")
    out.append("  }")
    out.append("  defer res.Body.Close()")
    out.append("")
    out.append("  body, err := io.ReadAll(res.Body)")
    out.append("  if err != nil {")
    out.append("    fmt.Println(err)")
    out.append("    return")
    out.append("  }")
    out.append("  fmt.Println(string(body))")
    out.append("}")
    return "\n".join(out)


GENERATORS: Dict[str, Tuple[Callable[[HttpSpec], str], str]] = {
    "cURL": (curl, "text"),
    "Python - Requests": (python_requests, "python"),
    "JavaScript - Fetch": (js_fetch, "javascript"),
    "NodeJs - Axios": (node_axios, "javascript"),
    "C# - HttpClient": (csharp_httpclient, "javascript"),
    "PowerShell - RestMethod": (powershell, "text"),
    "Go - Native": (go_nethttp, "javascript"),
}
