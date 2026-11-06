"""Web Push. The payload is encrypted to the phone; Apple only relays it."""
import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

log = logging.getLogger("relevo.push")


def keys(folder: str) -> tuple[str, str]:
    """(private key file, public key for the browser). Created once."""
    pem = Path(folder) / "vapid.pem"
    if not pem.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        pem.chmod(0o600)
    key = serialization.load_pem_private_key(pem.read_bytes(), None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return str(pem), base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def send(store, pem: str, site: str, title: str, body: str, item_id: int) -> int:
    sent = 0
    for sub in store.subs():
        try:
            webpush(sub, json.dumps({"title": title, "body": body, "id": item_id}), vapid_private_key=pem,
                    vapid_claims={"sub": site})
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):
                store.drop_sub(sub["endpoint"])
            else:
                log.warning("push failed: %s", str(e)[:160])
    return sent
