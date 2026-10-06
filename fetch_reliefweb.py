#!/usr/bin/env python3
"""Pull open ReliefWeb job postings for several term sets and write JSON + Markdown."""
import datetime as dt
import http.client
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

APPNAME = "compas-monitorlistings-h4fx"
BASE = "https://api.reliefweb.int/v2/jobs"
TERM_SETS = {
    "migration": "migration",
    "migration-data": "migration data OR migration statistics",
    "displacement": "displacement OR refugee OR asylum",
    "data-me": "data analyst OR statistician OR M&E",
}
FIELDS = [
    "title", "source.name", "url", "date.created", "date.closing",
    "country.name", "city.name", "type.name", "career_categories.name",
    "experience.name", "body",
]
LIMIT = 20
RETRIES = 5
OUT = pathlib.Path("data")
BODY_SNIPPET = 600


def fetch(term: str, offset: int, today: str) -> dict:
    payload = {
        "query": {"value": term, "fields": ["title", "body"], "operator": "OR"},
        "filter": {"field": "date.closing", "value": {"from": f"{today}T00:00:00+00:00"}},
        "fields": {"include": FIELDS},
        "sort": ["date.created:desc"],
        "limit": LIMIT,
        "offset": offset,
    }
    url = f"{BASE}?{urllib.parse.urlencode({'appname': APPNAME})}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"User-Agent": f"{APPNAME} (github actions mirror)",
                 "Content-Type": "application/json", "Accept-Encoding": "identity"})
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                sys.exit(f"ReliefWeb API HTTP {e.code} for term '{term}': {e.read()[:300]!r}")
            last = f"HTTP {e.code}"
        except (http.client.IncompleteRead, urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = repr(e)
        wait = 5 * (attempt + 1)
        print(f"  retry {attempt + 1}/{RETRIES} for '{term}' offset {offset} after {last}; sleeping {wait}s")
        time.sleep(wait)
    sys.exit(f"ReliefWeb API gave up on term '{term}' offset {offset}: {last}")


def trim(rec: dict) -> dict:
    f = rec.get("fields", {})
    return {
        "id": rec.get("id"),
        "title": f.get("title"),
        "source": [s.get("name") for s in f.get("source", [])],
        "url": f.get("url"),
        "created": (f.get("date") or {}).get("created"),
        "closing": (f.get("date") or {}).get("closing"),
        "country": [c.get("name") for c in f.get("country", [])],
        "city": [c.get("name") for c in f.get("city", [])],
        "type": [t.get("name") for t in f.get("type", [])],
        "career_categories": [c.get("name") for c in f.get("career_categories", [])],
        "experience": [e.get("name") for e in f.get("experience", [])],
        "body": f.get("body"),
    }


def main() -> None:
    today = dt.date.today().isoformat()
    OUT.mkdir(exist_ok=True)
    combined = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "appname": APPNAME, "closing_on_or_after": today, "term_sets": {}}
    seen: dict[str, dict] = {}
    for key, term in TERM_SETS.items():
        records, offset, total = [], 0, None
        while True:
            page = fetch(term, offset, today)
            total = page.get("totalCount", 0)
            data = page.get("data", [])
            records.extend(trim(r) for r in data)
            offset += len(data)
            if not data or offset >= total:
                break
        combined["term_sets"][key] = {"query": term, "totalCount": total, "jobs": records}
        for r in records:
            seen.setdefault(str(r["id"]), {**r, "matched_terms": []})["matched_terms"].append(key)
        (OUT / f"jobs-{key}.json").write_text(json.dumps(records, indent=1, ensure_ascii=False))
        print(f"{key}: totalCount={total}, fetched={len(records)}")

    combined["unique_jobs"] = len(seen)
    (OUT / "latest.json").write_text(json.dumps(combined, indent=1, ensure_ascii=False))

    lines = [f"# ReliefWeb jobs mirror — fetched {combined['fetched_at']}",
             f"Open postings only (date.closing >= {today}). Unique jobs: {len(seen)}.", ""]
    for key, block in combined["term_sets"].items():
        lines.append(f"## Term set `{key}` — \"{block['query']}\" — totalCount {block['totalCount']}")
        lines.append("")
        lines.append("| Closing | Created | Title | Organisation | Location | Experience | URL |")
        lines.append("|---|---|---|---|---|---|---|")
        for j in sorted(block["jobs"], key=lambda x: x["closing"] or ""):
            loc = ", ".join(j["city"] + j["country"]) or "—"
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                (j["closing"] or "")[:10], (j["created"] or "")[:10],
                (j["title"] or "").replace("|", "/"), "; ".join(j["source"]),
                loc, "; ".join(j["experience"]) or "—", j["url"]))
        lines.append("")
    lines.append("## Body snippets (unique jobs, by closing date)")
    lines.append("")
    for j in sorted(seen.values(), key=lambda x: x["closing"] or ""):
        body = " ".join((j["body"] or "").split())[:BODY_SNIPPET]
        lines.append(f"### {j['title']} — {'; '.join(j['source'])} — closes {(j['closing'] or '')[:10]}")
        lines.append(f"{j['url']}  \nTerms: {', '.join(j['matched_terms'])}  \n{body}…")
        lines.append("")
    (OUT / "latest.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
