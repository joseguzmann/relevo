#!/usr/bin/env python3
"""Runs on the Gateway host every few minutes: the only link between Vigía and Relevo, so Relevo stays isolated.
Vigía -> data/inbox/candidates.json. data/outbox/*.json -> Vigía applications."""
import json
import os
import subprocess
from pathlib import Path

BASE = Path("/srv/apps/relevo")
MIN_MATCH = 8
WINDOW_HOURS = int(os.environ.get("RELEVO_WINDOW_HOURS", "36"))
EXPORT = r'''
import json, sqlite3, sys
args = json.loads(sys.argv[1])
c = sqlite3.connect("/data/vigia.db"); c.row_factory = sqlite3.Row
acted = {r[0] for r in c.execute("select job_id from actions")}
acted |= {r[0] for r in c.execute("select job_id from applications where job_id is not null")}
rows = c.execute("""select j.id,j.title,j.company,j.url,j.description,j.posted_at,j.seen_in,j.flags,j.location,r.match,r.why,r.years,r.blockers
  from jobs j join job_readings r on r.job_id=j.id
  where r.match>=? and r.eligible='yes' and j.verdict in ('fit','review') and j.first_seen>=datetime('now',?)
  order by r.match desc, j.first_seen desc""", (args["min_match"], "-%d hours" % args["hours"])).fetchall()
out = []
for r in rows:
    if r["id"] in acted or r["id"] in args["shortlist"] or (r["years"] or 0) > 4:
        continue
    if "bairesdev" in (r["company"] or "").lower():
        continue
    flags = json.loads(r["flags"] or "{}")
    seen = json.loads(r["seen_in"] or "[]")
    urls = [s.get("url", "") for s in seen]
    hist = [dict(h) for h in c.execute("select number,title,status from applications where lower(company)=lower(?)", (r["company"],))]
    out.append({"id": r["id"], "title": r["title"], "company": r["company"], "url": r["url"], "urls": urls,
                "sources": sorted({s.get("source", "") for s in seen} - {""}), "location": r["location"],
                "description": r["description"], "posted_at": r["posted_at"], "match": r["match"], "why": r["why"],
                "blockers": r["blockers"], "category": flags.get("category"), "stack": flags.get("stack"), "history": hist})
print(json.dumps(out, ensure_ascii=False))
'''
RECORD = r'''
import json, sys
from vigia import applications as apps
r = json.load(sys.stdin)
a = apps.add(job_id=r["job_id"], family=r["family"], channel="email" if r["route"] == "email" else "company",
             cv=r["cv"], material=r["material"], notes="Sent by Relevo after approval in the app.")
print(a["number"])
'''


def vigia(code: str, *args: str, stdin: str | None = None) -> str:
    p = subprocess.run(["docker", "exec", "-i", "vigia", "python3", "-c", code, *args], input=stdin,
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr[-300:])
    return p.stdout


def shortlist() -> list[int]:
    path = BASE / "kit" / "shortlist.txt"
    return [int(x) for x in path.read_text().split()] if path.exists() else []


def main() -> None:
    (BASE / "data/inbox").mkdir(parents=True, exist_ok=True)
    (BASE / "data/outbox/done").mkdir(parents=True, exist_ok=True)
    out = vigia(EXPORT, json.dumps({"min_match": MIN_MATCH, "hours": WINDOW_HOURS, "shortlist": shortlist()}))
    tmp = BASE / "data/inbox/candidates.tmp"
    tmp.write_text(out)
    tmp.replace(BASE / "data/inbox/candidates.json")
    for f in sorted((BASE / "data/outbox").glob("*.json")):
        number = vigia(RECORD, stdin=f.read_text()).strip()
        f.rename(BASE / "data/outbox/done" / f"{number}-{f.name}")


if __name__ == "__main__":
    main()
