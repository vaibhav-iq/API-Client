# Changelog

All notable changes to **API Client** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0] - 2026-10-02

Major release adding a full security-testing toolkit, a Burp-style intercepting
proxy, and an in-app documentation viewer.

### Added

**Security testing**
- **Automatic response audit** — a **Security** tab on every response flags missing/weak
  security headers, insecure cookie flags, error/stack-trace signatures, and exposed
  secrets / PII.
- **Encoder / Decoder & hashing toolkit** — Base64, Base64-URL, URL, HTML, Hex and
  MD5/SHA-1/SHA-256/SHA-512.
- **JWT Workbench** — decode header/payload, detect `alg:none` and weak HMAC secrets,
  tamper and re-sign tokens.
- **Repeater** — resend and edit a request rapidly with a per-send history.
- **Intruder** — Burp-style fuzzer: mark parameters with `$$…$$` (Mark / Auto-mark),
  **Sniper** and **Battering-ram** modes, bundled payload presets, and a **Notes**
  column with automatic finding detection (error signatures, reflection) and
  time-based / blind-injection flags.
- **Multi-identity IDOR / BOLA matrix** — replay requests as several identities and
  compare status codes to spot broken object-level authorization.
- **Security assertion snippets** in the Scripts tab.

**Intercepting proxy**
- Built-in **HTTP/HTTPS intercepting proxy** with MITM, backed by the app's own
  generated **CA** (generate and export as DER/PEM from Settings → Proxy).
- Configurable **bind address** (loopback / all interfaces / specific IP) and **port**.
- **Proxy** section in the sidebar with **Intercept** and **HTTP history** tabs.
- Live capture list with columns (#, Method, Host, Path, Params, Edited, Status,
  Length, MIME, TLS, IP, Time), column sorting, a Burp-style **filter** popup, and
  **row highlighting**.
- Request/response detail with a syntax-highlighted request, **wrap / whitespace /
  find** controls, and **Original ↔ Edited** comparison for both request and response.
- **Intercept on/off** with **Forward / Drop / Forward all**, editing of held requests,
  and **Do intercept → Response to this request** to hold and edit the response.
- Right-click a captured call to **Send to Repeater / Intruder / Collection**.

**Documentation**
- Illustrated user guide under `docs/`, and an in-app **Help → Documentation** viewer
  (press **F1**) that renders the Markdown with images and cross-links.

### Changed
- New **blue** accent theme (dark and light) and a recoloured app logo.
- **SSL certificate verification is now off by default** (toggle in Settings → General).
- Assorted UI polish: filled-red **Drop** button, improved dropdown popups, matching
  Request/Response headers, a maximize button on the Documentation window, and a
  colored **Import** button.

### Dependencies / build
- Added **`cryptography`** (used by the proxy CA).
- Build script supports **one-file and one-dir** output (`--mode onefile|onedir|both`)
  and bundles the docs; added an **Inno Setup** installer script (`installer.iss`).

## [1.0] - 2026-09-30

Initial release — a Windows desktop app for sending HTTP requests and inspecting responses.

### Added
- **Requests** — GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, TRACE; tabbed editor;
  query params; path variables; enable/describe/bulk-edit headers and params; body
  modes (none, form-data, x-www-form-urlencoded, raw JSON/Text/XML/HTML/JS, binary,
  GraphQL); cURL paste; per-request Markdown docs.
- **Authorization** — API Key, Bearer, Basic, Digest, JWT (HS256/384/512) and
  OAuth 2.0, with inheritance from the parent folder/collection.
- **Responses** — status/time/size; Pretty, Raw, Preview and JSON Tree views; search,
  wrap, copy, save; cookies, headers, test results and timeline.
- **Testing & automation** — no-code assertions, "set variables from response" rules,
  Python pre/post scripts with a `pm` API, and a Collection Runner with iterations.
- **Organisation** — collections with nested folders and drag-and-drop; environments
  and globals; request history grouped by day; import of Postman v2.0/2.1, OpenAPI 3 /
  Swagger 2 (JSON) and cURL; Postman export; code-snippet generation.
- **General** — dark/light themes, two-pane layout, session restore, console, and
  settings for timeout, SSL and proxy.

### Known limitations
- Python scripts only (no JavaScript); no cookie persistence between requests;
  OpenAPI JSON only (YAML needs PyYAML); Windows-only; no WebSocket / gRPC / mock server.

[2.0]: https://github.com/vaibhav-iq/API-Client/releases/tag/v2.0
[1.0]: https://github.com/vaibhav-iq/API-Client/releases/tag/v1.0
