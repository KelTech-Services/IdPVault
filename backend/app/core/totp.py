"""TOTP (RFC 6238) with Python stdlib — no external dependency for the algorithm.
Secret is base32; codes are 6 digits on a 30s step, verified with +/-1 window."""
import base64
import hashlib
import hmac
import secrets
import struct
import time


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, counter: int, digits: int = 6) -> str:
    pad = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret + pad)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    num = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def verify(secret: str, code: str, window: int = 1, step: int = 30) -> bool:
    if not code or not code.isdigit():
        return False
    counter = int(time.time() // step)
    return any(hmac.compare_digest(_code_at(secret, counter + d), code.zfill(6))
               for d in range(-window, window + 1))


def provisioning_uri(secret: str, username: str, issuer: str = "IdPVault") -> str:
    from urllib.parse import quote
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"


def qr_svg(uri: str) -> str:
    import io
    import qrcode
    import qrcode.image.svg
    qr = qrcode.QRCode(border=2, box_size=9, image_factory=qrcode.image.svg.SvgPathImage)
    qr.add_data(uri)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue().decode()
