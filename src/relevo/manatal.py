"""Manatal (careers-page.com): fields and `required` come from its public form API; kinds and options from the
rendered page, because the page draws some fields (notice period, gender) with options the API does not list."""
import asyncio
import json
import re
import urllib.request

from relevo.models import Field, Form

URL = re.compile(r"https://(?:www\.)?careers-page\.com/([^/?#\s\"'<>]+)/job/([A-Z0-9]+)")
API = "https://www.careers-page.com/api/v1.0/jobs/{job}/application-form/"
UA = {"user-agent": "Mozilla/5.0 (relevo)"}
TERMS = "terms_and_condition"
SKIP = {"educations", "experiences"}  # whole sub-forms: only a person fills these
DOM_JS = r"""(ids) => ids.map(id => {
  const el = document.getElementById('field' + id);
  const group = [...document.querySelectorAll(`input[name="${id}"]`)].filter(e => ['radio', 'checkbox'].includes(e.type));
  if (group.length > 1 || (group.length && !el))
    return {id, tag: group[0].type, options: group.map(e => e.value)};
  if (!el) return {id, tag: 'none', options: []};
  const tag = el.tagName === 'SELECT' ? (el.multiple ? 'multiselect' : 'select') : el.tagName === 'TEXTAREA' ? 'textarea' : el.type;
  const options = el.tagName === 'SELECT' ? [...el.options].filter(o => o.value !== '').map(o => o.text.trim()) : [];
  return {id, tag, options};
})"""
KINDS = {"text": "text", "number": "number", "textarea": "long_text", "file": "file", "select": "single_choice",
         "radio": "single_choice", "multiselect": "multi_choice", "checkbox": "multi_choice"}


def parse_url(url: str) -> tuple[str, str]:
    m = URL.search(url or "")
    if not m:
        raise ValueError(f"not a Manatal job url: {url}")
    return m.group(1), m.group(2)


def apply_url(url: str) -> str:
    client, job = parse_url(url)
    return f"https://www.careers-page.com/{client}/job/{job}/apply"


def parse_form(url: str, api: list, dom: list) -> Form:
    seen = {d["id"]: d for d in dom}
    fields = []
    for raw in api:
        if raw["field_type"] in SKIP or raw["client_field_slug"] in ("current_salary", "expected_salary"):
            if raw["is_required"]:
                raise ValueError(f"Manatal field a person must fill: {raw['label']}")
            continue
        if raw["field_type"] == "attachment" and not raw["is_required"]:
            continue
        d = seen.get(str(raw["id"]), {"tag": "none", "options": []})
        tag = d["tag"]
        if tag == "checkbox" and not d["options"]:
            tag = "single_checkbox"
        if tag == "none":
            raise ValueError(f"Manatal field not on the page: {raw['label']}")
        kind = "yes_no" if tag == "single_checkbox" else KINDS.get(tag)
        if kind is None:
            raise ValueError(f"unsupported Manatal field {tag}: {raw['label']}")
        if raw["client_field_slug"] == "email":
            kind = "email"
        fields.append(Field(str(raw["id"]), raw["label"].strip(), kind, bool(raw["is_required"]), tuple(d["options"])))
    fields.append(Field(TERMS, "I agree to the terms and conditions & privacy policy", "yes_no", True))
    return Form(apply_url(url), fields, "manatal")


async def _dom(url: str, ids: list) -> list:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)
        out = await page.evaluate(DOM_JS, ids)
        await browser.close()
    return out


def fetch_form(url: str) -> Form:
    page = apply_url(url)
    with urllib.request.urlopen(urllib.request.Request(page, headers=UA), timeout=20) as r:
        m = re.search(r'selectedJobId\s*=\s*"(\d+)"', r.read().decode("utf-8", "ignore"))
    if not m:
        raise ValueError(f"job not found or closed: {url}")
    with urllib.request.urlopen(urllib.request.Request(API.format(job=m.group(1)), headers=UA), timeout=20) as r:
        api = json.load(r)
    return parse_form(url, api, asyncio.run(_dom(page, [str(f["id"]) for f in api])))
