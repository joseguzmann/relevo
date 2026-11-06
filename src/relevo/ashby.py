"""Ashby: the form comes from its public GraphQL, so types and `required` are facts, not guesses."""
import json
import re
import urllib.request

from relevo.models import Field, Form

API = "https://jobs.ashbyhq.com/api/non-user-graphql"
QUERY = ("query J($organizationHostedJobsPageName:String!,$jobPostingId:String!){jobPosting("
         "organizationHostedJobsPageName:$organizationHostedJobsPageName,jobPostingId:$jobPostingId)"
         "{applicationForm{sections{fieldEntries{field isRequired}}}}}")
KINDS = {"String": "text", "LongText": "long_text", "Email": "email", "Number": "number", "File": "file",
         "ValueSelect": "single_choice", "MultiValueSelect": "multi_choice", "Boolean": "yes_no",
         "Location": "location", "Phone": "text"}


def parse_url(url: str) -> tuple[str, str]:
    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        raise ValueError(f"not an Ashby job url: {url}")
    return m.group(1), m.group(2)


def parse_form(url: str, data: dict) -> Form:
    posting = data["data"]["jobPosting"]
    if not posting:
        raise ValueError(f"job not found or closed: {url}")
    fields = []
    for section in posting["applicationForm"]["sections"]:
        for entry in section["fieldEntries"]:
            raw = entry["field"]
            if raw["type"] not in KINDS:
                raise ValueError(f"unsupported Ashby field type {raw['type']}: {raw.get('title')}")
            options = tuple(o.get("label") or o.get("title") or "" for o in raw.get("selectableValues") or [])
            kind = "yes_no" if raw["type"] == "Boolean" else KINDS[raw["type"]]
            fields.append(Field(raw.get("path") or raw["id"], raw["title"].strip(), kind,
                                bool(entry["isRequired"]), options))
    return Form(url.split("/application")[0] + "/application", fields)


def fetch_form(url: str) -> Form:
    org, job = parse_url(url)
    body = json.dumps({"operationName": "J", "query": QUERY,
                       "variables": {"organizationHostedJobsPageName": org, "jobPostingId": job}}).encode()
    req = urllib.request.Request(API, body, {"content-type": "application/json", "user-agent": "relevo/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_form(url, json.load(r))
