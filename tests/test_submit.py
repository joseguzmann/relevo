"""The moment after the click, against a local page: nothing here reaches a real form."""
import asyncio
import tempfile
import threading
import unittest
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from relevo import filler
    from relevo.models import Field, Form
except ImportError:  # Playwright lives in the container
    filler = None

PAGE = """<!doctype html><div class="fieldEntry"><label>Name</label><input></div>
<p>Customer success team</p><button id=b>Submit Application</button><script>
b.onclick = async () => { const r = await fetch('/api/non-user-graphql?op=ApiSubmitForm', {method: 'POST'});
  const j = await r.json();
  if (j.ok) document.body.innerHTML = '<h1>Success</h1><p>Your application was successfully submitted.</p>';
  else document.body.insertAdjacentHTML('beforeend', '<div role=alert>flagged as possible spam</div>'); };
</script>"""


class Handler(BaseHTTPRequestHandler):
    def __init__(self, reply, *a, **k):
        self.reply = reply
        super().__init__(*a, **k)

    def _send(self, kind, body):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self._send("text/html", PAGE)

    def do_POST(self):
        self._send("application/json", self.reply)

    def log_message(self, *a):
        pass


@unittest.skipIf(filler is None, "needs Playwright")
class AfterClick(unittest.TestCase):
    def submit(self, reply):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, reply))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        form = Form(f"http://127.0.0.1:{server.server_port}/", [Field("n", "Name", "text", True)])
        with tempfile.TemporaryDirectory() as d:
            result = asyncio.run(filler.run(form, {"n": "Ana"}, f"{d}/a.png", True, (), f"{d}/b.png", 12))
        server.shutdown()
        return result

    def test_confirmed_when_the_form_is_gone(self):
        result = self.submit('{"ok": true}')
        self.assertTrue(result.confirmed)
        self.assertIn('"ok": true', result.server)

    def test_a_refusal_keeps_its_evidence(self):
        result = self.submit('{"ok": false}')
        self.assertFalse(result.confirmed)  # the word "success" on the form page must not count
        self.assertTrue(result.submitted)
        self.assertIn("flagged as possible spam", result.confirmation)
        self.assertIn('"ok": false', result.server)
