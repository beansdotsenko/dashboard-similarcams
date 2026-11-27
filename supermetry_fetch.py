"""
Fetch RecoDrawer stats from the Supermetry public API and save them to
public/supermetry_reco.json for dashboard.html.

Env vars (set as GitHub Actions secrets):
  SUPERMETRY_URL      e.g. https://analytics.example.com   (no trailing slash)
  SUPERMETRY_KEY      API key with stats:read
  SUPERMETRY_SITE_ID  site id of similarcams.com
  PERIOD_DAYS         optional, default 7
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = os.environ["SUPERMETRY_URL"].rstrip("/") + "/api/public/v1"
KEY = os.environ["SUPERMETRY_KEY"]
SITE = os.environ["SUPERMETRY_SITE_ID"]
DAYS = int(os.environ.get("PERIOD_DAYS", "7"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "supermetry_reco.json")

METRIC_KEYS = {"events", "visits", "visitors"}


def breakdown(dimension, filters, date_from, date_to, metrics=("events", "visits")):
    params = [
        ("site_id", SITE),
        ("period", "custom"),
        ("date_from", date_from.isoformat()),
        ("date_to", date_to.isoformat()),
        ("dimension", dimension),
        ("metrics", ",".join(metrics)),
        ("limit", "100"),
    ] + [("filter", f) for f in filters]
    url = f"{BASE}/stats/breakdown?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Supermetry {e.code} for {url}\n{e.read().decode(errors='replace')}")
    return to_map(data)


def to_map(data):
    """Turn a breakdown response into {value: {"events": n, "visits": n}}.
    The docs don't fix the rows key, so take the first list of objects."""
    rows = None
    if isinstance(data, list):
        rows = data
    else:
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    result = {}
    for row in rows or []:
        label_key = next((k for k in row if k not in METRIC_KEYS), None)
        if label_key is None:
            continue
        result[str(row[label_key])] = {m: row.get(m, 0) or 0 for m in METRIC_KEYS if m in row}
    return result


def snapshot(date_from, date_to):
    reco = "event:contains:reco_drawer"
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "events_all": breakdown("event", [reco], date_from, date_to),
        "events_online": breakdown("event", [reco, "prop:category:is:online"], date_from, date_to),
        "events_offline": breakdown("event", [reco, "prop:category:is:offline"], date_from, date_to),
        "brands": breakdown("prop:provider_slug", ["event:is:reco_drawer_provider_clicked"], date_from, date_to),
        "suppress_reasons": breakdown("prop:reason", ["event:is:reco_drawer_suppressed"], date_from, date_to),
    }


def main():
    today = date.today()
    cur_from = today - timedelta(days=DAYS - 1)
    prev_to = cur_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=DAYS - 1)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period_days": DAYS,
        "current": snapshot(cur_from, today),
        "previous": snapshot(prev_from, prev_to),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    shown = out["current"]["events_all"].get("reco_drawer_shown", {}).get("events", 0)
    print(f"Saved {OUT}: reco_drawer_shown={shown} for {cur_from}..{today}")


if __name__ == "__main__":
    main()
