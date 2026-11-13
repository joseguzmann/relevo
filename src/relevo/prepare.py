"""From a candidate job to a panel. Rules that matter are code, so a model cannot talk its way past them."""
import re
from datetime import date
from pathlib import Path

from relevo import filler, forms, general, llm, route, track

ALWAYS_ASK = re.compile(r"consent|agree|authori[sz]e|terms|privacy|gender|race|ethnic|disabilit|veteran|pronoun", re.I)
ALWAYS_CONFIRM = re.compile(r"salary|compensation|expect|rate|sponsor|visa|work permit|authorized to work|"
                            r"recruitment process|freelance|notice|availab|years", re.I)
FAMILIES = {"ai": "ai", "backend": "backend"}


class Kit:
    def __init__(self, path: str):
        self.path = Path(path)

    def text(self, name: str) -> str:
        return (self.path / name).read_text()

    def family(self, job: dict) -> str:
        return FAMILIES.get(job.get("category", ""), "fullstack")

    def cv(self, family: str) -> str:
        return str(self.path / "cv" / f"{family}.pdf")

    def facts(self, family: str) -> str:
        return self.text("facts.md") + "\n\n# CV\n" + self.text(f"cv/{family}.txt")


def merge(form, drafted: list[dict], cv_path: str) -> list[dict]:
    """Joins the form with the draft. A field's type, options and required flag come only from the form."""
    by_id = {a["id"]: a for a in drafted}
    out = []
    for f in form.fields:
        a = by_id.get(f.id, {})
        value, state, source = a.get("value"), a.get("state", "missing"), a.get("source", "")
        if f.kind == "file":
            value, state, source = cv_path, "ready", "CV of the role family"
        elif f.options and value not in (None, ""):
            values = value if isinstance(value, list) else [value]
            if any(v not in f.options for v in values):
                value, state, source = None, "missing", "the draft was not one of the options"
        if ALWAYS_ASK.search(f.label):
            value, state, source = None, "missing", "only you can answer this"
        elif state == "ready" and ALWAYS_CONFIRM.search(f.label):
            state = "confirm"
        if value in (None, "", []):
            state = "missing"
        out.append({"id": f.id, "label": f.label, "kind": f.kind, "required": f.required, "options": list(f.options),
                    "value": value, "state": state, "source": source})
    return out


def reread(job: dict, text: str) -> dict:
    system = ("You check a job posting for one candidate in Quito, Ecuador. keep is false only if the posting clearly "
              "rules the candidate out: it is not remote, it excludes Ecuador or is limited to countries that leave "
              "Ecuador out, or it asks more than 4 years of experience. A mention of one country is not an exclusion "
              "when the posting also says LATAM or global. doubt says, in one or two short sentences in Spanish, what "
              "ruled it out or what is unclear about remote work from Ecuador or the years asked, quoting the posting; "
              "it is empty when nothing is. LATAM, Latin America, South America, Americas and global all include Ecuador, "
              "and a posting that states no years is fine: neither is a doubt. Do not comment on the candidate's skills. "
              "The candidate reads it and decides.")
    return llm.ask(system, f"{job.get('title')} at {job.get('company')}\n\n{text}", llm.REREAD)


def prepare(job: dict, kit: Kit, data: str = "/data") -> tuple[str, str, dict, str]:
    """Returns (route, status, panel, reason). Nothing is dropped here: a doubt is a warning, and the person decides."""
    way, target = route.decide(job)
    panel = {"job": {k: job.get(k) for k in ("id", "title", "company", "url", "why", "match", "blockers", "posted_at",
                                             "stack", "description", "sources", "location")},
             "history": job.get("history", []), "target": target}
    if way == "manual":
        return way, "manual", panel, "no form or email Relevo can use"
    check = reread(job, job.get("description", ""))
    panel["reread"] = check
    if not check["keep"] or check.get("doubt"):
        panel["warning"] = {"discard": not check["keep"], "text": check.get("doubt") or check["reason"]}
    family = kit.family(job)
    panel["family"] = family
    panel["codes"] = {"pdf": track.new_code(), "form": track.new_code()}
    cv = track.stamp_cv(kit.cv(family), Path(data) / "cv" / str(job["id"]), panel["codes"]["pdf"])
    if way in forms.READERS:
        form = forms.fetch(way, target)
        fields = [{"id": f.id, "label": f.label, "kind": f.kind, "options": list(f.options)} for f in form.fields]
        system = ("You fill a job application form. One answer per field id; for options use the exact option text. "
                  "For open questions (motivation, 'tell us about…', 'your biggest accomplishment'), write a real answer "
                  "of 2-4 honest sentences in the candidate's VOICE following the writing rules, grounded in a specific "
                  "project or number from the facts — no empty adjectives, no invented facts, no recycled CV sentences; "
                  "respect any stated length limit. Mark those 'confirm' so the person reviews them.")
        user = (f"Today is {date.today().isoformat()}.\n\n# Writing rules (the candidate's voice)\n{kit.text('writing.md')}\n\n"
                f"# Candidate\n{kit.facts(family)}\n\n# Job\n{job.get('description', '')}\n\n# Fields\n{fields}")
        drafted = llm.ask(system, user, llm.ANSWERS)["answers"]
        panel["form_url"] = form.url
        panel["fields"] = merge(form, drafted, cv)
        learned = general.load_learned(data)  # the person's past corrections win (education, referral empty, pronoun…)
        for f in panel["fields"]:
            lv = general.from_learned(f["label"], learned)
            if lv is not None and f["kind"] != "file":
                f["value"] = lv
            elif isinstance(f["value"], str) and f["kind"] != "file":
                f["value"] = track.in_text(f["value"], panel["codes"]["form"])
        panel["unknown"] = filler.scan(form)
    else:
        system = "Write a short application email in the candidate's voice, following the writing rules exactly."
        user = (f"# Writing rules\n{kit.text('writing.md')}\n\n# Candidate\n{kit.facts(family)}\n\n"
                f"# Post\n{job.get('description', '')}")
        mail = llm.ask(system, user, llm.EMAIL)
        panel["email"] = {"to": target, "subject": mail["subject"], "body": track.in_text(mail["body"], panel["codes"]["form"]), "cv": cv}
    return way, "ready", panel, ""
