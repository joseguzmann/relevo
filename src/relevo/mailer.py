"""Sends one email through the Gmail API. The token can only send: it has no way to read the mailbox."""
import base64
import json
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path


def _access_token(token_file: str) -> str:
    t = json.loads(Path(token_file).read_text())
    body = urllib.parse.urlencode({"client_id": t["client_id"], "client_secret": t["client_secret"],
                                   "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen(t["token_uri"], body, timeout=20) as r:
        return json.load(r)["access_token"]


def build(sender: str, to: str, subject: str, body: str, attachment: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    path = Path(attachment)
    msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename="Jose_Guzman_CV.pdf")
    return msg


def send(token_file: str, msg: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    req = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                                 json.dumps({"raw": raw}).encode(),
                                 {"Authorization": f"Bearer {_access_token(token_file)}",
                                  "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["id"]
