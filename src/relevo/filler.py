"""Fills a form from its fields and approved answers, checks each one, and submits only if told to."""
import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

from relevo import bamboohr
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
        exact = page.get_by_role("option", name=str(value), exact=True)
        await (exact.first if await exact.count() else page.get_by_role("option").filter(has_text=city).first).click()
        await page.wait_for_timeout(500)
        shown = await loc.evaluate("e => (e.closest('.select__control')?.innerText || '') + ' ' + e.value")
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


async def _fill_manatal(page, f, value) -> bool:
    if f.id == "terms_and_condition" or (f.kind == "yes_no" and not f.options):
        box = page.locator(f'input[name="{f.id}"]').first
        await box.set_checked(value == "Yes")
        return await box.is_checked() == (value == "Yes")
    box = page.locator(f'[id="field{f.id}"]')
    if f.kind in ("text", "email", "number", "long_text"):
        await box.fill(str(value))
        return await box.input_value() == str(value)
    if f.kind == "file":
        await box.set_input_files(value)
        await page.wait_for_timeout(1000)
        return value.rsplit("/", 1)[-1] in await page.locator("form").inner_text()
    values = value if isinstance(value, list) else [value]
    if await box.count():  # a select
        await box.select_option(label=values, force=True)
        chosen = await box.evaluate("e => [...e.selectedOptions].map(o => o.text.trim())")
        return sorted(chosen) == sorted(values)
    for v in values:  # radios or checkboxes
        await page.locator(f'input[name="{f.id}"][value="{v}"]').check(force=True)
    return await page.locator(f'input[name="{f.id}"]:checked').count() == len(values)


BZ_FILL_JS = r"""([name, value]) => {
  const els = [...document.querySelectorAll(`[name="${name}"]`)];
  if (!els.length) return false;
  const e = els[0], fire = x => ['input', 'change'].forEach(t => x.dispatchEvent(new Event(t, {bubbles: true})));
  if (e.type === 'checkbox' && els.length === 1 && !Array.isArray(value)) {  // one consent box
    if (e.checked !== (value === 'Yes')) e.click();
    return e.checked === (value === 'Yes');
  }
  if (e.type === 'radio' || e.type === 'checkbox') {
    const want = Array.isArray(value) ? value : [value];
    for (const x of els) if (x.checked !== want.includes(x.value)) x.click();
    return els.filter(x => x.checked).map(x => x.value).sort().join('|') === [...want].sort().join('|');
  }
  if (e.tagName === 'SELECT') {
    const o = [...e.options].find(o => o.text.trim() === value);
    if (!o) return false;
    e.value = o.value; fire(e);
    return e.selectedOptions[0]?.text.trim() === value;
  }
  e.value = value; fire(e);
  return e.value === value;
}"""
BZ_SALARY_JS = r"""() => {  // the amount the form gets is per month, in the currency the job lists first
  const period = document.querySelector('select[ng-model="candidate.salary.period"]');
  if (!period) return false;
  period.value = 'monthly';
  period.dispatchEvent(new Event('change', {bubbles: true}));
  return period.value === 'monthly';
}"""


async def _fill_breezy(page, f, value) -> bool:
    if f.kind == "file":
        await page.locator("#main-attachment").set_input_files(value)
        for _ in range(15):
            await page.wait_for_timeout(1000)
            if value.rsplit("/", 1)[-1] in await page.locator("body").inner_text():
                return True
        return False
    ok = await page.evaluate(BZ_FILL_JS, [f.id, value if isinstance(value, list) else str(value)])
    if ok and f.id == "cSalary":
        ok = await page.evaluate(BZ_SALARY_JS)
    return ok


def us_date(text: str) -> str:
    """MM/DD/YYYY from the ways a person writes a date ("15 Nov 2026", "2026-11-15", "11/15/2026")."""
    from datetime import datetime
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    raise ValueError(f"not a date: {text}")


async def _fill_bamboohr(page, f, value) -> bool:
    if f.kind == "file":
        await page.locator("input[type=file]").first.set_input_files(value)
        for _ in range(15):
            await page.wait_for_timeout(1000)
            if value.rsplit("/", 1)[-1] in await page.locator("form").inner_text():
                return True
        return False
    if f.id == "countryId.value":  # a menu with a search box over a hidden select
        await page.locator('button[aria-label^="Country"]').click()
        await page.locator(".fab-MenuSearch__input").fill(value)
        await page.wait_for_timeout(600)
        await page.get_by_role("menuitem", name=value, exact=True).first.click()
        await page.wait_for_timeout(1500)  # the province and postal code fields change with the country
        return (await page.locator('button[aria-label^="Country"]').get_attribute("aria-label")).endswith(value)
    group = page.locator(f'input[name="{f.id}"]')
    if f.kind == "yes_no":
        await group.first.set_checked(value == "Yes", force=True)
        return await group.first.is_checked() == (value == "Yes")
    if f.kind == "single_choice":  # radios; a multi question keeps the option id as value, so pick by label
        await group.locator("xpath=ancestor::label").filter(has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")).first.click()
        return await page.locator(f'input[name="{f.id}"]:checked').locator("xpath=ancestor::label").inner_text() == value
    if f.id == "dateAvailable":
        value = us_date(str(value))
    box = page.get_by_label("Date Available").first if f.id == "dateAvailable" else page.locator(f'[name="{f.id}"]').first
    await box.fill(str(value))
    if f.id == "dateAvailable":
        await box.press("Escape")  # close the calendar it opens
    return await box.input_value() == str(value)


async def _fill_field(page, f, value, ats: str = "ashby") -> bool:
    if ats == "bamboohr":
        return await _fill_bamboohr(page, f, value)
    if ats == "breezy":
        return await _fill_breezy(page, f, value)
    if ats == "greenhouse":
        return await _fill_greenhouse(page, f, value)
    if ats == "manatal":
        return await _fill_manatal(page, f, value)
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


CAPTCHA_ATS = {"bamboohr"}  # a visible captcha box before the submit button
CAPTCHA_BOX = 'iframe[src*="recaptcha/api2/anchor"]'
TOKEN_JS = "() => !!document.querySelector('[name=g-recaptcha-response]')?.value"


async def _human_captcha(page, captcha: dict, minutes: int = 10) -> bool:
    """The person solves the captcha: the phone shows this page as it is and each tap lands here at the same spot.
    Relevo never answers it and never calls a solving service."""
    if not await page.locator(CAPTCHA_BOX).count() or await page.evaluate(TOKEN_JS):
        return True
    await page.locator(CAPTCHA_BOX).first.evaluate("e => { e.scrollIntoView({block: 'start'}); window.scrollBy(0, -140); }")
    live, taps = Path(captcha["live"]), Path(captcha["taps"])
    taps.unlink(missing_ok=True)
    if captcha.get("start"):
        captcha["start"]()
    size = page.viewport_size
    for _ in range(minutes * 60 * 2):
        if await page.evaluate(TOKEN_JS):
            await page.wait_for_timeout(800)  # let the box show its tick before the last picture
            await page.screenshot(path=str(live) + ".tmp", type="jpeg", quality=70)
            Path(str(live) + ".tmp").replace(live)
            return True
        await page.screenshot(path=str(live) + ".tmp", type="jpeg", quality=70)
        Path(str(live) + ".tmp").replace(live)
        if taps.exists():
            batch = taps.with_suffix(".work")
            taps.replace(batch)
            for line in batch.read_text().split():
                x, y = (float(v) for v in line.split(","))
                await page.mouse.click(x * size["width"], y * size["height"])
                await page.wait_for_timeout(250)
            batch.unlink()
        await page.wait_for_timeout(500)
    return False


CODE_ASKED = re.compile(r"verification code was sent|enter the \d+-character code", re.I)


async def _wait_code(code_file: str, minutes: int = 15) -> str:
    path = Path(code_file)
    for _ in range(minutes * 30):
        if path.exists():
            code = path.read_text().strip()
            path.unlink()
            return code
        await asyncio.sleep(2)
    return ""


async def _enter_code(page, code: str) -> None:
    boxes = page.get_by_text("Security code", exact=True).locator("xpath=following::input[not(@type='hidden')]")
    if await boxes.count() >= len(code) > 1:
        for i, ch in enumerate(code):
            await boxes.nth(i).fill(ch)
    else:
        await boxes.first.fill(code)
    await page.wait_for_timeout(500)


async def _submit(page, result: Result, after_shot: str, seconds: int, code_file: str = "", asked=None,
                  ats: str = "ashby", captcha: dict | None = None) -> None:
    """Clicks once, then waits for the answer and keeps the evidence, whatever it turns out to be."""
    replies = []
    if ats in CAPTCHA_ATS:
        if not captcha or not await _human_captcha(page, captcha):
            result.confirmation = "captcha not solved: nothing was sent"
            if after_shot:
                await page.screenshot(path=after_shot, full_page=True)
            return
        if captcha.get("done"):
            captcha["done"]()

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
    button = page.locator("form button[type=submit]").last if ats == "manatal" else page.get_by_role("button", name=SUBMIT)
    if ats == "breezy":  # the form comes in sections; Continue checks each one before the next shows
        more = page.get_by_role("button", name="Continue", exact=True)
        for _ in range(5):
            if await button.is_visible() or not await more.is_visible():
                break
            await more.click()
            await page.wait_for_timeout(1200)
    await button.click()
    result.submitted = True
    text, after_reply, tried_code = "", 0, False
    for _ in range(seconds):
        await page.wait_for_timeout(1000)
        text = await page.locator("body").inner_text()
        result.confirmed = await button.count() == 0 and looks_sent(text)
        after_reply += 1 if replies else 0
        if not result.confirmed and code_file and not tried_code and CODE_ASKED.search(text):
            tried_code = True  # the ATS emailed a code: the page stays open until the person types it
            if asked:
                asked()
            code = await _wait_code(code_file)
            if not code:
                result.confirmation = "no code arrived in 15 minutes"
                break
            await _enter_code(page, code)
            replies.clear()
            after_reply = 0
            await button.click()
            continue
        if result.confirmed or after_reply >= 8:
            break
    alerts = await page.locator('[role=alert], [class*="error" i]').all_inner_texts()
    result.server = " | ".join(replies)
    result.confirmation = " / ".join([*[a.strip() for a in alerts if a.strip()], text[:200], text[-300:]])[:900]
    if after_shot:
        await page.screenshot(path=after_shot, full_page=True)


MN_UNKNOWN_JS = r"""(ids) => {
  const out = [];
  for (const e of document.querySelectorAll('form input, form textarea, form select')) {
    const id = e.name || e.id;
    if (!id || e.type === 'hidden' || ids.includes(id) || ids.includes(id.replace(/^field/, '')) || !e.offsetParent) continue;
    const label = e.closest('.form-group')?.querySelector('label')?.innerText || e.placeholder || id;
    out.push(label.replace(/\s+/g, ' ').trim().slice(0, 200));
  }
  return [...new Set(out)];
}"""


BZ_UNKNOWN_JS = r"""(ids) => {
  const skip = /^(hp_|smsConsent$|salaryCurrency$|cHeadline$|cResume$)/, out = [];
  for (const e of document.querySelectorAll('form input, form textarea, form select')) {
    if (!e.name || e.type === 'hidden' || e.type === 'file' || skip.test(e.name) || ids.includes(e.name)) continue;
    if (e.closest('[ng-show*="eeoc"], .eeoc, .education, .work-history, [class*=experience]')) continue;
    let h = null;
    for (let n = e; n && !h; n = n.parentElement) h = n.querySelector(':scope > h3');
    out.push(((h && h.innerText) || e.placeholder || e.name).replace(/\s+/g, ' ').trim().slice(0, 200));
  }
  return [...new Set(out)];
}"""


BH_UNKNOWN_JS = r"""(ids) => {
  const skip = /^(nickname_|g-recaptcha|resumeFileId|genderId|ethnicityId|disabilityId|veteranStatusId)/, out = [];
  for (const e of document.querySelectorAll('form input, form textarea, form select')) {
    if (!e.name || e.type === 'hidden' || e.type === 'file' || skip.test(e.name) || ids.includes(e.name)) continue;
    const label = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)?.innerText) || e.name;
    out.push(label.replace(/\s+/g, ' ').trim().slice(0, 200));
  }
  return [...new Set(out)];
}"""


async def _unknown(page, form: Form) -> list:
    if form.ats == "bamboohr":
        return await page.evaluate(BH_UNKNOWN_JS, [f.id for f in form.fields])
    if form.ats == "breezy":
        return await page.evaluate(BZ_UNKNOWN_JS, [f.id for f in form.fields])
    if form.ats == "manatal":
        return await page.evaluate(MN_UNKNOWN_JS, [f.id for f in form.fields])
    if form.ats == "greenhouse":
        return await page.evaluate(GH_UNKNOWN_JS, [f.id for f in form.fields])
    return await page.evaluate(UNKNOWN_JS, [f.label for f in form.fields])


async def _open(page, form: Form) -> None:
    await page.goto(form.url, wait_until="networkidle", timeout=45000)
    if form.ats == "bamboohr":
        await page.get_by_text(bamboohr.OPEN).first.click()
        await page.wait_for_timeout(2500)
    await page.wait_for_timeout(1500)


async def run(form: Form, answers: dict, shot: str, submit: bool = False, accepted: tuple = (),
              after_shot: str = "", seconds: int = 45, code_file: str = "", asked=None, captcha: dict | None = None) -> Result:
    if form.missing(answers) or form.invalid(answers):
        raise ValueError(f"missing {form.missing(answers)}; invalid {form.invalid(answers)}")
    result = Result()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        # a phone-sized page when the person may have to solve a captcha on it
        view = {"width": 440, "height": 820} if form.ats in CAPTCHA_ATS else {"width": 1200, "height": 1000}
        page = await browser.new_page(viewport=view)
        await _open(page, form)
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
            await _submit(page, result, after_shot, seconds, code_file, asked, form.ats, captcha)
        await browser.close()
    return result


async def _scan(form: Form) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await _open(page, form)
        unknown = await _unknown(page, form)
        await browser.close()
    return unknown


def scan(form: Form) -> list:
    """What the page asks that the form data does not list. A person has to see these."""
    return asyncio.run(_scan(form))


def looks_sent(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ("success", "thank you for applying", "application was submitted",
                                   "application has been submitted", "application has been received", "thank you for your application"))


def fill(form: Form, answers: dict, shot: str, submit: bool = False, accepted: tuple = (), after_shot: str = "",
         code_file: str = "", asked=None, captcha: dict | None = None) -> Result:
    return asyncio.run(run(form, answers, shot, submit, tuple(accepted), after_shot, 45, code_file, asked, captcha))


async def _run_live(form: Form, answers: dict, shot: str, after_shot: str, on_ready, minutes: int) -> Result:
    """Fill a captcha form in a HEADED anti-detect browser on the real X display, then leave it open for the person to
    solve the captcha and click Submit over VNC. Reuses the same _open/_fill_field as the headless path, so the fill
    is the exact tested one. Nothing is submitted here: the person submits in the live view; this only watches for the
    success page. (Relevo's idea, proven 2026-11-12: headed + a human beats the headless screenshot relay.)"""
    from patchright.async_api import async_playwright as live_playwright

    result = Result()
    async with live_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/live-{os.getpid()}", headless=False, no_viewport=True,
            args=["--no-sandbox", "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await _open(page, form)
        for f in form.fields:
            if answers.get(f.id) in (None, "", []):
                continue
            try:
                result.verified[f.label] = await _fill_field(page, f, answers[f.id], form.ats)
            except Exception as e:
                result.verified[f.label] = False
                print(f"field failed: {f.label}: {str(e)[:160]}")
        result.unknown = await _unknown(page, form)
        try:
            await page.screenshot(path=shot, full_page=True)
        except Exception:
            pass
        await _scroll_to_captcha(page)  # open the live view already at the captcha / submit, so no scrolling
        if on_ready:
            on_ready()  # the form is filled; the app now shows "open the live view to solve the captcha and submit"
        for _ in range(minutes * 20):  # poll up to `minutes`, person-paced
            await asyncio.sleep(3)
            try:
                text = await page.locator("body").inner_text()
            except Exception:
                break
            if looks_sent(text):
                result.submitted = result.confirmed = True
                result.confirmation = next((ln for ln in text.splitlines() if looks_sent(ln)), "submitted")[:200]
                try:
                    await page.screenshot(path=after_shot, full_page=True)
                except Exception:
                    pass
                break
        await ctx.close()
    return result


def fill_live(form: Form, answers: dict, shot: str, after_shot: str = "", on_ready=None, minutes: int = 30) -> Result:
    if form.missing(answers) or form.invalid(answers):
        raise ValueError(f"missing {form.missing(answers)}; invalid {form.invalid(answers)}")
    return asyncio.run(_run_live(form, answers, shot, after_shot, on_ready, minutes))


APPLY = re.compile(r"\b(apply|postul|aplicar|solicitar)\b", re.I)
LOGIN_WALL = re.compile(r"sign in to apply|iniciar sesión|to apply on the company|easy apply|solicitud sencilla", re.I)

SCROLL_TO_CAPTCHA_JS = r"""() => {
  const el = document.querySelector('iframe[src*="recaptcha"], iframe[title*="captcha" i], .g-recaptcha, [data-sitekey]')
    || [...document.querySelectorAll('button, [type=submit], input[type=submit]')]
         .find(b => /submit|enviar|apply|postul/i.test((b.innerText || b.value || '')));
  if (el) { el.scrollIntoView({block: 'center', inline: 'center'}); return true; }
  window.scrollTo(0, document.body.scrollHeight);  // no captcha found: go to the bottom (submit is there)
  return false;
}"""


async def _scroll_to_captcha(page):
    try:
        await page.evaluate(SCROLL_TO_CAPTCHA_JS)
        await page.wait_for_timeout(600)
    except Exception:
        pass


FIND_APPLY_JS = r"""() => {
  const re = /\b(apply|postul|aplicar|solicitar)\b/i;
  const els = [...document.querySelectorAll('a, button, [role=button]')];
  const m = els.find(e => re.test((e.innerText || e.getAttribute('aria-label') || '').trim()) &&
                          !/sign in|log in|iniciar/i.test(e.innerText || ''));
  if (!m) return null;
  m.setAttribute('data-relevo-apply', '1');
  return '[data-relevo-apply="1"]';
}"""


async def _reach_form(page, url: str):
    """Phase 1: open the job link and get to the actual form. Follows an external 'Apply' button (incl. out of a
    LinkedIn job) and new tabs. Returns (status, page): 'form' (a fillable page), 'wall' (LinkedIn login / Easy
    Apply — not ours) or 'none'. Uses page.evaluate for counts (patchright's Locator.count is broken here)."""
    async def nfields(pg):
        try:
            return await pg.evaluate("() => document.querySelectorAll('input, textarea, select').length")
        except Exception:
            return 0

    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    ctx = page.context
    if await nfields(page) >= 3:
        return "form", page
    before = list(ctx.pages)
    apply_sel = await page.evaluate(FIND_APPLY_JS)
    if apply_sel:
        try:
            await page.locator(apply_sel).first.click(timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        extra = [pg for pg in ctx.pages if pg not in before]
        if extra:
            page = extra[-1]
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            await page.wait_for_timeout(1500)
    body = ((await page.locator("body").inner_text()) or "")[:4000]
    n = await nfields(page)
    if "linkedin.com" in (page.url or "") and n < 3 and LOGIN_WALL.search(body):
        return "wall", page
    return ("form", page) if n >= 3 else ("none", page)


async def _run_live_general(url: str, facts: str, cv_path: str, job: dict, data: str, shot: str, after_shot: str,
                            on_ready, minutes: int) -> Result:
    """Fill ANY form in a headed browser: reach the form, scan fields, map them with the LLM (or a saved recipe for
    this host), fill, then the person solves the captcha and submits. Learns the host on success."""
    from patchright.async_api import async_playwright as live_playwright
    from relevo import general

    result = Result()
    async with live_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/live-{os.getpid()}", headless=False, no_viewport=True,
            args=["--no-sandbox", "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        status, page = await _reach_form(page, url)
        if status == "wall":
            result.confirmation = "wall"
            await ctx.close()
            return result
        if status == "none":
            result.confirmation = "no-form"
            await ctx.close()
            return result
        raw = await general.read_fields(page)
        loop = asyncio.get_event_loop()
        plan_items = await loop.run_in_executor(None, general.plan, raw, facts, cv_path, job)
        fill = await general.fill_fields(page, raw, plan_items, cv_path)
        result.verified = {lbl: True for lbl in fill["filled"]}
        result.unknown = fill["skipped"]
        result.confirmation = f"llené {len(fill['filled'])}/{len(raw)}; revisa: {', '.join(fill['skipped'][:6])}"
        try:
            await page.screenshot(path=shot, full_page=True)
        except Exception:
            pass
        await _scroll_to_captcha(page)
        if on_ready:
            on_ready()
        for _ in range(minutes * 20):
            await asyncio.sleep(3)
            try:
                text = await page.locator("body").inner_text()
            except Exception:
                break
            if looks_sent(text):
                result.submitted = result.confirmed = True
                result.confirmation = next((ln for ln in text.splitlines() if looks_sent(ln)), "submitted")[:200]
                general.save_recipe(data, page.url, raw, plan_items, fill)
                try:
                    await page.screenshot(path=after_shot, full_page=True)
                except Exception:
                    pass
                break
        await ctx.close()
    return result


def fill_live_general(url: str, facts: str, cv_path: str, job: dict, data: str, shot: str, after_shot: str = "",
                      on_ready=None, minutes: int = 30) -> Result:
    return asyncio.run(_run_live_general(url, facts, cv_path, job, data, shot, after_shot, on_ready, minutes))
