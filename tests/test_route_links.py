import unittest
from unittest import mock

from relevo import route

LEAVING = '<html><link href="https://static.licdn.com/x.css"><a data-tracking-control-name="external_url_click" href="https://jobs.ashbyhq.com/acme/d17be3de-3231-4a70-9a52-4177ea73f4ac">go</a></html>'
AGG = '<html><a href="https://jobs.lever.co/acme/abcd-1234/apply">Apply</a></html>'


class LinkTests(unittest.TestCase):
    def test_unshorten_reads_destination_from_leaving_page(self):
        with mock.patch.object(route, "_get", return_value=(200, "", LEAVING)):
            self.assertEqual(route.unshorten("https://lnkd.in/abc"), "https://jobs.ashbyhq.com/acme/d17be3de-3231-4a70-9a52-4177ea73f4ac")

    def test_post_link_to_aggregator_reaches_ats_in_one_hop(self):
        calls = {"https://lnkd.in/x1": (200, "", '<a href="https://jobicy.com/jobs/1">x</a>'),
                 "https://jobicy.com/jobs/1": (200, "", AGG)}
        with mock.patch.object(route, "_get", side_effect=lambda u, follow=True: calls[u]):
            job = {"url": "https://www.linkedin.com/posts/someone_hiring-123", "description": "Apply: https://lnkd.in/x1"}
            ats, url = route.decide(job)
        self.assertEqual(ats, "manual")  # Lever has no reader: manual, but the live filler starts at the form
        self.assertIn("jobs.lever.co/acme", url)

    def test_readable_ats_behind_shortlink_wins_over_email(self):
        with mock.patch.object(route, "_get", return_value=(200, "", LEAVING)):
            job = {"url": "https://www.linkedin.com/posts/someone_hiring-123", "description": "https://lnkd.in/abc or a@b.co"}
            self.assertEqual(route.decide(job), ("ashby", "https://jobs.ashbyhq.com/acme/d17be3de-3231-4a70-9a52-4177ea73f4ac"))

    def test_post_without_links_still_goes_by_email(self):
        job = {"url": "https://www.linkedin.com/posts/someone_hiring-123", "description": "Send your CV to jobs@acme.io"}
        self.assertEqual(route.decide(job), ("email", "jobs@acme.io"))

    def test_never_returns_a_linkedin_destination(self):
        with mock.patch.object(route, "_get", return_value=(200, "", '<a href="https://www.linkedin.com/jobs/view/1">x</a>')):
            self.assertEqual(route.unshorten("https://lnkd.in/abc"), "")

    def test_unknown_destination_becomes_the_manual_target(self):
        calls = {"https://lnkd.in/f1": (200, "", '<a href="https://forms.fillout.com/t/abc">x</a>'),
                 "https://forms.fillout.com/t/abc": (200, "", "<form></form>")}
        with mock.patch.object(route, "_get", side_effect=lambda u, follow=True: calls[u]):
            job = {"url": "https://www.linkedin.com/posts/x_hiring-1", "description": "Apply https://lnkd.in/f1"}
            self.assertEqual(route.decide(job), ("manual", "https://forms.fillout.com/t/abc"))

    def test_destination_is_unescaped(self):
        with mock.patch.object(route, "_get", return_value=(200, "", '<a href="https://a.io/j?x=1&amp;y=2">x</a>')):
            self.assertEqual(route.unshorten("https://lnkd.in/zz"), "https://a.io/j?x=1&y=2")
