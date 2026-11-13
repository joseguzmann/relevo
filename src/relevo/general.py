"""Fill ANY application form, not only the known ATS. Opens the page in the live headed browser, reads every field
with the DOM, asks the LLM to map each field to the candidate's facts (page text is data, never instructions; the
model has no tools and no personal data beyond the facts given), fills with code, and leaves the person to solve the
captcha and submit in the live view. What worked is saved as a recipe per host, so the next form on that host is
faster and does not need the model (phase 3, learning). Known ATS keep their own fast readers; this is the fallback.
"""
import json
import re
from pathlib import Path

from relevo import llm

# One stable selector per field, its label, type and options. Groups radios/checkboxes by name.
SCAN_JS = r"""() => {
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  const sel = (el) => {
    if (el.id) return '#' + esc(el.id);
    // radios/checkboxes share a name: pin the option by value and skip hidden twins
    if ((el.type === 'radio' || el.type === 'checkbox') && el.name)
      return 'input[name="' + el.name.replace(/"/g, '\\"') + '"]' + (el.value ? '[value="' + el.value.replace(/"/g, '\\"') + '"]' : '') + ':not([type=hidden])';
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name.replace(/"/g, '\\"') + '"]';
    const path = []; let e = el;
    while (e && e.tagName && e.tagName.toLowerCase() !== 'body' && path.length < 6) {
      let p = e.tagName.toLowerCase();
      const sibs = [...(e.parentElement ? e.parentElement.children : [])].filter(s => s.tagName === e.tagName);
      if (sibs.length > 1) p += ':nth-of-type(' + (sibs.indexOf(e) + 1) + ')';
      path.unshift(p); e = e.parentElement;
    }
    return path.join(' > ');
  };
  const label = (el) => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + el.id.replace(/"/g,'\\"') + '"]'); if (l) t = l.innerText; }
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const box = el.closest('[class*=field], [class*=question], [class*=form-group], label'); if (box) t = box.innerText; }
    if (!t) t = el.placeholder || el.name || '';
    return t.replace(/\s+/g, ' ').trim().slice(0, 200);
  };
  const out = [], groups = {};
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    if (el.type === 'hidden' || el.name === 'g-recaptcha-response') continue;
    if (el.closest('[aria-hidden=true]')) continue;
    if (el.type !== 'file' && !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) continue;
    const type = tag === 'select' ? 'select' : (tag === 'textarea' ? 'textarea' : el.type);
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name || label(el);
      const fieldset = el.closest('fieldset, [role=radiogroup], [class*=question]');
      groups[key] = groups[key] || {name: key, type, label: label(fieldset || el), options: [], required: el.required};
      groups[key].options.push({label: label(el) || el.value, selector: sel(el), value: el.value});
      continue;
    }
    const options = type === 'select' ? [...el.options].map(o => o.textContent.replace(/\s+/g, ' ').trim()).filter(Boolean) : [];
    out.push({selector: sel(el), label: label(el), type, options, required: el.required});
  }
  for (const g of Object.values(groups)) out.push(g);
  return out;
}"""

PLAN_SCHEMA = {"type": "object", "required": ["fields"], "properties": {"fields": {"type": "array", "items": {
    "type": "object", "required": ["i", "value", "state"], "properties": {
        "i": {"type": "integer"},                       # index into the scanned field list
        "value": {},                                     # the text to type, the option label to pick, or "Yes"/"No"
        "state": {"type": "string", "enum": ["ready", "confirm", "skip"]},  # skip = the person must do it (unknown)
        "source": {"type": "string"}}}}}}                # which fact or draft this came from


def _label_list(raw: list) -> str:
    out = []
    for i, f in enumerate(raw):
        extra = ""
        if f["type"] in ("select", "radio", "checkbox"):
            opts = [o["label"] if isinstance(o, dict) else o for o in f.get("options", [])]
            extra = " · options: " + " | ".join(str(o) for o in opts[:20])
        out.append(f'[{i}] ({f["type"]}{"*" if f.get("required") else ""}) {f["label"]}{extra}')
    return "\n".join(out)


def plan(raw: list, facts: str, cv_path: str, job: dict, writing: str = "", data: str = "/data") -> list:
    """Map scanned fields to values. Fields the person has filled before (by label) come from the learned store first,
    so they are not re-asked; the rest go to the LLM, drafting open questions in the candidate's voice (never invented)."""
    learned = load_learned(data)
    out, ask_fields, idx_map = [], [], {}
    for i, f in enumerate(raw):
        lv = from_learned(f.get("label", ""), learned)
        if lv is not None:
            out.append({"i": i, "value": lv, "state": "ready" if lv != "" else "skip", "source": "learned"})
        else:
            idx_map[len(ask_fields)] = i
            ask_fields.append(f)
    if not ask_fields:
        return out
    system = ("You map an application form's fields to a candidate's facts. For each field, give the exact text to "
              "type, or for a select/radio/checkbox the option LABEL to choose, or 'Yes'/'No' for a yes-no. "
              "A resume/CV upload: value '" + Path(cv_path).name + "', state 'ready'. "
              "OPEN QUESTIONS (motivation, 'tell us about…', 'your biggest accomplishment', 'what do you think of X'): "
              "write a real answer, 2-4 honest sentences, in the candidate's VOICE following the writing rules, grounded "
              "in a specific project or number from the facts. No empty adjectives, no invented facts, no recycled CV "
              "sentences. Respect the question's length limit if it states one (e.g. 280 characters). State 'confirm' so "
              "the person reviews it. If a field needs something not in the facts (an education dropdown, a required doc, "
              "anything personal that is not given), state 'skip' so the person fills it in the live view. "
              + ("\n\nWRITING RULES (the candidate's voice):\n" + writing if writing else ""))
    user = (f"CANDIDATE FACTS:\n{facts}\n\nJOB: {job.get('company','')} — {job.get('title','')}\n"
            f"{job.get('description','')[:1500]}\n\nFIELDS (index, type, label, options):\n{_label_list(ask_fields)}")
    for it in llm.ask(system, user, PLAN_SCHEMA).get("fields", []):
        if isinstance(it.get("i"), int) and it["i"] in idx_map:
            it["i"] = idx_map[it["i"]]
            out.append(it)
    return out


async def read_fields(page) -> list:
    return await page.evaluate(SCAN_JS)


async def fill_fields(page, raw: list, plan_items: list, cv_path: str) -> dict:
    """Fill what the plan marked ready/confirm. Returns {"filled":[labels], "skipped":[labels], "failed":[labels]}."""
    result = {"filled": [], "skipped": [], "failed": []}
    by_i = {p["i"]: p for p in plan_items if isinstance(p.get("i"), int)}
    for i, f in enumerate(raw):
        p = by_i.get(i)
        label = f["label"] or f["type"]
        if not p or p["state"] == "skip" or p.get("value") in (None, "", []):
            result["skipped"].append(label)
            continue
        try:
            if await _fill_one(page, f, p["value"], cv_path):
                result["filled"].append(label)
            else:
                result["failed"].append(label)
        except Exception as e:
            print(f"general fill failed: {label}: {str(e)[:120]}")
            result["failed"].append(label)
    return result


async def _fill_one(page, f: dict, value, cv_path: str) -> bool:
    t = f["type"]
    if t == "file":
        await page.locator('input[type=file]').first.set_input_files(cv_path)
        await page.wait_for_timeout(1500)
        return True
    if t == "select":
        try:
            await page.locator(f["selector"]).select_option(label=str(value))
        except Exception:
            await page.locator(f["selector"]).select_option(str(value))
        return True
    if t in ("radio", "checkbox"):
        opts = f.get("options", [])
        want = [str(v).strip() for v in (value if isinstance(value, list) else [value])]
        affirm = {"yes", "sí", "si", "true", "1", "on", "acepto", "agree", "i agree"}
        if t == "checkbox" and len(opts) == 1 and want and want[0].lower() in affirm:
            await page.locator(opts[0]["selector"]).first.check(force=True)
            return True
        hit = False
        for o in opts:
            if str(o.get("label", "")).strip() in want or str(o.get("value", "")) in want:
                await page.locator(o["selector"]).first.check(force=True)
                hit = True
        return hit
    box = page.locator(f["selector"]).first
    await box.fill(str(value))
    return (await box.input_value()) == str(value)


# ---- phase 3: recipes (learning) ----
# A recipe is a saved plan for a host: {host: {"fields": [{label, type, value_source}], "saved_at": ...}}. For now it
# stores the LLM plan keyed by the normalized field set, so a form on a known host skips the LLM. Full reuse (selectors
# that survive re-renders) is iterated once we see real repeats.

def _host(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def recipe_path(data: str) -> Path:
    return Path(data) / "recipes.json"


def load_recipes(data: str) -> dict:
    p = recipe_path(data)
    return json.loads(p.read_text()) if p.exists() else {}


# ---- learning from the person's live-view corrections ----
# When the person fixes or completes a field in the live view and submits, we remember the VALUE by its normalized
# label, so the next form pre-fills it (your education, that a referral is empty, your pronoun, etc.). Long free-text
# answers are not learned (too company-specific). Stored in data/learned.json: {normalized_label: {"value", "type"}}.

READ_VALUES_JS = r"""() => {
  const lab = (el) => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + el.id.replace(/"/g,'\\"') + '"]'); if (l) t = l.innerText; }
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const b = el.closest('[class*=field], [class*=question], [class*=form-group], label'); if (b) t = b.innerText; }
    return (t || el.name || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  const out = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    if (el.type === 'hidden' || el.type === 'file' || el.name === 'g-recaptcha-response') continue;
    let v = null, type = el.type;
    if (tag === 'select') { v = (el.options[el.selectedIndex] || {}).text || ''; type = 'select'; }
    else if (el.type === 'checkbox' || el.type === 'radio') { if (!el.checked) continue; v = lab(el) || el.value; }
    else { v = el.value; type = tag === 'textarea' ? 'textarea' : 'text'; }
    if (v == null) continue;
    out.push({label: lab(el), value: String(v), type});
  }
  return out;
}"""


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (label or "").lower()).strip()


def learned_path(data: str) -> Path:
    return Path(data) / "learned.json"


def load_learned(data: str) -> dict:
    p = learned_path(data)
    return json.loads(p.read_text()) if p.exists() else {}


async def read_values(page) -> list:
    try:
        return await page.evaluate(READ_VALUES_JS)
    except Exception:
        return []


def learn(data: str, final_values: list) -> int:
    """Save the person's final values by normalized label, skipping long free-text (company-specific)."""
    learned = load_learned(data)
    n = 0
    for v in final_values:
        label, value, typ = v.get("label", ""), (v.get("value") or "").strip(), v.get("type")
        if not label or typ == "textarea" or len(value) > 120:
            continue  # long open answers are not reused
        key = _norm(label)
        if not key:
            continue
        if learned.get(key, {}).get("value") != value:
            learned[key] = {"value": value, "type": typ}
            n += 1
    if n:
        learned_path(data).write_text(json.dumps(learned, ensure_ascii=False, indent=1))
    return n


def from_learned(label: str, learned: dict):
    """The learned value for a field label, or None. Also leaves referral/'how did you hear' empty on purpose."""
    key = _norm(label)
    if key in learned:
        return learned[key]["value"]
    if any(k in key for k in ("who referred", "referred you", "referral", "how did you hear")):
        return ""  # nobody referred: leave empty
    return None


def save_recipe(data: str, url: str, raw: list, plan_items: list, result: dict) -> None:
    """Remember what filled on this host, so coverage grows on its own."""
    recipes = load_recipes(data)
    host = _host(url)
    recipes[host] = {
        "url": url,
        "fields": len(raw),
        "filled": len(result["filled"]),
        "skipped": result["skipped"],
        "labels": [f["label"] or f["type"] for f in raw],
        "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    recipe_path(data).write_text(json.dumps(recipes, ensure_ascii=False, indent=1))
