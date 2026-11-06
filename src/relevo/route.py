"""Which way out a job has. LinkedIn is never a way out: only an email address written in a post is."""
import re

ASHBY = re.compile(r"https://jobs\.ashbyhq\.com/[^/\s\"'<>]+/[0-9a-f-]{36}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
NOT_CONTACT = ("example.com", "sentry.io", "wixpress", ".png", ".jpg")


def ashby_url(*texts: str) -> str:
    for text in texts:
        m = ASHBY.search(text or "")
        if m:
            return m.group(0)
    return ""


def contact_email(text: str) -> str:
    for email in EMAIL.findall(text or ""):
        if not any(bad in email.lower() for bad in NOT_CONTACT):
            return email
    return ""


def decide(job: dict) -> tuple[str, str]:
    """Returns (route, target): ashby + url, email + address, or manual + the job's link."""
    urls = [job.get("url", ""), *job.get("urls", [])]
    url = ashby_url(*urls, job.get("page_text", ""))
    if url:
        return "ashby", url
    is_post = "linkedin.com" in job.get("url", "") and "/jobs/" not in job.get("url", "")
    if is_post:
        email = contact_email(job.get("description", ""))
        if email:
            return "email", email
    return "manual", job.get("url", "")
