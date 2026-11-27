#!/usr/bin/env python3
"""
similarcams.com main dashboard from Supermetry -> public/index.html

Env vars (GitHub Actions secrets):
  SUPERMETRY_URL       https://stat.similarcams.com
  SUPERMETRY_KEY       API key, scope "Read analytics"
  SUPERMETRY_SITE_ID   1
  DASHBOARD_PASSWORD   optional password gate

Writes public/index.html and dashboard_memory.json (daily history, 90 days).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.environ["SUPERMETRY_URL"].strip().rstrip("/") + "/api/public/v1"
KEY = os.environ["SUPERMETRY_KEY"].strip()
SITE = os.environ.get("SUPERMETRY_SITE_ID", "").strip()
if not SITE.isdigit():
    sys.exit(f"SUPERMETRY_SITE_ID must be a number like 1, got length {len(SITE)}")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
TZ = ZoneInfo("Europe/Moscow")
UA = "Mozilla/5.0 (compatible; similarcams-dashboard/1.0)"

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
MEMORY_FILE = os.path.join(ROOT, "dashboard_memory.json")

CLICK_EVENT = "provider_clicked"          # former Finteza ClickRef
EXCLUDE_PAGES = ("/test", "/admin", "/cdn-cgi", "/mi-deterrence")
EMPTY = ("", "<nil>", "None", "null", "Other")


# ── API ──────────────────────────────────────────────────────────────────
def call(path, params):
    q = [("site_id", SITE)]
    for k, v in params.items():
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            q.append((k, str(item)))
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}",
                                               "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"  ⚠ {e.code} {path} {params}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        print(f"  ⚠ {type(e).__name__} {path}: {e}")
    return {}


def rng(d_from, d_to):
    return {"period": "custom", "date_from": d_from.isoformat(), "date_to": d_to.isoformat()}


def summary(d_from, d_to, metrics="visitors,visits,pageviews,views_per_visit,bounce_rate,visit_duration"):
    data = call("/stats/summary", {**rng(d_from, d_to), "metrics": metrics})
    return data.get("totals") or (data.get("rows") or [{}])[0]


def timeseries(d_from, d_to, interval="day", metrics="visitors,visits,pageviews", filters=()):
    data = call("/stats/timeseries", {**rng(d_from, d_to), "interval": interval,
                                      "metrics": metrics, "filter": list(filters)})
    return data.get("rows", []) or []


def breakdown(dimension, d_from, d_to, metrics="visitors,visits,pageviews", filters=(), limit=100):
    data = call("/stats/breakdown", {**rng(d_from, d_to), "dimension": dimension,
                                     "metrics": metrics, "filter": list(filters), "limit": limit})
    return [r for r in data.get("rows", []) or [] if str(r.get(dimension, "")).strip() not in EMPTY]


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── helpers ──────────────────────────────────────────────────────────────
def delta(a, b):
    return round((a - b) / b * 100, 1) if b else None


def delta_ui(a, b, good="up"):
    d = delta(a, b)
    if d is None:
        return "—", "#898781"
    if abs(d) < 0.05:
        return "— 0%", "#898781"
    up = d > 0
    green = up == (good == "up")
    return f"{'↑ +' if up else '↓ '}{d:.1f}%", ("#1baf7a" if green else "#e24b4a")


def fmt_dur(sec):
    sec = int(num(sec))
    return f"{sec // 60}:{sec % 60:02d}" if sec > 0 else "—"


def flag(code):
    c = str(code).upper().strip()
    if len(c) != 2 or not c.isalpha():
        return ""
    return chr(0x1F1E6 + ord(c[0]) - 65) + chr(0x1F1E6 + ord(c[1]) - 65)


def parse_model_url(path):
    """/[xx/]cams/{gender}/{platform}/{model}[/suffix]  or  /[xx/]shorts/{gender}/{model}"""
    parts = [p for p in str(path).split("/") if p]
    if len(parts) >= 2 and len(parts[0]) == 2 and parts[1] in ("cams", "shorts"):
        parts = parts[1:]
    if len(parts) >= 4 and parts[0] == "cams" and parts[3] not in ("search", "favorites", "history"):
        return {"gender": parts[1], "platform": parts[2], "model": parts[3],
                "suffix": parts[4] if len(parts) > 4 else None}
    if len(parts) >= 3 and parts[0] == "shorts":
        return {"gender": parts[1], "platform": "shorts", "model": parts[2], "suffix": "shorts"}
    return None


def bars(rows, color, max_rows=10, total=None, width=90):
    if not rows:
        return '<span style="font-size:11px;color:#898781;">нет данных</span>'
    rows = sorted(rows, key=lambda x: -x[1])
    top = rows[0][1] or 1
    total = total or sum(v for _, v in rows) or 1
    out = ""
    for name, v in rows[:max_rows]:
        out += (f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px;">'
                f'<span style="width:{width}px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0;" title="{name}">{name}</span>'
                f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:6px;"><div style="width:{round(v / top * 100)}%;height:100%;background:{color};border-radius:3px;"></div></div>'
                f'<span style="font-size:11px;font-weight:600;width:44px;text-align:right;">{int(v):,}</span>'
                f'<span style="font-size:10px;color:#898781;width:30px;text-align:right;">{round(v / total * 100)}%</span></div>')
    return out


def sparkline(vals, color="#2a78d6", h=22, w=120):
    if len(vals) < 2 or max(vals) == min(vals):
        return ""
    mn, mx, n = min(vals), max(vals), len(vals)
    pts = " ".join(f"{round(i * w / (n - 1))},{round(h - (v - mn) / (mx - mn) * h)}" for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


def _dpct(cur, prev):
    """Finteza-style delta: +23%, -48%, +4k%."""
    if not prev:
        return ""
    d = (cur - prev) / prev * 100
    if abs(d) < 0.5:
        return ""
    txt = f"{d / 1000:+.0f}k%" if abs(d) >= 1000 else f"{d:+.0f}%"
    return f'<span class="dp" style="color:{"#1baf7a" if d > 0 else "#e24b4a"};">{txt}</span>'


def pages_table(rows, prev=None, total=0, limit=100, table_id="pages"):
    """Finteza-like pages table with search and CSV export.
    rows: breakdown rows with page/visitors/visits/pageviews/events; prev: {page: row} for deltas."""
    prev = prev or {}
    rows = sorted((r for r in rows if not str(r["page"]).startswith(EXCLUDE_PAGES)),
                  key=lambda r: -num(r.get("visitors")))[:limit]
    total = total or sum(num(r.get("visitors")) for r in rows) or 1
    body = ""
    for r in rows:
        p = str(r["page"])
        v, vs, pv, ev = (num(r.get(k)) for k in ("visitors", "visits", "pageviews", "events"))
        q = prev.get(p, {})
        qv, qvs, qpv, qev = (num(q.get(k)) for k in ("visitors", "visits", "pageviews", "events"))
        pps, qpps = (pv / vs if vs else 0), (qpv / qvs if qvs else 0)
        share = v / total * 100
        body += (f'<tr><td class="pg" title="{p}">{p}</td>'
                 f'<td class="tdr"><b>{int(v):,}</b>{_dpct(v, qv)}</td>'
                 f'<td class="tdr"><span class="sh"><span style="width:{min(100, share * 1.6):.0f}%"></span></span>{share:.2f}%</td>'
                 f'<td class="tdr">{int(ev):,}{_dpct(ev, qev)}</td>'
                 f'<td class="tdr">{int(vs):,}{_dpct(vs, qvs)}</td>'
                 f'<td class="tdr">{pps:.2f}{_dpct(pps, qpps)}</td></tr>')
    return f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px;">
  <input class="srch" placeholder="Поиск по странице…" oninput="filterTable('{table_id}', this.value)">
  <a href="#" class="csv" onclick="csvTable('{table_id}');return false;">⬇ CSV</a></div>
<div style="overflow-x:auto;"><table id="{table_id}" class="pt"><thead><tr>
<th style="text-align:left;">Страница</th><th>Посетители</th><th>%</th><th>События</th><th>Визиты</th><th>Стр/визит</th>
</tr></thead><tbody>{body}</tbody></table></div>"""


PT_CSS = """.pt{min-width:760px;} .pt td{border-bottom:.5px solid #eeeee8;}
.pt .pg{padding:7px 8px 7px 0;font-size:12px;word-break:break-all;}
.pt .tdr{font-size:12px;padding:7px 8px;}
.dp{font-size:10px;margin-left:4px;}
.sh{display:inline-block;width:56px;height:8px;background:#f0efe8;border-radius:2px;margin-right:6px;vertical-align:middle;overflow:hidden;}
.sh span{display:block;height:100%;background:#2a78d6;}
.srch{flex:1;max-width:360px;padding:8px 10px;border:1px solid #d5d4cd;border-radius:6px;font-size:12px;}
.csv{font-size:12px;text-decoration:none;}"""

PT_JS = r"""
function filterTable(id,q){q=q.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(r=>{
  r.style.display=r.cells[0].textContent.toLowerCase().includes(q)?'':'none';});}
function csvTable(id){const t=document.getElementById(id);const rows=[...t.rows].filter(r=>r.style.display!=='none');
  const esc=s=>'"'+s.replace(/"/g,'""')+'"';
  const lines=rows.map(r=>[...r.cells].map(c=>{const b=c.cloneNode(true);b.querySelectorAll('.dp').forEach(x=>x.remove());
    return esc(b.textContent.trim());}).join(','));
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿'+lines.join('\n')],{type:'text/csv'}));
  a.download=id+'.csv';a.click();}
"""


def card(icon, title, text, color):
    return (f'<div class="card" style="border-left:3px solid {color};">'
            f'<div style="display:flex;gap:8px;margin-bottom:5px;"><span style="font-size:14px;">{icon}</span>'
            f'<span style="font-size:12px;font-weight:600;color:{color};">{title}</span></div>'
            f'<div style="font-size:12px;color:#3a3a38;line-height:1.55;">{text}</div></div>')


def load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"days": {}, "model_rankings": {}, "shorts_rankings": {}, "country_daily": {}, "pinned": []}


def trim(d, n):
    return {k: d[k] for k in sorted(d)[-n:]}


# ── main ─────────────────────────────────────────────────────────────────
def main():
    today = datetime.now(TZ).date()
    yd, pb = today - timedelta(days=1), today - timedelta(days=2)
    d30_from = today - timedelta(days=30)
    print(f"Yesterday {yd}, day before {pb}, 30d from {d30_from}")

    # headline numbers
    sy, sp = summary(yd, yd), summary(pb, pb)
    vis_y, vis_p = int(num(sy.get("visitors"))), int(num(sp.get("visitors")))
    if not vis_y and not vis_p:
        sys.exit("No visitors returned — check key/site id.")
    visits_y, visits_p = int(num(sy.get("visits"))), int(num(sp.get("visits")))
    pps_y, pps_p = num(sy.get("views_per_visit")), num(sp.get("views_per_visit"))
    br_y, br_p = num(sy.get("bounce_rate")), num(sp.get("bounce_rate"))
    dur_y, dur_p = num(sy.get("visit_duration")), num(sp.get("visit_duration"))

    # ClickRef (provider_clicked)
    clk_f = [f"event:is:{CLICK_EVENT}"]
    cr_y_rows = breakdown("event", yd, yd, "events,visitors", clk_f, 5)
    cr_p_rows = breakdown("event", pb, pb, "events,visitors", clk_f, 5)
    cr_y_ev = int(num(cr_y_rows[0]["events"])) if cr_y_rows else 0
    cr_y_us = int(num(cr_y_rows[0]["visitors"])) if cr_y_rows else 0
    cr_p_ev = int(num(cr_p_rows[0]["events"])) if cr_p_rows else 0
    cr_p_us = int(num(cr_p_rows[0]["visitors"])) if cr_p_rows else 0
    ctr_y = round(cr_y_us / vis_y * 100, 1) if vis_y else 0.0
    ctr_p = round(cr_p_us / vis_p * 100, 1) if vis_p else 0.0
    brands_y = [(r["prop:provider_slug"], int(num(r["events"])))
                for r in breakdown("prop:provider_slug", yd, yd, "events", clk_f, 20)]
    surfaces_y = [(r["prop:surface"], int(num(r["events"])))
                  for r in breakdown("prop:surface", yd, yd, "events", clk_f, 20)]

    # RecoDrawer headline
    reco = {r["event"]: int(num(r["events"])) for r in
            breakdown("event", yd, yd, "events", ["event:contains:reco_drawer"], 20)}
    r_shown = reco.get("reco_drawer_shown", 0)
    r_model = reco.get("reco_drawer_model_clicked", 0)
    r_prov = reco.get("reco_drawer_provider_clicked", 0)
    r_dism = reco.get("reco_drawer_dismissed", 0)

    # audience yesterday
    countries = [(r["country"], int(num(r["visitors"]))) for r in breakdown("country", yd, yd, "visitors", limit=10)]
    channels = [(r["channel"], int(num(r["visitors"]))) for r in breakdown("channel", yd, yd, "visitors", limit=10)]
    devices = [(r["device"], int(num(r["visitors"]))) for r in breakdown("device", yd, yd, "visitors", limit=5)]

    # hourly yesterday
    hourly = [0] * 24
    for r in timeseries(yd, yd, "hour", "visitors"):
        try:
            hourly[int(str(r["time"])[11:13])] = int(num(r.get("visitors")))
        except (ValueError, KeyError):
            pass

    # pages yesterday / day before
    pages_y = {r["page"]: r for r in breakdown("page", yd, yd, "visitors,visits,pageviews,events", limit=1000)}
    pages_p = {r["page"]: r for r in breakdown("page", pb, pb, "visitors,visits,pageviews,events", limit=1000)}

    # 30 days
    ts30 = timeseries(d30_from, yd, "day", "visitors,visits,pageviews")
    cr30 = {r["time"]: r for r in timeseries(d30_from, yd, "day", "events,visitors", clk_f)}
    days = [str(r["time"])[:10] for r in ts30]
    vis30 = [int(num(r.get("visitors"))) for r in ts30]
    visits30 = [int(num(r.get("visits"))) for r in ts30]
    pps30 = [round(num(r.get("pageviews")) / num(r.get("visits")), 2) if num(r.get("visits")) else 0 for r in ts30]
    cr_ev30 = [int(num(cr30.get(r["time"], {}).get("events"))) for r in ts30]
    cr_us30 = [int(num(cr30.get(r["time"], {}).get("visitors"))) for r in ts30]
    ctr30 = [round(cr_us30[i] / vis30[i] * 100, 2) if vis30[i] else 0 for i in range(len(ts30))]
    prov30 = [(r["prop:provider_slug"], int(num(r["events"])))
              for r in breakdown("prop:provider_slug", d30_from, yd, "events", clk_f, 8)]
    active = [i for i, v in enumerate(vis30) if v > 0]   # days with data

    w_idx = list(range(max(0, len(ts30) - 7), len(ts30)))
    vis_7d = sum(vis30[i] for i in w_idx)
    visits_7d = sum(visits30[i] for i in w_idx)
    cr_7d = sum(cr_ev30[i] for i in w_idx)
    vis_prev7 = sum(vis30[max(0, len(ts30) - 14):max(0, len(ts30) - 7)])
    vis_30d, visits_30d, cr_30d = sum(vis30), sum(visits30), sum(cr_ev30)
    n_active = len(active) or 1
    ctr_30d_avg = round(sum(cr_us30) / vis_30d * 100, 1) if vis_30d else 0.0
    pps_30d_avg = round(sum(pps30[i] for i in active) / n_active, 2) if active else 0.0

    # ── memory ──
    mem = load_memory()
    for k in ("days", "model_rankings", "shorts_rankings", "country_daily"):
        mem.setdefault(k, {})
    mem.setdefault("pinned", [])
    y_iso = yd.isoformat()
    mem["days"][y_iso] = {"visitors": vis_y, "visits": visits_y, "pps": round(pps_y, 2), "bounce": round(br_y, 1),
                          "ctr": ctr_y, "cr_events": cr_y_ev, "cr_unique": cr_y_us, "reco_shown": r_shown,
                          "note": mem["days"].get(y_iso, {}).get("note", "")}

    # models / shorts
    models, shorts = {}, {}
    for path, r in pages_y.items():
        pm = parse_model_url(path)
        if not pm:
            continue
        v = int(num(r.get("visitors")))
        if pm["suffix"] == "shorts":
            k = f'{pm["gender"]}|{pm["model"]}'
            shorts.setdefault(k, {"gender": pm["gender"], "model": pm["model"], "vis": 0})["vis"] += v
            continue
        k = f'{pm["gender"]}|{pm["platform"]}|{pm["model"]}'
        m = models.setdefault(k, {"gender": pm["gender"], "platform": pm["platform"], "model": pm["model"],
                                  "vis": 0, "profile_vis": 0, "stats_vis": 0})
        if pm["suffix"] is None:
            m["vis"] += v
        elif pm["suffix"] == "profile":
            m["profile_vis"] += v
        elif pm["suffix"] in ("stats-and-schedule", "stats"):
            m["stats_vis"] += v
    top_models = sorted((m for m in models.values() if m["vis"] > 0),
                        key=lambda m: -(m["vis"] + m["profile_vis"] + m["stats_vis"]))[:50]
    top_shorts = sorted(shorts.values(), key=lambda m: -m["vis"])[:20]

    prev_rank = next((mem["model_rankings"][d] for d in sorted(mem["model_rankings"], reverse=True) if d != y_iso), [])
    prev_keys = {f'{m["gender"]}|{m["platform"]}|{m["model"]}': m["vis"] for m in prev_rank}
    today_keys = {f'{m["gender"]}|{m["platform"]}|{m["model"]}' for m in top_models}
    dropped = [m for m in prev_rank if f'{m["gender"]}|{m["platform"]}|{m["model"]}' not in today_keys]
    mem["model_rankings"][y_iso] = top_models
    mem["shorts_rankings"][y_iso] = top_shorts
    mem["country_daily"][y_iso] = [{"country": c, "vis": v, "pct": round(v / vis_y * 100, 1) if vis_y else 0}
                                   for c, v in countries]

    # ── findings / hypotheses / anomalies ──
    findings, hypos, anomalies = [], [], []
    wow = delta(vis_7d, vis_prev7)
    if wow is None:
        findings.append(("→", "Трафик: мало истории для WoW",
                         f"За 7 дней {vis_7d:,} посет. Сравнение неделя-к-неделе появится, когда накопится 14 дней данных Supermetry.", "#898781"))
    elif wow <= -5:
        findings.append(("↓", f"Трафик {wow:.0f}% WoW", f"7 дней: {vis_7d:,} посет. против {vis_prev7:,} неделей ранее.", "#e24b4a"))
    elif wow >= 5:
        findings.append(("↑", f"Трафик +{wow:.0f}% WoW", f"7 дней: {vis_7d:,} посет. против {vis_prev7:,} неделей ранее.", "#1baf7a"))
    else:
        findings.append(("→", f"Трафик {wow:+.0f}% WoW", f"7 дней: {vis_7d:,} посет., неделей ранее {vis_prev7:,}.", "#898781"))

    hp = pages_y.get("/")
    if hp:
        hv = int(num(hp.get("visitors")))
        hpv = int(num(pages_p.get("/", {}).get("visitors")))
        hd = delta(hv, hpv)
        share = round(hv / vis_y * 100, 1) if vis_y else 0
        if hd is not None and abs(hd) >= 10:
            findings.append(("↑" if hd > 0 else "↓", f"Главная {hd:+.0f}% к позавчера",
                             f"/ получила {hv:,} посет. (позавчера {hpv:,}), {share}% трафика.", "#1baf7a" if hd > 0 else "#e24b4a"))
        else:
            findings.append(("→", f"Главная: {hv:,} посет. ({share}% трафика)", f"Позавчера: {hpv:,}.", "#898781"))

    if len(active) >= 3:
        if ctr_y >= ctr_30d_avg + 1:
            findings.append(("✓", f"CTR {ctr_y}% — выше нормы", f"Среднее за период: {ctr_30d_avg}%. ClickRef вчера: {cr_y_ev:,} событий, {cr_y_us:,} уникальных.", "#1baf7a"))
        elif ctr_y <= ctr_30d_avg - 1:
            findings.append(("⚠", f"CTR {ctr_y}% — ниже нормы", f"Среднее за период: {ctr_30d_avg}%.", "#e24b4a"))
        else:
            findings.append(("→", f"CTR {ctr_y}% — в норме", f"Среднее за период: {ctr_30d_avg}%.", "#898781"))
        if pps_y < pps_30d_avg - 0.15:
            findings.append(("↓", "Глубина просмотра снизилась", f"Стр/визит вчера {pps_y:.2f} против нормы {pps_30d_avg:.2f}.", "#eb6834"))
        elif pps_y > pps_30d_avg + 0.15:
            findings.append(("↑", "Глубина просмотра выросла", f"Стр/визит вчера {pps_y:.2f} против нормы {pps_30d_avg:.2f}.", "#1baf7a"))

    top_ch = channels[0] if channels else None
    if top_ch and vis_y:
        findings.append(("→", f"Главный канал: {top_ch[0]} ({round(top_ch[1] / vis_y * 100)}%)",
                         " · ".join(f"{c}: {v:,}" for c, v in channels[:4]), "#898781"))

    if r_shown:
        reco_rate = round(r_shown / visits_y * 100, 1) if visits_y else 0
        if reco_rate < 8 and visits_y > 500:
            hypos.append(("→", "Охват RecoDrawer занижен",
                          f"Дровер показан в {reco_rate}% визитов. Проверить лимиты показа (session_cap, min_gap) на <a href='reco.html'>странице дровера</a>.", "#eb6834"))
        mc = round(r_model / r_shown * 100, 1)
        hypos.append(("✓" if mc >= 5 else "→", f"RecoDrawer: клик по модели {mc}% показов",
                      f"Показов {r_shown:,}, кликов по модели {r_model:,}, по провайдеру {r_prov:,}, закрытий {r_dism:,}.",
                      "#1baf7a" if mc >= 5 else "#898781"))
    if surfaces_y and cr_y_ev:
        s0 = surfaces_y[0]
        hypos.append(("→", f"ClickRef чаще всего из «{s0[0]}» ({round(s0[1] / cr_y_ev * 100)}%)",
                      "Места кликов: " + " · ".join(f"{s}: {v:,}" for s, v in surfaces_y[:5]), "#898781"))

    # country anomaly vs last 7 days
    hist = sorted(d for d in mem["country_daily"] if d != y_iso)[-7:]
    if len(hist) >= 3:
        avg = {}
        for d in hist:
            for e in mem["country_daily"][d]:
                avg.setdefault(e["country"], []).append(e["pct"])
        avg = {c: sum(v) / len(v) for c, v in avg.items()}
        for c, v in countries:
            pct = round(v / vis_y * 100, 1) if vis_y else 0
            a = avg.get(c, 0)
            if pct >= 10 and (a == 0 or pct / max(a, 0.5) >= 2.5):
                anomalies.append(("🌍", f"Нетипичный трафик: {c}", f"{pct}% трафика вчера ({v:,}), обычно {a:.1f}%", "#4a3aa7"))

    for k in ("days", "model_rankings", "shorts_rankings"):
        mem[k] = trim(mem[k], 90 if k == "days" else 30)
    mem["country_daily"] = trim(mem["country_daily"], 30)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=1, ensure_ascii=False)

    # ── HTML pieces ──
    yl = f"{yd.day}.{yd.month:02d}"

    def kpi(label, val, cur, prev, sub, good="up", color=""):
        lbl, col = delta_ui(cur, prev, good)
        st = f' style="color:{color};"' if color else ""
        return (f'<div class="card"><div class="lbl">{label}</div><div class="num"{st}>{val}</div>'
                f'<div class="delta" style="color:{col};">{lbl}</div><div class="sub">{sub}</div></div>')

    kpis = (kpi("посетители", f"{vis_y:,}", vis_y, vis_p, f"позавчера: {vis_p:,}")
            + kpi("визиты", f"{visits_y:,}", visits_y, visits_p, f"позавчера: {visits_p:,}")
            + kpi("стр / визит", f"{pps_y:.2f}", pps_y, pps_p, f"позавчера: {pps_p:.2f}")
            + kpi("отказы", f"{br_y:.1f}%", br_y, br_p, f"позавчера: {br_p:.1f}%", good="down")
            + kpi("ср. длительность", fmt_dur(dur_y), dur_y, dur_p, f"позавчера: {fmt_dur(dur_p)}"))

    d_cr, c_cr = delta_ui(cr_y_ev, cr_p_ev)
    d_ctr, c_ctr = delta_ui(ctr_y, ctr_p)
    top_brand = brands_y[0][0] if brands_y else "—"
    top_share = round(brands_y[0][1] / sum(v for _, v in brands_y) * 100) if brands_y else 0
    cpu = round(cr_y_ev / cr_y_us, 1) if cr_y_us else 0

    countries_html = "".join(
        f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:11px;">'
        f'<span><span style="font-size:15px;">{flag(c)}</span> {c}</span>'
        f'<span style="font-weight:600;">{v:,} <span style="color:#898781;font-weight:400;">({round(v / vis_y * 100) if vis_y else 0}%)</span></span></div>'
        for c, v in countries) or '<span style="font-size:11px;color:#898781;">нет данных</span>'

    hmax = max(hourly) or 1
    peak_hr = hourly.index(max(hourly)) if any(hourly) else -1
    bw, gap, ch = 7, 2, 52
    hsvg = "".join(f'<rect x="{i * (bw + gap)}" y="{round(ch - v / hmax * ch)}" width="{bw}" height="{max(1, round(v / hmax * ch))}" '
                   f'fill="{"#2a78d6" if i == peak_hr else "#c8daf5"}" rx="1"/>' for i, v in enumerate(hourly))
    periods = [("🌙 Ночь", sum(hourly[0:6])), ("🌅 Утро", sum(hourly[6:12])), ("☀️ День", sum(hourly[12:18])), ("🌆 Вечер", sum(hourly[18:24]))]
    best = max(periods, key=lambda p: p[1])[0] if any(hourly) else ""
    periods_html = "".join(f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:.5px solid #f0efe8;font-size:11px;">'
                           f'<span style="color:{"#0b0b0b" if p[0] == best else "#898781"};">{p[0]}</span>'
                           f'<span style="font-weight:{600 if p[0] == best else 400};">{p[1]:,}</span></div>' for p in periods)
    peak_html = f"{peak_hr:02d}:00–{(peak_hr + 1) % 24:02d}:00" if peak_hr >= 0 else "—"

    pages_html = pages_table(list(pages_y.values()), pages_p, vis_y, 100, "pages")

    plat_col = {"stripchat": "#eb6834", "chaturbate": "#1baf7a", "bongacams": "#2a78d6", "camsoda": "#a855f7", "livejasmin": "#e87ba4"}

    def model_spark(m, key="model_rankings", plat=True):
        vals = []
        for d in sorted(mem[key]):
            f = next((x for x in mem[key][d] if x["gender"] == m["gender"] and x["model"] == m["model"]
                      and (not plat or x.get("platform") == m.get("platform"))), None)
            vals.append(f["vis"] if f else 0)
        return sparkline(vals, "#2a78d6" if plat else "#a855f7")

    dash = '<span style="color:#c8c7c0;">—</span>'
    models_html = ""
    for i, m in enumerate(top_models, 1):
        k = f'{m["gender"]}|{m["platform"]}|{m["model"]}'
        pv = prev_keys.get(k)
        dstr = ""
        if pv:
            dv = delta(m["vis"], pv)
            dstr = f'<span style="color:{"#1baf7a" if dv >= 0 else "#e24b4a"};font-size:10px;margin-left:3px;">{dv:+.0f}%</span>'
        new = ('<span style="background:#1baf7a;color:#fff;font-size:9px;padding:1px 4px;border-radius:3px;margin-left:4px;">new</span>'
               if prev_rank and k not in prev_keys else "")
        models_html += (f'<tr style="border-bottom:.5px solid #eeeee8;"><td class="tdn">{i}</td>'
                        f'<td style="padding:5px 8px 5px 0;font-size:11px;">{m["model"]}{new}</td>'
                        f'<td style="padding:5px 8px;font-size:10px;"><span style="color:{plat_col.get(m["platform"], "#898781")};font-weight:600;">{m["platform"]}</span> <span style="color:#898781;">{m["gender"]}</span></td>'
                        f'<td class="tdr" style="font-weight:600;">{m["vis"]:,}{dstr}</td>'
                        f'<td class="tdr">{m["profile_vis"] or dash}</td><td class="tdr">{m["stats_vis"] or dash}</td>'
                        f'<td style="padding:5px 0 5px 12px;">{model_spark(m)}</td></tr>')
    dropped_html = ("" if not dropped else '<div style="margin-top:12px;font-size:11px;">⬇ Выпали из топ 50: ' +
                    " · ".join(f'<span style="color:#e24b4a;">{m["model"]}</span> <span style="color:#898781;font-size:10px;">({m["platform"]})</span>' for m in dropped[:5]) + "</div>")

    shorts_html = "".join(f'<tr style="border-bottom:.5px solid #eeeee8;"><td class="tdn">{i}</td><td style="padding:5px 8px 5px 0;font-size:11px;">{m["model"]}</td>'
                          f'<td style="padding:5px 8px;font-size:10px;color:#898781;">{m["gender"]}</td><td class="tdr" style="font-weight:600;">{m["vis"]:,}</td>'
                          f'<td style="padding:5px 0 5px 12px;">{model_spark(m, "shorts_rankings", False)}</td></tr>'
                          for i, m in enumerate(top_shorts, 1))

    hdays = sorted(mem["days"])
    hist_rows = ""
    prev_v = None
    for d in hdays:
        m = mem["days"][d]
        col = "" if prev_v is None else ("#1baf7a" if m["visitors"] >= prev_v else "#e24b4a")
        prev_v = m["visitors"]
        note = f'<div style="font-size:10px;color:#2a78d6;font-style:italic;">📌 {m["note"]}</div>' if m.get("note") else ""
        hist_rows = (f'<tr style="border-bottom:.5px solid #eeeee8;"><td style="padding:5px 8px 5px 0;font-size:11px;font-weight:500;white-space:nowrap;">{d}</td>'
                     f'<td class="tdr" style="color:{col};">{m["visitors"]:,}</td><td class="tdr">{m["visits"]:,}</td>'
                     f'<td class="tdr">{m["pps"]:.2f}</td><td class="tdr">{m["bounce"]:.1f}</td><td class="tdr">{m["ctr"]:.1f}</td>'
                     f'<td class="tdr">{m["cr_events"]:,}</td><td class="tdr">{m["cr_unique"]:,}</td><td class="tdr">{m["reco_shown"]:,}</td>'
                     f'<td style="padding:5px 0 5px 8px;">{note}</td></tr>') + hist_rows
    sp = lambda key, color: sparkline([mem["days"][d][key] for d in hdays], color, 28)
    sparks = (f'<div style="display:flex;gap:20px;margin-bottom:14px;flex-wrap:wrap;">'
              + "".join(f'<div><div style="font-size:10px;color:#898781;">{lbl}</div>{sp(k, c)}</div>'
                        for lbl, k, c in [("посетители", "visitors", "#2a78d6"), ("CTR %", "ctr", "#eb6834"),
                                          ("стр/визит", "pps", "#1baf7a"), ("ClickRef", "cr_events", "#4a3aa7"),
                                          ("RecoDrawer shown", "reco_shown", "#eda100")]) + "</div>")
    pinned = "".join(f'<div style="border-left:3px solid {p.get("color", "#2a78d6")};padding:6px 10px;margin-bottom:6px;background:#fff;border-radius:6px;">'
                     f'<span style="font-size:10px;color:#898781;">{p.get("date", "")}</span> <span style="font-size:12px;">📌 {p.get("text", "")}</span></div>'
                     for p in mem.get("pinned", []))

    anomalies_html = ("" if not anomalies else f'<div class="sec">⚠ Аномалии — {yl}</div>' + "".join(card(*a) for a in anomalies[:8]))

    chart_data = {
        "labels30": [f"{int(d[8:10])}/{int(d[5:7])}" for d in days], "vis30": vis30, "visits30": visits30,
        "cr30": cr_ev30, "ctr30": ctr30, "pps30": pps30,
        "w_idx": w_idx, "prov_labels": [p for p, _ in prov30], "prov_values": [v for _, v in prov30],
        "dev_labels": [d for d, _ in devices], "dev_values": [v for _, v in devices],
        "ch_labels": [c for c, _ in channels[:5]], "ch_values": [v for _, v in channels[:5]],
    }
    updated = datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК")
    gate = gate_script()

    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>similarcams.com — dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
body{{background:#f1efe8;color:#0b0b0b;padding:20px 24px;max-width:1100px;margin:0 auto;}}
.hdr{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px;}} h1{{font-size:16px;font-weight:600;}}
.meta{{font-size:12px;color:#898781;}} a{{color:#2a78d6;}}
.sec{{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#52514e;margin:26px 0 12px;padding-bottom:7px;border-bottom:1.5px solid #d5d4cd;}}
.g5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:10px;}}
.card{{background:#fff;border-radius:10px;border:.5px solid rgba(11,11,11,.08);padding:14px 16px;margin-bottom:10px;}}
.g5 .card,.g3 .card,.g2 .card{{margin-bottom:0;}}
.lbl{{font-size:11px;color:#898781;margin-bottom:3px;}} .num{{font-size:22px;font-weight:600;}} .num-sm{{font-size:17px;font-weight:600;}}
.delta{{font-size:11px;font-weight:500;margin-top:2px;}} .sub{{font-size:11px;color:#898781;margin-top:2px;}}
.cl{{font-size:11px;color:#898781;margin-bottom:6px;}} .cw{{position:relative;}}
.leg{{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#52514e;margin-bottom:7px;}}
.dot{{width:10px;height:3px;border-radius:2px;display:inline-block;vertical-align:middle;margin-right:3px;}}
.extra{{margin-top:8px;border-top:1px solid #eeeee8;padding-top:7px;font-size:10px;color:#52514e;line-height:1.8;}}
table{{width:100%;border-collapse:collapse;}} th{{padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:right;white-space:nowrap;border-bottom:1.5px solid #d5d4cd;}}
.tdn{{padding:6px 8px 6px 0;font-size:11px;color:#898781;text-align:right;}} .tdr{{padding:5px 8px;font-size:11px;text-align:right;white-space:nowrap;}}
{PT_CSS}
@media(max-width:900px){{.g5{{grid-template-columns:repeat(3,1fr);}}}}
@media(max-width:640px){{.g5,.g3{{grid-template-columns:1fr 1fr;}}.g2{{grid-template-columns:1fr;}}}}
</style>{gate}</head><body>
<div class="hdr"><h1>similarcams.com <span style="font-weight:400;color:#898781;">(Supermetry)</span></h1>
<span class="meta">обновлено: {updated} · <a href="today.html">Сегодня →</a> · <a href="reco.html">RecoDrawer →</a></span></div>

<div class="sec">Вчера — {yl} · последний полный день</div>
<div class="g5">{kpis}</div>

<div class="g3">
  <div class="card"><div class="lbl">ClickRef (provider_clicked)</div><div class="num-sm">{cr_y_ev:,}</div>
    <div class="delta" style="color:{c_cr};">{d_cr}</div><div class="sub">{cr_y_us:,} уникальных · позавчера {cr_p_ev:,}</div>
    <div class="extra">топ: <b>{top_brand}</b> {top_share}% · clicks/user: <b>{cpu}</b></div></div>
  <div class="card"><div class="lbl">CTR ClickRef</div><div class="num-sm">{ctr_y}%</div>
    <div class="delta" style="color:{c_ctr};">{d_ctr}</div><div class="sub">позавчера: {ctr_p}%</div>
    <div class="extra">среднее за период: <b>{ctr_30d_avg}%</b> ({len(active)} дн. данных)</div></div>
  <div class="card"><div class="lbl">RecoDrawer shown</div><div class="num-sm">{r_shown:,}</div>
    <div class="sub">модель: {r_model:,} ({round(r_model / r_shown * 100, 1) if r_shown else 0}%) · провайдер: {r_prov:,} ({round(r_prov / r_shown * 100, 1) if r_shown else 0}%)</div>
    <div class="extra">закрыт: {r_dism:,} ({round(r_dism / r_shown * 100, 1) if r_shown else 0}%)<br><a href="reco.html">подробно: online/offline, причины, A/B →</a></div></div>
</div>

<div class="g2">
  <div class="card"><div class="cl">Топ страны — вчера</div><div style="padding-top:8px;">{countries_html}</div></div>
  <div class="card"><div class="cl">Пик часа вчера (МСК)</div>
    <div style="margin-top:10px;font-size:20px;font-weight:700;color:#2a78d6;">{peak_html}</div>
    <div style="font-size:12px;color:#52514e;">{max(hourly):,} посет.</div>
    <svg viewBox="0 0 {24 * (bw + gap) - gap} {ch}" width="100%" height="{ch}" preserveAspectRatio="none" style="display:block;margin-top:10px;">{hsvg}</svg>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#b0afa8;margin:2px 0 10px;"><span>0h</span><span>6h</span><span>12h</span><span>18h</span><span>23h</span></div>
    {periods_html}</div>
</div>

<div class="g2">
  <div class="card"><div class="cl">ClickRef по брендам — вчера ({sum(v for _, v in brands_y):,})</div><div style="padding-top:6px;">{bars(brands_y, "#2a78d6")}</div></div>
  <div class="card"><div class="cl">ClickRef по местам на странице (surface) — вчера</div><div style="padding-top:6px;">{bars(surfaces_y, "#4a3aa7", width=130)}</div></div>
</div>

<div class="g2">
  <div class="card"><div class="cl">Каналы трафика — вчера</div><div class="cw" style="height:200px;"><canvas id="cch"></canvas></div></div>
  <div class="card"><div class="cl">Устройства — вчера</div><div class="cw" style="height:200px;"><canvas id="cdev"></canvas></div></div>
</div>

{anomalies_html}

<div class="sec">Страницы — {yl} · изменение к позавчера</div>
<div class="card">{pages_html}</div>

<div class="sec">Топ 50 моделей — {yl}</div>
<div class="card" style="overflow-x:auto;"><table style="min-width:520px;"><thead><tr><th>#</th><th style="text-align:left;">Модель</th><th style="text-align:left;">Платформа</th><th>stream</th><th>profile</th><th>stats</th><th style="text-align:left;">30 дней</th></tr></thead><tbody>{models_html}</tbody></table>{dropped_html}</div>

<div class="sec">Топ shorts — {yl}</div>
<div class="card" style="overflow-x:auto;">{"<table><thead><tr><th>#</th><th style='text-align:left;'>Модель</th><th style='text-align:left;'>Пол</th><th>Посет.</th><th style='text-align:left;'>30 дней</th></tr></thead><tbody>" + shorts_html + "</tbody></table>" if shorts_html else "<p style='font-size:12px;color:#898781;'>Нет данных по /shorts/.</p>"}</div>

<div class="sec">Неделя — последние 7 полных дней</div>
<div class="g3">
  <div class="card"><div class="lbl">посетителей</div><div class="num">{vis_7d:,}</div><div class="sub">{round(vis_7d / 7):,}/день</div></div>
  <div class="card"><div class="lbl">визитов</div><div class="num">{visits_7d:,}</div><div class="sub">{round(visits_7d / 7):,}/день</div></div>
  <div class="card"><div class="lbl">ClickRef</div><div class="num">{cr_7d:,}</div><div class="sub">{round(cr_7d / 7):,}/день</div></div>
</div>
<div class="card"><div class="cl">трафик — 7 дней</div><div class="cw" style="height:190px;"><canvas id="c7"></canvas></div></div>

<div class="sec">Месяц — 30 дней <span style="text-transform:none;letter-spacing:0;font-weight:400;color:#898781;">(данные Supermetry с 26.11.2026)</span></div>
<div class="g3">
  <div class="card"><div class="lbl">посетителей</div><div class="num">{vis_30d:,}</div><div class="sub">avg {round(vis_30d / n_active):,}/день с данными</div></div>
  <div class="card"><div class="lbl">визитов</div><div class="num">{visits_30d:,}</div><div class="sub">avg {round(visits_30d / n_active):,}/день</div></div>
  <div class="card"><div class="lbl">ClickRef</div><div class="num">{cr_30d:,}</div><div class="sub">avg {round(cr_30d / n_active):,}/день</div></div>
</div>
<div class="card"><div class="cl">трафик — 30 дней</div>
  <div class="leg"><span><span class="dot" style="background:#2a78d6;"></span>посетители</span><span><span class="dot" style="background:#1baf7a;"></span>визиты</span><span><span class="dot" style="background:#eb6834;"></span>ClickRef (правая ось)</span></div>
  <div class="cw" style="height:200px;"><canvas id="c30"></canvas></div></div>
<div class="g2">
  <div class="card"><div class="cl">CTR ClickRef % — 30 дней</div><div class="cw" style="height:155px;"><canvas id="cctr"></canvas></div></div>
  <div class="card"><div class="cl">стр/визит — 30 дней</div><div class="cw" style="height:155px;"><canvas id="cpps"></canvas></div></div>
</div>
<div class="card"><div class="cl">провайдеры — ClickRef за 30 дней ({sum(chart_data["prov_values"]):,})</div><div class="cw" style="height:{max(200, len(prov30) * 34 + 40)}px;"><canvas id="cprov"></canvas></div></div>

<div class="sec">Ключевые выводы — {yl}</div>{"".join(card(*f) for f in findings)}
<div class="sec">Гипотезы и рекомендации</div>{"".join(card(*h) for h in hypos) or "<p style='font-size:12px;color:#898781;'>Нет гипотез.</p>"}

<div class="sec">История наблюдений</div>
<div class="card" style="overflow-x:auto;">{sparks}{pinned}
<table style="min-width:700px;"><thead><tr><th style="text-align:left;">Дата</th><th>Посет.</th><th>Визиты</th><th>Стр/в</th><th>Отказ%</th><th>CTR%</th><th>CR ev.</th><th>CR uq.</th><th>Reco</th><th style="text-align:left;">Заметка</th></tr></thead><tbody>{hist_rows}</tbody></table></div>
<div style="margin-top:8px;font-size:11px;color:#898781;line-height:1.6;">💡 Заметка к дню — поле <code>"note"</code> в <code>dashboard_memory.json</code>. Закрепить событие — добавь в <code>"pinned"</code>: <code>{{"date": "27-11", "text": "Запущен A/B", "color": "#2a78d6"}}</code></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>const D = {json.dumps(chart_data, ensure_ascii=False)};</script>
<script>{CHART_JS}</script>
<script>{PT_JS}</script>
</body></html>"""
    os.makedirs(PUBLIC, exist_ok=True)
    with open(os.path.join(PUBLIC, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ public/index.html  visitors yesterday={vis_y}, ClickRef={cr_y_ev}, pages={len(pages_y)}")


def gate_script():
    if not PASSWORD:
        return ""
    return """<script>
(function(){var P=%s,K='cb_auth';if(sessionStorage.getItem(K)==='1')return;
var o=document.createElement('div');o.id='auth-overlay';
o.style.cssText='position:fixed;inset:0;background:#f5f4ef;display:flex;align-items:center;justify-content:center;z-index:9999;';
o.innerHTML='<div style="background:#fff;border-radius:14px;padding:32px 28px;width:300px;text-align:center;"><div style="font-size:18px;font-weight:600;margin-bottom:16px;">similarcams dashboard</div><input id="auth-pwd" type="password" placeholder="Пароль" style="width:100%%;padding:10px;border:1px solid #d5d4cd;border-radius:8px;margin-bottom:10px;"><div id="auth-err" style="font-size:11px;color:#e24b4a;min-height:16px;"></div><button onclick="__ck()" style="width:100%%;padding:10px;background:#0b0b0b;color:#fff;border:none;border-radius:8px;">Войти</button></div>';
document.addEventListener('DOMContentLoaded',function(){document.body.appendChild(o);});
window.__ck=function(){if(document.getElementById('auth-pwd').value===P){sessionStorage.setItem(K,'1');o.remove();}else{document.getElementById('auth-err').textContent='Неверный пароль';}};
document.addEventListener('keydown',function(e){if(e.key==='Enter')__ck();});})();
</script>""" % json.dumps(PASSWORD)


CHART_JS = r"""
const grd='#e1e0d9',tk='#898781';
const base={responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}}};
const ya=(a,b)=>({grid:{color:grd,lineWidth:.5},ticks:{color:tk,font:{size:11},maxTicksLimit:5},min:a,max:b,border:{display:false}});
const xa={grid:{display:false},ticks:{color:tk,font:{size:10},maxTicksLimit:10,maxRotation:0}};
const mx=a=>Math.max(...a,1);
const pick=(a,idx)=>idx.map(i=>a[i]);
const donut=(id,labels,values,colors)=>new Chart(document.getElementById(id),{type:'doughnut',data:{labels,datasets:[{data:values,backgroundColor:colors,borderWidth:2,borderColor:'#fff'}]},
  options:{...base,cutout:'60%',plugins:{legend:{display:true,position:'bottom',labels:{font:{size:11},color:tk,boxWidth:10,padding:6}},tooltip:{mode:'point',intersect:true}}}});
donut('cch',D.ch_labels,D.ch_values,['#2a78d6','#1baf7a','#eb6834','#eda100','#888780']);
donut('cdev',D.dev_labels,D.dev_values,['#2a78d6','#eb6834','#888780','#eda100']);

const w7l=pick(D.labels30,D.w_idx),w7v=pick(D.vis30,D.w_idx),w7s=pick(D.visits30,D.w_idx);
new Chart(document.getElementById('c7'),{type:'line',data:{labels:w7l,datasets:[
  {label:'посетители',data:w7v,borderColor:'#2a78d6',backgroundColor:'#2a78d612',fill:true,borderWidth:2,pointRadius:4,tension:.25},
  {label:'визиты',data:w7s,borderColor:'#1baf7a',borderWidth:1.5,pointRadius:3,tension:.25}]},
  options:{...base,scales:{x:xa,y:ya(0,mx(w7s)*1.12)}}});

new Chart(document.getElementById('c30'),{type:'line',data:{labels:D.labels30,datasets:[
  {label:'посетители',data:D.vis30,borderColor:'#2a78d6',backgroundColor:'#2a78d610',fill:true,borderWidth:2,pointRadius:0,tension:.3},
  {label:'визиты',data:D.visits30,borderColor:'#1baf7a',borderWidth:1.5,pointRadius:0,tension:.3},
  {label:'ClickRef',data:D.cr30,borderColor:'#eb6834',borderDash:[3,2],borderWidth:1.5,pointRadius:0,tension:.3,yAxisID:'y2'}]},
  options:{...base,scales:{x:xa,y:{...ya(0,mx(D.visits30)*1.08),position:'left'},y2:{...ya(0,mx(D.cr30)*1.15),position:'right',grid:{display:false}}}}});

new Chart(document.getElementById('cctr'),{type:'line',data:{labels:D.labels30,datasets:[
  {label:'CTR %',data:D.ctr30,borderColor:'#4a3aa7',backgroundColor:'#4a3aa712',fill:true,borderWidth:2,pointRadius:0,tension:.3}]},
  options:{...base,scales:{x:xa,y:ya(0,mx(D.ctr30)*1.1)}}});
new Chart(document.getElementById('cpps'),{type:'line',data:{labels:D.labels30,datasets:[
  {label:'стр/визит',data:D.pps30,borderColor:'#eda100',backgroundColor:'#eda10012',fill:true,borderWidth:2,pointRadius:0,tension:.3}]},
  options:{...base,scales:{x:xa,y:ya(0,mx(D.pps30)*1.1)}}});
new Chart(document.getElementById('cprov'),{type:'bar',data:{labels:D.prov_labels,datasets:[{data:D.prov_values,
  backgroundColor:['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#4a3aa7','#888780','#b0aead'],borderRadius:3}]},
  options:{...base,indexAxis:'y',scales:{x:ya(0,mx(D.prov_values)*1.15),y:{grid:{display:false},ticks:{color:tk,font:{size:11}}}}}});
"""

if __name__ == "__main__":
    main()
