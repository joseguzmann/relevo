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

from relevo import ashby, filler, mailer, prepare, push
from relevo.store import Store

log = logging.getLogger("relevo.worker")
TZ = ZoneInfo("America/Guayaquil")


class Config:
    def __init__(self):
        self.data = os.environ.get("RELEVO_DATA", "/data")
        self.kit = os.environ.get("RELEVO_KIT", "/kit")
        self.site = os.environ.get("RELEVO_SITE", "https://relevo.joseguzman.dev")
        self.sender = os.environ.get("RELEVO_FROM", "")
        self.daily_cap = int(os.environ.get("RELEVO_DAILY_CAP", "5"))
        self.quiet = (22, 8)
        self.token = f"{self.data}/gmail-send.json"


def quiet(cfg: Config, hour: int | None = None) -> bool:
    hour = datetime.now(TZ).hour if hour is None else hour
    start, end = cfg.quiet
    return hour >= start or hour < end


def candidates(cfg: Config) -> list[dict]:
    path = Path(cfg.data) / "inbox" / "candidates.json"
    return json.loads(path.read_text()) if path.exists() else []


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
        if store.prepared_today() >= cfg.daily_cap:
            return
        try:
            job["page_text"] = page_text(job.get("url", ""))
            way, status, panel, reason = prepare.prepare(job, kit)
        except Exception as e:  # one bad posting must not stop the others
            log.warning("job %s: %s", job["id"], str(e)[:200])
            store.add(job["id"], "manual", "manual", {"job": job, "target": job.get("url", "")}, str(e)[:200])
            continue
        item = store.add(job["id"], way, status, panel, reason)
        if item and status == "ready":
            push.send(store, pem, cfg.site, job.get("company") or "Relevo", job.get("title", "")[:90], item)


def outbox(cfg: Config, item: dict, material: str) -> None:
    if item["job_id"] < 0:  # a test item is not an application
        return
    folder = Path(cfg.data) / "outbox"
    folder.mkdir(exist_ok=True)
    panel = item["data"]
    record = {"job_id": item["job_id"], "family": panel.get("family"), "route": item["route"], "material": material,
              "cv": f"v3 {panel.get('family')} (Relevo)", "panel": panel}
    (folder / f"{item['id']}.json").write_text(json.dumps(record, ensure_ascii=False))


def form_material(panel: dict) -> str:
    return "Ashby form by Relevo: " + "; ".join(f"{f['label'][:40]}={f['value']}" for f in panel["fields"] if f["kind"] != "file")


def after_shot(cfg: Config, item_id: int) -> Path:
    return Path(cfg.data) / f"shot-{item_id}-after.png"


def resolve(cfg: Config, store: Store, item: dict, sent: bool) -> None:
    """The person checked an `unclear` send. Only their word moves it, in either direction."""
    if sent:
        outbox(cfg, item, form_material(item["data"]) + " (confirmed by hand)")
        store.finish(item["id"], "sent", "confirmed by the person")
    else:
        store.finish(item["id"], "failed", "the person says it did not arrive")


def send(cfg: Config, store: Store, item_id: int) -> None:
    """Runs after claim(). Any doubt ends in `unclear`, which is never retried by code."""
    item = store.get(item_id)
    panel = item["data"]
    try:
        if item["route"] == "email":
            mail = panel["email"]
            msg = mailer.build(cfg.sender, mail["to"], mail["subject"], mail["body"], mail["cv"])
            sent_id = mailer.send(cfg.token, msg)
            outbox(cfg, item, f"Email to {mail['to']}, Gmail id {sent_id}: {mail['subject']}")
            store.finish(item_id, "sent", "email sent")
            return
        form = ashby.fetch_form(panel["target"])
        known = {f["id"] for f in panel["fields"]}
        if {f.id for f in form.fields} != known:
            store.finish(item_id, "failed", "the form changed; nothing was sent")
            return
        answers = {f["id"]: f["value"] for f in panel["fields"]}
        shot = str(Path(cfg.data) / f"shot-{item_id}.png")
        result = filler.fill(form, answers, shot, submit=True, accepted=panel.get("accepted_unknown", []),
                             after_shot=str(after_shot(cfg, item_id)))
        if result.failed or (result.unknown and not result.submitted):
            store.finish(item_id, "failed", f"not sent: failed {result.failed}, unknown {result.unknown}")
        elif result.confirmed:
            outbox(cfg, item, form_material(panel))
            store.finish(item_id, "sent", result.confirmation[:200])
        else:
            store.finish(item_id, "unclear", f"submitted, no confirmation seen. ATS: {result.server or 'no answer seen'}. "
                                             f"Page: {result.confirmation}")
    except Exception as e:
        store.finish(item_id, "unclear", f"stopped mid-send, check before retrying: {str(e)[:200]}")


def loop(cfg: Config, store: Store, pem: str) -> None:
    kit = prepare.Kit(cfg.kit)
    while True:
        try:
            tick(cfg, store, kit, pem)
        except Exception:
            log.exception("tick failed")
        time.sleep(120)


def one(cfg: Config, store: Store, job_id: int) -> None:
    """Prepares one job on request. It skips the daily cap and quiet hours: a person asked for it."""
    job = next((j for j in candidates(cfg) if j["id"] == job_id), None)
    if job is None:
        raise SystemExit(f"job {job_id} is not among the candidates")
    if store.known(job_id):
        raise SystemExit(f"job {job_id} is already in Relevo")
    job["page_text"] = page_text(job.get("url", ""))
    way, status, panel, reason = prepare.prepare(job, prepare.Kit(cfg.kit))
    item = store.add(job_id, way, status, panel, reason)
    print(f"item {item}: {way}, {status}. {reason}")
    if item and status == "ready":
        push.send(store, push.keys(cfg.data)[0], cfg.site, job.get("company") or "Relevo", job.get("title", "")[:90], item)


if __name__ == "__main__":
    settings = Config()
    one(settings, Store(f"{settings.data}/relevo.db"), int(sys.argv[1]))
