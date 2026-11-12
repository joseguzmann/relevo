"""Open a URL in a HEADED patchright Chrome (anti-detect, real display) and report the reCAPTCHA token state.
Prototype of Jose's idea (2026-11-12): instead of a headless browser + screenshot relay (which Google rejects),
a person drives the real Gateway browser by VNC and solves the captcha with real mouse/keyboard. This proves the
captcha-level question WITHOUT submitting anything: if the g-recaptcha-response token populates and holds, the
headed+human approach beats the old headless+relay. Nothing is ever submitted here.
Usage: DISPLAY=:99 python probe.py <url>
"""
import asyncio
import sys
import time

from patchright.async_api import async_playwright

TOKEN_JS = "() => (document.querySelector('[name=g-recaptcha-response]')?.value || '').length"
FRAME_JS = "() => !!document.querySelector('iframe[src*=\"recaptcha\"]')"


async def main(url):
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/captcha-profile", headless=False, no_viewport=True,
            args=["--no-sandbox", "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        print(f"[probe] opened {url}", flush=True)
        print("[probe] solve the reCAPTCHA in the VNC window. Watching the token (no submit)...", flush=True)
        had = False
        while True:
            try:
                has_frame = await page.evaluate(FRAME_JS)
                tok = await page.evaluate(TOKEN_JS)
            except Exception as e:
                print(f"[probe] page gone: {e}", flush=True)
                break
            if tok and not had:
                print(f"[probe] >>> reCAPTCHA TOKEN PRESENT (len={tok}) at {time.strftime('%H:%M:%S')} "
                      f"- headed+human produced a token. This is what the old headless relay could not.", flush=True)
                had = True
            if had and not tok:
                print("[probe] token cleared (expired or re-challenged)", flush=True)
                had = False
            await asyncio.sleep(3)


asyncio.run(main(sys.argv[1]))
