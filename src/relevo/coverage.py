"""Registry of application exits: which ATS Relevo can send to, how, and which one is worth building next.

Single source of truth for the two questions Jose asked (2026-11-12):
  1. where can Relevo auto-apply today, and in what state;
  2. which site is the next most relevant to add.

`send`:    auto  = Relevo fills and submits by itself (no captcha)
           hand  = a captcha the person solves: Relevo fills the Gateway browser, the person solves it in the live
                   view on the phone and taps Enviar (since 2026-11-12)
           none  = no reader yet
`proven`:  a real application went through this ATS (job number in `note`)
`volume`:  jobs seen in the last 30 days at match >= 7 (Vigia ATS counts, card of 2026-11-06; refresh with
           `relevo coverage --from-vigia` when that is wired). Volume is the priority signal for what to build next.

Keep this in sync with `forms.READERS` / `forms.HAND`; `test_coverage` checks they agree.
"""
from dataclasses import dataclass


@dataclass
class Exit:
    key: str
    name: str
    send: str          # auto | hand | none
    captcha: str       # ""(none) | recaptcha | turnstile | hcaptcha | code-by-email
    proven: bool
    volume: int
    note: str = ""


# Order is by hand-tuned clarity, not priority; `next_to_build()` ranks by volume.
EXITS = [
    Exit("ashby",         "Ashby",          "auto", "",             False, 165, "reader built; no live send confirmed yet"),
    Exit("manatal",       "Manatal",        "auto", "",             True,  114, "proven #117 iDelsoft (API said success)"),
    Exit("greenhouse",    "Greenhouse",     "auto", "code-by-email", True,  73, "proven #116 Arionkoder: 428 then 8-char code by email, typed in the app"),
    Exit("bamboohr",      "BambooHR",       "hand", "recaptcha",    True,   46, "proven live view: #149 Koltin (12-nov), #148 Newsmatics (13-nov, 'submitted successfully')"),
    Exit("workday",       "Workday",        "none", "",             False,  36, "needs an account; no reader"),
    Exit("breezy",        "Breezy",         "auto", "",             True,    7, "proven #119 Urrly (POST 204, 'submitted successfully')"),
    Exit("workable",      "Workable",       "none", "turnstile",    False,   7, "no reader; general live filler untried here"),
    Exit("recruitee",     "Recruitee",      "none", "hcaptcha",     False,   7, "no reader; general live filler untried here"),
    Exit("lever",         "Lever",          "none", "hcaptcha",     False,   6, "no reader; general live filler filled 9/12 (12-nov), never sent"),
    Exit("smartrecruiters","SmartRecruiters","none", "",            False,   3, "too few to build for now"),
    Exit("teamtailor",    "Teamtailor",     "none", "",             False,   2, "too few to build for now"),
]

BY_KEY = {e.key: e for e in EXITS}


def covered() -> list[Exit]:
    """ATS Relevo can already send to (auto or hand)."""
    return [e for e in EXITS if e.send in ("auto", "hand")]


def next_to_build(limit: int = 3) -> list[Exit]:
    """The uncovered ATS worth building next, highest volume first."""
    return sorted((e for e in EXITS if e.send == "none"), key=lambda e: -e.volume)[:limit]


def summary() -> dict:
    cov = covered()
    return {
        "covered": [e.key for e in cov],
        "covered_volume": sum(e.volume for e in cov),
        "total_volume": sum(e.volume for e in EXITS),
        "next": [(e.key, e.volume, e.note) for e in next_to_build()],
    }


def render() -> str:
    rows = ["key           send   captcha        proven  vol  note"]
    for e in sorted(EXITS, key=lambda e: -e.volume):
        rows.append(f"{e.key:13} {e.send:5}  {e.captcha or '-':13}  {'yes' if e.proven else '-':6}  {e.volume:3}  {e.note}")
    s = summary()
    pct = round(100 * s["covered_volume"] / s["total_volume"]) if s["total_volume"] else 0
    rows.append("")
    rows.append(f"covered: {', '.join(s['covered'])}  ({pct}% of 30-day 7+ volume)")
    rows.append("next to build: " + ", ".join(f"{k} ({v})" for k, v, _ in s["next"]))
    return "\n".join(rows)


if __name__ == "__main__":
    print(render())
