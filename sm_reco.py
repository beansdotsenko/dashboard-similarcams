#!/usr/bin/env python3
"""
RecoDrawer dashboard from Supermetry -> public/reco.html

Env vars (GitHub Actions secrets):
  SUPERMETRY_URL       https://stat.similarcams.com
  SUPERMETRY_KEY       API key, scope "Read analytics"
  SUPERMETRY_SITE_ID   1
  DASHBOARD_PASSWORD   optional, same password gate as index.html

Writes:
  public/reco.html          the page
  public/supermetry_reco.json raw numbers (for debugging)
  supermetry_memory.json    daily history (kept 90 days)
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.environ["SUPERMETRY_URL"].strip().rstrip("/") + "/api/public/v1"
KEY = os.environ["SUPERMETRY_KEY"].strip()
SITE = os.environ.get("SUPERMETRY_SITE_ID", "").strip()
if not SITE.isdigit():
    sys.exit(f"SUPERMETRY_SITE_ID must be a number like 1, got {SITE!r} (length {len(SITE)})")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
TZ = ZoneInfo("Europe/Moscow")  # site timezone in Supermetry

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
MEMORY_FILE = os.path.join(ROOT, "supermetry_memory.json")

EV = {
    "shown": "reco_drawer_shown",
    "dism": "reco_drawer_dismissed",
    "suppr": "reco_drawer_suppressed",
    "all": "reco_drawer_all_clicked",
    "prov": "reco_drawer_provider_clicked",
    "model": "reco_drawer_model_clicked",
}
METRIC_KEYS = {"events", "visits", "visitors"}

SUPPR_LABELS = {
    "session_cap": ("лимит сессии", "#e24b4a"),
    "min_gap": ("cooldown", "#e24b4a"),
    "weekly_cap": ("недельный лимит", "#e24b4a"),
    "no_providers": ("нет провайдеров", "#eb6834"),
    "converted": ("уже перешёл", "#1baf7a"),
    "quick_dismiss": ("быстро закрыл", "#888780"),
    "bot": ("бот", "#888780"),
}


# ── API ──────────────────────────────────────────────────────────────────
def breakdown(dimension, filters, d_from, d_to, metrics=("events", "visits")):
    params = [
        ("site_id", SITE), ("period", "custom"),
        ("date_from", d_from.isoformat()), ("date_to", d_to.isoformat()),
        ("dimension", dimension), ("metrics", ",".join(metrics)), ("limit", "100"),
    ] + [("filter", f) for f in filters]
    url = f"{BASE}/stats/breakdown?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {KEY}",
        "Accept": "application/json",
        # Cloudflare blocks the default "Python-urllib" user agent (error 1010)
        "User-Agent": "Mozilla/5.0 (compatible; similarcams-dashboard/1.0; +https://github.com)",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"  ⚠ {e.code} {dimension} {filters}: {e.read().decode(errors='replace')[:300]}")
        return {}
    rows = data.get("rows") if isinstance(data, dict) else data
    out = {}
    for row in rows or []:
        label = next((k for k in row if k not in METRIC_KEYS), None)
        if label is not None:
            out[str(row[label])] = {m: int(row.get(m) or 0) for m in METRIC_KEYS if m in row}
    return out


RCR = ["event:is:provider_clicked", "prop:surface:is:provider_recommendation_drawer"]   # = Finteza RecoClickRef


def ev_counts(d_from, d_to, extra=()):
    """{key: events} for the six drawer events + rcr (outbound affiliate click from the drawer)."""
    m = breakdown("event", ["event:contains:reco_drawer", *extra], d_from, d_to)
    out = {k: m.get(name, {}).get("events", 0) for k, name in EV.items()}
    r = breakdown("event", [*RCR, *extra], d_from, d_to)
    out["rcr"] = r.get("provider_clicked", {}).get("events", 0)
    return out


def by_prop(prop, event, d_from, d_to):
    m = breakdown(f"prop:{prop}", [f"event:is:{event}"], d_from, d_to)
    return sorted(((k, v.get("events", 0)) for k, v in m.items() if k.strip() not in ("", "None", "null", "(none)", "<nil>")), key=lambda x: -x[1])


def period_block(d_from, d_to, prev_from, prev_to):
    allc = ev_counts(d_from, d_to)
    on = ev_counts(d_from, d_to, ["prop:category:is:online"])
    off = ev_counts(d_from, d_to, ["prop:category:is:offline"])
    prev = ev_counts(prev_from, prev_to)
    brands = by_prop("provider_slug", EV["prov"], d_from, d_to) or by_prop("provider", EV["prov"], d_from, d_to)
    brands_model = by_prop("provider_slug", EV["model"], d_from, d_to) or by_prop("provider", EV["model"], d_from, d_to)
    reasons = by_prop("reason", EV["suppr"], d_from, d_to)
    m = breakdown("prop:provider_slug", RCR, d_from, d_to)
    brands_rcr = sorted(((k, v.get("events", 0)) for k, v in m.items()
                         if k.strip() not in ("", "None", "null", "<nil>")), key=lambda x: -x[1])
    ab = {}
    for k in ("shown", "model", "prov", "dism"):
        for bucket, n in by_prop("ab_bucket", EV[k], d_from, d_to):
            ab.setdefault(bucket, {})[k] = n
    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "all": allc, "online": on,
            "offline": off, "prev": prev, "brands": brands, "brands_model": brands_model,
            "reasons": reasons, "ab": ab, "brands_rcr": brands_rcr}


# ── memory ───────────────────────────────────────────────────────────────
def load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"days": {}}


def update_memory(mem, today):
    """Fill every missing full day of the last 30 (Supermetry keeps 30 days raw)."""
    for i in range(1, 31):
        d = today - timedelta(days=i)
        iso = d.isoformat()
        if iso in mem["days"] and "rcr" in mem["days"][iso] and i > 2:   # always refresh the last 2 days
            continue
        mem["days"][iso] = ev_counts(d, d)
    keep = sorted(mem["days"])[-90:]
    mem["days"] = {d: mem["days"][d] for d in keep}
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)


# ── html helpers ─────────────────────────────────────────────────────────
def pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def delta_ui(a, b):
    if not b:
        return "—", "#898781"
    d = round((a - b) / b * 100, 1)
    if abs(d) < 0.05:
        return "— 0%", "#898781"
    return (f"↑ +{d:.1f}%", "#1baf7a") if d > 0 else (f"↓ {d:.1f}%", "#e24b4a")


def bars(rows, color, labels=None, max_rows=10):
    if not rows:
        return '<span style="font-size:11px;color:#898781;">нет данных</span>'
    top = rows[0][1] or 1
    total = sum(v for _, v in rows) or 1
    html = ""
    for name, v in rows[:max_rows]:
        lab, col = (labels or {}).get(name, (name, color))
        html += (
            f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px;">'
            f'<span style="width:96px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0;" title="{name}">{lab}</span>'
            f'<div style="flex:1;background:#f0efe8;border-radius:3px;height:6px;">'
            f'<div style="width:{round(v / top * 100)}%;height:100%;background:{col};border-radius:3px;"></div></div>'
            f'<span style="font-size:11px;font-weight:600;width:40px;text-align:right;">{v:,}</span>'
            f'<span style="font-size:10px;color:#898781;width:30px;text-align:right;">{round(v / total * 100)}%</span></div>'
        )
    return html


def reco_card(title, blk):
    a, on, off, prev = blk["all"], blk["online"], blk["offline"], blk["prev"]
    d_lbl, d_col = delta_ui(a["shown"], prev["shown"])

    def row(label, key, rate=False, bold=False):
        cells = ""
        for col in (on, off):
            n = col[key]
            txt = f"<b>{n:,}</b>" if bold else f"{n:,}"
            if rate:
                txt += f' <span style="color:#1baf7a;font-size:10px;">({pct(n, col["shown"])}%)</span>'
            cells += f'<td style="padding:4px 6px;text-align:right;">{txt}</td>'
        return (f'<tr style="border-top:.5px solid #eeeee8;"><td style="padding:4px 6px 4px 0;color:#898781;">{label}</td>'
                f'{cells}</tr>')

    return f"""<div class="card">
  <div class="lbl">RecoDrawer shown · {title}</div>
  <div class="num-sm">{a["shown"]:,}</div>
  <div class="delta" style="color:{d_col};">{d_lbl} <span style="color:#898781;font-weight:400;">к пред. периоду ({prev["shown"]:,})</span></div>
  <div class="sub">dismissed: {a["dism"]:,} ({pct(a["dism"], a["shown"])}%) · all clicked: {a["all"]:,} ({pct(a["all"], a["shown"])}%)</div>
  <div class="sub">💰 RecoClickRef: <b style="color:#eda100;">{a["rcr"]:,}</b> ({pct(a["rcr"], a["shown"])}% от shown · {pct(a["rcr"], a["prov"])}% от Watch on)</div>
  <div style="margin-top:8px;border-top:1px solid #eeeee8;padding-top:7px;">
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead><tr>
        <th style="width:40%;"></th>
        <th style="text-align:right;padding:0 6px 4px;color:#1baf7a;font-size:10px;">online</th>
        <th style="text-align:right;padding:0 6px 4px;color:#898781;font-size:10px;">offline</th>
      </tr></thead>
      <tbody>
        {row("shown", "shown", bold=True)}
        {row("dism", "dism", rate=True)}
        {row("suppr", "suppr")}
        {row("▶ Watch on", "prov", rate=True, bold=True)}
        {row("👤 модель", "model", rate=True, bold=True)}
        {row("💰 RecoClickRef", "rcr", rate=True, bold=True) if (on["rcr"] + off["rcr"]) else ""}
      </tbody>
    </table>
  </div>
</div>"""


def ab_table(ab):
    if not ab:
        return '<span style="font-size:11px;color:#898781;">нет данных</span>'
    rows = ""
    for bucket in sorted(ab):
        v = ab[bucket]
        sh = v.get("shown", 0)
        rows += (f'<tr style="border-top:.5px solid #eeeee8;"><td style="padding:5px 6px 5px 0;font-weight:600;">{bucket}</td>'
                 f'<td style="padding:5px 6px;text-align:right;">{sh:,}</td>'
                 f'<td style="padding:5px 6px;text-align:right;">{v.get("model", 0):,} <span style="color:#1baf7a;font-size:10px;">({pct(v.get("model", 0), sh)}%)</span></td>'
                 f'<td style="padding:5px 6px;text-align:right;">{v.get("prov", 0):,} <span style="color:#1baf7a;font-size:10px;">({pct(v.get("prov", 0), sh)}%)</span></td>'
                 f'<td style="padding:5px 0 5px 6px;text-align:right;">{v.get("dism", 0):,} <span style="color:#898781;font-size:10px;">({pct(v.get("dism", 0), sh)}%)</span></td></tr>')
    th = 'style="text-align:right;padding:0 6px 4px;color:#898781;font-size:10px;"'
    return (f'<table style="width:100%;border-collapse:collapse;font-size:11px;"><thead><tr>'
            f'<th style="text-align:left;color:#898781;font-size:10px;">вариант</th><th {th}>shown</th><th {th}>модель</th>'
            f'<th {th}>Watch on</th><th {th}>dism</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div style="font-size:10px;color:#898781;margin-top:6px;">если у кликов нет свойства ab_bucket, колонки кликов будут 0</div>')


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


def write_html(yest, week, mem):
    days = sorted(d for d, v in mem["days"].items() if any(v.values()))
    h = {k: [mem["days"][d].get(k, 0) for d in days] for k in list(EV) + ["rcr"]}
    labels = [d[5:] for d in days]
    y = yest["all"]
    funnel_vals = [y["shown"], y["suppr"], y["dism"], y["all"], y["prov"], y["model"], y["rcr"]]
    updated = datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК")

    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>similarcams — RecoDrawer</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
body{{background:#f1efe8;color:#0b0b0b;padding:20px 24px;max-width:1100px;margin:0 auto;}}
.hdr{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px;}}
h1{{font-size:16px;font-weight:600;}} .meta{{font-size:12px;color:#898781;}}
.sec{{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#52514e;margin:26px 0 12px;padding-bottom:7px;border-bottom:1.5px solid #d5d4cd;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:10px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:10px;}}
@media(max-width:900px){{.g3{{grid-template-columns:1fr;}}}}
.card{{background:#fff;border-radius:10px;border:.5px solid rgba(11,11,11,.08);padding:14px 16px;margin-bottom:10px;}}
.lbl{{font-size:11px;color:#898781;margin-bottom:3px;}} .num-sm{{font-size:17px;font-weight:600;}}
.delta{{font-size:11px;font-weight:500;margin-top:2px;}} .sub{{font-size:11px;color:#898781;margin-top:2px;}}
.cl{{font-size:11px;color:#898781;margin-bottom:6px;}} .cw{{position:relative;}}
.leg{{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#52514e;margin-bottom:7px;}}
.dot{{width:10px;height:3px;border-radius:2px;display:inline-block;vertical-align:middle;margin-right:3px;}}
@media(max-width:640px){{.g2{{grid-template-columns:1fr;}}}}
</style>{gate_script()}</head>
<body>
<div class="hdr"><h1>similarcams.com · RecoDrawer <span style="font-weight:400;color:#898781;">(Supermetry)</span></h1>
<span class="meta">обновлено: {updated} · <a href="index.html" style="color:#2a78d6;">основной дашборд</a></span></div>

<div class="sec">Вчера ({yest["from"]}) · 7 дней ({week["from"]} – {week["to"]})</div>
<div class="g2">{reco_card("вчера", yest)}{reco_card("7 дней", week)}</div>

<div class="g2">
  <div class="card"><div class="cl">RecoDrawer — воронка вчера (события)</div>
    <div class="cw" style="height:240px;"><canvas id="cf"></canvas></div></div>
  <div>
    <div class="card"><div class="cl">suppressed — причины вчера ({y["suppr"]:,})</div>
      <div style="padding-top:8px;">{bars(yest["reasons"], "#e24b4a", SUPPR_LABELS)}</div></div>
    <div class="card"><div class="cl">▶ Watch on — вчера ({y["prov"]:,})</div>
      <div style="padding-top:8px;">{bars(yest["brands"], "#eda100")}</div></div>
  </div>
</div>

<div class="g2">
  <div class="card"><div class="cl">💰 RecoClickRef по брендам — вчера ({y["rcr"]:,} кликов)</div>
    <div style="padding-top:8px;">{bars(yest["brands_rcr"], "#c27c0e")}</div></div>
  <div class="card"><div class="cl">💰 RecoClickRef по брендам — 7 дней ({week["all"]["rcr"]:,} кликов)</div>
    <div style="padding-top:8px;">{bars(week["brands_rcr"], "#c27c0e")}</div></div>
</div>

<div class="g2">
  <div class="card"><div class="cl">👤 клики по модели по брендам — 7 дней ({week["all"]["model"]:,})</div>
    <div style="padding-top:8px;">{bars(week["brands_model"], "#4a90d9")}</div></div>
  <div class="card"><div class="cl">▶ Watch on по брендам — 7 дней ({week["all"]["prov"]:,})</div>
    <div style="padding-top:8px;">{bars(week["brands"], "#eda100")}</div></div>
</div>

<div class="card"><div class="cl">A/B (ab_bucket) — 7 дней</div>{ab_table(week["ab"])}</div>

<div class="sec">История по дням</div>
<div class="card"><div class="cl">Охват и закрытия</div>
  <div class="leg"><span><span class="dot" style="background:#2a78d6;"></span>shown</span>
  <span><span class="dot" style="background:#e24b4a;"></span>dismissed</span>
  <span style="font-size:10px;color:#c0bfba;">— — dism % (правая ось)</span></div>
  <div class="cw" style="height:200px;"><canvas id="ch1"></canvas></div></div>
<div class="card"><div class="cl">Клики из drawer — кол-во (левая) + % от shown (правая, пунктир)</div>
  <div class="leg"><span><span class="dot" style="background:#1baf7a;"></span>all clicked</span>
  <span><span class="dot" style="background:#eda100;"></span>Watch on</span>
  <span><span class="dot" style="background:#4a90d9;"></span>model clicked</span>
  <span><span class="dot" style="background:#c27c0e;"></span>RecoClickRef</span></div>
  <div class="cw" style="height:200px;"><canvas id="ch2"></canvas></div></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const grd='#e1e0d9',tk='#898781';
const b={{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false}}}}}};
const xa={{grid:{{display:false}},ticks:{{color:tk,font:{{size:10}},maxTicksLimit:10,maxRotation:0}}}};
const ya=(mx)=>({{grid:{{color:grd,lineWidth:.5}},ticks:{{color:tk,font:{{size:11}},maxTicksLimit:5}},min:0,max:mx,border:{{display:false}}}});
const fv={json.dumps(funnel_vals)};
new Chart(document.getElementById('cf'),{{type:'bar',data:{{labels:['shown','suppressed','dismissed','all clicked','Watch on','model clicked','RecoClickRef'],
  datasets:[{{data:fv,backgroundColor:['#2a78d6','#888780','#eb6834','#1baf7a','#eda100','#4a90d9','#c27c0e'],borderRadius:3}}]}},
  options:{{...b,indexAxis:'y',scales:{{x:ya(Math.max(...fv,1)*1.08),y:{{grid:{{display:false}},ticks:{{color:tk,font:{{size:11}}}}}}}}}}}});
const L={json.dumps(labels)},S={json.dumps(h["shown"])},D={json.dumps(h["dism"])},A={json.dumps(h["all"])},P={json.dumps(h["prov"])},M={json.dumps(h["model"])},R={json.dumps(h["rcr"])};
const pc=a=>a.map((v,i)=>S[i]?Math.round(v/S[i]*1000)/10:0);
new Chart(document.getElementById('ch1'),{{type:'line',data:{{labels:L,datasets:[
  {{label:'shown',data:S,borderColor:'#2a78d6',backgroundColor:'#2a78d610',fill:true,borderWidth:2,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'dismissed',data:D,borderColor:'#e24b4a',borderWidth:1.5,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'dism %',data:pc(D),borderColor:'#e24b4a',borderDash:[4,3],borderWidth:1,pointRadius:0,tension:.3,yAxisID:'y2'}}]}},
  options:{{...b,scales:{{x:xa,y:{{...ya(Math.max(...S,1)*1.1),position:'left'}},y2:{{...ya(100),position:'right',grid:{{display:false}},ticks:{{color:'#c0bfba',callback:v=>v+'%'}}}}}}}}}});
new Chart(document.getElementById('ch2'),{{type:'line',data:{{labels:L,datasets:[
  {{label:'all clicked',data:A,borderColor:'#1baf7a',borderWidth:2,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'Watch on',data:P,borderColor:'#eda100',borderWidth:1.5,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'model clicked',data:M,borderColor:'#4a90d9',borderWidth:1.5,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'RecoClickRef',data:R,borderColor:'#c27c0e',borderWidth:2,pointRadius:2,tension:.3,yAxisID:'y'}},
  {{label:'all %',data:pc(A),borderColor:'#1baf7a',borderDash:[4,3],borderWidth:1,pointRadius:0,tension:.3,yAxisID:'y2'}},
  {{label:'prov %',data:pc(P),borderColor:'#eda100',borderDash:[4,3],borderWidth:1,pointRadius:0,tension:.3,yAxisID:'y2'}},
  {{label:'model %',data:pc(M),borderColor:'#4a90d9',borderDash:[4,3],borderWidth:1,pointRadius:0,tension:.3,yAxisID:'y2'}}]}},
  options:{{...b,scales:{{x:xa,y:{{...ya(Math.max(...A,...P,...M,...R,1)*1.2),position:'left'}},
  y2:{{...ya(Math.ceil(Math.max(...pc(A),...pc(P),...pc(M),1)*1.3)),position:'right',grid:{{display:false}},ticks:{{color:'#c0bfba',callback:v=>v+'%'}}}}}}}}}});
</script></body></html>"""
    os.makedirs(PUBLIC, exist_ok=True)
    with open(os.path.join(PUBLIC, "reco.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    today = datetime.now(TZ).date()
    yd = today - timedelta(days=1)
    print(f"Today {today}, yesterday {yd}")

    yest = period_block(yd, yd, yd - timedelta(days=1), yd - timedelta(days=1))
    w_from = today - timedelta(days=7)
    week = period_block(w_from, yd, w_from - timedelta(days=7), w_from - timedelta(days=1))

    if not (yest["all"]["shown"] or week["all"]["shown"]):
        sys.exit("No reco_drawer_shown events returned — check key, site id and event names.")

    mem = load_memory()
    update_memory(mem, today)

    os.makedirs(PUBLIC, exist_ok=True)
    with open(os.path.join(PUBLIC, "supermetry_reco.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "yesterday": yest, "week": week}, f, indent=2, ensure_ascii=False)
    write_html(yest, week, mem)
    print(f"✓ public/reco.html  shown yesterday={yest['all']['shown']}, 7d={week['all']['shown']}")


if __name__ == "__main__":
    main()
