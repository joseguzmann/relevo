"""Live apply: a HEADED patchright browser on the Gateway that Relevo fills, then the person solves the captcha
and submits by hand over noVNC. Replaces the headless + screenshot-relay that Google rejected (proven 2026-11-12).
Only BambooHR for now. Nothing is submitted by this script: the person clicks Submit in the VNC window; the script
only watches for the success page and records it.
    DISPLAY=:99 python live.py <form_url> <answers.json> <cv_path> <result.json>
answers.json: [{"id": "...", "value": "...", "kind": "text|yes_no|single_choice|file", ...}, ...] (Relevo's panel fields)
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

SENT = re.compile(r"thank you|received your application|application (?:was )?submitted|gracias por (?:tu|su) (?:solicitud|postulaci)", re.I)
TOKEN_JS = "() => (document.querySelector('[name=g-recaptcha-response]')?.value || '').length"


def us_date(s):
    return s


async def fill_bamboohr(page, f, value, cv):
    kind = f.get("kind")
    if kind == "file":
        await page.locator("input[type=file]").first.set_input_files(cv)
        for _ in range(15):
            await page.wait_for_timeout(1000)
            if Path(cv).name in await page.locator("form").inner_text():
                return True
        return False
    fid = f["id"]
    if fid == "countryId.value":
        await page.locator('button[aria-label^="Country"]').click()
        await page.locator(".fab-MenuSearch__input").fill(value)
        await page.wait_for_timeout(600)
        await page.get_by_role("menuitem", name=value, exact=True).first.click()
        await page.wait_for_timeout(1500)
        return True
    group = page.locator(f'input[name="{fid}"]')
    if kind == "yes_no":
        await group.first.set_checked(value == "Yes", force=True)
        return True
    if kind == "single_choice":
        await group.locator("xpath=ancestor::label").filter(
            has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")).first.click()
        return True
    box = page.locator(f'[name="{fid}"]').first
    await box.fill(str(value))
    return (await box.input_value()) == str(value)


async def main(url, answers_path, cv, result_path):
    answers = json.loads(Path(answers_path).read_text())
    result = {"filled": [], "missed": [], "submitted": False, "confirmation": ""}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/live-profile", headless=False, no_viewport=True,
            args=["--no-sandbox", "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        # BambooHR opens the form behind an "Apply" button on some pages.
        try:
            apply = page.get_by_role("button", name=re.compile("apply|postular", re.I)).first
            if await apply.is_visible(timeout=3000):
                await apply.click()
                await page.wait_for_timeout(1500)
        except Exception:
            pass
        for f in answers:
            try:
                ok = await fill_bamboohr(page, f, f.get("value"), cv)
                (result["filled"] if ok else result["missed"]).append(f["id"])
            except Exception as e:
                result["missed"].append(f'{f["id"]}: {str(e)[:60]}')
        Path(result_path).write_text(json.dumps(result, ensure_ascii=False))
        print(f"[live] filled {len(result['filled'])}, missed {len(result['missed'])}: {result['missed']}", flush=True)
        print("[live] ready. Solve the captcha and click Submit in the VNC window. Watching for the success page...", flush=True)
        for _ in range(1200):  # up to 60 min
            await asyncio.sleep(3)
            try:
                body = await page.locator("body").inner_text()
                tok = await page.evaluate(TOKEN_JS)
            except Exception:
                break
            if tok and "token" not in result:
                result["token"] = True; print(f"[live] reCAPTCHA token present ({tok})", flush=True)
            if SENT.search(body):
                result["submitted"] = True
                result["confirmation"] = next((l for l in body.splitlines() if SENT.search(l)), "submitted")[:200]
                Path(result_path).write_text(json.dumps(result, ensure_ascii=False))
                print(f"[live] >>> SUBMITTED: {result['confirmation']}", flush=True)
                await page.screenshot(path=str(Path(result_path).with_suffix(".png")), full_page=True)
                break
        await ctx.close()


asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
