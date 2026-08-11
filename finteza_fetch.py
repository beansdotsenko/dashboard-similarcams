#!/usr/bin/env python3
"""
Finteza data fetcher — similarcams.com
Uses internal panel.finteza.com API (same as web panel).
Cookies last until 2027, so no daily login needed.

Usage:
  python3 finteza_fetch.py                  # вчера (обычный режим)
  python3 finteza_fetch.py --date 2026-08-05  # конкретная дата (бэкфил)
Output: finteza_data.json + finteza_memory.json
"""

import urllib.request
import urllib.parse
import json
import gzip
import os
import sys
import ssl
import time
from datetime import datetime, timezone, timedelta

# macOS SSL fix
ssl._create_default_https_context = ssl._create_unverified_context

# ── CONFIG ─────────────────────────────────────────────────────────────
WEBSITE_ID = "18924"
BASE_URL   = "https://panel.finteza.com/api/statistics"
OUTPUT_DIR   = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE  = os.path.join(OUTPUT_DIR, "finteza_memory.json")

# Session cookies — локально хардкод, в CI берётся из env FINTEZA_COOKIE
_COOKIE_ENV = os.environ.get("FINTEZA_COOKIE", "")
COOKIES = {
    "_fz_uniq": "6387735736774797247",
    "_fz_fvdt": "1781282239",
    "LLT":      "qxvswoptpylzozzktoyjbzlzdmkulzx",
    "lang":     "ru",
    "_fz_ssn":  "1785858980431302718",
}

# ── ФИЛЬТРЫ — настрой под себя ──────────────────────────────────────────
# Реферреры/домены которые исключать из анализа страниц и графиков
EXCLUDE_REFERRERS = {
    "similarcams.com",      # self-referral
    "(direct)",
    "t.co",                 # Twitter preview bots
}

# Паттерны страниц которые исключать (startswith)
EXCLUDE_PAGES = {
    "/test",
    "/admin",
    "/cdn-cgi",
}

# Дни с трафиком > среднее × N считаются аномалией и выбрасываются из 30d графика
ANOMALY_MULTIPLIER = 2.5
# ───────────────────────────────────────────────────────────────────────


def fz_ts(unix_ts: int) -> int:
    """Unix timestamp (seconds) → Finteza FILETIME (100-ns intervals since 1601-01-01)"""
    return (int(unix_ts) + 11_644_473_600) * 10_000_000


def _cookie_str() -> str:
    if _COOKIE_ENV:
        return _COOKIE_ENV
    return "; ".join(f"{k}={v}" for k, v in COOKIES.items())


# ── Память ──────────────────────────────────────────────────────────────

def load_memory() -> dict:
    """Load or create the memory file."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"entries": [], "pinned": []}


def save_memory(mem: dict, date_str: str, metrics: dict,
                findings: list, hypos: list) -> None:
    """Upsert today's entry in memory and persist."""
    # Remove existing entry for same date (re-run)
    mem["entries"] = [e for e in mem["entries"] if e.get("date") != date_str]
    mem["entries"].append({
        "date":     date_str,
        "metrics":  metrics,
        "findings": [f[1] for f in findings],   # just the titles
        "details":  [f[2][:120] for f in findings],
        "hypos":    [h[1] for h in hypos],
        "note":     "",   # user fills manually in JSON
    })
    # Keep last 90 entries
    mem["entries"] = sorted(mem["entries"], key=lambda e: e["date"])[-90:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)
    print(f"✓ Memory  → {MEMORY_FILE}")


def fetch(endpoint: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{endpoint}?{qs}"
    print(f"  → {endpoint}  {dict(list(params.items())[:4])}...")

    req = urllib.request.Request(url, headers={
        "Accept":           "*/*",
        "Accept-Encoding":  "gzip, deflate",
        "Accept-Language":  "ru,en;q=0.9",
        "Cookie":           _cookie_str(),
        "Referer":          f"https://panel.finteza.com/website/pages?ids={WEBSITE_ID}",
        "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    })

    for attempt in range(1, 4):   # до 3 попыток
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                print(f"     {r.status} {r.headers.get('Content-Type','?')} — {len(raw)} bytes")
                if not raw:
                    return {}
                result = json.loads(raw)
                print(f"     preview: {str(result)[:120]}")
                return result
        except urllib.error.HTTPError as e:
            body = e.read()
            print(f"     HTTP {e.code}: {body[:200]}")
            return {"error": e.code}
        except Exception as e:
            print(f"     ⚠ попытка {attempt}/3: {type(e).__name__}: {e}")
            if attempt < 3:
                time.sleep(2 * attempt)   # 2s, затем 4s
            else:
                print("     ✗ все попытки исчерпаны, возвращаем {}")
                return {}


def main():
    # ── CLI: --date YYYY-MM-DD для бэкфила ───────────────────────────────
    import argparse
    parser = argparse.ArgumentParser(description="Finteza daily report")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="Дата для анализа (по умолчанию: вчера)")
    args = parser.parse_args()

    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Ошибка: неверный формат даты '{args.date}'. Используйте YYYY-MM-DD.")
            sys.exit(1)
        today = target + timedelta(days=1)   # «вчера» станет target
        print(f"[БЭКФИЛ] Анализируем дату: {args.date}")
    else:
        now   = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Time windows
    d_from30 = fz_ts(int((today - timedelta(days=30)).timestamp()))
    d_from7  = fz_ts(int((today - timedelta(days=7)).timestamp()))
    d_from_yest = fz_ts(int((today - timedelta(days=1)).timestamp()))
    d_from0  = fz_ts(int(today.timestamp()))
    d_to     = fz_ts(int((today + timedelta(days=1)).timestamp()))

    print(f"Period (30d): {(today-timedelta(days=30)).date()} → {today.date()}")
    print(f"Today only:   {today.date()}\n")

    results = {}

    base = {"type0": "website", "ids0": WEBSITE_ID, "ac": "iia"}
    metrics_vis = "total_users,total_newusers,webvisits,webvisits_sessions,webvisits_pages"

    # ── 1. Today — overview ─────────────────────────────────────────────
    print("1. Today overview...")
    results["today"] = fetch("count", {
        **base,
        "metrics":     metrics_vis,
        "order":       "total_users",
        "prev_period": "1",
        "date_from":   d_from0,
        "date_to":     d_to,
    })

    # ── 2. Daily breakdown (last 30 days) ───────────────────────────────
    print("\n2. Daily (last 30 days)...")
    results["daily_30d"] = fetch("count", {
        **base,
        "metrics":  metrics_vis,
        "group":    "day",
        "order":    "-day",
        "date_from": d_from30,
        "date_to":  d_to,
    })

    # ── 3. Top referrers today ──────────────────────────────────────────
    print("\n3. Referrers today...")
    results["referrers_today"] = fetch("count", {
        **base,
        "metrics":   metrics_vis,
        "group":     "referrer_path,referrer_domain",
        "limit":     "21",
        "order":     "total_users",
        "prev_period": "1",
        "get_other": "1",
        "date_from": d_from0,
        "date_to":   d_to,
    })

    # ── 3b. Top referrers last 7 days ───────────────────────────────────
    print("\n3b. Referrers last 7 days...")
    results["referrers_7d"] = fetch("count", {
        **base,
        "metrics":   metrics_vis,
        "group":     "referrer_path,referrer_domain",
        "limit":     "21",
        "order":     "total_users",
        "prev_period": "1",
        "get_other": "1",
        "date_from": d_from7,
        "date_to":   d_to,
    })

    # ── 3c. Top pages yesterday (with prev_period for delta) ────────────
    print("\n3c. Top pages yesterday...")
    results["pages_yesterday"] = fetch("count", {
        **base,
        "metrics":     metrics_vis,
        "group":       "referrer_path,referrer_domain",
        "limit":       "22",
        "order":       "total_users",
        "prev_period": "1",
        "get_other":   "1",
        "date_from":   d_from_yest,
        "date_to":     d_from0,
    })

    # ── 3d. Hourly today ────────────────────────────────────────────────
    print("\n3c. Hourly today...")
    results["hourly_today"] = fetch("count", {
        **base,
        "metrics":  metrics_vis,
        "group":    "hour",
        "order":    "-hour",
        "date_from": d_from0,
        "date_to":  d_to,
    })

    # ── 3e. Hourly yesterday (для пика часа) ────────────────────────────
    print("\n3e. Hourly yesterday...")
    results["hourly_yesterday"] = fetch("count", {
        **base,
        "metrics":   "total_users",
        "group":     "hour",
        "order":     "-hour",
        "date_from": d_from_yest,
        "date_to":   d_from0,
    })

    # ── 3f. Traffic sources yesterday (attribution) ──────────────────────
    print("\n3f. Traffic sources yesterday...")
    results["sources_yesterday"] = fetch("count", {
        **base,
        "metrics":  "total_users",
        "group":    "attribution_source_type",
        "start":    "0",
        "limit":    "4",
        "get_other": "1",
        "order":    "total_users",
        "date_from": d_from_yest,
        "date_to":   d_from0,
    })

    # ── 4. Events today (by type) ───────────────────────────────────────
    print("\n4. Events today...")
    base_ev = {**base, "type1": "tracking_event", "exclude1": "Visit,empty_value", "ac": "iia"}
    results["events_today"] = fetch("count", {
        **base_ev,
        "metrics":     "events_users,events",
        "order":       "events_users",
        "prev_period": "1",
        "date_from":   d_from0,
        "date_to":     d_to,
    })

    # ── 4b. Events today breakdown by unit (ClickRef SC/CB/BC etc.) ─────
    print("\n4b. Events × unit today...")
    results["events_unit_today"] = fetch("count", {
        **base_ev,
        "metrics":     "events_users",
        "group":       "tracking_event,unit",
        "limit":       "21",
        "order":       "events_users",
        "prev_period": "1",
        "date_from":   d_from0,
        "date_to":     d_to,
    })

    # ── 4c. Events yesterday (explicit range, no period ambiguity) ──────
    print("\n4c. Events × unit yesterday...")
    results["events_unit_yesterday"] = fetch("count", {
        **base_ev,
        "metrics":  "events,events_users",   # events=общий счёт, events_users=уники
        "group":    "tracking_event,unit",
        "limit":    "200",
        "order":    "events",
        "date_from": d_from_yest,
        "date_to":   d_from0,
    })


    # ── 5. Events last 30 days ──────────────────────────────────────────
    print("\n5. Events last 30 days...")
    results["events_30d"] = fetch("count", {
        **base_ev,
        "metrics":     "events_users,events",
        "order":       "events_users",
        "prev_period": "1",
        "date_from":   d_from30,
        "date_to":     d_to,
    })

    # ── 5b. Events × unit last 30 days ──────────────────────────────────
    print("\n5b. Events × unit last 30 days...")
    results["events_unit_30d"] = fetch("count", {
        **base_ev,
        "metrics":     "events_users",
        "group":       "tracking_event,unit",
        "limit":       "50",
        "order":       "events_users",
        "prev_period": "1",
        "date_from":   d_from30,
        "date_to":     d_to,
    })

    # ── 5c. ClickRef by day (30d) — trend ───────────────────────────────
    print("\n5c. ClickRef daily trend (30d)...")
    results["clickref_daily_30d"] = fetch("count", {
        **base,
        "type1": "tracking_event", "ids1": "ClickRef", "ac": "iia",
        "metrics":  "events_users,events",
        "group":    "day",
        "order":    "-day",
        "date_from": d_from30,
        "date_to":  d_to,
    })

    # ── 5d. RecoDrawer key metrics by day (30d) ──────────────────────────
    print("\n5d. RecoDrawer daily trend (30d)...")
    results["reco_daily_30d"] = fetch("count", {
        **base,
        "type1": "tracking_event",
        "ids1":  "RecoDrawerShown,RecoDrawerDismissed,RecoDrawerProviderClicked,RecoDrawerAllClicked",
        "ac": "iia",
        "metrics":  "events_users,events",
        "group":    "day",
        "order":    "-day",
        "date_from": d_from30,
        "date_to":  d_to,
    })

    # ── 6. Audience — country, device, OS, browser, language ────────────
    print("\n6. Audience today...")

    def audience(group, limit="5", extra=None):
        p = {**base, "metrics": "total_users", "group": group,
             "limit": limit, "get_other": "1", "order": "total_users",
             "date_from": d_from0, "date_to": d_to}
        if extra:
            p.update(extra)
        return fetch("count", p)

    results["audience_country"]  = audience("country")
    results["audience_device"]   = audience("ua_device", limit="4")
    results["audience_os"]       = audience("ua_os")
    results["audience_browser"]  = audience("ua_client_name")
    results["audience_language"] = audience("language",
        extra={"type1": "language", "exclude1": "empty_value"})

    # ── Save JSON ───────────────────────────────────────────────────────
    out = os.path.join(OUTPUT_DIR, "finteza_data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Saved → {out}")

    # ── Generate dashboard.html ─────────────────────────────────────────
    write_html(results, OUTPUT_DIR, today)


def fz_label(ts: str) -> str:
    """Finteza FILETIME → 'D/M' string for chart labels."""
    unix = int(ts) // 10_000_000 - 11_644_473_600
    d = datetime.fromtimestamp(unix, tz=timezone.utc)
    return f"{d.day}/{d.month}"


def _delta(a: float, b: float) -> float:
    return round((a - b) / b * 100, 1) if b else 0.0


def _delta_ui(a: float, b: float, good: str = "up") -> tuple[str, str]:
    """Returns (label, color). good='up' means higher=better, 'down' means lower=better."""
    d = _delta(a, b)
    if abs(d) < 0.05:
        return "— 0%", "#898781"
    up = d > 0
    arrow = "↑" if up else "↓"
    sign  = "+" if up else ""
    green = (up and good == "up") or (not up and good == "down")
    col   = "#1baf7a" if green else "#e24b4a"
    return f"{arrow} {sign}{d:.1f}%", col


def _fmt_time(sec) -> str:
    if sec is None or sec <= 0:
        return "—"
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}:{s:02d}"


def _rows(results: dict, key: str) -> list:
    """Extract data rows from a Finteza API response (skipping header row).
    Handles: list (normal), dict with error, missing key."""
    v = results.get(key)
    if not isinstance(v, list) or len(v) < 2:
        return []
    return v[1:]  # v[0] is the column-names header row


def write_html(results: dict, out_dir: str, today=None):
    def fz_hour(ts):
        unix = int(ts) // 10_000_000 - 11_644_473_600
        d = datetime.fromtimestamp(unix, tz=timezone.utc)
        return f"{d.hour:02d}h"


    # ── 30d daily (ascending: d30[0]=oldest, d30[-1]=today partial, d30[-2]=yesterday) ─
    d30 = _rows(results, "daily_30d")
    if len(d30) < 3:
        print("⚠ Insufficient daily_30d data")
        return

    # Yesterday = last complete day = d30[-2]; day before = d30[-3]
    yest = d30[-2]
    prev = d30[-3]

    vis_y   = int(yest[1]); vis_p  = int(prev[1])
    new_y   = int(yest[2]); new_p  = int(prev[2])
    sess_y  = int(yest[4]); sess_p = int(prev[4])
    pps_y   = float(yest[5]); pps_p = float(prev[5])
    ret_y   = vis_y - new_y;   ret_p = vis_p - new_p
    pct_new_y = round(new_y / vis_y * 100, 1) if vis_y else 0.0
    pct_ret_y = round(100.0 - pct_new_y, 1)
    yest_label = fz_label(yest[0])


    d_vis_lbl,  d_vis_col  = _delta_ui(vis_y,  vis_p)
    d_sess_lbl, d_sess_col = _delta_ui(sess_y, sess_p)
    d_pps_lbl,  d_pps_col  = _delta_ui(pps_y,  pps_p)
    d_new_lbl,  d_new_col  = _delta_ui(new_y,  new_p)
    d_ret_lbl,  d_ret_col  = _delta_ui(ret_y,  ret_p)
    # ── Yesterday events — explicit date range call (no period column) ──
    eu_yest = _rows(results, "events_unit_yesterday")
    # Columns: [tracking_event, unit, events, events_users]
    # events     = r[2] = суммарное кол-во срабатываний (используем для shown/dism/etc.)
    # events_users = r[3] = уникальных пользователей

    def _ev_match(row_name, event):
        """Совпадает с точным именем ИЛИ с Online/Offline вариантом (RecoDrawerShown → RecoDrawerOnlineShown + RecoDrawerOfflineShown)."""
        if row_name == event:
            return True
        if event.startswith("RecoDrawer"):
            suffix = event[len("RecoDrawer"):]  # e.g. "Shown", "Dismissed"
            return row_name in (f"RecoDrawerOnline{suffix}", f"RecoDrawerOffline{suffix}")
        return False

    def eu_ev_sum(event, unit=None):
        """Суммирует total events (r[2])."""
        return sum(int(r[2]) for r in eu_yest
                   if _ev_match(r[0], event) and (unit is None or r[1] == unit))

    def eu_sum(event, unit=None):
        """Суммирует уникальных пользователей (r[3])."""
        return sum(int(r[3]) for r in eu_yest
                   if _ev_match(r[0], event) and len(r) > 3 and (unit is None or r[1] == unit))

    def _exact_ev_sum(event_name, unit=None):
        """Точное совпадение имени события (без Online/Offline агрегации)."""
        return sum(int(r[2]) for r in eu_yest
                   if r[0] == event_name and (unit is None or r[1] == unit))

    reco_shown_y         = eu_ev_sum("RecoDrawerShown")
    reco_shown_online_y  = _exact_ev_sum("RecoDrawerOnlineShown")
    reco_shown_offline_y = _exact_ev_sum("RecoDrawerOfflineShown")

    reco_dism_y          = eu_ev_sum("RecoDrawerDismissed")
    reco_dism_online_y   = _exact_ev_sum("RecoDrawerOnlineDismissed")
    reco_dism_offline_y  = _exact_ev_sum("RecoDrawerOfflineDismissed")

    reco_all_y           = eu_ev_sum("RecoDrawerAllClicked")
    reco_provcl_y        = eu_ev_sum("RecoDrawerProviderClicked")
    reco_model_cl_y      = eu_ev_sum("RecoDrawerModelClicked")

    reco_suppr_y         = eu_ev_sum("RecoDrawerSuppressed")
    reco_suppr_online_y  = sum(int(r[2]) for r in eu_yest if r[0] == "RecoDrawerOnlineSuppressed")
    reco_suppr_offline_y = sum(int(r[2]) for r in eu_yest if r[0] == "RecoDrawerOfflineSuppressed")

    # Online/Offline клики (три параллельных пути из drawer)
    reco_allcl_online_y    = _exact_ev_sum("RecoDrawerOnlineAllClicked")
    reco_allcl_offline_y   = _exact_ev_sum("RecoDrawerOfflineAllClicked")
    reco_provcl_online_y   = _exact_ev_sum("RecoDrawerOnlineProviderClicked")
    reco_provcl_offline_y  = _exact_ev_sum("RecoDrawerOfflineProviderClicked")
    reco_modelcl_online_y  = _exact_ev_sum("RecoDrawerOnlineModelClicked")
    reco_modelcl_offline_y = _exact_ev_sum("RecoDrawerOfflineModelClicked")
    reco_cl_online_y  = reco_provcl_online_y  + reco_modelcl_online_y
    reco_cl_offline_y = reco_provcl_offline_y + reco_modelcl_offline_y
    cr_all_online     = round(reco_allcl_online_y  / reco_shown_online_y  * 100, 1) if reco_shown_online_y  else 0.0
    cr_all_offline    = round(reco_allcl_offline_y / reco_shown_offline_y * 100, 1) if reco_shown_offline_y else 0.0
    cr_prov_online    = round(reco_provcl_online_y  / reco_shown_online_y  * 100, 1) if reco_shown_online_y  else 0.0
    cr_prov_offline   = round(reco_provcl_offline_y / reco_shown_offline_y * 100, 1) if reco_shown_offline_y else 0.0
    cr_model_online   = round(reco_modelcl_online_y  / reco_shown_online_y  * 100, 1) if reco_shown_online_y  else 0.0
    cr_model_offline  = round(reco_modelcl_offline_y / reco_shown_offline_y * 100, 1) if reco_shown_offline_y else 0.0
    reco_dism_rate  = round(reco_dism_y / reco_shown_y * 100, 1) if reco_shown_y else 0.0
    reco_all_rate   = round(reco_all_y  / reco_shown_y * 100, 1) if reco_shown_y else 0.0

    # RecoClickRef — клики по провайдеру из Drawer (аффилиат-клики через реко)
    _reco_cr_dict: dict = {}
    for r in eu_yest:
        if r[0] == "RecoClickRef":
            _reco_cr_dict[r[1]] = _reco_cr_dict.get(r[1], 0) + int(r[2])
    reco_cr_brands_y = sorted(_reco_cr_dict.items(), key=lambda x: -x[1])
    reco_cr_total_y  = sum(b[1] for b in reco_cr_brands_y)

    cr_brands_y = sorted(
        [(r[1], int(r[2])) for r in eu_yest if r[0] == "ClickRef"],
        key=lambda x: -x[1]
    )
    cr_y_total = sum(b[1] for b in cr_brands_y)  # total click events (для бар-чарта)

    # ClickRef events yesterday / day-before (from clickref_daily_30d ascending)
    cr_raw   = _rows(results, "clickref_daily_30d")
    cr_y_ev  = int(cr_raw[-2][2]) if len(cr_raw) >= 2 else 0   # total events
    cr_pb_ev = int(cr_raw[-3][2]) if len(cr_raw) >= 3 else 0
    cr_y_us  = int(cr_raw[-2][1]) if len(cr_raw) >= 2 else 0   # unique users
    cr_pb_us = int(cr_raw[-3][1]) if len(cr_raw) >= 3 else 0
    d_cr_lbl, d_cr_col = _delta_ui(cr_y_ev, cr_pb_ev)
    # CTR = уникальных кликнувших / посетители (правильная метрика охвата)
    ctr_y   = round(cr_y_us  / vis_y * 100, 1) if vis_y else 0.0
    ctr_p   = round(cr_pb_us / vis_p * 100, 1) if vis_p else 0.0
    d_ctr_lbl, d_ctr_col = _delta_ui(ctr_y, ctr_p)

    # RecoDrawer delta via reco_daily_30d
    reco_d30 = _rows(results, "reco_daily_30d")
    if len(reco_d30) >= 2:
        r_ev_y  = int(reco_d30[-2][2])
        r_ev_pb = int(reco_d30[-3][2]) if len(reco_d30) >= 3 else r_ev_y
        d_reco_lbl, d_reco_col = _delta_ui(r_ev_y, r_ev_pb)
    else:
        d_reco_lbl, d_reco_col = "—", "#898781"

    # Suppression yesterday — агрегируем по unit (Online+Offline суммируются)
    _suppr_dict: dict = {}
    for r in eu_yest:
        if _ev_match(r[0], "RecoDrawerSuppressed"):
            _suppr_dict[r[1]] = _suppr_dict.get(r[1], 0) + int(r[2])
    suppr_y   = sorted(_suppr_dict.items(), key=lambda x: -x[1])
    s_total_y = sum(s[1] for s in suppr_y)

    # ── 30d chart arrays (ascending) ────────────────────────────────────
    labels30 = json.dumps([fz_label(r[0]) for r in d30])
    vis30    = json.dumps([int(r[1]) for r in d30])
    new30    = json.dumps([int(r[2]) for r in d30])
    cr_ev30  = json.dumps([int(r[2]) for r in cr_raw])
    cr_us30  = [int(r[1]) for r in cr_raw]
    vis30_l  = [int(r[1]) for r in d30]
    ctr30    = json.dumps([
        round(cr_us30[i] / vis30_l[i] * 100, 2) if vis30_l[i] else 0
        for i in range(min(len(vis30_l), len(cr_us30)))
    ])
    pps30    = json.dumps([round(float(r[5]), 2) for r in d30])

    # 7d: last 7 complete days = d30[-8:-1]
    d7      = d30[-8:-1]
    w_labels = json.dumps([fz_label(r[0]) for r in d7])
    w_vis    = json.dumps([int(r[1]) for r in d7])
    w_new    = json.dumps([int(r[2]) for r in d7])
    cr_7d_l  = cr_raw[len(cr_raw)-8:len(cr_raw)-1]
    w_cr     = json.dumps([int(r[2]) for r in cr_7d_l])

    vis_7d  = sum(int(r[1]) for r in d7)
    new_7d  = sum(int(r[2]) for r in d7)
    sess_7d = sum(int(r[4]) for r in d7)
    cr_7d   = sum(int(r[2]) for r in cr_7d_l)
    pct_new_7d = round(new_7d / vis_7d * 100, 1) if vis_7d else 0.0

    # 30d totals
    vis_30d  = sum(int(r[1]) for r in d30)
    new_30d  = sum(int(r[2]) for r in d30)
    sess_30d = sum(int(r[4]) for r in d30)
    cr_30d   = sum(int(r[2]) for r in cr_raw)
    pct_new_30d = round(new_30d / vis_30d * 100, 1) if vis_30d else 0.0

    # ── Providers 30d ───────────────────────────────────────────────────
    eu30    = [r for r in _rows(results, "events_unit_30d") if str(r[0]) == "1" and r[1] == "ClickRef"]
    prov30  = sorted([(r[2], int(r[3])) for r in eu30], key=lambda x: -x[1])[:8]
    prov_total = sum(p[1] for p in prov30)
    p_labels   = json.dumps([p[0] for p in prov30])
    p_values   = json.dumps([p[1] for p in prov30])
    p_max_val  = prov30[0][1] if prov30 else 100

    # ── Audience ────────────────────────────────────────────────────────
    def aud_rows(key):
        return [r for r in _rows(results, key) if r[0] != "[other]"]
    dev_rows   = aud_rows("audience_device")
    dev_labels = json.dumps([r[0] for r in dev_rows])
    dev_values = json.dumps([int(r[1]) for r in dev_rows])

    # ── Топ 20 страниц за вчера ──────────────────────────────────────────
    # Columns: [period, referrer_path, referrer_domain, total_users, total_newusers,
    #           webvisits, webvisits_sessions, webvisits_pages]
    py_all = _rows(results, "pages_yesterday")
    # Note: referrer_domain here = own domain (similarcams.com), NOT excluded
    # EXCLUDE_REFERRERS applies only to external traffic sources
    py_cur = {(r[1], r[2]): r for r in py_all if str(r[0]) == "1"
              and r[1] not in ("[other]",)
              and not any(str(r[1]).startswith(p) for p in EXCLUDE_PAGES)}
    py_prv = {(r[1], r[2]): r for r in py_all if str(r[0]) == "0"}

    # Sort by visitors desc, take top 20
    top20 = sorted(py_cur.values(), key=lambda r: -int(r[3]))[:20]

    def _page_comment(r, prev_r, site_new_pct, site_pps):
        """Generate a short comment for a page row."""
        vis   = int(r[3])
        new_u = int(r[4]) if len(r) > 4 else 0
        pps   = float(r[7]) if len(r) > 7 else 0
        new_pct = new_u / vis if vis else 0
        notes = []
        # Delta vs prev day
        if prev_r is not None:
            prev_vis = int(prev_r[3])
            d = _delta(vis, prev_vis)
            if d >= 15:
                notes.append(f"↑{d:+.0f}% к позавчера")
            elif d <= -15:
                notes.append(f"↓{d:.0f}% к позавчера")
        # Engagement
        if pps >= site_pps + 0.6:
            notes.append(f"глубина {pps:.1f} стр/с ↑")
        elif pps > 0 and pps <= site_pps - 0.4:
            notes.append(f"глубина {pps:.1f} стр/с ↓")
        # Audience type
        if new_pct < site_new_pct - 0.12 and vis >= 20:
            notes.append(f"лояльные ({round(new_pct*100)}% новых)")
        elif new_pct > site_new_pct + 0.08 and vis >= 20:
            notes.append(f"холодный трафик ({round(new_pct*100)}% новых)")
        # High sessions / pages
        sess = int(r[6]) if len(r) > 6 else 0
        if sess > 0 and vis > 0 and sess / vis >= 1.4:
            notes.append("много сессий на посет.")
        return " · ".join(notes) if notes else "—"

    site_new_frac = pct_new_y / 100

    pages_rows_html = ""
    for i, r in enumerate(top20, 1):
        path    = str(r[1])
        domain  = str(r[2])
        vis_pg  = int(r[3])
        new_pg  = int(r[4]) if len(r) > 4 else 0
        pps_pg  = float(r[7]) if len(r) > 7 else 0
        prev_r  = py_prv.get((r[1], r[2]))
        prev_vis = int(prev_r[3]) if prev_r else None
        delta_v  = _delta(vis_pg, prev_vis) if prev_vis else None
        delta_str = (f'<span style="color:{"#1baf7a" if delta_v>=0 else "#e24b4a"};font-size:10px;margin-left:4px;">'
                     f'{"+" if delta_v>=0 else ""}{delta_v:.0f}%</span>') if delta_v is not None else ""
        comment = _page_comment(r, prev_r, site_new_frac, pps_y)
        # Shorten path for display
        display_path = path if path else "/"
        disp_path = display_path if len(display_path) <= 52 else display_path[:49] + "…"
        share = round(vis_pg / vis_y * 100, 1) if vis_y else 0
        pages_rows_html += (
            f'<tr style="border-bottom:.5px solid #eeeee8;">'
            f'<td style="padding:6px 8px 6px 0;font-size:11px;color:#52514e;text-align:right;">{i}</td>'
            f'<td style="padding:6px 8px;font-size:11px;word-break:break-all;max-width:320px;">'
            f'<span style="color:#898781;font-size:10px;">{domain}</span><br>'
            f'<span title="{path or "/"}">{disp_path}</span></td>'
            f'<td style="padding:6px 8px;font-size:11px;text-align:right;white-space:nowrap;font-weight:600;">'
            f'{vis_pg:,}{delta_str}</td>'
            f'<td style="padding:6px 8px;font-size:11px;text-align:right;color:#898781;">{share}%</td>'
            f'<td style="padding:6px 8px;font-size:11px;text-align:right;color:#898781;">{pps_pg:.1f}</td>'
            f'<td style="padding:6px 0 6px 8px;font-size:11px;color:#52514e;">{comment}</td>'
            f'</tr>'
        )

    pages_table_html = f"""<div style="overflow-x:auto;">
  <table style="width:100%;border-collapse:collapse;min-width:620px;">
    <thead><tr style="border-bottom:1.5px solid #d5d4cd;">
      <th style="padding:0 8px 7px 0;font-size:10px;font-weight:700;color:#898781;text-align:right;width:28px;">#</th>
      <th style="padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:left;">Страница</th>
      <th style="padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Посет.</th>
      <th style="padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:right;">Доля</th>
      <th style="padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Стр/с</th>
      <th style="padding:0 0 7px 8px;font-size:10px;font-weight:700;color:#898781;text-align:left;">Сигнал</th>
    </tr></thead>
    <tbody>{pages_rows_html}</tbody>
  </table></div>"""

    # ── Аномальные дни (для справки) ────────────────────────────────────
    if len(d30) > 5:
        vis_vals = [int(r[1]) for r in d30]
        avg30_raw = sum(vis_vals) / len(vis_vals)
        anomaly_days = [fz_label(r[0]) for r in d30 if int(r[1]) > avg30_raw * ANOMALY_MULTIPLIER]
        d30_clean = [r for r in d30 if int(r[1]) <= avg30_raw * ANOMALY_MULTIPLIER]
    else:
        anomaly_days = []
        d30_clean = d30

    # ── Базовые метрики (без аномалий) ─────────────────────────────────
    pps_30d_avg = round(sum(float(r[5]) for r in d30_clean) / len(d30_clean), 2) if d30_clean else 0
    vis_30d_clean = sum(int(r[1]) for r in d30_clean)
    avg_daily_clean = vis_30d_clean / len(d30_clean) if d30_clean else 0
    ctr_30d_avg = round(sum(int(r[1]) for r in cr_raw) / vis_30d * 100, 1) if vis_30d else 0
    prev_7 = d30[-15:-8] if len(d30) >= 15 else []
    vis_prev7 = sum(int(r[1]) for r in prev_7) if prev_7 else 0
    wow = _delta(vis_7d, vis_prev7) if vis_prev7 else 0
    peak_row = max(d30_clean, key=lambda r: int(r[1])) if d30_clean else d30[-2]
    peak_vis = int(peak_row[1])
    dist_peak = _delta(vis_y, peak_vis)

    # ── Страницы для наблюдения ──────────────────────────────────────────
    # Берём топ страниц из 7d referrer-данных, исключаем EXCLUDE списки
    ref7_all = _rows(results, "referrers_7d")
    # Columns: [period, referrer_path, referrer_domain, total_users, total_newusers, webvisits, webvisits_sessions, webvisits_pages]
    ref7 = [
        r for r in ref7_all
        if str(r[0]) == "1"                                           # current period
        and r[2] not in EXCLUDE_REFERRERS                             # not excluded domain
        and not any(str(r[1]).startswith(p) for p in EXCLUDE_PAGES)   # not excluded page
        and r[1] not in ("", "/", "[other]")
        and int(r[3]) >= 10                                            # min 10 visitors
    ]
    watch_pages = []
    if ref7:
        avg_pps_ref = sum(float(r[7]) for r in ref7) / len(ref7)
        avg_vis_ref = sum(int(r[3]) for r in ref7) / len(ref7)
        site_new_pct = pct_new_y / 100

        for r in ref7[:30]:
            path    = str(r[1])
            domain  = str(r[2])
            vis_p   = int(r[3])
            new_p   = int(r[4]) if len(r) > 4 else 0
            pps_p   = float(r[7]) if len(r) > 7 else 0
            flags   = []
            new_pct = new_p / vis_p if vis_p else 0

            if pps_p >= avg_pps_ref + 0.5:
                flags.append(f"глубокий просмотр {pps_p:.1f} стр/сессию")
            if pps_p > 0 and pps_p <= avg_pps_ref - 0.5:
                flags.append(f"низкая глубина {pps_p:.1f} стр/сессию")
            if vis_p >= avg_vis_ref * 2.5:
                flags.append(f"доминирует: {vis_p:,} посет. за неделю")
            if new_pct < site_new_pct - 0.15 and vis_p >= 30:
                flags.append(f"лоял. аудитория: {round(new_pct*100)}% новых")
            if new_pct > site_new_pct + 0.1 and vis_p >= 30:
                flags.append(f"холодный трафик: {round(new_pct*100)}% новых")

            if flags:
                watch_pages.append((path, domain, vis_p, flags))

        watch_pages.sort(key=lambda x: -x[2])
        watch_pages = watch_pages[:6]

    # ── Ключевые выводы (факты) ──────────────────────────────────────────
    findings = []   # (icon, title, text, color)

    # Трафик
    if wow <= -5:
        findings.append(("↓", "Трафик снижается",
            f"Последние 7 дней: {vis_7d:,} посет. — на {abs(wow):.1f}% меньше предыдущей семидневки ({vis_prev7:,}).",
            "#e24b4a"))
    elif wow >= 5:
        findings.append(("↑", "Трафик растёт",
            f"Последние 7 дней: {vis_7d:,} посет. — рост {wow:.1f}% WoW. Предыдущая семидневка: {vis_prev7:,}.",
            "#1baf7a"))
    else:
        findings.append(("→", "Трафик стабилен",
            f"WoW изменение {'+' if wow>0 else ''}{wow:.1f}%. Средний день за 30д (без аномалий): {round(avg_daily_clean):,} посет.",
            "#898781"))

    # Пик
    if dist_peak <= -20:
        findings.append(("↓", "Далеко от пика",
            f"Вчера {vis_y:,} посет. — на {abs(dist_peak):.0f}% ниже 30д-пика {peak_vis:,} ({fz_label(peak_row[0])}).",
            "#e24b4a"))
    elif dist_peak >= -7:
        findings.append(("★", "Около пика",
            f"Вчера {vis_y:,} — лишь {abs(dist_peak):.0f}% ниже 30д-пика {peak_vis:,} ({fz_label(peak_row[0])}).",
            "#1baf7a"))

    # Homepage (/) трафик
    _hp_key = ("/", "similarcams.com")
    _hp_row = py_cur.get(_hp_key)
    if _hp_row:
        hp_vis      = int(_hp_row[3])
        hp_share    = round(hp_vis / vis_y * 100, 1) if vis_y else 0
        _hp_prv     = py_prv.get(_hp_key)
        hp_prev_vis = int(_hp_prv[3]) if _hp_prv else None
        if hp_prev_vis:
            hp_delta = _delta(hp_vis, hp_prev_vis)
            if hp_delta >= 10:
                findings.append(("↑", f"Homepage растёт: +{hp_delta:.0f}% к позавчера",
                    f"/ получила {hp_vis:,} посет. (позавчера: {hp_prev_vis:,}). Доля в трафике: {hp_share}%. "
                    f"Рост главной = рост органики / прямых заходов / бренд-трафика.",
                    "#1baf7a"))
            elif hp_delta <= -10:
                findings.append(("↓", f"Homepage снижается: {hp_delta:.0f}% к позавчера",
                    f"/ получила {hp_vis:,} посет. (позавчера: {hp_prev_vis:,}). Доля в трафике: {hp_share}%. "
                    f"Снижение главной влияет на общий discovery-трафик.",
                    "#e24b4a"))
            else:
                findings.append(("→", f"Homepage стабильна: {hp_vis:,} посет. ({hp_share}% трафика)",
                    f"/ → {hp_vis:,} вчера vs {hp_prev_vis:,} позавчера ({'+' if hp_delta >= 0 else ''}{hp_delta:.0f}%).",
                    "#898781"))
        else:
            findings.append(("→", f"Homepage: {hp_vis:,} посет. ({hp_share}% трафика)",
                f"/ — основная точка входа. Данных позавчера нет для сравнения.",
                "#898781"))

    # CTR
    if ctr_y >= ctr_30d_avg + 1:
        findings.append(("✓", f"CTR {ctr_y}% — выше нормы",
            f"30д среднее: {ctr_30d_avg}%. Вчера ClickRef: {cr_y_ev:,} events, {cr_y_us:,} уникальных пользователей.",
            "#1baf7a"))
    elif ctr_y <= ctr_30d_avg - 1:
        findings.append(("⚠", f"CTR {ctr_y}% — ниже нормы",
            f"30д среднее: {ctr_30d_avg}%. Возможно виджеты менее заметны или трафик менее целевой.",
            "#e24b4a"))
    else:
        findings.append(("→", f"CTR {ctr_y}% — в норме",
            f"30д среднее: {ctr_30d_avg}%. Конверсия в клик стабильна.",
            "#898781"))

    # Глубина
    if pps_y < pps_30d_avg - 0.15:
        findings.append(("↓", "Глубина просмотра снизилась",
            f"Стр/сессию вчера {pps_y:.2f} vs 30д норма {pps_30d_avg:.2f}.",
            "#eb6834"))
    elif pps_y > pps_30d_avg + 0.15:
        findings.append(("↑", "Глубина просмотра выросла",
            f"Стр/сессию вчера {pps_y:.2f} vs 30д норма {pps_30d_avg:.2f}. Пользователи более вовлечены.",
            "#1baf7a"))

    # Новые/вернувшиеся
    new_30d_avg = round(new_30d / vis_30d * 100, 1) if vis_30d else 0
    if pct_new_y > new_30d_avg + 3:
        findings.append(("→", "Больше новых чем обычно",
            f"Вчера {pct_new_y}% новых vs 30д средн. {new_30d_avg}%. Возможен всплеск внешнего трафика.",
            "#eb6834"))
    elif pct_new_y < new_30d_avg - 3:
        findings.append(("✓", "Больше вернувшихся чем обычно",
            f"Вчера {pct_new_y}% новых vs 30д средн. {new_30d_avg}%. Хороший сигнал лояльности.",
            "#1baf7a"))

    # Аномальные дни
    if anomaly_days:
        findings.append(("⚠", "Аномальные дни исключены",
            f"Из 30д расчётов исключены дни с аномально высоким трафиком (>{ANOMALY_MULTIPLIER}× среднего): {', '.join(anomaly_days)}.",
            "#898781"))

    # ── Гипотезы и рекомендации ──────────────────────────────────────────
    hypos = []   # (icon, title, text, color)

    # Brand concentration
    if cr_brands_y and cr_y_total > 0:
        top_share = round(cr_brands_y[0][1] / cr_y_total * 100)
        if top_share >= 55:
            hypos.append(("⚠", "Риск: концентрация бренда",
                f"{cr_brands_y[0][0]} — {top_share}% ClickRef-кликов. При изменении RevShare или правил партнёрки это потеря большой доли дохода. Рекомендуется диверсификация: тест A/B с другими провайдерами на high-traffic страницах.",
                "#e24b4a"))
        elif len(cr_brands_y) >= 3 and cr_brands_y[0][1] / cr_y_total < 0.4:
            hypos.append(("✓", "Хорошая диверсификация брендов",
                f"Топ бренд {cr_brands_y[0][0]} — лишь {top_share}% кликов. Распределение здоровое.",
                "#1baf7a"))

    # Churn / retention
    if pct_new_y > 82:
        hypos.append(("→", "Гипотеза: низкий ретеншн",
            f"{pct_new_y}% новых посетителей — типично для adult-трафика, но возврат {pct_ret_y}% очень мал. Гипотеза: нет механизма возврата. Что стоит проверить: push-уведомления (например, новые камщицы), weekly email «самое горячее за неделю», bookmark-подсказка после 3й страницы.",
            "#eb6834"))
    elif pct_new_y < 72:
        hypos.append(("✓", "Гипотеза: сильный ретеншн",
            f"{pct_ret_y}% вернувшихся — высокий показатель. Текущий контент и UX создают причину вернуться. Стоит усилить персонализацию для вернувшихся (недавно просмотренные, рекомендации).",
            "#1baf7a"))

    # RecoDrawer
    if reco_shown_y > 0:
        reco_rate = round(reco_shown_y / vis_y * 100, 1) if vis_y else 0
        if reco_dism_rate > 45:
            hypos.append(("→", "Гипотеза: RecoDrawer показывается не вовремя",
                f"Dismissed {reco_dism_rate}% от shown. Высокий процент dismissal может означать что drawer появляется слишком рано или мешает. Гипотеза: попробовать показывать после 2й просмотренной страницы вместо 1й.",
                "#eb6834"))
        if reco_rate < 8 and vis_y > 500:
            hypos.append(("→", "Гипотеза: охват RecoDrawer занижен",
                f"RecoDrawer shown у {reco_rate}% посетителей ({reco_shown_y:,}/{vis_y:,}). Возможно условия показа слишком строгие (session_, weekly_cap). Проверить правила триггеров.",
                "#eb6834"))
        if reco_all_y > 0 and reco_shown_y > 0:
            conv = round(reco_all_y / reco_shown_y * 100, 1)
            if conv >= 3:
                hypos.append(("✓", "RecoDrawer конвертирует",
                    f"'All clicked' у {conv}% от shown ({reco_all_y}/{reco_shown_y}). Аудитория, которая видит drawer, вовлечена. Потенциал в расширении охвата.",
                    "#1baf7a"))

    # CTR growth trend
    ctr_vals = [round(int(cr_raw[i][1]) / int(d30[i][1]) * 100, 1) for i in range(min(len(cr_raw), len(d30))) if int(d30[i][1]) > 0]
    ctr_trend_icon = ""
    if len(ctr_vals) >= 14:
        ctr_first7 = sum(ctr_vals[:7]) / 7
        ctr_last7  = sum(ctr_vals[-7:]) / 7
        ctr_trend  = _delta(ctr_last7, ctr_first7)
        if ctr_trend >= 3:
            ctr_trend_icon = f'<span style="color:#1baf7a;font-size:10px;">↑ тренд +{ctr_trend:.0f}%</span>'
        elif ctr_trend <= -3:
            ctr_trend_icon = f'<span style="color:#e24b4a;font-size:10px;">↓ тренд {ctr_trend:.0f}%</span>'
        else:
            ctr_trend_icon = '<span style="color:#898781;font-size:10px;">→ тренд стабилен</span>'
        if ctr_trend >= 5:
            hypos.append(("↑", "CTR растёт — масштабировать",
                f"CTR вырос на {ctr_trend:.1f}% за последние 30 дней ({ctr_first7:.1f}% → {ctr_last7:.1f}%). Что работает — масштабировать: больше ClickRef-виджетов на страницах с высоким трафиком.",
                "#1baf7a"))
        elif ctr_trend <= -5:
            hypos.append(("⚠", "CTR снижается — проверить виджеты",
                f"CTR снизился на {abs(ctr_trend):.1f}% за 30 дней ({ctr_first7:.1f}% → {ctr_last7:.1f}%). Гипотеза: смена аудитории или ухудшение видимости виджетов. Проверить A/B тест позиционирования.",
                "#e24b4a"))

    # ── ClickRef card extras ─────────────────────────────────────────────
    top_brand_name  = cr_brands_y[0][0] if cr_brands_y else "—"
    top_brand_share = round(cr_brands_y[0][1] / cr_y_total * 100) if (cr_y_total and cr_brands_y) else 0
    clicks_per_user = round(cr_y_ev / cr_y_us, 1) if cr_y_us else 0.0

    # ── Источники трафика вчера (attribution_source_type) ────────────────
    # cols: [attribution_source_type_id, total_users]
    # Finteza IDs: 1=Direct, 2=Referral, 3=Search, 4=Advertising
    # (подтверждено: curl ids1=2→domain, ids1=3→domain_alias)
    _src_rows = _rows(results, "sources_yesterday")
    _direct = _search = _social = _referral = 0
    for _r in _src_rows:
        _tid = str(_r[0])
        _v   = int(_r[1])
        if _tid == "1":
            _direct += _v
        elif _tid == "2":
            _referral += _v
        elif _tid == "3":
            _search += _v
        elif _tid == "4":
            _referral += _v   # Advertising → в Referral bucket
        # [other] или 5+ → игнорируем (обычно 0)
    _src_total = max(_direct + _search + _social + _referral, 1)

    def _src_bar(val, total, color):
        pct = round(val / total * 100) if total else 0
        return (
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">'
            f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:5px;">'
            f'<div style="width:{pct}%;height:100%;background:{color};border-radius:3px;"></div></div>'
            f'<span style="font-size:10px;font-weight:600;width:26px;text-align:right;">{pct}%</span>'
            f'</div>'
        )

    # ── Пик часа вчера ──────────────────────────────────────────────────
    _hourly_yest = _rows(results, "hourly_yesterday")  # [hour_ts, total_users]
    if _hourly_yest:
        _ph_row   = max(_hourly_yest, key=lambda r: int(r[1]))
        _peak_hr  = int(str(fz_hour(_ph_row[0])).rstrip('h'))
        _peak_vis = int(_ph_row[1])
        peak_hr_html = f"{_peak_hr:02d}:00–{(_peak_hr+1)%24:02d}:00"
        peak_hr_sub  = f"{_peak_vis:,} посет."
    else:
        peak_hr_html = "—"
        peak_hr_sub  = ""

    # ── Топ страны ───────────────────────────────────────────────────────
    _cntry = [r for r in _rows(results, "audience_country") if r[0] != "[other]"][:5]
    _cntry_total = sum(int(r[1]) for r in _cntry)

    def _card(icon, title, text, color):
        return f"""<div style="background:#fff;border-radius:10px;border:.5px solid rgba(11,11,11,.08);
    padding:14px 16px;border-left:3px solid {color};margin-bottom:10px;">
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:5px;">
      <span style="font-size:14px;">{icon}</span>
      <span style="font-size:12px;font-weight:600;color:{color};">{title}</span>
    </div>
    <div style="font-size:12px;color:#3a3a38;line-height:1.55;">{text}</div>
  </div>"""

    findings_html = "".join(_card(*f) for f in findings) or "<p style='font-size:12px;color:#898781;'>Нет данных.</p>"
    hypos_html    = "".join(_card(*h) for h in hypos)    or "<p style='font-size:12px;color:#898781;'>Нет гипотез.</p>"

    # ── Сохранение в память ──────────────────────────────────────────────
    mem = load_memory()
    _today_ref = today if today else datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    yest_iso = (_today_ref - timedelta(days=1)).strftime("%Y-%m-%d")
    save_memory(mem, yest_iso, {
        "visitors":    vis_y,
        "new_users":   new_y,
        "ret_users":   ret_y,
        "new_pct":     pct_new_y,
        "sessions":    sess_y,
        "pps":         pps_y,
        "ctr":         ctr_y,
        "cr_events":   cr_y_ev,
        "cr_unique":   cr_y_us,
        "reco_shown":  reco_shown_y,
        "reco_dism":   reco_dism_y,
        "reco_all":    reco_all_y,
        "vis_7d":      vis_7d,
        "vis_30d":     vis_30d,
        "cr_30d":      cr_30d,
        "ctr_30d_avg": ctr_30d_avg,
        "peak_vis":    peak_vis,
    }, findings, hypos)

    # ── История из памяти ────────────────────────────────────────────────
    history_entries = mem["entries"][-14:]  # last 14 days

    def _sparkline(vals, color="#2a78d6", h=28):
        """Tiny inline SVG sparkline."""
        if not vals or max(vals) == min(vals):
            return ""
        mn, mx = min(vals), max(vals)
        n = len(vals)
        w = 120
        pts = " ".join(
            f"{round(i * w / (n-1))},{round(h - (v - mn) / (mx - mn) * h)}"
            for i, v in enumerate(vals)
        )
        return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                f'xmlns="http://www.w3.org/2000/svg" style="display:block;">'
                f'<polyline points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="1.5" stroke-linejoin="round"/></svg>')

    if history_entries:
        # Sparkline data (chronological order)
        sp_vis  = [e["metrics"].get("visitors",  0) for e in history_entries]
        sp_ctr  = [e["metrics"].get("ctr",       0) for e in history_entries]
        sp_pps  = [e["metrics"].get("pps",       0) for e in history_entries]
        sp_cr   = [e["metrics"].get("cr_events", 0) for e in history_entries]
        sp_reco = [e["metrics"].get("reco_shown",0) for e in history_entries]

        def _td(val, fmt=",", color=None):
            s = f"{val:{fmt}}" if isinstance(val, int) else (f"{val:.2f}" if isinstance(val, float) else str(val))
            style = f'color:{color};' if color else ''
            return f'<td style="padding:5px 8px;font-size:11px;text-align:right;{style}">{s}</td>'

        hist_rows = ""
        prev_e = None
        for e in reversed(history_entries):
            d   = e.get("date", "")
            m   = e.get("metrics", {})
            note = e.get("note", "")
            f_titles = e.get("findings", [])
            h_titles = e.get("hypos", [])

            # Delta colour for visitors vs previous row
            vis = m.get("visitors", 0)
            vis_prev = prev_e["metrics"].get("visitors", 0) if prev_e else 0
            vis_col = ("#1baf7a" if vis >= vis_prev else "#e24b4a") if prev_e else ""

            note_html = (f'<div style="margin-top:2px;font-size:10px;color:#2a78d6;'
                         f'font-style:italic;">📌 {note}</div>') if note else ""
            badges = "".join(
                f'<span style="display:inline-block;background:#f0efe8;border-radius:3px;'
                f'padding:1px 5px;font-size:10px;color:#52514e;margin:1px 2px 1px 0;">{t}</span>'
                for t in (f_titles + h_titles)[:4]
            )

            hist_rows += (
                f'<tr style="border-bottom:.5px solid #eeeee8;vertical-align:top;">'
                f'<td style="padding:5px 8px 5px 0;font-size:11px;color:#52514e;'
                f'white-space:nowrap;font-weight:500;">{d}</td>'
                + _td(m.get("visitors", 0), color=vis_col)
                + _td(m.get("sessions",  0))
                + _td(m.get("new_pct",   0), fmt=".1f")
                + _td(m.get("pps",       0), fmt=".2f")
                + _td(m.get("ctr",       0), fmt=".1f")
                + _td(m.get("cr_events", 0))
                + _td(m.get("cr_unique", 0))
                + _td(m.get("reco_shown",0))
                + f'<td style="padding:5px 0 5px 8px;font-size:11px;">{badges}{note_html}</td>'
                + f'</tr>'
            )
            prev_e = e

        sparklines_html = (
            f'<div style="display:flex;gap:20px;margin-bottom:14px;flex-wrap:wrap;">'
            f'<div><div style="font-size:10px;color:#898781;margin-bottom:2px;">посетители</div>'
            f'{_sparkline(sp_vis,"#2a78d6")}</div>'
            f'<div><div style="font-size:10px;color:#898781;margin-bottom:2px;">CTR %</div>'
            f'{_sparkline(sp_ctr,"#eb6834")}</div>'
            f'<div><div style="font-size:10px;color:#898781;margin-bottom:2px;">стр/сессию</div>'
            f'{_sparkline(sp_pps,"#1baf7a")}</div>'
            f'<div><div style="font-size:10px;color:#898781;margin-bottom:2px;">ClickRef events</div>'
            f'{_sparkline(sp_cr,"#4a3aa7")}</div>'
            f'<div><div style="font-size:10px;color:#898781;margin-bottom:2px;">RecoDrawer shown</div>'
            f'{_sparkline(sp_reco,"#eda100")}</div>'
            f'</div>'
        )

        pinned_html = ""
        for pin in mem.get("pinned", []):
            col = pin.get("color", "#2a78d6")
            pinned_html += (
                f'<div style="border-left:3px solid {col};padding:6px 10px;'
                f'margin-bottom:6px;background:#fff;border-radius:6px;">'
                f'<span style="font-size:10px;color:#898781;">{pin.get("date","")}</span> '
                f'<span style="font-size:12px;color:#0b0b0b;">📌 {pin.get("text","")}</span></div>'
            )

        history_html = f"""
{sparklines_html}
{"<div style='margin-bottom:10px;'>" + pinned_html + "</div>" if pinned_html else ""}
<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;min-width:700px;">
  <thead><tr style="border-bottom:1.5px solid #d5d4cd;">
    <th style="padding:0 8px 6px 0;font-size:10px;font-weight:700;color:#898781;text-align:left;white-space:nowrap;">Дата</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Посет.</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Сессии</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Нов.%</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Стр/с</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">CTR%</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">CR ev.</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">CR uq.</th>
    <th style="padding:0 8px 6px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;">Reco</th>
    <th style="padding:0 0 6px 8px;font-size:10px;font-weight:700;color:#898781;text-align:left;">Выводы / гипотезы</th>
  </tr></thead>
  <tbody>{hist_rows}</tbody>
</table></div>"""
    else:
        history_html = "<p style='font-size:12px;color:#898781;'>История появится после нескольких запусков.</p>"

    # Страницы для наблюдения HTML
    if watch_pages:
        watch_rows = "".join(
            f'<tr style="border-bottom:.5px solid #e8e7e0;">'
            f'<td style="padding:7px 10px 7px 0;font-size:11px;color:#2a78d6;word-break:break-all;">'
            f'<span style="color:#898781;margin-right:4px;">{p[1]}</span>{p[0]}</td>'
            f'<td style="padding:7px 6px;font-size:11px;text-align:right;white-space:nowrap;">{p[2]:,}</td>'
            f'<td style="padding:7px 0 7px 6px;font-size:11px;color:#52514e;">{" · ".join(p[3])}</td>'
            f'</tr>'
            for p in watch_pages
        )
        watch_html = f"""<table style="width:100%;border-collapse:collapse;">
    <thead><tr style="border-bottom:1px solid #d5d4cd;">
      <th style="padding:0 10px 6px 0;font-size:11px;font-weight:600;text-align:left;color:#52514e;">Страница</th>
      <th style="padding:0 6px 6px;font-size:11px;font-weight:600;text-align:right;color:#52514e;white-space:nowrap;">Посет. 7д</th>
      <th style="padding:0 0 6px 6px;font-size:11px;font-weight:600;text-align:left;color:#52514e;">Сигнал</th>
    </tr></thead>
    <tbody>{watch_rows}</tbody>
  </table>"""
    else:
        watch_html = "<p style='font-size:12px;color:#898781;'>Выраженных аномалий на страницах нет.</p>"

    updated = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"

    # ── Dynamic HTML snippets ────────────────────────────────────────────
    br_rows = "".join(
        f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px;">'
        f'<span style="width:68px;font-size:11px;white-space:nowrap;overflow:hidden;'
        f'text-overflow:ellipsis;flex-shrink:0;">{b[0]}</span>'
        f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:6px;">'
        f'<div style="width:{round(b[1]/cr_brands_y[0][1]*100) if cr_brands_y else 0}%;'
        f'height:100%;background:#2a78d6;border-radius:3px;"></div></div>'
        f'<span style="font-size:11px;font-weight:600;width:36px;text-align:right;">{b[1]:,}</span>'
        f'<span style="font-size:10px;color:#898781;width:30px;text-align:right;">'
        f'{round(b[1]/cr_y_total*100) if cr_y_total else 0}%</span></div>'
        for b in cr_brands_y[:9]
    )

    _SUPPR_LABELS = {
        "session_cap":   ("лимит сессии", "6ч окно показа", "#e24b4a"),
        "min_gap":       ("cooldown",      "~5ч между показами", "#e24b4a"),
        "weekly_cap":    ("недельный лим", "", "#e24b4a"),
        "no_providers":  ("нет провайдеров", "⚠ техн. баг", "#eb6834"),
        "converted":     ("уже перешёл",   "21д блок", "#1baf7a"),
        "quick_dismiss": ("быстро закрыл", "3д блок", "#888780"),
    }
    suppr_rows = "".join(
        f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px;">'
        f'<span style="width:90px;font-size:11px;white-space:nowrap;flex-shrink:0;" '
        f'title="{_SUPPR_LABELS.get(s[0], (s[0],"",""))[1]}">'
        f'{_SUPPR_LABELS.get(s[0], (s[0],"","#e24b4a"))[0]}</span>'
        f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:6px;">'
        f'<div style="width:{round(s[1]/suppr_y[0][1]*100) if suppr_y else 0}%;'
        f'height:100%;background:{_SUPPR_LABELS.get(s[0], ("","","#e24b4a"))[2]};border-radius:3px;"></div></div>'
        f'<span style="font-size:11px;font-weight:600;width:36px;text-align:right;">{s[1]:,}</span>'
        f'<span style="font-size:10px;color:#898781;width:30px;text-align:right;">'
        f'{round(s[1]/s_total_y*100) if s_total_y else 0}%</span></div>'
        for s in suppr_y
    )

    _f_all   = [reco_shown_y, reco_suppr_y, reco_dism_y, reco_all_y,
                reco_provcl_y, reco_model_cl_y, reco_cr_total_y]
    f_y_vals = json.dumps(_f_all)
    f_y_max  = round(max(_f_all) * 1.06) + 1   # ось всегда вмещает наибольший бар

    reco_cr_rows = "".join(
        f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px;">'
        f'<span style="width:82px;font-size:11px;white-space:nowrap;flex-shrink:0;">{b[0]}</span>'
        f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:6px;">'
        f'<div style="width:{round(b[1]/reco_cr_brands_y[0][1]*100) if reco_cr_brands_y else 0}%;'
        f'height:100%;background:#eda100;border-radius:3px;"></div></div>'
        f'<span style="font-size:11px;font-weight:600;width:28px;text-align:right;">{b[1]}</span>'
        f'</div>'
        for b in reco_cr_brands_y
    ) if reco_cr_brands_y else '<span style="font-size:11px;color:#898781;">нет данных</span>'

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>similarcams.com — dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
body{{background:#f1efe8;color:#0b0b0b;padding:20px 24px;max-width:1100px;margin:0 auto;}}
.hdr{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px;}}
h1{{font-size:16px;font-weight:600;}}
.meta{{font-size:12px;color:#898781;}}
.sec{{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:#52514e;margin:26px 0 12px;padding-bottom:7px;border-bottom:1.5px solid #d5d4cd;}}
.g6{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px;}}
.g4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:10px;}}
.card{{background:#fff;border-radius:10px;border:.5px solid rgba(11,11,11,.08);padding:14px 16px;}}
.lbl{{font-size:11px;color:#898781;margin-bottom:3px;}}
.num{{font-size:22px;font-weight:600;line-height:1.15;}}
.num-sm{{font-size:17px;font-weight:600;}}
.delta{{font-size:11px;font-weight:500;margin-top:2px;}}
.sub{{font-size:11px;color:#898781;margin-top:2px;}}
.cl{{font-size:11px;color:#898781;margin-bottom:6px;}}
.leg{{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#52514e;margin-bottom:7px;}}
.dot{{width:10px;height:3px;border-radius:2px;display:inline-block;vertical-align:middle;margin-right:3px;}}
.cw{{position:relative;}}
@media(max-width:900px){{.g6{{grid-template-columns:repeat(3,1fr);}}}}
@media(max-width:640px){{.g6{{grid-template-columns:1fr 1fr;}}}}
@media(max-width:640px){{.g3{{grid-template-columns:1fr 1fr;}}.g2{{grid-template-columns:1fr;}}}}
@media(max-width:480px){{.g2,.g3,.g4{{grid-template-columns:1fr;}}}}
</style>
</head>
<body>
<div class="hdr">
  <h1>similarcams.com</h1>
  <span class="meta">обновлено: {updated}</span>
</div>

<!-- ══ ВЧЕРА ══════════════════════════════════════════════════════════ -->
<div class="sec">Вчера — {yest_label} · последний полный день</div>

<div class="g6">
  <div class="card">
    <div class="lbl">посетители</div>
    <div class="num">{vis_y:,}</div>
    <div class="delta" style="color:{d_vis_col};">{d_vis_lbl}</div>
    <div class="sub">позавчера: {vis_p:,}</div>
  </div>
  <div class="card">
    <div class="lbl">сессии</div>
    <div class="num">{sess_y:,}</div>
    <div class="delta" style="color:{d_sess_col};">{d_sess_lbl}</div>
    <div class="sub">позавчера: {sess_p:,}</div>
  </div>
  <div class="card">
    <div class="lbl">стр / сессию</div>
    <div class="num">{pps_y:.2f}</div>
    <div class="delta" style="color:{d_pps_col};">{d_pps_lbl}</div>
    <div class="sub">позавчера: {pps_p:.2f}</div>
  </div>
  <div class="card">
    <div class="lbl">новые</div>
    <div class="num" style="color:#1baf7a;">{new_y:,}</div>
    <div class="delta" style="color:{d_new_col};">{d_new_lbl}</div>
    <div class="sub">{pct_new_y}% аудитории</div>
  </div>
  <div class="card">
    <div class="lbl">вернувшиеся</div>
    <div class="num" style="color:#2a78d6;">{ret_y:,}</div>
    <div class="delta" style="color:{d_ret_col};">{d_ret_lbl}</div>
    <div class="sub">{pct_ret_y}% аудитории</div>
  </div>
</div>

<div class="g3">
  <div class="card">
    <div class="lbl">ClickRef events</div>
    <div class="num-sm">{cr_y_ev:,}</div>
    <div class="delta" style="color:{d_cr_col};">{d_cr_lbl}</div>
    <div class="sub">{cr_y_us:,} уникальных</div>
    <div style="margin-top:8px;border-top:1px solid #eeeee8;padding-top:7px;font-size:10px;color:#52514e;line-height:1.8;">
      топ: <b>{top_brand_name}</b> {top_brand_share}%<br>
      clicks/user: <b>{clicks_per_user}</b> · RecoClickRef: <b>{reco_cr_total_y}</b>
    </div>
  </div>
  <div class="card">
    <div class="lbl">CTR ClickRef</div>
    <div class="num-sm">{ctr_y}%</div>
    <div class="delta" style="color:{d_ctr_col};">{d_ctr_lbl}</div>
    <div class="sub">позавчера: {ctr_p}%</div>
    <div style="margin-top:8px;border-top:1px solid #eeeee8;padding-top:7px;font-size:10px;color:#52514e;line-height:1.8;">
      30д среднее: <b>{ctr_30d_avg}%</b><br>
      {ctr_trend_icon}
    </div>
  </div>
  <div class="card">
    <div class="lbl">RecoDrawer shown</div>
    <div class="num-sm">{reco_shown_y:,}</div>
    <div class="delta" style="color:{d_reco_col};">{d_reco_lbl}</div>
    <div class="sub">dismissed: {reco_dism_y} ({reco_dism_rate}%) · all clicked: {reco_all_y} ({reco_all_rate}%)</div>
    <div style="margin-top:8px;border-top:1px solid #eeeee8;padding-top:7px;">
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead>
          <tr>
            <th style="text-align:left;padding:0 6px 4px 0;color:#898781;font-size:10px;font-weight:600;width:40%;"></th>
            <th style="text-align:right;padding:0 6px 4px;color:#1baf7a;font-size:10px;font-weight:600;">online</th>
            <th style="text-align:right;padding:0 0 4px 6px;color:#898781;font-size:10px;font-weight:600;">offline</th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-top:.5px solid #eeeee8;">
            <td style="padding:4px 6px 4px 0;color:#898781;">shown</td>
            <td style="padding:4px 6px;text-align:right;font-weight:600;">{reco_shown_online_y:,}</td>
            <td style="padding:4px 0 4px 6px;text-align:right;font-weight:600;">{reco_shown_offline_y:,}</td>
          </tr>
          <tr style="border-top:.5px solid #eeeee8;">
            <td style="padding:4px 6px 4px 0;color:#898781;">dism</td>
            <td style="padding:4px 6px;text-align:right;">{reco_dism_online_y:,}</td>
            <td style="padding:4px 0 4px 6px;text-align:right;">{reco_dism_offline_y:,}</td>
          </tr>
          <tr style="border-top:.5px solid #eeeee8;">
            <td style="padding:4px 6px 4px 0;color:#898781;">suppr</td>
            <td style="padding:4px 6px;text-align:right;">{reco_suppr_online_y:,}</td>
            <td style="padding:4px 0 4px 6px;text-align:right;">{reco_suppr_offline_y:,}</td>
          </tr>
          <tr style="border-top:.5px solid #eeeee8;">
            <td style="padding:4px 6px 4px 0;color:#898781;">🌐 provider</td>
            <td style="padding:4px 6px;text-align:right;"><b>{reco_provcl_online_y}</b> <span style="color:#1baf7a;font-size:10px;">({cr_prov_online}%)</span></td>
            <td style="padding:4px 0 4px 6px;text-align:right;"><b>{reco_provcl_offline_y}</b> <span style="color:#1baf7a;font-size:10px;">({cr_prov_offline}%)</span></td>
          </tr>
          <tr style="border-top:.5px solid #eeeee8;">
            <td style="padding:4px 6px 4px 0;color:#898781;">👤 модель</td>
            <td style="padding:4px 6px;text-align:right;"><b>{reco_modelcl_online_y}</b> <span style="color:#1baf7a;font-size:10px;">({cr_model_online}%)</span></td>
            <td style="padding:4px 0 4px 6px;text-align:right;"><b>{reco_modelcl_offline_y}</b> <span style="color:#1baf7a;font-size:10px;">({cr_model_offline}%)</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="g4">
  <div class="card">
    <div class="cl">Топ страны</div>
    <div style="padding-top:8px;">{"".join(
      f'<div style="display:flex;justify-content:space-between;margin-bottom:5px;font-size:11px;">'
      f'<span>{r[0]}</span>'
      f'<span style="font-weight:600;">{int(r[1]):,} <span style="color:#898781;font-weight:400;">({round(int(r[1])/_cntry_total*100) if _cntry_total else 0}%)</span></span>'
      f'</div>'
      for r in _cntry
    )}</div>
  </div>
  <div class="card">
    <div class="cl">Устройства</div>
    <div style="padding-top:8px;">{"".join(
      f'<div style="display:flex;justify-content:space-between;margin-bottom:5px;font-size:11px;">'
      f'<span>{r[0]}</span>'
      f'<span style="font-weight:600;">{int(r[1]):,}</span>'
      f'</div>'
      for r in dev_rows
    )}</div>
  </div>
  <div class="card">
    <div class="cl">Источники вчера</div>
    <div style="padding-top:8px;">
      <div style="font-size:11px;margin-bottom:3px;display:flex;justify-content:space-between;"><span>🔗 Direct</span><span style="font-weight:600;">{round(_direct/_src_total*100)}%</span></div>
      {_src_bar(_direct, _src_total, "#2a78d6")}
      <div style="font-size:11px;margin-bottom:3px;display:flex;justify-content:space-between;"><span>🔍 Search</span><span style="font-weight:600;">{round(_search/_src_total*100)}%</span></div>
      {_src_bar(_search, _src_total, "#1baf7a")}
      <div style="font-size:11px;margin-bottom:3px;display:flex;justify-content:space-between;"><span>🌐 Referral</span><span style="font-weight:600;">{round(_referral/_src_total*100)}%</span></div>
      {_src_bar(_referral, _src_total, "#888780")}
    </div>
  </div>
  <div class="card">
    <div class="cl">Пик часа вчера</div>
    <div style="margin-top:12px;font-size:22px;font-weight:700;color:#2a78d6;">{peak_hr_html}</div>
    <div style="font-size:12px;color:#52514e;margin-top:4px;">{peak_hr_sub}</div>
  </div>
</div>

<div class="g2">
  <div class="card">
    <div class="cl">ClickRef по брендам — вчера ({cr_y_total:,} кликов)</div>
    <div style="padding-top:6px;">{br_rows}</div>
  </div>
  <div class="card">
    <div class="cl">новые vs вернувшиеся — вчера</div>
    <div style="display:flex;gap:16px;align-items:center;min-height:160px;">
      <div class="cw" style="width:160px;height:160px;flex-shrink:0;"><canvas id="c6"></canvas></div>
      <div style="font-size:12px;line-height:1.9;">
        <div>
          <span style="display:inline-block;width:10px;height:10px;border-radius:50%;
            background:#1baf7a;margin-right:5px;vertical-align:middle;"></span>Новые<br>
          <strong style="font-size:20px;color:#1baf7a;">{new_y:,}</strong><br>
          <span style="color:#898781;">{pct_new_y}%</span>
        </div>
        <div style="margin-top:12px;">
          <span style="display:inline-block;width:10px;height:10px;border-radius:50%;
            background:#2a78d6;margin-right:5px;vertical-align:middle;"></span>Вернувшиеся<br>
          <strong style="font-size:20px;color:#2a78d6;">{ret_y:,}</strong><br>
          <span style="color:#898781;">{pct_ret_y}%</span>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="g2">
  <div class="card">
    <div class="cl">RecoDrawer — воронка вчера</div>
    <div class="cw" style="height:260px;"><canvas id="cf"></canvas></div>
  </div>
  <div style="display:flex;flex-direction:column;gap:12px;">
    <div class="card" style="flex:1;">
      <div class="cl">suppressed — причины вчера ({s_total_y:,} всего)</div>
      <div style="padding-top:10px;">{suppr_rows}</div>
    </div>
    <div class="card" style="flex:0 0 auto;">
      <div class="cl">RecoClickRef по брендам ({reco_cr_total_y:,} кликов)</div>
      <div style="padding-top:10px;">{reco_cr_rows}</div>
    </div>
  </div>
</div>

<!-- ══ ТОП СТРАНИЦ ════════════════════════════════════════════════════ -->
<div class="sec">Топ 20 страниц — {yest_label} · динамика к позавчера</div>
<div class="card">{pages_table_html}</div>

<!-- ══ НЕДЕЛЯ ════════════════════════════════════════════════════════ -->
<div class="sec">Неделя — последние 7 полных дней</div>

<div class="g3">
  <div class="card">
    <div class="lbl">посетителей</div>
    <div class="num">{vis_7d:,}</div>
    <div class="sub">{sess_7d:,} сессий · {round(vis_7d/7):,}/день</div>
  </div>
  <div class="card">
    <div class="lbl">новых</div>
    <div class="num" style="color:#1baf7a;">{new_7d:,}</div>
    <div class="sub">{pct_new_7d}% аудитории</div>
  </div>
  <div class="card">
    <div class="lbl">ClickRef events</div>
    <div class="num">{cr_7d:,}</div>
    <div class="sub">avg {round(cr_7d/7):,}/день</div>
  </div>
</div>

<div class="card">
  <div class="cl">трафик — 7 дней</div>
  <div class="leg">
    <span><span class="dot" style="background:#2a78d6;"></span>посетители</span>
    <span><span class="dot" style="background:#1baf7a;"></span>новые</span>
  </div>
  <div class="cw" style="height:190px;"><canvas id="c7"></canvas></div>
</div>

<!-- ══ МЕСЯЦ ═════════════════════════════════════════════════════════ -->
<div class="sec">Месяц — 30 дней</div>

<div class="g3">
  <div class="card">
    <div class="lbl">посетителей</div>
    <div class="num">{vis_30d:,}</div>
    <div class="sub">{sess_30d:,} сессий · avg {round(vis_30d/30):,}/день</div>
  </div>
  <div class="card">
    <div class="lbl">новых</div>
    <div class="num" style="color:#1baf7a;">{new_30d:,}</div>
    <div class="sub">{pct_new_30d}% · вернувшихся {100-pct_new_30d:.0f}%</div>
  </div>
  <div class="card">
    <div class="lbl">ClickRef events</div>
    <div class="num">{cr_30d:,}</div>
    <div class="sub">avg {round(cr_30d/30):,}/день</div>
  </div>
</div>

<div class="card" style="margin-bottom:10px;">
  <div class="cl">трафик — 30 дней</div>
  <div class="leg">
    <span><span class="dot" style="background:#2a78d6;"></span>посетители</span>
    <span><span class="dot" style="background:#1baf7a;"></span>новые</span>
    <span><span class="dot" style="background:#eb6834;border-top:2px dashed #eb6834;height:0;width:14px;"></span>ClickRef events (right)</span>
  </div>
  <div class="cw" style="height:200px;"><canvas id="c1"></canvas></div>
</div>

<div class="g2">
  <div class="card">
    <div class="cl">CTR ClickRef % — 30 дней</div>
    <div class="cw" style="height:155px;"><canvas id="cctr"></canvas></div>
  </div>
  <div class="card">
    <div class="cl">стр/сессию — 30 дней</div>
    <div class="cw" style="height:155px;"><canvas id="cpps"></canvas></div>
  </div>
</div>

<div class="g2">
  <div class="card">
    <div class="cl">провайдеры — ClickRef за 30 дней ({prov_total:,} кликов)</div>
    <div class="cw" style="height:{max(220, len(prov30)*38+40)}px;"><canvas id="c2"></canvas></div>
  </div>
  <div class="card">
    <div class="cl">устройства — сегодня</div>
    <div class="cw" style="height:200px;"><canvas id="c5"></canvas></div>
  </div>
</div>

<!-- ══ АНАЛИТИКА ═══════════════════════════════════════════════════════ -->
<div class="sec">Ключевые выводы — {yest_label}</div>
{findings_html}

<div class="sec">Гипотезы и рекомендации</div>
{hypos_html}

<div class="sec">Страницы для наблюдения — 7 дней</div>
<div class="card">{watch_html}</div>

<!-- ══ ИСТОРИЯ ════════════════════════════════════════════════════════ -->
<div class="sec">История наблюдений — последние 14 дней</div>
<div class="card" style="padding:16px 18px;">{history_html}</div>
<div style="margin-top:8px;font-size:11px;color:#898781;line-height:1.6;">
  💡 Чтобы добавить заметку к дню — открой <code>finteza_memory.json</code> и заполни поле <code>"note"</code> нужной записи.<br>
  Чтобы закрепить событие (запуск теста, изменение структуры) — добавь объект в <code>"pinned"</code>:<br>
  <code style="background:#e8e7e0;padding:2px 6px;border-radius:3px;">
    {{"date": "5-8", "text": "Запущен A/B тест RecoDrawer", "color": "#2a78d6"}}
  </code>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const grd='#e1e0d9',tk='#898781';
const b={{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false}}}}}};
const ya=(a,bv)=>{{return{{grid:{{color:grd,lineWidth:.5}},ticks:{{color:tk,font:{{size:11}},maxTicksLimit:5}},min:a,max:bv,border:{{display:false}}}}}};
const xa={{grid:{{display:false}},ticks:{{color:tk,font:{{size:10}},maxTicksLimit:9,maxRotation:0}}}};

// New/Returning donut (yesterday)
new Chart(document.getElementById('c6'),{{type:'doughnut',data:{{
  labels:['новые','вернувшиеся'],
  datasets:[{{data:[{new_y},{ret_y}],backgroundColor:['#1baf7a','#2a78d6'],borderWidth:2,borderColor:'#fff'}}]
}},options:{{...b,cutout:'64%',plugins:{{legend:{{display:false}},tooltip:{{mode:'point',intersect:true}}}}}}}});

// RecoDrawer funnel (yesterday)
new Chart(document.getElementById('cf'),{{type:'bar',data:{{
  labels:['shown','suppressed','dismissed','all clicked','prov. clicked','model clicked','RecoClickRef'],
  datasets:[{{data:{f_y_vals},backgroundColor:['#2a78d6','#888780','#eb6834','#1baf7a','#47a06e','#4a90d9','#eda100'],borderRadius:3,borderSkipped:false}}]
}},options:{{...b,indexAxis:'y',
  interaction:{{mode:'index',axis:'y',intersect:false}},
  scales:{{
    x:{{...ya(0,{f_y_max}),grid:{{color:grd,lineWidth:.5}}}},
    y:{{grid:{{display:false}},ticks:{{color:tk,font:{{size:11}}}}}}
  }}
}}}});

// 7d line
const w7v={w_vis},w7n={w_new};
new Chart(document.getElementById('c7'),{{type:'line',data:{{labels:{w_labels},datasets:[
  {{label:'посетители',data:w7v,borderColor:'#2a78d6',backgroundColor:'#2a78d612',fill:true,borderWidth:2,pointRadius:4,pointHoverRadius:5,tension:.25}},
  {{label:'новые',data:w7n,borderColor:'#1baf7a',backgroundColor:'#1baf7a12',fill:true,borderWidth:1.5,pointRadius:3,tension:.25}}
]}},options:{{...b,scales:{{x:xa,y:{{...ya(0,Math.max(...w7v)*1.12)}}}}}}}});

// 30d traffic
const vis30={vis30},new30={new30},cr30={cr_ev30};
const vMx=Math.max(...vis30),cMx=Math.max(...cr30);
new Chart(document.getElementById('c1'),{{type:'line',data:{{labels:{labels30},datasets:[
  {{label:'посетители',data:vis30,borderColor:'#2a78d6',backgroundColor:'#2a78d610',fill:true,borderWidth:2,pointRadius:0,tension:.3}},
  {{label:'новые',data:new30,borderColor:'#1baf7a',backgroundColor:'#1baf7a10',fill:true,borderWidth:1.5,pointRadius:0,tension:.3}},
  {{label:'ClickRef events',data:cr30,borderColor:'#eb6834',borderDash:[3,2],borderWidth:1.5,pointRadius:0,tension:.3,yAxisID:'y2'}}
]}},options:{{...b,scales:{{x:xa,y:{{...ya(0,vMx*1.08),position:'left'}},y2:{{...ya(0,cMx*1.15),position:'right',grid:{{display:false}}}}}}}}}});

// CTR 30d
const ctr30={ctr30};
new Chart(document.getElementById('cctr'),{{type:'line',data:{{labels:{labels30},datasets:[
  {{label:'CTR %',data:ctr30,borderColor:'#4a3aa7',backgroundColor:'#4a3aa712',fill:true,borderWidth:2,pointRadius:0,tension:.3}}
]}},options:{{...b,scales:{{x:xa,y:{{...ya(Math.min(...ctr30)*0.9,Math.max(...ctr30)*1.05)}}}}}}}});

// PPS 30d
const pps30={pps30};
new Chart(document.getElementById('cpps'),{{type:'line',data:{{labels:{labels30},datasets:[
  {{label:'стр/сессию',data:pps30,borderColor:'#eda100',backgroundColor:'#eda10012',fill:true,borderWidth:2,pointRadius:0,tension:.3}}
]}},options:{{...b,scales:{{x:xa,y:{{...ya(Math.min(...pps30)*0.92,Math.max(...pps30)*1.05)}}}}}}}});

// Providers bar
new Chart(document.getElementById('c2'),{{type:'bar',data:{{
  labels:{p_labels},
  datasets:[{{data:{p_values},backgroundColor:['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#4a3aa7','#888780','#b0aead'],borderRadius:3,borderSkipped:false}}]
}},options:{{...b,indexAxis:'y',scales:{{
  x:{{...ya(0,{p_max_val}*1.15),grid:{{color:grd,lineWidth:.5}}}},
  y:{{grid:{{display:false}},ticks:{{color:tk,font:{{size:11}}}}}}
}}}}}});

// Device donut
new Chart(document.getElementById('c5'),{{type:'doughnut',data:{{
  labels:{dev_labels},
  datasets:[{{data:{dev_values},backgroundColor:['#2a78d6','#eb6834','#888780','#eda100'],borderWidth:2,borderColor:'#fff'}}]
}},options:{{...b,cutout:'60%',plugins:{{legend:{{display:true,position:'bottom',labels:{{font:{{size:11}},color:tk,boxWidth:10,padding:6}}}},tooltip:{{mode:'point',intersect:true}}}}}}}});
</script>
</body>
</html>"""

    public_dir = os.path.join(out_dir, "public")
    os.makedirs(public_dir, exist_ok=True)
    out = os.path.join(public_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ Dashboard  → {out}")


if __name__ == "__main__":
    main()
