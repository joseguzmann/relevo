"""Breezy HR: the apply page carries the whole form as JSON (`#portal-data`): which standard fields are on, and the
questionnaire with types, options and required flags. The page names each question `section_<id>_question_<n>`."""
import json
import re
import urllib.request

from relevo.models import Field, Form

URL = re.compile(r"https://([a-z0-9-]+)\.breezy\.hr/p/([a-z0-9]+(?:-[a-z0-9-]*)?)")
UA = {"user-agent": "Mozilla/5.0 (relevo)"}
DATA = re.compile(r'<script id="portal-data" type="application/json">(.*?)</script>', re.S)
STANDARD = [  # (form key, page name, label, kind)
    ("resume", "cResume", "Resume", "file"),
    ("name", "cName", "Full Name", "text"),
    ("email_address", "cEmail", "Email Address", "email"),
    ("phone_number", "cPhoneNumber", "Phone Number", "text"),
    ("address", "cAddress", "Address (city, country)", "text"),
    ("summary", "cSummary", "Experience Summary", "long_text"),
    ("cover_letter", "cCoverLetter", "Cover Letter", "long_text"),
]
SUBFORMS = ("education", "work_history", "profile_photo", "eeoc")  # only a person fills these
KINDS = {"text": "text", "paragraph": "long_text", "dropdown": "single_choice", "multiplechoice": "single_choice",
         "checkboxes": "multi_choice", "date": "text"}
GDPR = "gdprAgreement"


def parse_url(url: str) -> tuple[str, str]:
    m = URL.search(url or "")
    if not m:
        raise ValueError(f"not a Breezy job url: {url}")
    return m.group(1), m.group(2)


def apply_url(url: str) -> str:
    company, position = parse_url(url)
    return f"https://{company}.breezy.hr/p/{position}/apply"


def parse_form(url: str, data: dict) -> Form:
    position = data.get("position") or {}
    if position.get("state") not in (None, "published") or not position.get("application_form"):
        raise ValueError(f"job not found or closed: {url}")
    form = position["application_form"]
    for key in SUBFORMS:
        if form.get(key) == "required":
            raise ValueError(f"Breezy field a person must fill: {key}")
    fields = []
    for key, name, label, kind in STANDARD:
        state = form.get(key, "optional" if key == "summary" else "hidden")  # the page shows the summary box unless told not to
        if state != "hidden":
            fields.append(Field(name, label, kind, state == "required"))
    if form.get("salary", "hidden") != "hidden":
        currency = (form.get("salary_currency") or [{"code": "USD"}])[0]["code"]
        fields.append(Field("cSalary", f"Desired salary in {currency} per month (number only)", "number",
                            form["salary"] == "required"))
    if form.get("preferred_location", "hidden") != "hidden":
        names = tuple(loc["name"] for loc in position.get("locations") or [])
        fields.append(Field("cLocation", "Which location are you applying for", "single_choice",
                            form["preferred_location"] == "required", names))
    for section in data.get("questions") or []:
        for i, q in enumerate(section.get("questions", [])):
            kind, required = KINDS.get(q["type"]["id"]), bool(q.get("required"))
            if kind is None:
                if required:
                    raise ValueError(f"Breezy question a person must answer ({q['type']['id']}): {q['text'][:80]}")
                continue
            label = q["text"].strip() + (" (date, YYYY-MM-DD)" if q["type"]["id"] == "date" else "")
            options = tuple(o["text"] for o in q.get("options") or [])
            fields.append(Field(f"section_{section['_id']}_question_{i}", label, kind, required, options))
    if data.get("gdprEnabled") and form.get("gdpr") != "hidden":
        fields.append(Field(GDPR, "Recruitment privacy notice: I consent to the processing of my data", "yes_no", True))
    return Form(apply_url(url), fields, "breezy")


def fetch_form(url: str) -> Form:
    page = apply_url(url)
    with urllib.request.urlopen(urllib.request.Request(page, headers=UA), timeout=20) as r:
        m = DATA.search(r.read().decode("utf-8", "ignore"))
    if not m:
        raise ValueError(f"job not found or closed: {url}")
    return parse_form(url, json.loads(m.group(1))["data"])
