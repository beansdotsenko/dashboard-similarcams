#!/usr/bin/env python3
"""
"Сегодня" dashboard (hourly) from Supermetry -> public/today.html

Reuses helpers from supermetry_dashboard.py (same folder, same env vars).
Compares today with yesterday up to the same hour (complete hours only).
Does not touch dashboard_memory.json.
"""
import json
import os
from datetime import datetime, timedelta

from supermetry_dashboard import (CLICK_EVENT, EXCLUDE_PAGES, PUBLIC, TZ, bars, breakdown, delta_ui, flag,
                                  gate_script, num, parse_model_url, summary, timeseries)


def hourly(day, metrics, filters=()):
    """24 dicts (hour 0..23) of the requested metrics."""
    out = [{m: 0 for m in metrics.split(",")} for _ in range(24)]
    for r in timeseries(day, day, "hour", metrics, filters):
        try:
            h = int(str(r["time"])[11:13])
        except (ValueError, KeyError):
            continue
        out[h] = {m: int(num(r.get(m))) for m in metrics.split(",")}
    return out


def main():
    now = datetime.now(TZ)
    today, yd = now.date(), now.date() - timedelta(days=1)
    H = now.hour                     # hours 0..H-1 are complete
    clk = [f"event:is:{CLICK_EVENT}"]
    print(f"Today {today}, complete hours: {H}")

    st, sy = summary(today, today), summary(yd, yd)
    vis_t, vis_y = int(num(st.get("visitors"))), int(num(sy.get("visitors")))

    ht, hy = hourly(today, "visitors,visits,pageviews"), hourly(yd, "visitors,visits,pageviews")
    ct, cy = hourly(today, "events", clk), hourly(yd, "events", clk)
    s = lambda arr, m, n=24: sum(x[m] for x in arr[:n])

    visits_t, pv_t, cr_t = s(ht, "visits"), s(ht, "pageviews"), s(ct, "events")
    # same-hour comparison (complete hours only)
    visits_th, visits_yh = s(ht, "visits", H), s(hy, "visits", H)
    pv_th, pv_yh = s(ht, "pageviews", H), s(hy, "pageviews", H)
    cr_th, cr_yh = s(ct, "events", H), s(cy, "events", H)
    pps_t = pv_th / visits_th if visits_th else 0
    pps_y = pv_yh / visits_yh if visits_yh else 0

    cr_rows = breakdown("event", today, today, "events,visitors", clk, 5)
    cr_us_t = int(num(cr_rows[0]["visitors"])) if cr_rows else 0
    ctr_t = round(cr_us_t / vis_t * 100, 1) if vis_t else 0.0
    cr_rows_y = breakdown("event", yd, yd, "events,visitors", clk, 5)
    ctr_y = round(int(num(cr_rows_y[0]["visitors"])) / vis_y * 100, 1) if (cr_rows_y and vis_y) else 0.0

    brands = [(r["prop:provider_slug"], int(num(r["events"]))) for r in breakdown("prop:provider_slug", today, today, "events", clk, 20)]
    surfaces = [(r["prop:surface"], int(num(r["events"]))) for r in breakdown("prop:surface", today, today, "events", clk, 20)]
    reco = {r["event"]: int(num(r["events"])) for r in breakdown("event", today, today, "events", ["event:contains:reco_drawer"], 20)}
    countries = [(r["country"], int(num(r["visitors"]))) for r in breakdown("country", today, today, "visitors", limit=10)]
    channels = [(r["channel"], int(num(r["visitors"]))) for r in breakdown("channel", today, today, "visitors", limit=6)]
    devices = [(r["device"], int(num(r["visitors"]))) for r in breakdown("device", today, today, "visitors", limit=5)]
    pages = breakdown("page", today, today, "visitors,visits,pageviews", limit=1000)

    # models today
    models = {}
    for r in pages:
        pm = parse_model_url(r["page"])
        if pm and pm["suffix"] != "shorts":
            k = (pm["platform"], pm["model"])
            models.setdefault(k, {"platform": pm["platform"], "gender": pm["gender"], "model": pm["model"], "vis": 0})
            models[k]["vis"] += int(num(r.get("visitors")))
    top_models = sorted(models.values(), key=lambda m: -m["vis"])[:20]
    top_pages = sorted((r for r in pages if not r["page"].startswith(EXCLUDE_PAGES)), key=lambda r: -num(r.get("visitors")))[:20]

    # ── html ──
    cmp_lbl = f"вчера к {H:02d}:00" if H else "вчера"

    def kpi(label, val, cur, prev, sub, good="up"):
        d, c = delta_ui(cur, prev, good) if H else ("—", "#898781")
        return (f'<div class="card"><div class="lbl">{label}</div><div class="num">{val}</div>'
                f'<div class="delta" style="color:{c};">{d} <span style="color:#898781;font-weight:400;">к {cmp_lbl}</span></div>'
                f'<div class="sub">{sub}</div></div>')

    kpis = (f'<div class="card"><div class="lbl">посетители</div><div class="num">{vis_t:,}</div>'
            f'<div class="sub">вчера за весь день: {vis_y:,}</div></div>'
            + kpi("визиты", f"{visits_t:,}", visits_th, visits_yh, f"{cmp_lbl}: {visits_yh:,}")
            + kpi("просмотры", f"{pv_t:,}", pv_th, pv_yh, f"{cmp_lbl}: {pv_yh:,}")
            + kpi("стр / визит", f"{pps_t:.2f}", pps_t, pps_y, f"{cmp_lbl}: {pps_y:.2f}")
            + kpi("ClickRef", f"{cr_t:,}", cr_th, cr_yh, f"{cmp_lbl}: {cr_yh:,} · {cr_us_t:,} уник."))

    sh = reco.get("reco_drawer_shown", 0)
    pc = lambda k: f'{reco.get(k, 0):,} ({round(reco.get(k, 0) / sh * 100, 1) if sh else 0}%)'
    reco_html = (f'<div class="card"><div class="lbl">RecoDrawer shown</div><div class="num-sm">{sh:,}</div>'
                 f'<div class="sub">модель: {pc("reco_drawer_model_clicked")} · провайдер: {pc("reco_drawer_provider_clicked")}</div>'
                 f'<div class="sub">закрыт: {pc("reco_drawer_dismissed")} · all sites: {pc("reco_drawer_all_clicked")}</div>'
                 f'<div class="sub"><a href="reco.html">подробно →</a></div></div>')
    ctr_d, ctr_c = delta_ui(ctr_t, ctr_y)
    ctr_html = (f'<div class="card"><div class="lbl">CTR ClickRef</div><div class="num-sm">{ctr_t}%</div>'
                f'<div class="delta" style="color:{ctr_c};">{ctr_d} <span style="color:#898781;font-weight:400;">к вчера (весь день {ctr_y}%)</span></div>'
                f'<div class="sub">уникальные кликнувшие / посетители</div></div>')

    countries_html = "".join(
        f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:11px;"><span><span style="font-size:15px;">{flag(c)}</span> {c}</span>'
        f'<span style="font-weight:600;">{v:,} <span style="color:#898781;font-weight:400;">({round(v / vis_t * 100) if vis_t else 0}%)</span></span></div>'
        for c, v in countries) or '<span style="font-size:11px;color:#898781;">нет данных</span>'

    pages_html = "".join(
        f'<tr style="border-bottom:.5px solid #eeeee8;"><td class="tdn">{i}</td><td style="padding:6px 8px;font-size:11px;word-break:break-all;">{r["page"]}</td>'
        f'<td class="tdr" style="font-weight:600;">{int(num(r.get("visitors"))):,}</td>'
        f'<td class="tdr" style="color:#898781;">{round(num(r.get("visitors")) / vis_t * 100, 1) if vis_t else 0}%</td>'
        f'<td class="tdr" style="color:#898781;">{(num(r.get("pageviews")) / num(r.get("visits"))) if num(r.get("visits")) else 0:.1f}</td></tr>'
        for i, r in enumerate(top_pages, 1))
    models_html = "".join(
        f'<tr style="border-bottom:.5px solid #eeeee8;"><td class="tdn">{i}</td><td style="padding:5px 8px 5px 0;font-size:11px;">{m["model"]}</td>'
        f'<td style="padding:5px 8px;font-size:10px;"><b>{m["platform"]}</b> <span style="color:#898781;">{m["gender"]}</span></td>'
        f'<td class="tdr" style="font-weight:600;">{m["vis"]:,}</td></tr>' for i, m in enumerate(top_models, 1))

    data = {"hours": [f"{h:02d}" for h in range(24)], "H": H,
            "vis_t": [x["visits"] for x in ht][:H + 1], "vis_y": [x["visits"] for x in hy],
            "cr_t": [x["events"] for x in ct][:H + 1], "cr_y": [x["events"] for x in cy],
            "ch_labels": [c for c, _ in channels], "ch_values": [v for _, v in channels],
            "dev_labels": [d for d, _ in devices], "dev_values": [v for _, v in devices]}
    updated = now.strftime("%d.%m.%Y %H:%M МСК")

    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><meta http-equiv="refresh" content="900"><title>similarcams — сегодня</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
body{{background:#f1efe8;color:#0b0b0b;padding:20px 24px;max-width:1100px;margin:0 auto;}}
.hdr{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px;}} h1{{font-size:16px;font-weight:600;}}
.meta{{font-size:12px;color:#898781;}} a{{color:#2a78d6;}}
.sec{{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#52514e;margin:26px 0 12px;padding-bottom:7px;border-bottom:1.5px solid #d5d4cd;}}
.g5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:10px;}}
.card{{background:#fff;border-radius:10px;border:.5px solid rgba(11,11,11,.08);padding:14px 16px;margin-bottom:10px;}}
.g5 .card,.g2 .card{{margin-bottom:0;}}
.lbl{{font-size:11px;color:#898781;margin-bottom:3px;}} .num{{font-size:22px;font-weight:600;}} .num-sm{{font-size:17px;font-weight:600;}}
.delta{{font-size:11px;font-weight:500;margin-top:2px;}} .sub{{font-size:11px;color:#898781;margin-top:3px;}}
.cl{{font-size:11px;color:#898781;margin-bottom:6px;}} .cw{{position:relative;}}
.leg{{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#52514e;margin-bottom:7px;}}
.dot{{width:10px;height:3px;border-radius:2px;display:inline-block;vertical-align:middle;margin-right:3px;}}
table{{width:100%;border-collapse:collapse;}} th{{padding:0 8px 7px;font-size:10px;font-weight:700;color:#898781;text-align:right;border-bottom:1.5px solid #d5d4cd;}}
.tdn{{padding:6px 8px 6px 0;font-size:11px;color:#898781;text-align:right;}} .tdr{{padding:5px 8px;font-size:11px;text-align:right;white-space:nowrap;}}
@media(max-width:900px){{.g5{{grid-template-columns:repeat(3,1fr);}}}}
@media(max-width:640px){{.g5{{grid-template-columns:1fr 1fr;}}.g2{{grid-template-columns:1fr;}}}}
</style>{gate_script()}</head><body>
<div class="hdr"><h1>similarcams.com · сегодня <span style="font-weight:400;color:#898781;">(обновляется каждый час)</span></h1>
<span class="meta">обновлено: {updated} · <a href="index.html">основной дашборд</a> · <a href="reco.html">RecoDrawer</a></span></div>

<div class="sec">Сегодня — {today.day}.{today.month:02d}, неполный день · сравнение по полным часам 00:00–{H:02d}:00</div>
<div class="g5">{kpis}</div>
<div class="g2">{ctr_html}{reco_html}</div>

<div class="card"><div class="cl">визиты по часам — сегодня vs вчера</div>
  <div class="leg"><span><span class="dot" style="background:#2a78d6;"></span>сегодня</span><span><span class="dot" style="background:#c0bfba;"></span>вчера</span></div>
  <div class="cw" style="height:190px;"><canvas id="cv"></canvas></div></div>
<div class="card"><div class="cl">ClickRef по часам — сегодня vs вчера</div>
  <div class="leg"><span><span class="dot" style="background:#eb6834;"></span>сегодня</span><span><span class="dot" style="background:#c0bfba;"></span>вчера</span></div>
  <div class="cw" style="height:170px;"><canvas id="cc"></canvas></div></div>

<div class="g2">
  <div class="card"><div class="cl">ClickRef по брендам — сегодня ({sum(v for _, v in brands):,})</div><div style="padding-top:6px;">{bars(brands, "#2a78d6")}</div></div>
  <div class="card"><div class="cl">ClickRef по местам (surface) — сегодня</div><div style="padding-top:6px;">{bars(surfaces, "#4a3aa7", width=130)}</div></div>
</div>
<div class="g2">
  <div class="card"><div class="cl">Топ страны — сегодня</div><div style="padding-top:8px;">{countries_html}</div></div>
  <div class="card"><div class="cl">Каналы трафика — сегодня</div><div style="padding-top:8px;">{bars(channels, "#1baf7a", width=110, total=vis_t)}</div>
    <div class="cl" style="margin-top:14px;">Устройства — сегодня</div><div>{bars(devices, "#888780", width=110, total=vis_t)}</div></div>
</div>

<div class="sec">Топ 20 страниц — сегодня</div>
<div class="card" style="overflow-x:auto;"><table style="min-width:520px;"><thead><tr><th>#</th><th style="text-align:left;">Страница</th><th>Посет.</th><th>Доля</th><th>Стр/визит</th></tr></thead><tbody>{pages_html}</tbody></table></div>
<div class="sec">Топ 20 моделей — сегодня</div>
<div class="card" style="overflow-x:auto;"><table><thead><tr><th>#</th><th style="text-align:left;">Модель</th><th style="text-align:left;">Платформа</th><th>Посет.</th></tr></thead><tbody>{models_html}</tbody></table></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>const D = {json.dumps(data, ensure_ascii=False)};</script>
<script>{CHART_JS}</script>
</body></html>"""
    os.makedirs(PUBLIC, exist_ok=True)
    with open(os.path.join(PUBLIC, "today.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ public/today.html  visitors={vis_t}, visits={visits_t}, ClickRef={cr_t}")


CHART_JS = r"""
const grd='#e1e0d9',tk='#898781';
const base={responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}}};
const ya=m=>({grid:{color:grd,lineWidth:.5},ticks:{color:tk,font:{size:11},maxTicksLimit:5},min:0,max:m,border:{display:false}});
const xa={grid:{display:false},ticks:{color:tk,font:{size:10},maxRotation:0}};
const line=(id,t,y,col)=>new Chart(document.getElementById(id),{type:'line',data:{labels:D.hours,datasets:[
  {label:'сегодня',data:t,borderColor:col,backgroundColor:col+'14',fill:true,borderWidth:2,pointRadius:2,tension:.3},
  {label:'вчера',data:y,borderColor:'#c0bfba',borderDash:[4,3],borderWidth:1.5,pointRadius:0,tension:.3}]},
  options:{...base,scales:{x:xa,y:ya(Math.max(...t,...y,1)*1.12)}}});
line('cv',D.vis_t,D.vis_y,'#2a78d6');
line('cc',D.cr_t,D.cr_y,'#eb6834');
"""

if __name__ == "__main__":
    main()
