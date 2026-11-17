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


@unittest.skipIf(filler is None or filler.async_playwright is None, "needs Playwright")
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
        self.assertEqual(result.requests[0]["method"], "POST")
        self.assertIn("ApiSubmitForm", result.requests[0]["url"])

    def test_a_refusal_keeps_its_evidence(self):
        result = self.submit('{"ok": false}')
        self.assertFalse(result.confirmed)  # the word "success" on the form page must not count
        self.assertTrue(result.submitted)
        self.assertIn("flagged as possible spam", result.confirmation)
        self.assertIn('"ok": false', result.server)


CONSENT = ("I understand and consent that Camunda may transfer my application data to Ashby who will store my application "
           "data on Camunda's behalf in the US (consent in accord with Art. 49 I a GDPR). The US data protection level is "
           "not equivalent to the EU one.")
CAMUNDA = f"""<!doctype html><form>
<div class="fieldEntry"><label>Are you legally eligible to work in the country where you are planning to work from?</label>
<input type=checkbox id=elig></div>
<div class="fieldEntry"><label>Country</label><input id=country></div>
<div class="fieldEntry"><label><input type=checkbox id=ok> {CONSENT}</label></div>
</form><button id=b>Submit Application</button><script>
b.onclick = async () => {{ if (!document.getElementById('ok').checked) return;
  await fetch('/submit', {{method: 'POST', body: document.getElementById('country').value}});
  document.body.innerHTML = '<h1>Success</h1><p>Your application was successfully submitted.</p>'; }};
</script>"""


class Page(Handler):
    def do_GET(self):
        self._send("text/html", CAMUNDA)


@unittest.skipIf(filler is None or filler.async_playwright is None, "needs Playwright")
class CamundaCase(unittest.TestCase):
    """17-nov: 'Country' hit the eligibility field, and the accepted consent (cut at 200, ending in a space) was not found."""

    def run_form(self, accepted):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Page, '{"ok": true}'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        form = Form(f"http://127.0.0.1:{server.server_port}/", [
            Field("e", "Are you legally eligible to work in the country where you are planning to work from?", "yes_no", False),
            Field("c", "Country", "text", False)])
        with tempfile.TemporaryDirectory() as d:
            result = asyncio.run(filler.run(form, {"c": "Ecuador"}, f"{d}/a.png", True, accepted, f"{d}/b.png", 12))
        server.shutdown()
        return result

    def unknown_text(self):
        return CONSENT[:200]  # what _unknown reports

    def test_country_fills_its_own_field_and_the_consent_is_ticked(self):
        result = self.run_form((self.unknown_text(),))
        self.assertEqual(result.failed, [])
        self.assertTrue(result.confirmed)

    def test_a_consent_not_accepted_blocks_the_submit_without_raising(self):
        result = self.run_form(())
        self.assertFalse(result.submitted)
        self.assertEqual(len(result.unknown), 1)


CHALLENGE_PAGE = """<!doctype html><div class="fieldEntry"><label>Name</label><input></div>
<button id=b>Submit Application</button><script>
b.onclick = () => document.body.insertAdjacentHTML('beforeend',
  '<iframe title="recaptcha challenge expires in two minutes" src="/recaptcha/api2/bframe?k=x" style="width:400px;height:580px"></iframe>');
</script>"""


class ChallengeHandler(Handler):
    def do_GET(self):
        self._send("text/html", CHALLENGE_PAGE if self.path == "/" else "<p>challenge</p>")


@unittest.skipIf(filler is None or filler.async_playwright is None, "needs Playwright")
class ImageCaptchaOnClick(unittest.TestCase):
    """17-nov, Camunda (Ashby): the click raised an image captcha and Relevo waited and called it 'no confirmation'."""

    def test_the_challenge_is_seen_and_nothing_went_out(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(ChallengeHandler, "{}"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        form = Form(f"http://127.0.0.1:{server.server_port}/", [Field("n", "Name", "text", True)])
        with tempfile.TemporaryDirectory() as d:
            result = asyncio.run(filler.run(form, {"n": "Ana"}, f"{d}/a.png", True, (), f"{d}/b.png", 12))
        server.shutdown()
        self.assertTrue(result.challenge)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.requests, [])
