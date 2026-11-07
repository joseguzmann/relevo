"""The API the phone talks to, and the app's static files. Every route but login needs a session."""
import hashlib
import json
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import base64

from relevo import filler, forms, push, route, worker
from relevo.store import Store

cfg = worker.Config()
Path(cfg.data).mkdir(exist_ok=True)
store = Store(f"{cfg.data}/relevo.db")
pem, public_key = push.keys(cfg.data)
WEB = Path(__file__).parent / "web"
secret_file = Path(cfg.data) / "session.key"
if not secret_file.exists():
    secret_file.write_text(secrets.token_hex(32))
    secret_file.chmod(0o600)
SECRET = secret_file.read_text().encode()
fill_key_file = Path(cfg.data) / "fill.key"  # the Safari shortcut's key: it can only read answers of open items
if not fill_key_file.exists():
    fill_key_file.write_text(secrets.token_urlsafe(24))
    fill_key_file.chmod(0o600)
DAYS = 30
fails = {"n": 0, "until": 0.0}
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


def check_passcode(given: str) -> bool:
    stored = os.environ.get("RELEVO_PASSCODE_HASH", "")
    if "$" not in stored:
        return False
    salt, want = stored.split("$", 1)
    got = hashlib.pbkdf2_hmac("sha256", given.encode(), salt.encode(), 300_000).hex()
    return hmac.compare_digest(got, want)


def sign(expires: int) -> str:
    return f"{expires}.{hmac.new(SECRET, str(expires).encode(), hashlib.sha256).hexdigest()}"


def session(request: Request) -> None:
    raw = request.cookies.get("relevo", "")
    expires = raw.split(".")[0]
    if not expires.isdigit() or int(expires) < time.time() or not hmac.compare_digest(raw, sign(int(expires))):
        raise HTTPException(401)


@app.post("/api/login")
async def login(request: Request, response: Response):
    if time.time() < fails["until"]:
        raise HTTPException(429, "wait a moment")
    body = await request.json()
    if not check_passcode(str(body.get("passcode", ""))):
        fails["n"] += 1
        if fails["n"] >= 5:
            fails.update(n=0, until=time.time() + 300)
        raise HTTPException(401)
    fails["n"] = 0
    response.set_cookie("relevo", sign(int(time.time()) + DAYS * 86400), max_age=DAYS * 86400, httponly=True,
                        secure=True, samesite="strict")
    return {"ok": True}


@app.get("/api/me", dependencies=[Depends(session)])
def me():
    return {"vapid": public_key}


@app.post("/api/subscribe", dependencies=[Depends(session)])
async def subscribe(request: Request):
    store.add_sub(await request.json())
    return {"ok": True}


@app.get("/api/items", dependencies=[Depends(session)])
def items():
    keep = ("title", "company", "match")
    return [{"id": i["id"], "route": i["route"], "status": i["status"], "reason": i["reason"], "origin": origin(i),
             "warning": i["data"].get("warning"),
             "job": {k: i["data"].get("job", {}).get(k) for k in keep}} for i in store.list()]


def origin(it: dict) -> dict:
    panel = it["data"]
    return route.origin(panel.get("job", {}), it["route"], panel.get("email", {}).get("to", ""))


def need(item_id: int) -> dict:
    item = store.get(item_id)
    if not item:
        raise HTTPException(404)
    return item


@app.get("/api/items/{item_id}", dependencies=[Depends(session)])
def item(item_id: int):
    it = need(item_id)
    return {**it, "origin": origin(it)}


@app.put("/api/items/{item_id}", dependencies=[Depends(session)])
async def edit(item_id: int, request: Request):
    it, body = need(item_id), await request.json()
    panel = it["data"]
    for f in panel.get("fields", []):
        if f["id"] in body.get("fields", {}) and f["kind"] != "file":
            f["value"] = body["fields"][f["id"]]
            f["state"] = "ready" if f["value"] not in (None, "", []) else "missing"
    if "email" in body and "email" in panel:
        panel["email"].update({k: body["email"][k] for k in ("to", "subject", "body") if k in body["email"]})
    if "accepted_unknown" in body:
        panel["accepted_unknown"] = [u for u in body["accepted_unknown"] if u in panel.get("unknown", [])]
    if not store.save_data(item_id, panel):
        raise HTTPException(409, "this one can no longer be edited")
    if it["status"] == "failed":  # `failed` means nothing went out, so it can be tried again
        store.finish(item_id, "ready")
    return store.get(item_id)


def blockers(it: dict) -> list[str]:
    panel = it["data"]
    out = [f["label"] for f in panel.get("fields", []) if f["required"] and f["value"] in (None, "", [])]
    out += [u[:80] for u in panel.get("unknown", []) if u not in panel.get("accepted_unknown", [])]
    if it["route"] == "email" and not all(panel.get("email", {}).get(k) for k in ("to", "subject", "body")):
        out.append("email incomplete")
    return out


@app.post("/api/items/{item_id}/send", dependencies=[Depends(session)])
def send(item_id: int):
    it = need(item_id)
    if it["route"] == "manual":
        raise HTTPException(400, "this one has no way out from here")
    if it["route"] in forms.HAND:
        raise HTTPException(400, "Esta se llena en Safari con el atajo y la envías tú.")
    if it["route"] == "email" and not Path(cfg.token).exists():
        raise HTTPException(503, "Gmail todavía no está conectado. No se envió nada.")
    missing = blockers(it)
    if missing:
        raise HTTPException(400, {"missing": missing})
    if not store.claim(item_id):
        raise HTTPException(409, "already sent or being sent")
    threading.Thread(target=worker.send, args=(cfg, store, item_id), daemon=True).start()
    return {"status": "sending"}


@app.get("/api/items/{item_id}/shot", dependencies=[Depends(session)])
def shot(item_id: int):
    path = worker.after_shot(cfg, need(item_id)["id"])
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@app.post("/api/items/{item_id}/code", dependencies=[Depends(session)])
async def code(item_id: int, request: Request):
    if need(item_id)["status"] != "code":
        raise HTTPException(409, "this one is not waiting for a code")
    value = "".join(ch for ch in str((await request.json()).get("code", "")) if ch.isalnum())
    if not 4 <= len(value) <= 12:
        raise HTTPException(400, "Ese código no parece válido")
    worker.code_file(cfg, item_id).write_text(value)
    store.finish(item_id, "sending", "code entered, waiting for the ATS")
    return {"ok": True}


@app.get("/api/items/{item_id}/live", dependencies=[Depends(session)])
def live(item_id: int):
    path = worker.live_file(cfg, need(item_id)["id"])
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/items/{item_id}/tap", dependencies=[Depends(session)])
async def tap(item_id: int, request: Request):
    """A tap on the live picture, as a fraction of its width and height. The worker clicks the same spot."""
    if need(item_id)["status"] != "captcha":
        raise HTTPException(409, "this one is not waiting for a captcha")
    body = await request.json()
    x, y = float(body.get("x", -1)), float(body.get("y", -1))
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise HTTPException(400, "tap outside the picture")
    with worker.taps_file(cfg, item_id).open("a") as f:
        f.write(f"{x:.4f},{y:.4f}\n")
    return {"ok": True}


@app.get("/api/fillkey", dependencies=[Depends(session)])
def fill_key():
    return {"key": fill_key_file.read_text().strip()}


def same_form(a: str, b: str) -> bool:
    return bool(a and b) and forms.find(a)[1].lower() == forms.find(b)[1].lower() != ""


@app.get("/api/fill/script")
def fill_script(url: str, k: str):
    """What the Safari shortcut runs on the page open there: the approved answers and the CV, nothing else."""
    if not hmac.compare_digest(k.encode(), fill_key_file.read_text().strip().encode()):
        raise HTTPException(403)
    open_items = [i for i in store.list() if i["route"] in forms.HAND and i["status"] in ("ready", "failed")]
    it = next((i for i in open_items if same_form(url, i["data"].get("target", ""))), None)
    if it is None and not url and len(open_items) == 1:  # the shortcut sent no address: the page itself checks it
        it = open_items[0]
    if it is None:
        return PlainTextResponse("completion('Relevo: esta página no tiene una postulación abierta en Relevo.');",
                                 media_type="text/javascript")
    missing = blockers(it)
    if missing:
        text = json.dumps("Relevo: falta en la app: " + " · ".join(missing))
        return PlainTextResponse(f"completion({text});", media_type="text/javascript")
    fields, cv = [], None
    for f in it["data"]["fields"]:
        if f["value"] in (None, "", []):
            continue
        value = f["value"]
        if f["kind"] == "file":
            cv = Path(value)
        elif f["id"] == "dateAvailable":
            value = filler.us_date(str(value))
        fields.append({"id": f["id"], "kind": f["kind"], "label": f["label"][:60], "value": value})
    data = {"page": it["data"].get("form_url") or it["data"]["target"], "fields": fields, "cv": {"name": cv.name, "b64": base64.b64encode(cv.read_bytes()).decode()} if cv else None}
    script = (WEB / f"fill-{it['route']}.js").read_text().replace("__DATA__", json.dumps(data, ensure_ascii=False))
    store.finish(it["id"], "ready", "filled in Safari, waiting for the person to submit")
    return PlainTextResponse(script, media_type="text/javascript")


@app.post("/api/items/{item_id}/done", dependencies=[Depends(session)])
def done_by_hand(item_id: int):
    """The person submitted it in Safari. Their word records it, as with an unclear send."""
    it = need(item_id)
    if it["route"] not in forms.HAND or it["status"] not in ("ready", "failed"):
        raise HTTPException(409, "nothing to record")
    worker.outbox(cfg, it, worker.form_material(it["data"], it["route"]) + " (filled by Relevo in Safari, submitted by the person)")
    store.finish(item_id, "sent", "submitted by the person in Safari")
    return store.get(item_id)


@app.get("/api/items/{item_id}/sent", dependencies=[Depends(session)])
def sent(item_id: int):
    path = worker.evidence(cfg, need(item_id)["id"])
    return json.loads(path.read_text()) if path.exists() else []


@app.post("/api/items/{item_id}/resolve", dependencies=[Depends(session)])
async def resolve(item_id: int, request: Request):
    it = need(item_id)
    if it["status"] != "unclear":
        raise HTTPException(409, "nothing to resolve")
    worker.resolve(cfg, store, it, bool((await request.json()).get("sent")))
    return store.get(item_id)


@app.post("/api/items/{item_id}/discard", dependencies=[Depends(session)])
def discard(item_id: int):
    if need(item_id)["status"] in ("ready", "failed", "manual"):
        store.finish(item_id, "dropped", "discarded by the person")
    return {"ok": True}


@app.on_event("startup")
def start():
    threading.Thread(target=worker.loop, args=(cfg, store, pem), daemon=True).start()


@app.get("/")
def index():
    # The CDN keeps .js and .css for hours whatever we send, so each version gets its own URL.
    version = hashlib.sha256((WEB / "app.js").read_bytes() + (WEB / "app.css").read_bytes()).hexdigest()[:10]
    return HTMLResponse((WEB / "index.html").read_text().replace("?v=2", f"?v={version}"))


@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")
