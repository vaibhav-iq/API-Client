# API Client

API Client is a Windows desktop app for sending HTTP requests to APIs and inspecting the responses.
Organise requests into collections, switch between environments (e.g. Dev, QA, Prod), add automated checks,
and run whole collections in one go.

**Version 2.0**

> 📖 **New here? Read the [illustrated User Guide](docs/README.md)** for a screenshot walkthrough of
> requests, authorization, environments, testing and more.

---

## Contents

1. [Requirements](#1-requirements)
2. [First-time setup](#2-first-time-setup)
3. [Run the app](#3-run-the-app)
4. [Getting started](#4-getting-started)
5. [Build a standalone EXE](#5-build-a-standalone-exe)
6. [Where your data is stored](#6-where-your-data-is-stored)
7. [Troubleshooting](#7-troubleshooting)
8. [Features](#8-features)
9. [Keyboard shortcuts](#9-keyboard-shortcuts)
10. [Project structure](#10-project-structure)

---

## 1. Requirements

| Item | Version |
| --- | --- |
| Windows | 10 or 11 |
| Python | 3.9 or newer (tested with 3.14) |
| Python packages | `PyQt5` 5.15+, `requests` 2.31+, `cryptography` 42+ (listed in `requirements.txt`) |
| PyInstaller | Only needed to build the EXE |

Check that Python is available:

```powershell
python --version
```

If the command is not found, install Python from <https://www.python.org/downloads/> and tick
**"Add python.exe to PATH"** during installation.

---

## 2. First-time setup

Open PowerShell in the project folder and install the dependencies:

```powershell
cd "C:\Tools\ECWEvents API"
python -m pip install -r requirements.txt
```

### Optional: use a virtual environment

A virtual environment keeps the app's packages separate from other Python projects.

```powershell
cd "C:\Tools\ECWEvents API"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks the activation script, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Activate the environment (`.\.venv\Scripts\Activate.ps1`) each time you open a new terminal before running
or building the app.

---

## 3. Run the app

From the project folder, use any one of these:

```powershell
python APIClient.pyw                # recommended
python -m api_client                # run as a module
```

You can also double-click **`APIClient.pyw`** in File Explorer (no console window is shown).

On the first start the app imports **`openapi.json`** from the folder it is started in (if the file exists)
as a collection. It is imported only once.

---

## 4. Getting started

### Send your first request

1. Click **New → HTTP Request** (or press **Ctrl+T**).
2. Choose the method, type the URL, e.g. `https://httpbin.org/get`.
3. Press **Send** (or **Ctrl+Enter**).
4. Press **Ctrl+S** to save the request into a collection.

Tip: paste a `curl ...` command into the empty URL bar to turn it into a request.

### Set the base URL

Requests imported from `openapi.json` use URLs like `{{baseUrl}}/api/v1/...`. Set `baseUrl` in one of two places:

- **One server:** right-click the collection → **Edit (auth, variables, docs)…** → **Variables** tab →
  set `baseUrl`, e.g. `https://api.example.com` → **Save**.
- **Several servers (Dev / QA / Prod):** click **New → Environment**, add a variable `baseUrl` with that
  server's address, and pick the environment in the selector at the top right. Environment values override
  collection values.

In the URL bar a variable shows **orange** when it is defined and **red** when it is not. Hover it to see its value,
or click the **eye** icon next to the environment selector to see all values.

### Authorization

Set auth once on the collection (right-click → **Edit…** → **Authorization**) and every request set to
**Inherit auth from parent** uses it. For OAuth 2.0, fill in the token settings and click
**Get New Access Token**.

### Chain requests and add tests

- **Tests** tab → *Assertions*: e.g. *Status code equals 200*.
- **Tests** tab → *Set variables from response*: e.g. store `$.access_token` as `accessToken` in the environment,
  then use `{{accessToken}}` in later requests.
- **Scripts** tab: Python pre-request / post-response scripts using the built-in `pm` object. Pick a snippet on the
  right to get started.
- Right-click a collection or folder → **Run Collection** to run all requests with a pass/fail summary.

---

## 5. Build a standalone EXE

The build creates a single `.exe` that runs on Windows PCs **without Python installed**.

### Step 1 – Install PyInstaller (once)

```powershell
cd "C:\Tools\ECWEvents API"
python -m pip install pyinstaller
```

(or let the build script do it with `--install-pyinstaller`, see below)

### Step 2 – Close the app

Close any running copy of the EXE you are about to rebuild, otherwise Windows cannot overwrite the file.

### Step 3 – Build

**Python build script (recommended):**

```powershell
python build_onefile.py
```

| Option | What it does |
| --- | --- |
| `--app-name APIClient` | Name of the EXE (default `api-client`) |
| `--install-pyinstaller` | Installs / upgrades PyInstaller before building |
| `--python "C:\path\to\python.exe"` | Build with a specific Python installation |

Examples:

```powershell
python build_onefile.py --app-name APIClient
python build_onefile.py --install-pyinstaller --app-name APIClient
```

**PowerShell build script (alternative):**

```powershell
.\build-onefile.ps1
.\build-onefile.ps1 -AppName APIClient
.\build-onefile.ps1 -AppName APIClient -InstallPyInstaller
.\build-onefile.ps1 -PythonExe "C:\path\to\python.exe"
```

The build takes about 1–2 minutes. The EXE gets the app's orange `</>` icon from `assetspp.ico`;
if that file is missing, the build scripts create it automatically.

### EXE icon

The icon is generated from the same logo the app draws on screen. To recreate it (e.g. after changing the logo in
`api_client	heme.py`), run:

```powershell
python make_icon.py
```

then build again.

### Step 4 – Run the EXE

```powershell
.\dist\onefile\api-client.exe      # one-file build (or APIClient.exe with --app-name APIClient)
.\dist\onedir\api-client\api-client.exe   # one-dir build
```

| Folder | Contents |
| --- | --- |
| `dist\onefile\` | The finished single-file EXE – the only file you need to share |
| `dist\onedir\` | The one-dir build (EXE + `_internal\`); used by the installer |
| `build\` | Temporary build files (safe to delete) |
| `*.spec` | PyInstaller spec generated by the build (safe to delete; it is regenerated) |

### Distributing the EXE

- Copy just the `.exe` to another PC and double-click it. No installation is required.
- Each user's collections and settings are stored in their own profile (see next section), not next to the EXE,
  so replacing the EXE with a newer build keeps all data.
- To have `openapi.json` imported automatically on first start, place it in the folder the EXE is started from.
- The first start of a one-file EXE takes a few seconds while it unpacks.

---

## 6. Where your data is stored

Everything is saved automatically in **`%LOCALAPPDATA%\APIClient\`**
(e.g. `C:\Users\<you>\AppData\Local\APIClient`). Open it from **Help → Open Data Folder**.

| File | Contents |
| --- | --- |
| `settings.json` | Preferences (theme, timeout, proxy, layout, font size) |
| `collections.json` | Collections, folders, saved requests, collection variables and auth |
| `environments.json` | Environments, globals and the active environment |
| `session.json` | Open tabs (including unsaved edits) and window layout |
| `cache.sqlite3` | Request history (last 1000 requests) |
| `error.log` | Details of unexpected errors, if any occurred |

- **Back up / move to another PC:** close the app and copy the whole folder.
- **Reset the app:** close the app and delete the folder (or a single file, e.g. `cache.sqlite3` to clear history).
- **Share with a teammate:** use **Export** on a collection or environment (Postman format) instead of sharing the
  folder.

> **Security:** tokens, passwords and "secret" variables are stored as plain text in these files; *secret* only
> hides the value on screen. Remove sensitive values before exporting and sharing.
>
> Scripts are Python and run with your Windows user's permissions when you send a request. Only run scripts
> you trust, especially in collections received from others.

---

## 7. Troubleshooting

| Problem | Solution |
| --- | --- |
| `python` is not recognised | Install Python and tick "Add python.exe to PATH", or use `py` instead of `python`. |
| `No module named PyQt5` / `requests` | Run `python -m pip install -r requirements.txt` (inside the virtual environment if you use one). |
| `No module named PyInstaller` | Run `python -m pip install pyinstaller`, or build with `--install-pyinstaller`. |
| Build fails with "Access is denied" / "Permission denied" | The EXE is still running – close it (check Task Manager) and build again. |
| Antivirus quarantines or blocks the EXE | Common with one-file PyInstaller builds. Add an exception for `dist\` or run from source. |
| SSL / certificate error when sending | Settings → General → turn off *SSL certificate verification* (only for hosts you trust). |
| Requests fail behind a corporate proxy | Settings → Proxy → use the system proxy or add a custom proxy. |
| `{{baseUrl}}` is shown in red | The variable is not defined – see [Set the base URL](#set-the-base-url). |
| The EXE still shows an old or generic icon in Explorer | Windows caches icons. Rename the EXE or run `ie4uinit.exe -show`, then refresh the folder. |
| The window opens off-screen or looks wrong | Close the app and delete `session.json` from the data folder. |
| Something crashed or behaves oddly | Check `error.log` in the data folder. |

---

## 8. Features

**Workspace**
- Dark and light themes; the Windows title bar follows the theme
- Multiple request tabs with method badges, unsaved-change indicators and session restore
- Sidebar with Collections, Environments and History; console panel; side-by-side (two-pane) layout

**Requests**
- GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, TRACE
- `{{variable}}` highlighting, hover values and autocomplete; paste a cURL command to import it
- Query params synced with the URL; path variables (`/users/:id`)
- Headers and params tables with enable checkboxes, descriptions and Bulk Edit
- Body: none, form-data (text and files), x-www-form-urlencoded, raw (JSON/Text/XML/HTML/JavaScript), binary, GraphQL
- Auth: inherit from parent, API Key, Bearer, Basic, Digest, JWT (HS256/384/512), OAuth 2.0
  (client credentials / password grant)
- Markdown documentation per request

**Responses**
- Status, time and size with breakdown tooltips
- Pretty, Raw, Preview (HTML / images) and JSON Tree views; search, wrap, copy, save to file
- Cookies, Headers, Test Results and Timeline

**Testing and automation**
- No-code assertions and "set variables from response" rules
- Python pre-request / post-response scripts with a `pm` API
- Collection Runner with iterations, delay and pass/fail summary

**Security testing** (see the [Security testing guide](docs/08-security-testing.md))
- Automatic response audit: security headers, cookie flags, exposed secrets / PII
- Encoder/decoder & hashing toolkit (Base64/URL/HTML/Hex, MD5/SHA)
- JWT workbench: decode, weak-secret & `alg:none` checks, re-sign
- Repeater (rapid resend with send history) and Intruder (payload fuzzer with presets)
- Multi-identity IDOR / BOLA matrix; built-in security assertion snippets
- Built-in intercepting proxy (HTTP/HTTPS MITM) with its own CA: capture live traffic, then send to Repeater / Intruder

**Organisation**
- Collections with nested folders, drag and drop, collection and folder auth, collection variables
- Environments and globals, active environment selector and quick look
- Request history grouped by day
- Import: Postman Collection v2.0/v2.1, Postman Environment, OpenAPI 3.x / Swagger 2.0 (JSON), cURL –
  or drag files onto the window
- Export: Postman Collection v2.1 and Postman Environment
- Code snippets: cURL, Python, JavaScript fetch, Node axios, C# HttpClient, PowerShell, Go

---

## 9. Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+T / Ctrl+N | New request tab |
| Ctrl+Enter | Send |
| Ctrl+S / Ctrl+Shift+S | Save / Save As |
| Ctrl+W | Close tab |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+L | Focus URL bar |
| Ctrl+O | Import |
| Ctrl+Shift+C | Code snippet |
| Ctrl+F | Find in the focused editor / response |
| Ctrl+\ | Toggle sidebar |
| Ctrl+Alt+C | Toggle console |
| Ctrl+Alt+V | Toggle two-pane view |
| Ctrl+, | Settings |

---

## 10. Project structure

```
ECWEvents API/
├── APIClient.pyw             Launcher script
├── requirements.txt          Python dependencies
├── build_onefile.py          EXE build script (Python)
├── build-onefile.ps1         EXE build script (PowerShell)
├── make_icon.py              Generates assets/app.ico from the app logo
├── assets/app.ico            EXE icon
├── dist/                     Built EXE / one-dir output (created by the build)
└── api_client/
    ├── app.py                Application start-up
    ├── main_window.py        Main window: tabs, collections, environments, history, session
    ├── request_tab.py        Request editor tab (URL bar, sub-tabs, response)
    ├── request_panels.py     Authorization, Body, Scripts, Tests and Docs panels
    ├── response_panel.py     Response viewer
    ├── sidebar.py            Collections / Environments / History panels
    ├── dialogs.py            Save, import, code snippet, collection settings, environment editor
    ├── runner.py             Collection Runner
    ├── settings_dialog.py    Settings dialog
    ├── kv_table.py           Key/value table widget
    ├── code_editor.py        Code editor with highlighting and search
    ├── widgets.py            Shared widgets
    ├── engine.py             Variables, request building, HTTP, tests, extraction
    ├── security.py           Security logic: encoders, JWT, header audit, secret scan, signatures
    ├── security_tools.py     Security UI: Encoder, JWT, Repeater, Intruder, IDOR/BOLA matrix
    ├── ca.py                 Self-signed CA + per-host leaf certs for HTTPS interception
    ├── proxy_server.py       Intercepting HTTP/HTTPS proxy (capture engine)
    ├── proxy_panel.py        Proxy sidebar panel + captured request/response viewer
    ├── help_docs.py          In-app Markdown documentation viewer (Help → Documentation)
    ├── scripting.py          Python scripts with the pm API
    ├── importers.py          Postman / OpenAPI / cURL import and export
    ├── codegen.py            Code snippet generators
    ├── models.py             Data model
    ├── storage.py            Saving collections, environments, session and history
    ├── config_store.py       Settings file
    └── theme.py              Colours, styles and icons
```
