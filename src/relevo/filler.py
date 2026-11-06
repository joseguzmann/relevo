"""Fills a form from its fields and approved answers, checks each one, and submits only if told to."""
import asyncio
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


@dataclass
class Result:
    verified: dict = field(default_factory=dict)
    submitted: bool = False
    confirmation: str = ""
    confirmed: bool = False  # the form is gone and the page says it went
    server: str = ""  # what the ATS answered to the submit request
    unknown: list = field(default_factory=list)  # on the page but not in the form data: a person must see these

    @property
    def failed(self) -> list[str]:
        return [k for k, ok in self.verified.items() if not ok]


async def _fill_field(page, f, value) -> bool:
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

    async def note(response):
        if "graphql" in response.url and "submit" in response.url.lower():
            try:
                replies.append(f"{response.status} {(await response.text())[:400]}")
            except Exception:
                replies.append(str(response.status))

    page.on("response", note)
    button = page.get_by_role("button", name="Submit Application")
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
                result.verified[f.label] = await _fill_field(page, f, answers[f.id])
            except Exception as e:  # one bad field must block the submit, not hide the others
                result.verified[f.label] = False
                print(f"field failed: {f.label}: {str(e)[:160]}")
        result.unknown = await page.evaluate(UNKNOWN_JS, [f.label for f in form.fields])
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
        unknown = await page.evaluate(UNKNOWN_JS, [f.label for f in form.fields])
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
