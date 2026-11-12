import unittest
from relevo import coverage, forms


class TestCoverage(unittest.TestCase):
    def test_registry_matches_forms(self):
        # Every ATS with a reader is in the registry as auto or hand; HAND ones are marked hand.
        for ats in forms.READERS:
            e = coverage.BY_KEY.get(ats)
            self.assertIsNotNone(e, f"{ats} has a reader but is not in coverage.EXITS")
            self.assertIn(e.send, ("auto", "hand"), f"{ats} has a reader but send={e.send}")
        for ats in forms.HAND:
            self.assertEqual(coverage.BY_KEY[ats].send, "hand", f"{ats} is in forms.HAND but not marked hand")
        # Nothing claims a reader it does not have.
        for e in coverage.covered():
            self.assertIn(e.key, forms.READERS, f"{e.key} marked {e.send} but has no reader")

    def test_next_to_build_by_volume(self):
        nxt = coverage.next_to_build()
        self.assertEqual(nxt[0].key, "workday")  # highest-volume uncovered
        self.assertTrue(all(nxt[i].volume >= nxt[i + 1].volume for i in range(len(nxt) - 1)))

    def test_render(self):
        self.assertIn("next to build", coverage.render())


if __name__ == "__main__":
    unittest.main()
