"""The loop: read candidates, prepare, notify. And the send, which only an approved item reaches."""
import json
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
    folder = Path(cfg.data) / "outbox"
    folder.mkdir(exist_ok=True)
    panel = item["data"]
    record = {"job_id": item["job_id"], "family": panel.get("family"), "route": item["route"], "material": material,
              "cv": f"v3 {panel.get('family')} (Relevo)", "panel": panel}
    (folder / f"{item['id']}.json").write_text(json.dumps(record, ensure_ascii=False))


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
        result = filler.fill(form, answers, shot, submit=True, accepted=panel.get("accepted_unknown", []))
        if result.failed or (result.unknown and not result.submitted):
            store.finish(item_id, "failed", f"not sent: failed {result.failed}, unknown {result.unknown}")
        elif filler.looks_sent(result.confirmation):
            outbox(cfg, item, "Ashby form by Relevo: " + "; ".join(f"{f['label'][:40]}={f['value']}" for f in panel["fields"]
                                                                  if f["kind"] != "file"))
            store.finish(item_id, "sent", result.confirmation[:200])
        else:
            store.finish(item_id, "unclear", "submitted, no confirmation seen: " + result.confirmation[:200])
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
