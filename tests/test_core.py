import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
try:
    import playwright.async_api  # noqa: F401
except ImportError:  # the core tests run without a browser
    sys.modules["playwright"] = type(sys)("playwright")
    sys.modules["playwright.async_api"] = type("m", (), {"async_playwright": None})
from relevo import ashby, prepare, route  # noqa: E402
from relevo.store import Store  # noqa: E402

URL = "https://jobs.ashbyhq.com/devsavant/51c02958-afac-4589-8be5-0339dc1003dd"
FORM = ashby.parse_form(URL, json.loads((Path(__file__).parent / "fixtures/ashby_devsavant.json").read_text()))


class Route(unittest.TestCase):
    def test_linkedin_job_is_never_a_way_out(self):
        self.assertEqual(route.decide({"url": "https://www.linkedin.com/jobs/view/1", "description": "a@b.co"})[0], "manual")

    def test_post_with_email(self):
        job = {"url": "https://www.linkedin.com/posts/x", "description": "send it to jobs@acme.io please"}
        self.assertEqual(route.decide(job), ("email", "jobs@acme.io"))

    def test_post_without_email(self):
        self.assertEqual(route.decide({"url": "https://www.linkedin.com/posts/x", "description": "DM me"})[0], "manual")

    def test_ashby_found_in_an_aggregator_page(self):
        job = {"url": "https://remoteyeah.com/x", "page_text": f'href="{URL}/application"'}
        self.assertEqual(route.decide(job), ("ashby", URL))


class Merge(unittest.TestCase):
    def field(self, text):
        return next(f for f in FORM.fields if text in f.label)

    def merged(self, text, value, state="ready"):
        f = self.field(text)
        out = prepare.merge(FORM, [{"id": f.id, "value": value, "state": state, "source": "s"}], "/kit/cv/backend.pdf")
        return next(x for x in out if x["id"] == f.id)

    def test_consent_is_never_answered_by_the_model(self):
        got = self.merged("Consent", "I authorize")
        self.assertEqual((got["value"], got["state"]), (None, "missing"))

    def test_salary_always_needs_a_look(self):
        self.assertEqual(self.merged("compensation", 4000)["state"], "confirm")

    def test_value_outside_the_options_is_dropped(self):
        got = self.merged("specify your country", "Atlantis")
        self.assertEqual((got["value"], got["state"]), (None, "missing"))

    def test_required_comes_from_the_form(self):
        self.assertTrue(self.merged("Full Name", "x")["required"])

    def test_file_is_the_family_cv(self):
        self.assertEqual(self.merged("Resume", "evil.pdf")["value"], "/kit/cv/backend.pdf")


class Claim(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_two_taps_send_once(self):
        i = self.store.add(1, "ashby", "ready", {})
        self.assertTrue(self.store.claim(i))
        self.assertFalse(self.store.claim(i))

    def test_a_sent_item_cannot_be_edited(self):
        i = self.store.add(2, "ashby", "ready", {})
        self.store.finish(i, "sent")
        self.assertFalse(self.store.save_data(i, {"x": 1}))
        self.assertFalse(self.store.claim(i))

    def test_same_job_enters_once(self):
        self.assertIsNotNone(self.store.add(3, "email", "ready", {}))
        self.assertIsNone(self.store.add(3, "email", "ready", {}))


class Track(unittest.TestCase):
    def test_site_link_gets_the_code(self):
        from relevo.track import in_text
        self.assertEqual(in_text("my website is https://joseguzman.dev.", "ab2c"), "my website is https://joseguzman.dev/r/ab2c.")
        self.assertEqual(in_text("joseguzman.dev/ and more", "ab2c"), "https://joseguzman.dev/r/ab2c and more")
        self.assertEqual(in_text("https://joseguzman.dev/r/zzzz", "ab2c"), "https://joseguzman.dev/r/zzzz")
        self.assertEqual(in_text("me@joseguzman.dev", "ab2c"), "me@joseguzman.dev")


class Origin(unittest.TestCase):
    def test_post_board_and_ats(self):
        from relevo.route import origin
        post = origin({"url": "https://www.linkedin.com/posts/a_b"}, "email", "x@y.co")
        self.assertEqual((post["channel"], post["exit"]), ("Post de LinkedIn", "Correo a x@y.co"))
        ats = origin({"url": "https://jobs.ashbyhq.com/c/1", "sources": ["ashby"], "location": "San Francisco"}, "ashby")
        self.assertEqual((ats["channel"], ats["source"], ats["location"]), ("ATS", "Ashby", "San Francisco"))
        board = origin({"url": "https://weworkremotely.com/j", "sources": ["weworkremotely"]}, "manual")
        self.assertEqual((board["channel"], board["source"], board["exit"]), ("Bolsa", "We Work Remotely", "A mano"))
        self.assertEqual(origin({"url": "https://acme.io/j"}, "manual")["source"], "acme.io")


if __name__ == "__main__":
    unittest.main()
