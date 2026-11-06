import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from relevo import forms, greenhouse  # noqa: E402

URL = "https://job-boards.greenhouse.io/arionkoder/jobs/5470073008"
DATA = json.loads((Path(__file__).parent / "fixtures/greenhouse_arionkoder.json").read_text())


class ParseForm(unittest.TestCase):
    def setUp(self):
        self.form = greenhouse.parse_form(URL, DATA)
        self.by_id = {f.id: f for f in self.form.fields}

    def test_fields_and_kinds(self):
        self.assertEqual(self.form.ats, "greenhouse")
        self.assertEqual(self.by_id["email"].kind, "email")
        self.assertEqual(self.by_id["resume"].kind, "file")
        self.assertEqual(self.by_id["location"].kind, "location")
        self.assertEqual(self.by_id["question_19157366008[]"].kind, "multi_choice")
        self.assertIn("C1 - Advanced", self.by_id["question_19157364008"].options)

    def test_hidden_and_text_twins_are_left_out(self):
        self.assertFalse({"latitude", "longitude", "resume_text"} & set(self.by_id))

    def test_links_are_routed(self):
        self.assertEqual(forms.find("see https://boards.greenhouse.io/acme/jobs/123?gh_src=x"),
                         ("greenhouse", "https://boards.greenhouse.io/acme/jobs/123"))
        self.assertEqual(forms.find("https://jobs.ashbyhq.com/a/51c02958-afac-4589-8be5-0339dc1003dd")[0], "ashby")
        self.assertEqual(forms.find("https://example.com"), ("", ""))
