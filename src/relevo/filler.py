"""Fills a form from its fields and approved answers, checks each one, and submits only if told to."""
import asyncio
import re
from dataclasses import dataclass, field

from playwright.async_api import async_playwright

from relevo.models import Form


UNKNOWN_JS = r"""(labels) => {
  const seen = new Set(), out = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (el.type === 'hidden' || el.type === 'file' || el.name === 'g-recaptcha-response') continue;
    if (el.closest('[aria-hidden=true]') || !el.closest('form, [class*=application], body')) continue;
    const box = el.closest('[class*="fieldEntry"]') || el.closest('label')?.parentElement || el.parentElement;
    const text = (box?.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (labels.some(l => text.includes(l)) || seen.has(text)) continue;
    seen.add(text); out.push(text.slice(0, 200) || el.type);
  }
  return out;
}"""


GH_UNKNOWN_JS = r"""(ids) => {
  const skip = /^(country|candidate-location|iti-|g-recaptcha|resume_text|cover_letter_text)/, out = [];
  for (const e of document.querySelectorAll('form input, form textarea, form select')) {
    const id = e.id || e.name;
    if (!id || e.type === 'hidden' || skip.test(id) || ids.includes(id)) continue;
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText || e.getAttribute('aria-label') || id;
    out.push(label.replace(/\s+/g, ' ').trim().slice(0, 200));
  }
  return [...new Set(out)];
}"""
SUBMIT = re.compile(r"submit application", re.I)
NOISE = re.compile(r"recaptcha|google|gstatic|analytics|sentry|datadog|segment|hotjar|intercom|clarity|doubleclick|"
                   r"facebook|linkedin|cookielaw|onetrust", re.I)


def _body(request) -> str:
    """What the browser sent, readable: a file inside it is named, not copied."""
    try:
        raw = request.post_data_buffer or b""
    except Exception:
        return ""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(Content-Type: application/pdf\r?\n\r?\n).*?(\r?\n--)", r"\1[PDF file]\2", text, flags=re.S)
    return text[:6000]


@dataclass
class Result:
    verified: dict = field(default_factory=dict)
    submitted: bool = False
    confirmation: str = ""
    confirmed: bool = False  # the form is gone and the page says it went
    server: str = ""  # what the ATS answered to the submit request
    requests: list = field(default_factory=list)  # every request the click sent: method, url, body, answer
    unknown: list = field(default_factory=list)  # on the page but not in the form data: a person must see these

    @property
    def failed(self) -> list[str]:
        return [k for k, ok in self.verified.items() if not ok]


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


async def _fill_greenhouse(page, f, value) -> bool:
    box = page.locator(f'[id="{f.id}"]')
    if f.kind in ("text", "email", "number", "long_text"):
        await box.fill(str(value))
        got = await box.input_value()
        if f.id == "phone":
            return _digits(got)[-9:] == _digits(str(value))[-9:]
        return got == str(value)
    if f.kind == "file":
        await box.set_input_files(value)
        await page.wait_for_timeout(2500)
        return value.rsplit("/", 1)[-1] in await page.locator("form").inner_text()
    if f.kind == "location":
        city = str(value).split(",")[0].strip()
        loc = page.locator("#candidate-location")
        await loc.click()
        await loc.fill(city)
        await page.wait_for_timeout(2500)
        await page.get_by_role("option").filter(has_text=city).first.click()
        await page.wait_for_timeout(500)
        shown = await loc.evaluate("e => e.closest('[class*=select]')?.innerText || e.value")
        return city.lower() in shown.lower()
    if f.kind in ("single_choice", "multi_choice"):
        values = value if isinstance(value, list) else [value]
        for v in values:
            await box.click()
            await box.fill(v)
            await page.wait_for_timeout(600)
            await page.get_by_role("option", name=v, exact=True).first.click()
            await page.wait_for_timeout(300)
        shown = await box.evaluate("e => e.closest('.select__control, [class*=select__control]')?.innerText || ''")
        return all(v in shown for v in values)
    raise ValueError(f"cannot fill {f.kind}")


async def _fill_field(page, f, value, ats: str = "ashby") -> bool:
    if ats == "greenhouse":
        return await _fill_greenhouse(page, f, value)
    entry = page.locator('[class*="fieldEntry"]').filter(has_text=f.label).first
    if f.kind in ("text", "email", "number", "long_text"):
        box = entry.locator("input, textarea").first
        await box.fill(str(value))
        return await box.input_value() == str(value)
    if f.kind == "file":
        await entry.locator("input[type=file]").first.set_input_files(value)
        await page.wait_for_timeout(2000)
        return value.rsplit("/", 1)[-1] in await entry.inner_text()
    if f.kind == "yes_no":
        await entry.get_by_role("button", name=value, exact=True).click()
        return await entry.locator("input[type=checkbox]").first.is_checked() == (value == "Yes")
    if f.kind == "single_choice" and await entry.locator("input[type=radio]").count():
        await entry.locator("label").filter(has_text=value).first.click()
        return await entry.locator("input[type=radio]:checked").count() == 1
    if f.kind in ("single_choice", "location"):
        box = entry.locator("input").first
        await box.click()
        await box.fill(value)
        await page.wait_for_timeout(1200)
        await page.get_by_role("option", name=value).first.click()
        await page.wait_for_timeout(300)
        return value.split(",")[0].lower() in (await box.input_value()).lower()
    if f.kind == "multi_choice":
        for v in value if isinstance(value, list) else [value]:
            await entry.locator("label").filter(has_text=v).first.click()
        return await entry.locator("input[type=checkbox]:checked").count() == len(value if isinstance(value, list) else [value])
    raise ValueError(f"cannot fill {f.kind}")


async def _submit(page, result: Result, after_shot: str, seconds: int) -> None:
    """Clicks once, then waits for the answer and keeps the evidence, whatever it turns out to be."""
    replies = []

    def sent(request):
        if request.method in ("POST", "PUT", "PATCH") and not NOISE.search(request.url):
            result.requests.append({"method": request.method, "url": request.url[:300], "body": _body(request), "answer": ""})

    async def note(response):
        for r in result.requests:
            if r["url"] == response.url[:300] and not r["answer"]:
                try:
                    r["answer"] = f"{response.status} {(await response.text())[:1500]}"
                except Exception:
                    r["answer"] = str(response.status)
                replies.append(f"{response.status} {r['answer'][4:400]}")
                break

    page.on("request", sent)
    page.on("response", note)
    button = page.get_by_role("button", name=SUBMIT)
    await button.click()
    result.submitted = True
    text, after_reply = "", 0
    for _ in range(seconds):
        await page.wait_for_timeout(1000)
        text = await page.locator("body").inner_text()
        result.confirmed = await button.count() == 0 and looks_sent(text)
        after_reply += 1 if replies else 0
        if result.confirmed or after_reply >= 8:
            break
    alerts = await page.locator('[role=alert], [class*="error" i]').all_inner_texts()
    result.server = " | ".join(replies)
    result.confirmation = " / ".join([*[a.strip() for a in alerts if a.strip()], text[:200], text[-300:]])[:900]
    if after_shot:
        await page.screenshot(path=after_shot, full_page=True)


async def _unknown(page, form: Form) -> list:
    if form.ats == "greenhouse":
        return await page.evaluate(GH_UNKNOWN_JS, [f.id for f in form.fields])
    return await page.evaluate(UNKNOWN_JS, [f.label for f in form.fields])


async def run(form: Form, answers: dict, shot: str, submit: bool = False, accepted: tuple = (),
              after_shot: str = "", seconds: int = 45) -> Result:
    if form.missing(answers) or form.invalid(answers):
        raise ValueError(f"missing {form.missing(answers)}; invalid {form.invalid(answers)}")
    result = Result()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1200, "height": 1000})
        await page.goto(form.url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)
        for f in form.fields:
            if answers.get(f.id) in (None, "", []):
                continue
            try:
                result.verified[f.label] = await _fill_field(page, f, answers[f.id], form.ats)
            except Exception as e:  # one bad field must block the submit, not hide the others
                result.verified[f.label] = False
                print(f"field failed: {f.label}: {str(e)[:160]}")
        result.unknown = await _unknown(page, form)
        for text in accepted:  # a box the person read and ticked in the panel
            if text in result.unknown:
                await page.get_by_text(text.rsplit(" ", 2)[-2] + " " + text.rsplit(" ", 1)[-1], exact=True).first.click()
                result.unknown.remove(text)
        await page.screenshot(path=shot, full_page=True)
        if submit and not result.failed and not result.unknown:
            await _submit(page, result, after_shot, seconds)
        await browser.close()
    return result


async def _scan(form: Form) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(form.url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)
        unknown = await _unknown(page, form)
        await browser.close()
    return unknown


def scan(form: Form) -> list:
    """What the page asks that the form data does not list. A person has to see these."""
    return asyncio.run(_scan(form))


def looks_sent(text: str) -> bool:
    low = text.lower()
    return "success" in low or "thank you for applying" in low or "application was submitted" in low


def fill(form: Form, answers: dict, shot: str, submit: bool = False, accepted: tuple = (), after_shot: str = "") -> Result:
    return asyncio.run(run(form, answers, shot, submit, tuple(accepted), after_shot))
