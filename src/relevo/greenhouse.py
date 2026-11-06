"""Greenhouse: the form comes from its public job board API, so types and `required` are facts, not guesses."""
import json
import re
import urllib.request

from relevo.models import Field, Form

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"
URL = re.compile(r"https://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([^/?#\s\"'<>]+)/jobs/(\d+)")
KINDS = {"input_text": "text", "textarea": "long_text", "input_file": "file",
         "multi_value_single_select": "single_choice", "multi_value_multi_select": "multi_choice"}
TEXT_TWIN = {"resume_text", "cover_letter_text"}  # the same question as the file next to it: the file answers it


def parse_url(url: str) -> tuple[str, str]:
    m = URL.search(url or "")
    if not m:
        raise ValueError(f"not a Greenhouse job url: {url}")
    return m.group(1), m.group(2)


def parse_form(url: str, data: dict) -> Form:
    board, job = parse_url(url)
    fields = []
    for q in data.get("questions", []) + data.get("location_questions", []):
        for raw in q["fields"]:
            if raw["type"] == "input_hidden" or raw["name"] in TEXT_TWIN:
                continue
            if raw["type"] not in KINDS:
                raise ValueError(f"unsupported Greenhouse field type {raw['type']}: {q['label']}")
            kind = KINDS[raw["type"]]
            if raw["name"] == "email":
                kind = "email"
            elif raw["name"] == "location":
                kind = "location"
            options = tuple(v["label"] for v in raw.get("values") or [])
            fields.append(Field(raw["name"], q["label"].strip(), kind, bool(q["required"]), options))
    return Form(f"https://job-boards.greenhouse.io/{board}/jobs/{job}", fields, "greenhouse")


def fetch_form(url: str) -> Form:
    board, job = parse_url(url)
    req = urllib.request.Request(API.format(board=board, job=job), headers={"user-agent": "relevo/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_form(url, json.load(r))
