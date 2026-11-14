"""Which way out a job has. LinkedIn is never a way out: an email written in a post is, and so is an ATS form that a
post links to. Post links come shortened (lnkd.in); they are resolved without cookies or an account, reading only the
destination from LinkedIn's "you are leaving LinkedIn" page (Jose, 2026-11-13). No LinkedIn page is ever opened."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
NOT_CONTACT = ("example.com", "sentry.io", "wixpress", ".png", ".jpg")


def contact_email(text: str) -> str:
    for email in EMAIL.findall(text or ""):
        if not any(bad in email.lower() for bad in NOT_CONTACT):
            return email
    return ""


SHORT = re.compile(r"https?://lnkd\.in/[A-Za-z0-9_-]+")
HREF = re.compile(r'href="(https?://[^"]+)"')
NOT_DEST = re.compile(r"https?://([^/]*\.)?(linkedin\.com|licdn\.com|lnkd\.in)(/|$)", re.I)
UA = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(url: str, follow: bool = True) -> tuple[int, str, str]:
    """(status, location, body). No cookies are kept or sent."""
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return r.status, r.headers.get("location", ""), r.read(500_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("location", ""), ""
    except Exception:
        return 0, "", ""


def unshorten(url: str, cache_file: str = "") -> str:
    """Destination of a lnkd.in link, or "". Cached, so each link is asked once."""
    cache = {}
    if cache_file and os.path.exists(cache_file):
        try:
            cache = json.loads(Path(cache_file).read_text())
        except ValueError:
            cache = {}
    if url in cache:
        return cache[url]
    status, location, body = _get(url, follow=False)
    dest = location if location and not NOT_DEST.match(location) else ""
    if not dest:
        dest = next((h for h in HREF.findall(body) if not NOT_DEST.match(h)), "")
    if cache_file:
        cache[url] = dest
        try:
            Path(cache_file).write_text(json.dumps(cache))
        except OSError:
            pass
    return dest


ANY_ATS = re.compile(r"https?://(?:jobs\.lever\.co|apply\.workable\.com|[a-z0-9-]+\.workable\.com|[a-z0-9-]+\.recruitee\.com"
                     r"|jobs\.smartrecruiters\.com|[a-z0-9-]+\.teamtailor\.com|[a-z0-9-]+\.myworkdayjobs\.com)/[^\s\"'<>]*", re.I)


def linked_form(text: str, cache_file: str = "", limit: int = 3) -> tuple[str, str, str]:
    """(ats, url, other): an ATS form Relevo can read, reached from the links in a post (the link itself, or one hop
    into the page it lands on: aggregators like Jobicy or RemoteYeah link on to the company's ATS). `other` is a form
    on an ATS without a reader (Lever, Workable...), kept so the live filler starts there instead of at the post."""
    from relevo import forms
    other = ""
    for short in list(dict.fromkeys(SHORT.findall(text or "")))[:limit]:
        dest = unshorten(short, cache_file)
        if not dest:
            continue
        ats, url = forms.find(dest)
        if ats:
            return ats, url, ""
        m = ANY_ATS.search(dest)
        if m:
            other = other or m.group(0)
            continue
        _, _, body = _get(dest)
        ats, url = forms.find(body)
        if ats:
            return ats, url, ""
        m = ANY_ATS.search(body)
        other = other or (m.group(0) if m else dest)  # any destination beats the post: the post is behind a login
    return "", "", other


def decide(job: dict) -> tuple[str, str]:
    """Returns (route, target): an ATS + its form url, email + address, or manual + the job's link."""
    from relevo import forms
    ats, url = forms.find(job.get("url", ""), *job.get("urls", []), job.get("page_text", ""), job.get("description", ""))
    if ats:
        return ats, url
    is_post = "linkedin.com" in job.get("url", "") and "/jobs/" not in job.get("url", "")
    if is_post:
        cache = os.path.join(os.environ.get("RELEVO_DATA", "/data"), "lnkd.json")
        ats, url, other = linked_form(job.get("description", ""), cache if os.path.isdir(os.path.dirname(cache)) else "")
        if ats:
            return ats, url
        email = contact_email(job.get("description", ""))
        if email:
            return "email", email
        if other:
            return "manual", other  # a form Relevo has no reader for: the live filler starts there, not at the post
    return "manual", job.get("url", "")


ATS = {"ashby": "Ashby", "greenhouse": "Greenhouse", "manatal": "Manatal", "lever": "Lever", "workable": "Workable", "bamboohr": "BambooHR",
       "breezy": "Breezy", "recruitee": "Recruitee", "smartrecruiters": "SmartRecruiters", "teamtailor": "Teamtailor",
       "workday": "Workday"}
BOARDS = {"computrabajo": "Computrabajo", "freehire": "FreeHire", "getonbrd": "Get on Board", "gmail": "Alerta por correo",
          "himalayas": "Himalayas", "hn_whoishiring": "Hacker News", "indeed": "Indeed", "jobicy": "Jobicy",
          "multitrabajos": "Multitrabajos", "openroles": "OpenRoles", "remoteok": "Remote OK", "remoteyeah": "RemoteYeah",
          "torre": "Torre", "weworkremotely": "We Work Remotely", "workingnomads": "Working Nomads"}


def origin(job: dict, way: str, target: str = "") -> dict:
    """Where a job was found and how it leaves, in the words the app shows."""
    url = job.get("url", "")
    sources = job.get("sources") or []
    if "linkedin.com" in url:
        channel, source = ("LinkedIn Jobs" if "/jobs/" in url else "Post de LinkedIn"), "LinkedIn"
    else:
        ats = next((name for key, name in ATS.items() if key in sources or key in url), "")
        board = next((BOARDS.get(s, s) for s in sources if not s.startswith("linkedin")), "")
        channel, source = ("ATS", ats) if ats else ("Bolsa", board or url.split("/")[2] if "//" in url else board)
    exits = {"ashby": "Formulario de Ashby", "greenhouse": "Formulario de Greenhouse", "manatal": "Formulario de Manatal", "breezy": "Formulario de Breezy", "bamboohr": "Formulario de BambooHR", "email": f"Correo a {target}",
             "manual": "A mano"}
    return {"channel": channel, "source": source, "exit": exits.get(way, way), "location": job.get("location") or ""}
