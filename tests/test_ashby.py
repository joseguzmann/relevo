import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from relevo import ashby  # noqa: E402

URL = "https://jobs.ashbyhq.com/devsavant/51c02958-afac-4589-8be5-0339dc1003dd"
DATA = json.loads((Path(__file__).parent / "fixtures/ashby_devsavant.json").read_text())


class ParseForm(unittest.TestCase):
    def setUp(self):
        self.form = ashby.parse_form(URL, DATA)
        self.by_label = {f.label: f for f in self.form.fields}

    def test_all_seventeen_required(self):
        self.assertEqual(len(self.form.fields), 17)
        self.assertTrue(all(f.required for f in self.form.fields))

    def test_country_is_a_choice_with_ecuador(self):
        f = self.by_label["If you selected LATAM, please specify your country"]
        self.assertEqual(f.kind, "single_choice")
        self.assertIn("Ecuador", f.options)

    def test_missing_lists_required_without_answer(self):
        self.assertEqual(len(self.form.missing({})), 17)

    def test_invalid_rejects_a_value_outside_the_options(self):
        f = self.by_label["If you selected LATAM, please specify your country"]
        self.assertEqual(self.form.invalid({f.id: "Atlantis"}), [f.label])
        self.assertEqual(self.form.invalid({f.id: "Ecuador"}), [])

    def test_url_parsing(self):
        self.assertEqual(ashby.parse_url(URL + "/application")[0], "devsavant")
        with self.assertRaises(ValueError):
            ashby.parse_url("https://www.linkedin.com/jobs/view/1")

    def test_closed_job_raises(self):
        with self.assertRaises(ValueError):
            ashby.parse_form(URL, {"data": {"jobPosting": None}})


if __name__ == "__main__":
    unittest.main()
