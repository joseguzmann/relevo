"""BambooHR: `/careers/<id>/detail` returns the job and its whole form (`formFields`: which standard fields are on and
required, and the custom questions with type and options). The form shows after "Apply for This Job" and ends in a
visible reCAPTCHA box, which the person solves from the phone."""
import json
import re
import urllib.request

from relevo.models import Field, Form

URL = re.compile(r"https://([A-Za-z0-9-]+)\.bamboohr\.com/careers/(\d+)")
UA = {"user-agent": "Mozilla/5.0 (relevo)"}
OPEN = "Apply for This Job"
STANDARD = [  # (form key, page name, label, kind); country goes before province, which depends on it
    ("firstName", "firstName", "First Name", "text"),
    ("lastName", "lastName", "Last Name", "text"),
    ("email", "email", "Email", "email"),
    ("phone", "phone", "Phone", "text"),
    ("resumeFileId", "resume", "Resume", "file"),
    ("countryId", "countryId.value", "Country", "single_choice"),
    ("streetAddress", "streetAddress.value", "Street address", "text"),
    ("city", "city.value", "City", "text"),
    ("state", "state.value", "Province / State", "text"),
    ("zip", "zip.value", "Postal code", "text"),
    ("dateAvailable", "dateAvailable", "Date Available (MM/DD/YYYY)", "text"),
    ("desiredPay", "desiredPay", "Desired Pay", "text"),
    ("websiteUrl", "websiteUrl", "Website, Blog or Portfolio", "text"),
    ("linkedinUrl", "linkedinUrl", "LinkedIn URL", "text"),
    ("referredBy", "referredBy", "Who referred you", "text"),
]
KINDS = {"short": "text", "long": "long_text", "yes_no": "single_choice", "multi": "single_choice"}


def parse_url(url: str) -> tuple[str, str]:
    m = URL.search(url or "")
    if not m:
        raise ValueError(f"not a BambooHR job url: {url}")
    return m.group(1), m.group(2)


def parse_form(url: str, result: dict) -> Form:
    company, job = parse_url(url)
    if (result.get("jobOpening") or {}).get("jobOpeningStatus") != "Open":
        raise ValueError(f"job not found or closed: {url}")
    raw = result.get("formFields") or {}
    known = {key for key, *_ in STANDARD} | {"customQuestions"}
    for key, value in raw.items():  # education, references, cover letter file, EEO answers: only a person fills these
        if key not in known and isinstance(value, dict) and value.get("isRequired"):
            raise ValueError(f"BambooHR field a person must fill: {value.get('label') or key}")
    fields = []
    for key, name, label, kind in STANDARD:
        if key not in raw:
            continue
        options = ()
        if key == "countryId":
            options = tuple(o["text"] for o in raw[key].get("options") or [])
        fields.append(Field(name, label, kind, bool(raw[key].get("isRequired")), options))
    for q in raw.get("customQuestions") or []:
        kind, required, name = KINDS.get(q["type"]), bool(q.get("isRequired")), f"customQuestionAnswers.{q['type']}_{q['id']}"
        if q["type"] == "checkbox" and not q.get("options"):
            kind = "yes_no"  # one box to tick, usually a consent
        if kind is None:
            if required:
                raise ValueError(f"BambooHR question a person must answer ({q['type']}): {q['question'][:80]}")
            continue
        options = ("Yes", "No") if q["type"] == "yes_no" else tuple(o["option"] for o in q.get("options") or [])
        fields.append(Field(name, q["question"].strip(), kind, required, options))
    return Form(f"https://{company}.bamboohr.com/careers/{job}", fields, "bamboohr")


def fetch_form(url: str) -> Form:
    company, job = parse_url(url)
    req = urllib.request.Request(f"https://{company}.bamboohr.com/careers/{job}/detail", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_form(url, json.load(r)["result"])
