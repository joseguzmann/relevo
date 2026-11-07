import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from relevo import bamboohr, forms  # noqa: E402

DATA = json.loads((Path(__file__).parent / "fixtures/bamboohr_forms.json").read_text())
NEWS = "https://newsmatics.bamboohr.com/careers/55"
BG = "https://berkshiregrey.bamboohr.com/careers/780"


def berkshire_without_education():
    data = json.loads(json.dumps(DATA["berkshiregrey"]))
    for key in ("educationLevelId", "educationInstitutionName"):
        data["formFields"].pop(key, None)
    return data


class Dates(unittest.TestCase):
    def test_written_dates_become_us_format(self):
        try:
            from relevo.filler import us_date
        except ImportError:
            self.skipTest("playwright not installed here")
        self.assertEqual(us_date("15 Nov 2026"), "11/15/2026")
        self.assertEqual(us_date("2026-11-13"), "11/13/2026")
        self.assertEqual(us_date("11/13/2026"), "11/13/2026")


class ParseForm(unittest.TestCase):
    def test_standard_fields_and_page_names(self):
        form = bamboohr.parse_form(NEWS + "?source=x", DATA["newsmatics"])
        by_id = {f.id: f for f in form.fields}
        self.assertEqual(form.url, NEWS)
        self.assertEqual(form.ats, "bamboohr")
        self.assertEqual(by_id["resume"].kind, "file")
        self.assertIn("Ecuador", by_id["countryId.value"].options)
        self.assertTrue(by_id["state.value"].required)
        self.assertEqual(by_id["customQuestionAnswers.long_80"].kind, "long_text")
        ids = [f.id for f in form.fields]
        self.assertLess(ids.index("countryId.value"), ids.index("state.value"))  # the province depends on the country

    def test_a_required_education_stops_it(self):
        with self.assertRaises(ValueError):
            bamboohr.parse_form(BG, DATA["berkshiregrey"])

    def test_question_types(self):
        by_id = {f.id: f for f in bamboohr.parse_form(BG, berkshire_without_education()).fields}
        self.assertEqual(by_id["customQuestionAnswers.yes_no_1770"].options, ("Yes", "No"))
        self.assertIn("H-1B", by_id["customQuestionAnswers.multi_1771"].options)
        self.assertEqual(by_id["customQuestionAnswers.checkbox_1772"].kind, "yes_no")
        self.assertEqual(by_id["customQuestionAnswers.short_1769"].kind, "text")

    def test_closed_job_stops_it(self):
        with self.assertRaises(ValueError):
            bamboohr.parse_form(NEWS, {"jobOpening": {"jobOpeningStatus": "Closed"}, "formFields": {}})

    def test_link_is_routed(self):
        self.assertEqual(forms.find("see " + NEWS + "/detail"), ("bamboohr", NEWS))
        self.assertEqual(forms.find("https://MetalToad.bamboohr.com/careers/596")[0], "bamboohr")


if __name__ == "__main__":
    unittest.main()
