from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pandas as pd

from .contracts import DashboardErrorCode, DashboardWorkflowError


def render_dashboard(
    plan: dict[str, Any],
    csv_data: dict[str, pd.DataFrame],
    sources: list[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "plan": plan,
        "datasets": {name: _records(frame) for name, frame in csv_data.items()},
        "sources": sources,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    payload_text = (
        payload_text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    page_buttons = "".join(
        f'<button class="page-tab" type="button" data-page="{index}">{html.escape(str(page["name"]))}</button>'
        for index, page in enumerate(plan.get("pages", []))
    )
    source_summary = ", ".join(
        f"{html.escape(str(source.get('name', 'dataset')))} ({int(source.get('rows', 0)):,} rows)"
        for source in sources
    )
    document = (
        _HTML_TEMPLATE.replace("__TITLE__", html.escape(str(plan.get("title", "Analytics Dashboard"))))
        .replace("__SUBTITLE__", html.escape(str(plan.get("subtitle", ""))))
        .replace("__ACCENT__", html.escape(str(plan.get("theme", {}).get("accent", "#2563eb"))))
        .replace("__BACKGROUND__", html.escape(str(plan.get("theme", {}).get("background", "#f4f7fb"))))
        .replace("__PAGE_BUTTONS__", page_buttons)
        .replace("__SOURCE_SUMMARY__", source_summary)
        .replace("__PAYLOAD__", payload_text)
    )
    try:
        output_path.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise DashboardWorkflowError(DashboardErrorCode.RENDER_FAILED, str(exc)) from exc
    return {
        "html": str(output_path),
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "page_count": len(plan.get("pages", [])),
        "chart_count": sum(len(page.get("charts", [])) for page in plan.get("pages", [])),
        "kpi_count": len(plan.get("kpis", [])),
        "filter_count": len(plan.get("filters", [])),
        "embedded_rows": sum(len(frame) for frame in csv_data.values()),
        "self_contained": True,
    }


def validate_dashboard_html(
    html_path: Path,
    plan: dict[str, Any],
    csv_data: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not html_path.is_file() or html_path.stat().st_size < 2000:
        issues.append({"code": DashboardErrorCode.RENDER_FAILED.value, "message": "Dashboard HTML is missing or empty."})
        return {"passed": False, "issues": issues, "checks": {}}
    text = html_path.read_text(encoding="utf-8", errors="replace")
    parser = _DashboardHTMLParser()
    parser.feed(text)
    external_patterns = [
        r"<(?:script|link|img)[^>]+(?:src|href)\s*=\s*[\"']https?://",
        r"\bfetch\s*\(",
        r"\bXMLHttpRequest\b",
        r"\bWebSocket\s*\(",
    ]
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in external_patterns):
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "Dashboard contains an external network dependency."})
    expected_pages = len(plan.get("pages", []))
    expected_charts = sum(len(page.get("charts", [])) for page in plan.get("pages", []))
    if parser.ids.count("dashboard-data") != 1:
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "Embedded dashboard payload is missing or duplicated."})
    if parser.page_tabs != expected_pages:
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "Page-tab count does not match the approved plan."})
    if expected_charts < 1:
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "The approved dashboard has no charts."})
    if any(len(page.get("charts", [])) > 2 for page in plan.get("pages", [])):
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "A dashboard page contains more than two charts."})
    if not csv_data or any(frame.empty for frame in csv_data.values()):
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "An embedded dashboard dataset is empty."})
    checks = {
        "html_document": parser.has_html and parser.has_body,
        "self_contained": not any(re.search(pattern, text, re.IGNORECASE) for pattern in external_patterns),
        "payload_present": parser.ids.count("dashboard-data") == 1,
        "page_tabs": parser.page_tabs,
        "expected_pages": expected_pages,
        "expected_charts": expected_charts,
        "responsive_layout": "@media (max-width: 760px)" in text,
        "interactive_filters": "applyFilters" in text and "filter-select" in text,
        "semantic_fallback": "<noscript>" in text,
        "sha256": _sha256(html_path),
    }
    if not all(
        checks[name]
        for name in ("html_document", "self_contained", "payload_present", "responsive_layout", "interactive_filters", "semantic_fallback")
    ):
        issues.append({"code": DashboardErrorCode.QA_FAILED.value, "message": "One or more structural dashboard checks failed."})
    return {"passed": not issues, "issues": issues, "checks": checks}


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    # Pandas JSON conversion normalizes NumPy scalars, timestamps, NaN, and NaT.
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _DashboardHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.page_tabs = 0
        self.has_html = False
        self.has_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.has_html = True
        if tag == "body":
            self.has_body = True
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "button" and "page-tab" in str(values.get("class", "")).split():
            self.page_tabs += 1


_HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root{--accent:__ACCENT__;--bg:__BACKGROUND__;--ink:#172033;--muted:#667085;--card:#fff;--line:#dfe5ee;--good:#0f766e;--shadow:0 12px 35px rgba(15,23,42,.08)}
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 Inter,Segoe UI,Arial,sans-serif}
    button,select{font:inherit}.shell{max-width:1500px;margin:auto;padding:28px}.hero{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:22px}
    h1{margin:0;font-size:clamp(28px,4vw,46px);letter-spacing:-.035em}.subtitle{color:var(--muted);max-width:760px;margin:7px 0 0}.status{font-size:12px;color:var(--muted);white-space:nowrap}
    .filters{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}.filter{min-width:210px;flex:1}.filter label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin:0 0 5px}
    select{width:100%;border:1px solid var(--line);border-radius:10px;background:#fff;padding:10px 12px;color:var(--ink)}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow)}
    .kpi-label{font-size:13px;color:var(--muted);font-weight:650}.kpi-value{font-size:34px;font-weight:760;letter-spacing:-.035em;margin:7px 0 3px}.kpi-desc{font-size:12px;color:var(--muted)}
    .tabs{display:flex;gap:8px;overflow:auto;margin:24px 0 14px;padding-bottom:2px}.page-tab{border:1px solid var(--line);background:#fff;color:var(--muted);padding:9px 14px;border-radius:999px;cursor:pointer;white-space:nowrap}
    .page-tab.active{background:var(--ink);border-color:var(--ink);color:#fff}.page{display:none}.page.active{display:block}.page-head{margin:8px 0 14px}.page-head h2{margin:0;font-size:24px}.page-head p{margin:4px 0;color:var(--muted)}
    .chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.chart-card,.table-card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow);min-width:0}
    .chart-card h3,.table-card h3{margin:0;font-size:17px}.chart-note{margin:4px 0 14px;color:var(--muted);font-size:12px}.chart-host{height:330px;overflow:auto}.empty{display:grid;place-items:center;height:100%;color:var(--muted);border:1px dashed var(--line);border-radius:10px}
    .bars{display:grid;gap:9px}.bar-row{display:grid;grid-template-columns:minmax(110px,34%) 1fr auto;align-items:center;gap:9px}.bar-label{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-track{height:16px;border-radius:6px;background:#eef2f7;overflow:hidden}.bar-fill{height:100%;background:var(--accent);border-radius:6px}.bar-value{font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted)}
    svg{width:100%;height:100%;overflow:visible}.axis{fill:none;stroke:#cad3df;stroke-width:1}.line{fill:none;stroke:var(--accent);stroke-width:3}.point{fill:var(--accent);opacity:.75}.tick{font-size:11px;fill:#667085}
    .table-card{margin-top:16px;overflow:hidden}.table-scroll{overflow:auto;max-height:390px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:9px 10px;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc;color:#475467}
    .sources{margin:24px 0 0;padding:16px 2px;color:var(--muted);font-size:12px}.sources strong{color:var(--ink)}
    @media (max-width: 760px){.shell{padding:18px}.hero{display:block}.status{margin-top:8px}.chart-grid{grid-template-columns:1fr}.chart-host{height:300px}.bar-row{grid-template-columns:minmax(90px,36%) 1fr auto}}
    @media print{body{background:#fff}.shell{max-width:none}.filters,.tabs{display:none}.page{display:block!important;break-after:page}.chart-card,.table-card,.kpi{box-shadow:none}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero"><div><h1>__TITLE__</h1><p class="subtitle">__SUBTITLE__</p></div><div class="status" id="row-status" aria-live="polite"></div></header>
    <section class="filters" id="filters" aria-label="Dashboard filters"></section>
    <section class="kpis" id="kpis" aria-label="Key metrics"></section>
    <nav class="tabs" aria-label="Dashboard pages">__PAGE_BUTTONS__</nav>
    <section id="pages"></section>
    <footer class="sources"><strong>Sources:</strong> __SOURCE_SUMMARY__. Values are calculated locally from data embedded in this file.</footer>
  </main>
  <noscript><main class="shell"><h1>__TITLE__</h1><p>JavaScript is disabled. Source data is embedded in this self-contained file; enable JavaScript to use filters and charts.</p><p><strong>Sources:</strong> __SOURCE_SUMMARY__</p></main></noscript>
  <script id="dashboard-data" type="application/json">__PAYLOAD__</script>
  <script>
  (()=>{'use strict';
    const model=JSON.parse(document.getElementById('dashboard-data').textContent); const plan=model.plan; const state={filters:{},page:0};
    const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node};
    const finite=value=>{const number=Number(value);return Number.isFinite(number)?number:null};
    const rowsFor=dataset=>(model.datasets[dataset]||[]).filter(row=>Object.entries(state.filters).every(([key,value])=>{if(!value)return true;const [source,field]=key.split('::');return source!==dataset||String(row[field]??'')===value}));
    const compact=value=>new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(value);
    const formatted=(value,kind)=>{if(value===null||value===undefined||Number.isNaN(value))return '—';if(kind==='currency')return new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value);if(kind==='percent')return new Intl.NumberFormat(undefined,{style:'percent',maximumFractionDigits:1}).format(value);if(kind==='integer')return new Intl.NumberFormat(undefined,{maximumFractionDigits:0}).format(value);return compact(value)};
    const aggregate=(rows,field,method)=>{const values=rows.map(row=>finite(row[field])).filter(value=>value!==null);if(method==='count')return rows.length;if(method==='count_distinct')return new Set(rows.map(row=>String(row[field]??''))).size;if(!values.length)return null;if(method==='sum')return values.reduce((a,b)=>a+b,0);if(method==='mean')return values.reduce((a,b)=>a+b,0)/values.length;if(method==='min')return Math.min(...values);if(method==='max')return Math.max(...values);return null};
    function renderFilters(){const host=document.getElementById('filters');host.replaceChildren();plan.filters.forEach(filter=>{const wrap=el('div','filter'),label=el('label','',filter.label),select=el('select','filter-select');const key=filter.dataset+'::'+filter.field;label.htmlFor='filter-'+key;select.id='filter-'+key;select.append(new Option('All',''));const values=[...new Set((model.datasets[filter.dataset]||[]).map(row=>row[filter.field]).filter(value=>value!==null&&value!==''))].sort((a,b)=>String(a).localeCompare(String(b))).slice(0,100);values.forEach(value=>select.append(new Option(String(value),String(value))));select.value=state.filters[key]||'';select.addEventListener('change',()=>{state.filters[key]=select.value;applyFilters()});wrap.append(label,select);host.append(wrap)})}
    function renderKpis(){const host=document.getElementById('kpis');host.replaceChildren();plan.kpis.forEach(metric=>{const rows=rowsFor(metric.dataset),card=el('article','kpi'),label=el('div','kpi-label',metric.label),value=el('div','kpi-value',formatted(aggregate(rows,metric.field,metric.aggregation),metric.format)),desc=el('div','kpi-desc',metric.description||metric.aggregation+' of '+metric.field);card.append(label,value,desc);host.append(card)})}
    function grouped(chart){const groups=new Map;rowsFor(chart.dataset).forEach(row=>{const key=String(row[chart.x]??'Missing');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row)});return [...groups].map(([label,rows])=>({label,value:chart.aggregation==='count'?rows.length:aggregate(rows,chart.y,chart.aggregation)})).filter(item=>item.value!==null)}
    function barChart(chart,host){const items=grouped(chart).sort((a,b)=>b.value-a.value).slice(0,12),max=Math.max(...items.map(item=>Math.abs(item.value)),1),bars=el('div','bars');if(!items.length){host.append(el('div','empty','No matching data'));return}items.forEach(item=>{const row=el('div','bar-row'),label=el('div','bar-label',item.label),track=el('div','bar-track'),fill=el('div','bar-fill'),value=el('div','bar-value',compact(item.value));fill.style.width=(Math.abs(item.value)/max*100)+'%';track.append(fill);row.append(label,track,value);bars.append(row)});host.append(bars)}
    function svgRoot(){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 720 300');svg.setAttribute('role','img');return svg}
    function lineChart(chart,host){let items=grouped(chart);items.sort((a,b)=>{const da=Date.parse(a.label),db=Date.parse(b.label);return Number.isNaN(da)||Number.isNaN(db)?String(a.label).localeCompare(String(b.label)):da-db});if(!items.length){host.append(el('div','empty','No matching data'));return}const svg=svgRoot(),values=items.map(item=>item.value),min=Math.min(...values),max=Math.max(...values),span=max-min||1,points=items.map((item,index)=>{const x=40+(items.length===1?320:index/(items.length-1)*640),y=260-(item.value-min)/span*220;return {x,y,item}});const axis=document.createElementNS(svg.namespaceURI,'path');axis.setAttribute('d','M40 20V260H690');axis.setAttribute('class','axis');svg.append(axis);const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',points.map(p=>p.x+','+p.y).join(' '));line.setAttribute('class','line');svg.append(line);points.forEach((point,index)=>{const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('cx',point.x);dot.setAttribute('cy',point.y);dot.setAttribute('r','4');dot.setAttribute('class','point');dot.append(document.createElementNS(svg.namespaceURI,'title'));dot.firstChild.textContent=point.item.label+': '+formatted(point.item.value,'number');svg.append(dot);if(index===0||index===points.length-1){const label=document.createElementNS(svg.namespaceURI,'text');label.setAttribute('x',point.x);label.setAttribute('y','282');label.setAttribute('text-anchor',index===0?'start':'end');label.setAttribute('class','tick');label.textContent=point.item.label.slice(0,18);svg.append(label)}});host.append(svg)}
    function scatterChart(chart,host){const points=rowsFor(chart.dataset).map(row=>({x:finite(row[chart.x]),y:finite(row[chart.y])})).filter(p=>p.x!==null&&p.y!==null).slice(0,600);if(!points.length){host.append(el('div','empty','No matching data'));return}const xs=points.map(p=>p.x),ys=points.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),xspan=xmax-xmin||1,yspan=ymax-ymin||1,svg=svgRoot(),axis=document.createElementNS(svg.namespaceURI,'path');axis.setAttribute('d','M40 20V260H690');axis.setAttribute('class','axis');svg.append(axis);points.forEach(point=>{const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('cx',40+(point.x-xmin)/xspan*640);dot.setAttribute('cy',260-(point.y-ymin)/yspan*220);dot.setAttribute('r','3.5');dot.setAttribute('class','point');const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=chart.x+': '+formatted(point.x,'number')+', '+chart.y+': '+formatted(point.y,'number');dot.append(title);svg.append(dot)});host.append(svg)}
    function histogram(chart,host){const values=rowsFor(chart.dataset).map(row=>finite(row[chart.x])).filter(value=>value!==null);if(!values.length){host.append(el('div','empty','No matching data'));return}const min=Math.min(...values),max=Math.max(...values),span=max-min||1,bins=10,items=Array.from({length:bins},(_,index)=>({label:formatted(min+index/bins*span,'number')+'–'+formatted(min+(index+1)/bins*span,'number'),value:0}));values.forEach(value=>items[Math.min(bins-1,Math.floor((value-min)/span*bins))].value++);const synthetic={...chart},bars=el('div','bars'),peak=Math.max(...items.map(item=>item.value),1);items.forEach(item=>{const row=el('div','bar-row'),label=el('div','bar-label',item.label),track=el('div','bar-track'),fill=el('div','bar-fill'),value=el('div','bar-value',item.value);fill.style.width=(item.value/peak*100)+'%';track.append(fill);row.append(label,track,value);bars.append(row)});host.append(bars)}
    function renderChart(chart){const card=el('article','chart-card'),title=el('h3','',chart.title),note=el('p','chart-note',chart.description||''),host=el('div','chart-host');card.append(title,note,host);if(chart.type==='bar')barChart(chart,host);else if(chart.type==='line')lineChart(chart,host);else if(chart.type==='scatter')scatterChart(chart,host);else histogram(chart,host);return card}
    function renderTable(spec){if(!spec||!spec.dataset)return null;const card=el('article','table-card'),title=el('h3','',spec.title||'Detail'),scroll=el('div','table-scroll'),table=el('table'),head=el('thead'),headRow=el('tr');spec.columns.forEach(column=>headRow.append(el('th','',column)));head.append(headRow);const body=el('tbody');rowsFor(spec.dataset).slice(0,100).forEach(row=>{const tr=el('tr');spec.columns.forEach(column=>tr.append(el('td','',row[column]??'')));body.append(tr)});table.append(head,body);scroll.append(table);card.append(title,scroll);return card}
    function renderPages(){const host=document.getElementById('pages');host.replaceChildren();plan.pages.forEach((page,index)=>{const section=el('section','page'+(index===state.page?' active':'')),head=el('div','page-head'),title=el('h2','',page.name),purpose=el('p','',page.purpose||''),grid=el('div','chart-grid');head.append(title,purpose);page.charts.forEach(chart=>grid.append(renderChart(chart)));section.append(head,grid);const table=renderTable(page.table);if(table)section.append(table);host.append(section)});document.querySelectorAll('.page-tab').forEach((button,index)=>button.classList.toggle('active',index===state.page))}
    function updateStatus(){const counts=Object.entries(model.datasets).map(([name,rows])=>name+': '+rowsFor(name).length.toLocaleString()+' of '+rows.length.toLocaleString());document.getElementById('row-status').textContent=counts.join(' · ')}
    function applyFilters(){renderKpis();renderPages();updateStatus()}
    document.querySelectorAll('.page-tab').forEach((button,index)=>button.addEventListener('click',()=>{state.page=index;renderPages()}));renderFilters();applyFilters();
    window.applyFilters=applyFilters;
  })();
  </script>
</body>
</html>'''
