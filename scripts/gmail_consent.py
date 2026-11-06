#!/usr/bin/env python3
"""Run on your computer once: asks Google for a token that can only send mail.
    python3 scripts/gmail_consent.py <client_id> <client_secret> <output.json>"""
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

client_id, client_secret, output = sys.argv[1:4]
PORT, SCOPE = 8765, "https://www.googleapis.com/auth/gmail.send"
redirect, state, got = f"http://127.0.0.1:{PORT}/", secrets.token_urlsafe(16), {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] == state:
            got["code"] = q.get("code", [""])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Listo, ya puedes cerrar esta pagina.")

    def log_message(self, *a):
        pass


url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state,
    "access_type": "offline", "prompt": "consent", "login_hint": ""})
print(url)
webbrowser.open(url)
server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
while "code" not in got:
    server.handle_request()
body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "code": got["code"],
                               "grant_type": "authorization_code", "redirect_uri": redirect}).encode()
with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=20) as r:
    token = json.load(r)
if "refresh_token" not in token:
    sys.exit("Google did not return a refresh token")
with open(output, "w") as f:
    json.dump({"client_id": client_id, "client_secret": client_secret, "refresh_token": token["refresh_token"],
               "token_uri": "https://oauth2.googleapis.com/token", "scope": SCOPE}, f)
print("saved", output)
