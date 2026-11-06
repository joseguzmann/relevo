import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from relevo import forms, manatal  # noqa: E402

URL = "https://www.careers-page.com/idelsoft/job/6383VW7R"
DATA = json.loads((Path(__file__).parent / "fixtures/manatal_idelsoft.json").read_text())


class ParseForm(unittest.TestCase):
    def setUp(self):
        self.form = manatal.parse_form(URL, DATA["api"], DATA["dom"])
        self.by_id = {f.id: f for f in self.form.fields}

    def test_kinds_come_from_the_page(self):
        self.assertEqual(self.form.url, URL + "/apply")
        self.assertEqual(self.by_id["1352950"].kind, "file")
        self.assertEqual(self.by_id["1352947"].kind, "email")
        self.assertEqual(self.by_id["1352955"].kind, "single_choice")
        self.assertIn("Immediately", self.by_id["1352955"].options)
        self.assertEqual(self.by_id["1352949"].kind, "long_text")

    def test_terms_box_is_a_required_field(self):
        self.assertTrue(self.by_id["terms_and_condition"].required)

    def test_a_required_subform_stops_it(self):
        api = DATA["api"] + [{"id": 1, "label": "Experience", "is_required": True, "field_type": "experiences",
                              "client_field_slug": ""}]
        with self.assertRaises(ValueError):
            manatal.parse_form(URL, api, DATA["dom"])

    def test_link_is_routed(self):
        self.assertEqual(forms.find(URL + "?ref=x")[0], "manatal")
