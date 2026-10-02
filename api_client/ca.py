"""Self-signed CA for the intercepting proxy.

Generates a root CA (like Burp's) that the user exports and trusts, then mints
per-host leaf certificates on the fly so HTTPS traffic can be intercepted. Needs
the `cryptography` package. Nothing here touches Qt or the network.
"""
import datetime
import ipaddress
import ssl
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_NAME = "API Client Proxy CA"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class CertAuthority:
    """Loads (or creates) a root CA and issues leaf certs for HTTPS MITM."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cert_path = self.dir / "ca_cert.pem"
        self.key_path = self.dir / "ca_key.pem"
        self.leaf_dir = self.dir / "leaf"
        self.leaf_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._contexts: Dict[str, ssl.SSLContext] = {}
        self._ca_cert: Optional[x509.Certificate] = None
        self._ca_key = None
        if self.exists():
            self._load()

    # -- lifecycle ---------------------------------------------------------------
    def exists(self) -> bool:
        return self.cert_path.exists() and self.key_path.exists()

    def created_at(self) -> Optional[datetime.datetime]:
        if not self._ca_cert:
            return None
        try:
            return self._ca_cert.not_valid_before_utc
        except AttributeError:  # older cryptography
            return self._ca_cert.not_valid_before

    def _load(self) -> None:
        self._ca_cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self._ca_key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)

    def ensure(self) -> None:
        if not self.exists():
            self.generate()
        elif self._ca_cert is None:
            self._load()

    def generate(self) -> None:
        """Create (or replace) the root CA key + certificate."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "API Client"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - datetime.timedelta(days=1))
            .not_valid_after(_now() + datetime.timedelta(days=365 * 5))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    content_commitment=False, key_encipherment=False, data_encipherment=False,
                    key_agreement=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        self.key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self._ca_cert, self._ca_key = cert, key
        # invalidate cached leaf contexts (they were signed by the old CA)
        self._contexts.clear()
        for stale in self.leaf_dir.glob("*.pem"):
            try:
                stale.unlink()
            except OSError:
                pass

    # -- export ------------------------------------------------------------------
    def cert_pem(self) -> bytes:
        self.ensure()
        return self._ca_cert.public_bytes(serialization.Encoding.PEM)

    def cert_der(self) -> bytes:
        self.ensure()
        return self._ca_cert.public_bytes(serialization.Encoding.DER)

    def export(self, path: Path) -> None:
        path = Path(path)
        data = self.cert_der() if path.suffix.lower() in (".der", ".cer", ".crt") else self.cert_pem()
        path.write_bytes(data)

    # -- leaf certs for MITM -----------------------------------------------------
    def context_for(self, host: str) -> ssl.SSLContext:
        """A server-side SSL context presenting a leaf cert for `host`."""
        self.ensure()
        with self._lock:
            ctx = self._contexts.get(host)
            if ctx is not None:
                return ctx
            cert_file, key_file = self._leaf_files(host)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
            self._contexts[host] = ctx
            return ctx

    def _leaf_files(self, host: str) -> Tuple[Path, Path]:
        safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in host)[:80]
        cert_file = self.leaf_dir / f"{safe}.cert.pem"
        key_file = self.leaf_dir / f"{safe}.key.pem"
        if cert_file.exists() and key_file.exists():
            return cert_file, key_file

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            san = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:60])]))
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - datetime.timedelta(days=1))
            .not_valid_after(_now() + datetime.timedelta(days=365 * 2))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([san]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(self._ca_key, hashes.SHA256())
        )
        key_file.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cert_file, key_file
