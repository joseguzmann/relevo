"""Which way out a job has. LinkedIn is never a way out: only an email address written in a post is."""
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
NOT_CONTACT = ("example.com", "sentry.io", "wixpress", ".png", ".jpg")


def contact_email(text: str) -> str:
    for email in EMAIL.findall(text or ""):
        if not any(bad in email.lower() for bad in NOT_CONTACT):
            return email
    return ""


def decide(job: dict) -> tuple[str, str]:
    """Returns (route, target): an ATS + its form url, email + address, or manual + the job's link."""
    from relevo import forms
    ats, url = forms.find(job.get("url", ""), *job.get("urls", []), job.get("page_text", ""))
    if ats:
        return ats, url
    is_post = "linkedin.com" in job.get("url", "") and "/jobs/" not in job.get("url", "")
    if is_post:
        email = contact_email(job.get("description", ""))
        if email:
            return "email", email
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
    exits = {"ashby": "Formulario de Ashby", "greenhouse": "Formulario de Greenhouse", "manatal": "Formulario de Manatal", "email": f"Correo a {target}",
             "manual": "A mano"}
    return {"channel": channel, "source": source, "exit": exits.get(way, way), "location": job.get("location") or ""}
