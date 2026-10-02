"""Security-testing helpers: encoders, hashing, JWT analysis, response header
auditing and sensitive-data scanning.

Pure logic only (no Qt, no network) so it can be unit-tested and reused from the
UI, scripts and background workers. Intended for authorized API security testing.
"""
import base64
import binascii
import hashlib
import hmac
import html
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

# ---------------------------------------------------------------------------
# Severities
# ---------------------------------------------------------------------------
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"
SEV_INFO = "info"
SEV_OK = "ok"

SEVERITY_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2, SEV_INFO: 3, SEV_OK: 4}


@dataclass
class SecFinding:
    title: str
    severity: str = SEV_INFO
    detail: str = ""
    evidence: str = ""
    category: str = "general"

    def to_dict(self) -> Dict[str, str]:
        return {
            "title": self.title,
            "severity": self.severity,
            "detail": self.detail,
            "evidence": self.evidence,
            "category": self.category,
        }


def sort_findings(findings: List[SecFinding]) -> List[SecFinding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))


# ---------------------------------------------------------------------------
# Encoders / decoders
# ---------------------------------------------------------------------------
def _b(text: str) -> bytes:
    return text.encode("utf-8")


def base64_encode(text: str) -> str:
    return base64.b64encode(_b(text)).decode("ascii")


def base64_decode(text: str) -> str:
    pad = "=" * (-len(text.strip()) % 4)
    return base64.b64decode(text.strip() + pad).decode("utf-8", errors="replace")


def base64url_encode(text: str) -> str:
    return base64.urlsafe_b64encode(_b(text)).rstrip(b"=").decode("ascii")


def base64url_decode(text: str) -> str:
    raw = text.strip()
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad).decode("utf-8", errors="replace")


def url_encode(text: str) -> str:
    return quote(text, safe="")


def url_decode(text: str) -> str:
    return unquote(text)


def html_encode(text: str) -> str:
    return html.escape(text)


def html_decode(text: str) -> str:
    return html.unescape(text)


def hex_encode(text: str) -> str:
    return _b(text).hex()


def hex_decode(text: str) -> str:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", text)
    return bytes.fromhex(cleaned).decode("utf-8", errors="replace")


def _hash(algorithm: str) -> Callable[[str], str]:
    return lambda text: hashlib.new(algorithm, _b(text)).hexdigest()


# name -> callable(str) -> str  (used to build the encoder/decoder toolkit UI)
TRANSFORMS: Dict[str, Callable[[str], str]] = {
    "Base64 encode": base64_encode,
    "Base64 decode": base64_decode,
    "Base64 URL encode": base64url_encode,
    "Base64 URL decode": base64url_decode,
    "URL encode": url_encode,
    "URL decode": url_decode,
    "HTML encode": html_encode,
    "HTML decode": html_decode,
    "Hex encode": hex_encode,
    "Hex decode": hex_decode,
    "MD5": _hash("md5"),
    "SHA-1": _hash("sha1"),
    "SHA-256": _hash("sha256"),
    "SHA-512": _hash("sha512"),
}


def transform(name: str, text: str) -> str:
    fn = TRANSFORMS.get(name)
    if fn is None:
        raise ValueError(f"Unknown transform: {name}")
    try:
        return fn(text)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Entropy (used by the secret scanner and JWT secret guessing)
# ---------------------------------------------------------------------------
def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
JWT_ALGS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}

COMMON_JWT_SECRETS = [
    "secret", "password", "123456", "changeme", "admin", "jwt", "token", "key",
    "your-256-bit-secret", "secretkey", "mysecret", "test", "qwerty", "letmein",
    "default", "supersecret", "private", "s3cr3t", "secret123", "jwtsecret",
]


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class JwtParts:
    header: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    header_b64: str = ""
    payload_b64: str = ""
    error: str = ""


def jwt_decode(token: str) -> JwtParts:
    parts = (token or "").strip().split(".")
    if len(parts) < 2:
        return JwtParts(error="Not a JWT: expected at least two dot-separated segments.")
    out = JwtParts(header_b64=parts[0], payload_b64=parts[1], signature=parts[2] if len(parts) > 2 else "")
    try:
        out.header = json.loads(_b64url_decode(parts[0]))
    except Exception as exc:  # noqa: BLE001
        out.error = f"Invalid header: {exc}"
    try:
        out.payload = json.loads(_b64url_decode(parts[1]))
    except Exception as exc:  # noqa: BLE001
        out.error = (out.error + "; " if out.error else "") + f"Invalid payload: {exc}"
    return out


def _epoch_to_str(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:  # noqa: BLE001
        return str(value)


def jwt_verify_hs(token: str, secret: str, secret_b64: bool = False) -> bool:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return False
    header = jwt_decode(token).header
    alg = header.get("alg", "HS256")
    func = JWT_ALGS.get(alg)
    if func is None:
        return False
    key = base64.b64decode(secret) if secret_b64 else _b(secret)
    expected = _b64url_encode(hmac.new(key, f"{parts[0]}.{parts[1]}".encode("ascii"), func).digest())
    return hmac.compare_digest(expected, parts[2])


def jwt_guess_secret(token: str, wordlist: Optional[List[str]] = None) -> Optional[str]:
    for candidate in (wordlist or COMMON_JWT_SECRETS):
        if jwt_verify_hs(token, candidate):
            return candidate
    return None


def jwt_none_token(parts: JwtParts) -> str:
    header = dict(parts.header or {})
    header["alg"] = "none"
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url_encode(json.dumps(parts.payload or {}, separators=(",", ":")).encode("utf-8"))
    return f"{h}.{p}."


def jwt_resign_hs(parts: JwtParts, secret: str, algorithm: str = "HS256", secret_b64: bool = False) -> str:
    header = dict(parts.header or {})
    header["alg"] = algorithm
    func = JWT_ALGS[algorithm]
    key = base64.b64decode(secret) if secret_b64 else _b(secret)
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url_encode(json.dumps(parts.payload or {}, separators=(",", ":")).encode("utf-8"))
    sig = _b64url_encode(hmac.new(key, f"{h}.{p}".encode("ascii"), func).digest())
    return f"{h}.{p}.{sig}"


def jwt_analyze(token: str) -> List[SecFinding]:
    parts = jwt_decode(token)
    findings: List[SecFinding] = []
    if parts.error:
        findings.append(SecFinding("Could not fully decode token", SEV_INFO, parts.error, category="jwt"))
    alg = str(parts.header.get("alg", "")).upper()
    if alg in ("NONE", ""):
        findings.append(SecFinding(
            "Algorithm is 'none'", SEV_HIGH,
            "The token header declares alg=none; if the server accepts it, signatures are not verified.",
            category="jwt"))
    elif alg.startswith("HS"):
        guessed = jwt_guess_secret(token)
        if guessed is not None:
            findings.append(SecFinding(
                "Weak HMAC signing secret", SEV_HIGH,
                f"The signature verifies with the common secret '{guessed}'. Forge tokens at will.",
                evidence=guessed, category="jwt"))
        else:
            findings.append(SecFinding(
                f"HMAC ({alg}) signed", SEV_INFO,
                "Signed with a symmetric secret. Try alg confusion (RS→HS) and weak-secret attacks.",
                category="jwt"))
    elif alg.startswith(("RS", "ES", "PS")):
        findings.append(SecFinding(
            f"Asymmetric signature ({alg})", SEV_INFO,
            "Consider RS→HS algorithm-confusion if the public key is known.", category="jwt"))

    exp = parts.payload.get("exp")
    if exp is None:
        findings.append(SecFinding("No expiry (exp) claim", SEV_MEDIUM,
                                   "Token never expires; stolen tokens stay valid.", category="jwt"))
    else:
        expired = False
        try:
            expired = int(exp) < datetime.now(tz=timezone.utc).timestamp()
        except Exception:  # noqa: BLE001
            pass
        findings.append(SecFinding(
            "Expired" if expired else "Expiry (exp)",
            SEV_LOW if expired else SEV_OK,
            f"exp = {_epoch_to_str(exp)}", category="jwt"))
    for claim in ("sub", "iss", "aud", "role", "roles", "scope", "admin"):
        if claim in parts.payload:
            findings.append(SecFinding(f"Claim '{claim}'", SEV_INFO,
                                       f"{claim} = {parts.payload[claim]!r}  (try tampering)", category="jwt"))
    return sort_findings(findings)


# ---------------------------------------------------------------------------
# Response security-header audit
# ---------------------------------------------------------------------------
def _get(headers: List[Tuple[str, str]], name: str) -> Optional[str]:
    low = name.lower()
    for key, value in headers:
        if key.lower() == low:
            return value
    return None


def audit_headers(headers: List[Tuple[str, str]]) -> List[SecFinding]:
    findings: List[SecFinding] = []

    def present(name: str) -> Optional[str]:
        return _get(headers, name)

    checks = [
        ("Strict-Transport-Security", SEV_MEDIUM,
         "HSTS not set — connections can be downgraded to HTTP."),
        ("Content-Security-Policy", SEV_MEDIUM,
         "No CSP — reduces defence against XSS / data injection."),
        ("X-Content-Type-Options", SEV_LOW,
         "Missing — browsers may MIME-sniff responses. Expected 'nosniff'."),
        ("X-Frame-Options", SEV_LOW,
         "Missing — page may be framed (clickjacking) unless CSP frame-ancestors is set."),
        ("Referrer-Policy", SEV_LOW, "Missing — referrer may leak to third parties."),
    ]
    for name, sev, msg in checks:
        value = present(name)
        if value:
            findings.append(SecFinding(name, SEV_OK, value, category="headers"))
        else:
            findings.append(SecFinding(f"Missing {name}", sev, msg, category="headers"))

    xcto = present("X-Content-Type-Options")
    if xcto and xcto.strip().lower() != "nosniff":
        findings.append(SecFinding("Weak X-Content-Type-Options", SEV_LOW,
                                   f"Expected 'nosniff', got {xcto!r}", category="headers"))

    acao = present("Access-Control-Allow-Origin")
    if acao == "*":
        acac = present("Access-Control-Allow-Credentials")
        if acac and acac.strip().lower() == "true":
            findings.append(SecFinding("Insecure CORS", SEV_HIGH,
                                       "Access-Control-Allow-Origin: * together with Allow-Credentials: true.",
                                       category="headers"))
        else:
            findings.append(SecFinding("Permissive CORS", SEV_MEDIUM,
                                       "Access-Control-Allow-Origin is a wildcard (*).", category="headers"))

    for name in ("Server", "X-Powered-By", "X-AspNet-Version", "X-AspNetMvc-Version"):
        value = present(name)
        if value and re.search(r"[\d]", value):
            findings.append(SecFinding(f"Version disclosure: {name}", SEV_LOW,
                                       f"{name}: {value}", evidence=value, category="headers"))

    return sort_findings(findings)


def audit_cookies(cookies: List[Dict[str, str]]) -> List[SecFinding]:
    findings: List[SecFinding] = []
    for cookie in cookies:
        name = cookie.get("name", "cookie")
        issues = []
        if str(cookie.get("secure", "")).lower() != "true":
            issues.append("not Secure")
        if str(cookie.get("httponly", "")).lower() != "true":
            issues.append("not HttpOnly")
        if issues:
            findings.append(SecFinding(f"Cookie '{name}' flags", SEV_MEDIUM,
                                       ", ".join(issues), category="cookies"))
        else:
            findings.append(SecFinding(f"Cookie '{name}'", SEV_OK, "Secure + HttpOnly", category="cookies"))
    return sort_findings(findings)


# ---------------------------------------------------------------------------
# Sensitive-data / secret scanning
# ---------------------------------------------------------------------------
SECRET_PATTERNS: List[Tuple[str, str, str]] = [
    # (label, regex, severity)
    ("AWS access key id", r"AKIA[0-9A-Z]{16}", SEV_HIGH),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{35}", SEV_HIGH),
    ("Slack token", r"xox[baprs]-[0-9A-Za-z\-]{10,48}", SEV_HIGH),
    ("GitHub token", r"gh[pousr]_[0-9A-Za-z]{36,}", SEV_HIGH),
    ("Private key block", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----", SEV_HIGH),
    ("JWT", r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*", SEV_MEDIUM),
    ("Bearer token", r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}", SEV_MEDIUM),
    ("Password field", r'"?(?:password|passwd|pwd|secret|api[_\-]?key|client[_\-]?secret)"?\s*[:=]\s*"?[^"\s,}&]{4,}', SEV_MEDIUM),
    ("Email address", r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", SEV_LOW),
    ("Credit-card-like number", r"\b(?:\d[ \-]?){13,16}\b", SEV_MEDIUM),
    ("US SSN-like", r"\b\d{3}-\d{2}-\d{4}\b", SEV_MEDIUM),
    ("Private IPv4", r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){1,3}\b", SEV_LOW),
]


def _redact(value: str, keep: int = 4) -> str:
    value = value.strip()
    if len(value) <= keep * 2:
        return value[:keep] + "…"
    return f"{value[:keep]}…{value[-keep:]}"


# ---------------------------------------------------------------------------
# Intruder payload-position markers ($$value$$, Burp-style)
# ---------------------------------------------------------------------------
MARKER = "$$"
_POS_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)


def count_positions(text: str) -> int:
    return len(_POS_RE.findall(text or ""))


def marker_values(text: str) -> List[str]:
    return [m.group(1) for m in _POS_RE.finditer(text or "")]


def strip_markers(text: str) -> str:
    return _POS_RE.sub(lambda m: m.group(1), text or "")


def wrap_selection(text: str, start: int, end: int) -> Tuple[str, int, int]:
    """Wrap text[start:end] in $$…$$ markers; return (new_text, new_start, new_end)."""
    if start == end:
        return text, start, end
    lo, hi = min(start, end), max(start, end)
    new = text[:lo] + MARKER + text[lo:hi] + MARKER + text[hi:]
    return new, lo, hi + 2 * len(MARKER)


def fill_markers(text: str, replacement: Callable[[int, str], str]) -> str:
    """Replace each $$…$$ region using replacement(position_index, original_value)."""
    counter = [-1]

    def repl(match: "re.Match") -> str:
        counter[0] += 1
        return replacement(counter[0], match.group(1))

    return _POS_RE.sub(repl, text or "")


def auto_mark_query(text: str) -> str:
    """Wrap every value in key=value pairs (query string / urlencoded body)."""
    text = strip_markers(text)
    return re.sub(r"([^?&#=\s]+)=([^&#\s]*)", lambda m: f"{m.group(1)}={MARKER}{m.group(2)}{MARKER}", text)


def auto_mark_json(text: str) -> str:
    """Wrap JSON string and numeric values after a colon."""
    text = strip_markers(text)
    text = re.sub(r':(\s*)"([^"]*)"', lambda m: f':{m.group(1)}"{MARKER}{m.group(2)}{MARKER}"', text)
    text = re.sub(r':(\s*)(-?\d+(?:\.\d+)?)(\s*[,}\]\n])',
                  lambda m: f':{m.group(1)}{MARKER}{m.group(2)}{MARKER}{m.group(3)}', text)
    return text


# ---------------------------------------------------------------------------
# Response signatures (errors / stack traces / verbose debug output)
# ---------------------------------------------------------------------------
RESPONSE_SIGNATURES: List[Tuple[str, str, str]] = [
    # (label, regex, severity)
    ("SQL error (MySQL)", r"SQL syntax.*MySQL|valid MySQL result|MySqlException|Warning.*mysqli?_", SEV_HIGH),
    ("SQL error (PostgreSQL)", r"PostgreSQL.*ERROR|pg_query\(\)|PG::\w*Error|unterminated quoted string", SEV_HIGH),
    ("SQL error (MS SQL Server)", r"Microsoft SQL (Server|Native)|ODBC SQL Server Driver|SqlException|Unclosed quotation mark", SEV_HIGH),
    ("SQL error (Oracle)", r"ORA-\d{5}|Oracle error|quoted string not properly terminated", SEV_HIGH),
    ("SQL error (SQLite)", r"SQLiteException|sqlite3\.\w*Error|SQLITE_ERROR", SEV_HIGH),
    ("SQL error (generic)", r"SQLSTATE\[|java\.sql\.SQLException|System\.Data\.SqlClient", SEV_HIGH),
    ("Python traceback", r"Traceback \(most recent call last\)", SEV_MEDIUM),
    ("Java stack trace", r"\bat [\w.$]+\([\w.]+\.java:\d+\)|Exception in thread", SEV_MEDIUM),
    (".NET exception", r"System\.\w+(?:\.\w+)*Exception|at [\w.<>]+\(.*?\) in .+:line \d+", SEV_MEDIUM),
    ("PHP error", r"<b>(?:Fatal error|Warning|Notice|Parse error)</b>|on line \d+ in", SEV_MEDIUM),
    ("Node.js stack trace", r"\bat [\w$.]+ \(.*?:\d+:\d+\)", SEV_MEDIUM),
    ("Debug page / verbose error", r"Whoops, looks like something went wrong|Werkzeug Debugger|DEBUG\s*=\s*True|stack trace:", SEV_MEDIUM),
]

# Payloads/inputs that intentionally delay the response (blind SQLi / command injection)
TIMING_PAYLOAD_RE = re.compile(r"sleep\s*\(|waitfor\s+delay|pg_sleep|benchmark\s*\(|\btimeout\b|ping\s+-[nc]\b", re.IGNORECASE)


def is_timing_payload(payload: str) -> bool:
    return bool(TIMING_PAYLOAD_RE.search(payload or ""))


def scan_response_signatures(text: str, max_findings: int = 50) -> List[SecFinding]:
    findings: List[SecFinding] = []
    if not text:
        return findings
    sample = text[:500_000]
    for label, pattern, sev in RESPONSE_SIGNATURES:
        match = re.search(pattern, sample, re.IGNORECASE)
        if match:
            snippet = match.group(0)
            findings.append(SecFinding(label, sev, "Error/stack-trace signature in the response body",
                                       evidence=snippet[:120], category="signature"))
            if len(findings) >= max_findings:
                break
    return sort_findings(findings)


def response_signature_labels(text: str) -> List[str]:
    """Just the labels of matched signatures — used for compact flagging in the Intruder."""
    return [f.title for f in scan_response_signatures(text)]


def scan_secrets(text: str, max_findings: int = 100) -> List[SecFinding]:
    findings: List[SecFinding] = []
    seen: set = set()
    if not text:
        return findings
    sample = text[:500_000]
    for label, pattern, sev in SECRET_PATTERNS:
        for match in re.finditer(pattern, sample):
            snippet = match.group(0)
            key = (label, snippet[:40])
            if key in seen:
                continue
            seen.add(key)
            findings.append(SecFinding(label, sev, f"Matched at offset {match.start()}",
                                       evidence=_redact(snippet), category="secrets"))
            if len(findings) >= max_findings:
                return sort_findings(findings)
    # High-entropy standalone tokens (possible keys) not already flagged.
    for token in set(re.findall(r"[A-Za-z0-9+/_\-]{24,}", sample)):
        if any(token in f.evidence or f.evidence in token for f in findings):
            continue
        if shannon_entropy(token) >= 4.0:
            findings.append(SecFinding("High-entropy string", SEV_LOW,
                                       f"Entropy {shannon_entropy(token):.1f} — possible key/token",
                                       evidence=_redact(token), category="secrets"))
            if len(findings) >= max_findings:
                break
    return sort_findings(findings)
