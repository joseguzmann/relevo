import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from relevo import breezy, forms  # noqa: E402

DATA = json.loads((Path(__file__).parent / "fixtures/breezy_forms.json").read_text())
URRLY = "https://urrly.breezy.hr/p/52a8d92b1010-ai-automation-engineer-remote-latam-philippines"
IAM = "https://inallmedia-llc.breezy.hr/p/31d5583bb722-2121-backend-software-engineer-shared-platforms"
COGENT = "https://cogentlabs.breezy.hr/p/da9ab38c15b5-ai-engineer"


class ParseForm(unittest.TestCase):
    def test_standard_fields_follow_the_form(self):
        form = breezy.parse_form(URRLY, DATA["urrly"])
        by_id = {f.id: f for f in form.fields}
        self.assertEqual(form.url, URRLY + "/apply")
        self.assertEqual(form.ats, "breezy")
        self.assertEqual(by_id["cResume"].kind, "file")
        self.assertTrue(by_id["cPhoneNumber"].required)
        self.assertFalse(by_id["cCoverLetter"].required)
        self.assertNotIn("cAddress", by_id)  # hidden in this job
        self.assertNotIn("cSalary", by_id)

    def test_a_required_subform_stops_it(self):
        with self.assertRaises(ValueError):  # In All Media asks for education
            breezy.parse_form(IAM, DATA["inallmedia"])

    def test_questions_keep_type_options_and_page_name(self):
        data = json.loads(json.dumps(DATA["inallmedia"]))
        data["position"]["application_form"]["education"] = "optional"
        by_id = {f.id: f for f in breezy.parse_form(IAM, data).fields}
        years = by_id["section_1747783397549_question_1"]
        self.assertEqual(years.kind, "single_choice")
        self.assertIn("1–3 years", years.options)
        self.assertIn("YYYY-MM-DD", by_id["section_1747783397549_question_4"].label)
        self.assertFalse(by_id["section_1747783397549_question_11"].required)  # no flag means optional
        self.assertIn("cSalary", by_id)
        self.assertIn("Costa Rica", by_id["cLocation"].options)

    def test_consent_is_a_required_field(self):
        by_id = {f.id: f for f in breezy.parse_form(COGENT, DATA["cogent"]).fields}
        self.assertTrue(by_id["gdprAgreement"].required)
        self.assertFalse(by_id["cResume"].required)

    def test_closed_job_stops_it(self):
        with self.assertRaises(ValueError):
            breezy.parse_form(URRLY, {"position": {}})

    def test_link_is_routed(self):
        self.assertEqual(forms.find("apply at " + URRLY + "?src=x"), ("breezy", URRLY))


if __name__ == "__main__":
    unittest.main()
