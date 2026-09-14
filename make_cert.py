"""Generate a self-signed cert for local HTTPS, so phones on the LAN get a secure
context and the browser will hand out GPS. Re-run whenever your Wi-Fi IP changes."""

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = Path(__file__).resolve().parent / "certs"


def lan_ip():
    # Pick the interface that would carry outbound traffic; no packet is actually sent.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def main():
    CERT_DIR.mkdir(exist_ok=True)
    ip = lan_ip()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
    now = datetime.datetime.now(datetime.timezone.utc)

    # Browsers match the address you type against the SAN list, not the common name,
    # so every address the phone or laptop might use has to be listed here.
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address(ip)),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    (CERT_DIR / "key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (CERT_DIR / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Certificate written for {ip}. Open https://{ip}:8000 on your phone.")


if __name__ == "__main__":
    main()
