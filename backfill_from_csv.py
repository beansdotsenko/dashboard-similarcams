#!/usr/bin/env python3
"""
Backfill finteza_memory.json from exported CSV files.

Usage:
  python3 backfill_from_csv.py --date 2026-08-20 \
      --pages website_18924_pages_20260820_20260820.csv \
      --hours website_18924_hours_20260820_20260820.csv \
      --events website_18924_events_20260820_20260820.csv

--country is optional. --hours and --events are optional too but give richer data.
"""

import csv
import json
import os
import sys
import argparse
from datetime import datetime

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finteza_memory.json")

EXCLUDE_PAGES = {"/test", "/admin", "/cdn-cgi", "/mi-deterrence"}


def parse_model_url(path: str):
    parts = [p for p in str(path).split("/") if p]
    if len(parts) >= 4 and parts[0] == "cams":
        return {"gender": parts[1], "platform": parts[2], "model": parts[3],
                "suffix": parts[4] if len(parts) > 4 else None,
                "base": f"/cams/{parts[1]}/{parts[2]}/{parts[3]}",
                "is_shorts": False}
    if len(parts) >= 5 and parts[0] == "ru" and parts[1] == "cams":
        return {"gender": parts[2], "platform": parts[3], "model": parts[4],
                "suffix": parts[5] if len(parts) > 5 else None,
                "base": f"/cams/{parts[2]}/{parts[3]}/{parts[4]}",
                "is_shorts": False}
    if len(parts) >= 3 and parts[0] == "shorts":
        return {"gender": parts[1], "platform": "shorts", "model": parts[2],
                "suffix": "shorts", "base": f"/shorts/{parts[1]}/{parts[2]}",
                "is_shorts": True}
    if len(parts) >= 4 and parts[0] == "ru" and parts[1] == "shorts":
        return {"gender": parts[2], "platform": "shorts", "model": parts[3],
                "suffix": "shorts", "base": f"/shorts/{parts[2]}/{parts[3]}",
                "is_shorts": True}
    return None


def read_csv(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append({k.strip('"'): v.strip('"') for k, v in row.items()})
    return rows


def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"entries": [], "pinned": []}


def save_memory(mem):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)
    print(f"✓ Memory  → {MEMORY_FILE}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    parser.add_argument("--pages", required=True)
    parser.add_argument("--hours", default=None)
    parser.add_argument("--events", default=None)
    parser.add_argument("--country", default=None)
    args = parser.parse_args()

    date_str = args.date

    # ── Parse pages ──────────────────────────────────────────────────────
    pages_rows = read_csv(args.pages)
    model_agg = {}
    shorts_agg = {}
    total_visitors = 0
    total_new = 0
    total_sessions = 0
    total_pageviews = 0

    for row in pages_rows:
        path = row.get("Page", "")
        vis = int(row.get("Visitors", 0) or 0)
        new_vis = int(row.get("New visitors", 0) or 0)
        sessions = int(row.get("Sessions", 0) or 0)
        pageviews = int(row.get("Pageviews", 0) or 0)

        # skip excluded
        if any(path.startswith(p) for p in EXCLUDE_PAGES):
            continue

        # homepage counts toward totals
        if path == "/":
            total_visitors += vis
            total_new += new_vis
            total_sessions += sessions
            total_pageviews += pageviews
            continue

        pm = parse_model_url(path)
        if pm is None:
            continue

        if pm["is_shorts"]:
            key = (pm["gender"], pm["model"])
            if key not in shorts_agg:
                shorts_agg[key] = {"gender": pm["gender"], "model": pm["model"], "vis": 0}
            shorts_agg[key]["vis"] += vis
        else:
            key = (pm["gender"], pm["platform"], pm["model"])
            if key not in model_agg:
                model_agg[key] = {"gender": pm["gender"], "platform": pm["platform"],
                                   "model": pm["model"], "base": pm["base"],
                                   "vis": 0, "profile_vis": 0, "stats_vis": 0}
            suffix = pm["suffix"]
            if suffix is None:
                model_agg[key]["vis"] += vis
                total_visitors += vis
                total_new += new_vis
                total_sessions += sessions
                total_pageviews += pageviews
            elif suffix == "profile":
                model_agg[key]["profile_vis"] += vis
            elif suffix in ("stats-and-schedule", "stats"):
                model_agg[key]["stats_vis"] += vis

    top20_models = sorted(
        (m for m in model_agg.values() if m["vis"] > 0),
        key=lambda x: -(x["vis"] + x["profile_vis"] + x["stats_vis"])
    )[:20]

    top20_shorts = sorted(shorts_agg.values(), key=lambda x: -x["vis"])[:20]

    # ── Parse hours ──────────────────────────────────────────────────────
    visitors_h = 0
    new_h = 0
    sessions_h = 0
    pageviews_h = 0
    bounce_sum = 0
    dur_sum = 0
    hour_count = 0

    if args.hours:
        for row in read_csv(args.hours):
            visitors_h += int(row.get("Visitors", 0) or 0)
            new_h += int(row.get("New visitors", 0) or 0)
            sessions_h += int(row.get("Sessions", 0) or 0)
            pageviews_h += int(row.get("Pageviews", 0) or 0)
            try:
                bounce_sum += float(row.get("Bounce rate", 0) or 0)
                dur_sum += float(row.get("Session duration", 0) or 0)
                hour_count += 1
            except Exception:
                pass

    visitors = visitors_h if visitors_h else total_visitors
    new_users = new_h if new_h else total_new
    sessions = sessions_h if sessions_h else total_sessions
    pageviews = pageviews_h if pageviews_h else total_pageviews
    ret_users = visitors - new_users
    new_pct = round(new_users / visitors * 100, 1) if visitors else 0
    pps = round(pageviews / sessions, 2) if sessions else 0

    # ── Parse events ─────────────────────────────────────────────────────
    cr_events = 0
    cr_unique = 0
    reco_shown = 0
    reco_dism = 0
    reco_all = 0

    if args.events:
        for row in read_csv(args.events):
            event = row.get("Tracked event", "")
            ev_vis = int(row.get("Visitors", 0) or 0)
            ev_cnt = int(row.get("Events", 0) or 0)
            if event == "ClickRef":
                cr_unique += ev_vis
                cr_events += ev_cnt
            elif event in ("RecoDrawerOfflineShown", "RecoDrawerOnlineShown"):
                reco_shown += ev_vis
            elif "Dismissed" in event or "dismissed" in event:
                reco_dism += ev_vis
            elif event == "RecoDrawerOnlineSuppressed":
                reco_all += ev_vis

    ctr = round(cr_unique / visitors * 100, 1) if visitors else 0

    # ── Build entry ───────────────────────────────────────────────────────
    entry = {
        "date": date_str,
        "metrics": {
            "visitors": visitors,
            "new_users": new_users,
            "ret_users": ret_users,
            "new_pct": new_pct,
            "sessions": sessions,
            "pps": pps,
            "ctr": ctr,
            "cr_events": cr_events,
            "cr_unique": cr_unique,
            "reco_shown": reco_shown,
            "reco_dism": reco_dism,
            "reco_all": reco_all,
            "vis_7d": 0,
            "vis_30d": 0,
            "cr_30d": 0,
            "ctr_30d_avg": 0,
            "peak_vis": visitors,
        },
        "findings": ["восстановлено из CSV"],
        "details": [],
        "hypos": [],
        "note": "backfilled"
    }

    model_rankings_entry = [
        {"gender": m["gender"], "platform": m["platform"], "model": m["model"],
         "vis": m["vis"], "profile_vis": m["profile_vis"], "stats_vis": m["stats_vis"]}
        for m in top20_models
    ]

    shorts_rankings_entry = [
        {"gender": m["gender"], "model": m["model"], "vis": m["vis"]}
        for m in top20_shorts
    ]

    # ── Update memory ─────────────────────────────────────────────────────
    mem = load_memory()

    # entries: replace if exists, else append
    existing_dates = [e["date"] for e in mem.get("entries", [])]
    if date_str in existing_dates:
        mem["entries"] = [e if e["date"] != date_str else entry for e in mem["entries"]]
        print(f"  → updated existing entry {date_str}")
    else:
        mem.setdefault("entries", []).append(entry)
        mem["entries"].sort(key=lambda x: x["date"])
        print(f"  → added entry {date_str}")

    mem.setdefault("model_rankings", {})[date_str] = model_rankings_entry
    mem.setdefault("shorts_rankings", {})[date_str] = shorts_rankings_entry

    save_memory(mem)
    print(f"  Visitors: {visitors}, CTR: {ctr}%, top model: {top20_models[0]['model'] if top20_models else '—'}")


if __name__ == "__main__":
    main()
