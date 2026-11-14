"""The loop: read candidates, prepare, notify. And the send, which only an approved item reaches."""
import json
import sys
import logging
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from relevo import filler, forms, mailer, prepare, push, track
from relevo.store import Store

log = logging.getLogger("relevo.worker")
TZ = ZoneInfo("America/Guayaquil")


class Config:
    def __init__(self):
        self.data = os.environ.get("RELEVO_DATA", "/data")
        self.kit = os.environ.get("RELEVO_KIT", "/kit")
        self.site = os.environ.get("RELEVO_SITE", "https://relevo.joseguzman.dev")
        self.sender = os.environ.get("RELEVO_FROM", "")
        self.quiet = (22, 8)
        self.token = f"{self.data}/gmail-send.json"


def quiet(cfg: Config, hour: int | None = None) -> bool:
    hour = datetime.now(TZ).hour if hour is None else hour
    start, end = cfg.quiet
    return hour >= start or hour < end


def candidates(cfg: Config) -> list[dict]:
    path = Path(cfg.data) / "inbox" / "candidates.json"
    return json.loads(path.read_text()) if path.exists() else []


def backlog(cfg: Config) -> list[dict]:
    path = Path(cfg.data) / "inbox" / "backlog.json"
    return json.loads(path.read_text()) if path.exists() else []


def notify(cfg: Config, store: Store, pem: str, job: dict, status: str, item: int | None, panel: dict) -> None:
    """Every job in the app gets a notification."""
    if item:
        from relevo import route
        way = "manual" if status == "manual" else (panel.get("route") or store.get(item)["route"])
        sup = route.support(way, panel.get("target", ""), job.get("url", ""))
        short = {"auto": "Relevo la envía", "captcha": "llena + captcha", "ai": "IA en vivo", "hand": "a mano"}[sup["level"]]
        warn = "⚠️ " if panel.get("warning") else ""
        push.send(store, pem, cfg.site, f"{sup['icon']} {job.get('company') or 'Relevo'}",
                  f"{warn}{short} · {job.get('title', '')[:80]}", item)


def page_text(url: str) -> str:
    """An aggregator page may hold the real form link. LinkedIn is never fetched from here."""
    if not url or "linkedin.com" in url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"user-agent": "relevo/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(500_000).decode("utf-8", "ignore")
    except Exception:
        return ""


def tick(cfg: Config, store: Store, kit: prepare.Kit, pem: str) -> None:
    if quiet(cfg):
        return
    for job in candidates(cfg):
        if store.known(job["id"]):
            continue
        try:
            job["page_text"] = page_text(job.get("url", ""))
            way, status, panel, reason = prepare.prepare(job, kit, cfg.data)
        except Exception as e:  # one bad posting must not stop the others
            log.warning("job %s: %s", job["id"], str(e)[:200])
            way, status, panel, reason = "manual", "manual", {"job": job, "target": job.get("url", "")}, str(e)[:200]
        notify(cfg, store, pem, job, status, store.add(job["id"], way, status, panel, reason), panel)


def outbox(cfg: Config, item: dict, material: str) -> None:
    if item["job_id"] < 0 or is_test(item):  # a test item is not an application
        return
    folder = Path(cfg.data) / "outbox"
    folder.mkdir(exist_ok=True)
    panel = item["data"]
    record = {"job_id": item["job_id"], "family": panel.get("family"), "route": item["route"], "material": material,
              "cv": f"{cv_version(cfg)} {panel.get('family')} (Relevo)", "panel": panel, "codes": panel.get("codes", {}),
              "company": panel.get("job", {}).get("company", "")}
    (folder / f"{item['id']}.json").write_text(json.dumps(record, ensure_ascii=False))


def form_material(panel: dict, ats: str = "") -> str:
    return f"{forms.NAMES.get(ats, 'ATS')} form by Relevo: " + "; ".join(f"{f['label'][:40]}={f['value']}" for f in panel["fields"] if f["kind"] != "file")


def cv_version(cfg: Config) -> str:
    """The CV folder the kit was built from (written by career-os scripts/relevo-kit.sh), e.g. 2026-11-12-v4."""
    try:
        return (Path(cfg.kit) / "cv" / "version").read_text().strip() or "cv?"
    except OSError:
        return "cv?"


def code_file(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"code-{item_id}.txt"


def live_file(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"live-{item_id}.jpg"


def taps_file(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"taps-{item_id}.txt"


def evidence(cfg: Config, item_id: int) -> Path:
    """Every request the click sent and what came back: the record of what actually went out."""
    return Path(cfg.data) / f"sent-{item_id}.json"


def live_cmd(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"live-cmd-{item_id}"


def after_shot(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"shot-{item_id}-after.png"


TEST_JOB_IDS = 999000  # seeded test items live at job ids >= this


def is_test(item: dict) -> bool:
    """A test item never applies: dry run in the live browser, no submit on headless ATSs, never marked sent."""
    job = (item.get("data") or {}).get("job", {})
    return bool((item.get("data") or {}).get("test")) or int(item.get("job_id") or job.get("id") or 0) >= TEST_JOB_IDS \
        or str(job.get("title", "")).upper().startswith("PRUEBA") or str(job.get("company", "")).upper().startswith("PRUEBA")


def _finish_test(cfg: Config, store: Store, item_id: int, result) -> None:
    """Keep every blocked request (evidence file) and show the submit-like ones first, not the analytics beacons."""
    evidence(cfg, item_id).write_text(json.dumps({"dry_run": True, "blocked": result.blocked}, ensure_ascii=False, indent=1))
    noise = ("pendo", "ptm.gif", "analytics", "collect", "beacon", "sentry", "datadog", "hotjar")
    real = [b for b in result.blocked if not any(n in b["url"].lower() for n in noise)]
    shown = "; ".join(f"{b['method']} {b['url'][:90]}" for b in real[:3]) or "solo analítica (ningún POST del formulario)"
    store.finish(item_id, "failed", f"{result.confirmation or 'PRUEBA: no se envió'}. Del formulario: {shown}")


def resolve(cfg: Config, store: Store, item: dict, sent: bool) -> None:
    """The person checked an `unclear` send. Only their word moves it, in either direction."""
    if sent:
        outbox(cfg, item, form_material(item["data"], item["route"]) + " (confirmed by hand)")
        store.finish(item["id"], "sent", "confirmed by the person")
    else:
        store.finish(item["id"], "failed", "the person says it did not arrive")


def send(cfg: Config, store: Store, item_id: int) -> None:
    """Runs after claim(). Any doubt ends in `unclear`, which is never retried by code."""
    item = store.get(item_id)
    panel = item["data"]
    test = is_test(item)
    try:
        if item["route"] == "email":
            if test:
                store.finish(item_id, "failed", "PRUEBA: el correo no se envía")
                return
            mail = panel["email"]
            msg = mailer.build(cfg.sender, mail["to"], mail["subject"], mail["body"], mail["cv"])
            sent_id = mailer.send(cfg.token, msg)
            outbox(cfg, item, f"Email to {mail['to']}, Gmail id {sent_id}: {mail['subject']}")
            store.finish(item_id, "sent", "email sent")
            return
        form = forms.fetch(item["route"], panel["target"])
        known = {f["id"] for f in panel["fields"]}
        if {f.id for f in form.fields} != known:
            store.finish(item_id, "failed", "the form changed; nothing was sent")
            return
        answers = {f["id"]: f["value"] for f in panel["fields"]}
        shot = str(Path(cfg.data) / f"shot-{item_id}.png")
        company = panel.get("job", {}).get("company", "")

        if item["route"] in forms.HAND:
            # Captcha ATS: Relevo fills a headed browser on the Gateway; the person opens the live view (only through
            # the app, behind the passcode), solves the captcha and clicks Submit. The browser is shared via /live.
            def live_ready():
                store.finish(item_id, "captcha", "lleno: abre la vista para resolver el captcha y enviar")
                push.send(store, push.keys(cfg.data)[0], cfg.site, "Resuelve el captcha",
                          f"{company}: ábrela en la app, resuelve el captcha y envía", item_id)

            # `unknown` fields (ones the form asks that the panel did not have) are NOT a failure here: the person
            # fills them in the live view before submitting. Only a field that should have filled but did not fails.
            result = filler.fill_live(form, answers, shot, after_shot=str(after_shot(cfg, item_id)), on_ready=live_ready,
                                      cmd_file=str(live_cmd(cfg, item_id)), dry=test)
            if test:
                _finish_test(cfg, store, item_id, result)
            elif result.failed:
                store.finish(item_id, "failed", f"no se llenó: campos fallidos {result.failed}")
            elif result.confirmed:
                outbox(cfg, item, form_material(panel, item["route"]))
                store.finish(item_id, "sent", result.confirmation[:200])
            else:
                store.finish(item_id, "failed", "no se envió (captcha/Submit no completado a tiempo)")
            return

        def asked():
            store.finish(item_id, "code", "the ATS emailed a code to confirm the application")
            push.send(store, push.keys(cfg.data)[0], cfg.site, "Falta el código",
                      f"{company}: escribe el código que llegó al correo", item_id)

        result = filler.fill(form, answers, shot, submit=not test, accepted=panel.get("accepted_unknown", []),
                             after_shot=str(after_shot(cfg, item_id)), code_file=str(code_file(cfg, item_id)), asked=asked)
        evidence(cfg, item_id).write_text(json.dumps(result.requests, ensure_ascii=False, indent=1))
        if test:
            store.finish(item_id, "failed", f"PRUEBA: llenado sin enviar; fallidos {result.failed}, desconocidos {result.unknown}")
        elif result.failed or (result.unknown and not result.submitted):
            store.finish(item_id, "failed", f"not sent: failed {result.failed}, unknown {result.unknown}")
        elif result.confirmed:
            outbox(cfg, item, form_material(panel, item["route"]))
            store.finish(item_id, "sent", result.confirmation[:200])
        else:
            store.finish(item_id, "unclear", f"submitted, no confirmation seen. ATS: {result.server or 'no answer seen'}. "
                                             f"Page: {result.confirmation}")
    except Exception as e:
        store.finish(item_id, "unclear", f"stopped mid-send, check before retrying: {str(e)[:200]}")


def try_live(cfg: Config, store: Store, item_id: int) -> None:
    """General live fill of ANY form (manual items): reach the form from the job link, fill what the LLM can map, then
    the person reviews, solves the captcha and submits in the live view. LinkedIn login / Easy Apply falls back to 'a mano'."""
    item = store.get(item_id)
    panel = item["data"]
    job = panel.get("job", {})
    url = job.get("url") or panel.get("target")
    if "linkedin.com" in (url or "") and panel.get("target") and "linkedin.com" not in panel["target"]:
        url = panel["target"]  # the post is behind a login; its link (resolved from lnkd.in) is where the form is
    company = job.get("company", "")
    try:
        kit = prepare.Kit(cfg.kit)
        family = kit.family(job)
        facts = kit.facts(family)
        writing = kit.text("writing.md")
        codes = panel.get("codes") or {"pdf": track.new_code(), "form": track.new_code()}
        cv = track.stamp_cv(kit.cv(family), Path(cfg.data) / "cv" / str(job.get("id", item_id)), codes["pdf"])
        shot = str(Path(cfg.data) / f"shot-{item_id}.png")

        def ready():
            store.finish(item_id, "captcha", "llené lo que supe: abre la vista, revisa los campos que faltan, resuelve el captcha y envía")
            push.send(store, push.keys(cfg.data)[0], cfg.site, "Revisa y envía",
                      f"{company}: ábrela, completa lo que no supe, resuelve el captcha y envía", item_id)

        result = filler.fill_live_general(url, facts, cv, job, cfg.data, shot,
                                          after_shot=str(after_shot(cfg, item_id)), on_ready=ready, writing=writing,
                                          cmd_file=str(live_cmd(cfg, item_id)), dry=is_test(item))
        if is_test(item) and result.confirmation not in ("wall", "no-form"):
            _finish_test(cfg, store, item_id, result)
        elif result.confirmation == "wall":
            store.finish(item_id, "manual", "es LinkedIn/login (Easy Apply): esta se postula a mano")
        elif result.confirmation == "no-form":
            store.finish(item_id, "manual", "no encontré un formulario que llenar en ese link: a mano")
        elif result.confirmed:
            outbox(cfg, item, f"General form by Relevo at {url}")
            store.finish(item_id, "sent", result.confirmation[:200])
        else:
            store.finish(item_id, "failed", "no se envió (captcha/Submit no completado a tiempo)")
    except Exception as e:
        store.finish(item_id, "manual", f"no se pudo llenar en vivo ({str(e)[:140]}); a mano")


def loop(cfg: Config, store: Store, pem: str) -> None:
    kit = prepare.Kit(cfg.kit)
    while True:
        try:
            tick(cfg, store, kit, pem)
        except Exception:
            log.exception("tick failed")
        time.sleep(120)


def one(cfg: Config, store: Store, job_id: int) -> None:
    """Prepares one job on request, also from the backlog and in quiet hours: a person asked for it."""
    job = next((j for j in candidates(cfg) + backlog(cfg) if j["id"] == job_id), None)
    if job is None:
        raise SystemExit(f"job {job_id} is not among the candidates")
    if store.known(job_id):
        raise SystemExit(f"job {job_id} is already in Relevo")
    job["page_text"] = page_text(job.get("url", ""))
    way, status, panel, reason = prepare.prepare(job, prepare.Kit(cfg.kit), cfg.data)
    item = store.add(job_id, way, status, panel, reason)
    print(f"item {item}: {way}, {status}. {reason}")
    notify(cfg, store, push.keys(cfg.data)[0], job, status, item, panel)


if __name__ == "__main__":
    settings = Config()
    one(settings, Store(f"{settings.data}/relevo.db"), int(sys.argv[1]))
