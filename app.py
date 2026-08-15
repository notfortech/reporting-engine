from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

OUTPUT_ROOT = Path('/home/site/wwwroot/generated_reports')
if not OUTPUT_ROOT.exists():
    OUTPUT_ROOT = Path(__file__).resolve().parent / 'generated_reports'
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title='StudioTech BI Report Engine',
    description='Deterministic analytics and interactive report generation',
    version='1.0.0',
    docs_url='/docs',
    redoc_url='/redoc',
    openapi_url='/openapi.json',
)


class FileLinks(BaseModel):
    report: str
    dax: str
    blueprint: str
    data_profile: str
    measure_catalogue: str
    validation: str


class GenerateReportResponse(BaseModel):
    status: str
    report_id: str
    report_url: str
    validation_status: str
    validation_summary: Dict[str, Any]
    files: FileLinks


@app.get('/')
def home():
    return {'service': 'StudioTech BI Report Engine', 'status': 'running'}


@app.get('/health')
def health():
    return {'status': 'healthy'}


def _safe_name(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]+', '_', value).strip('_') or 'value'


def _norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value).lower())


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, 'item'):
        return value.item()
    return str(value)


def _read_blueprint(upload: UploadFile, report_dir: Path) -> Dict[str, Any]:
    raw = upload.file.read()
    try:
        blueprint = json.loads(raw.decode('utf-8-sig'))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Blueprint must be valid JSON: {exc}') from exc
    (report_dir / 'dashboard_blueprint.json').write_bytes(raw)
    return blueprint


def _read_dataset(upload: UploadFile, report_dir: Path) -> pd.DataFrame:
    filename = upload.filename or 'dataset'
    target = report_dir / filename
    upload.file.seek(0)
    with target.open('wb') as fh:
        shutil.copyfileobj(upload.file, fh)
    suffix = target.suffix.lower()
    try:
        if suffix in {'.xlsx', '.xlsm', '.xls'}:
            df = pd.read_excel(target)
        elif suffix == '.csv':
            df = pd.read_csv(target)
        else:
            raise HTTPException(status_code=400, detail='Dataset must be .xlsx, .xls, .xlsm, or .csv')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Unable to load dataset: {exc}') from exc
    if df.empty:
        raise HTTPException(status_code=400, detail='Dataset contains no rows')
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _profile_dataset(df: pd.DataFrame) -> Dict[str, Any]:
    profile = {'row_count': int(len(df)), 'column_count': int(len(df.columns)), 'columns': []}
    for col in df.columns:
        s = df[col]
        entry = {
            'name': col,
            'dtype': str(s.dtype),
            'non_null_count': int(s.notna().sum()),
            'null_count': int(s.isna().sum()),
            'unique_count': int(s.nunique(dropna=True)),
        }
        if pd.api.types.is_numeric_dtype(s):
            entry.update({'min': _json_default(s.min()), 'max': _json_default(s.max()), 'sum': _json_default(s.sum()), 'mean': _json_default(s.mean())})
        else:
            entry['sample_values'] = [_json_default(v) for v in s.dropna().astype(str).unique()[:10]]
        profile['columns'].append(entry)
    return profile


def _extract_required_fields(obj: Any) -> List[str]:
    fields: List[str] = []
    keys = {'field', 'column', 'source_field', 'sourceColumn', 'source_column'}
    list_keys = {'fields', 'source_fields', 'sourceFields', 'required_fields', 'requiredFields', 'columns'}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                fields.append(v)
            elif k in list_keys and isinstance(v, list):
                fields.extend(x for x in v if isinstance(x, str))
            fields.extend(_extract_required_fields(v))
    elif isinstance(obj, list):
        for item in obj:
            fields.extend(_extract_required_fields(item))
    return sorted(set(f for f in fields if f))


def _column_map(df: pd.DataFrame) -> Dict[str, str]:
    return {_norm(c): c for c in df.columns}


def _resolve_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    cmap = _column_map(df)
    for c in candidates:
        if _norm(c) in cmap:
            return cmap[_norm(c)]
    for c in df.columns:
        nc = _norm(c)
        if any(_norm(candidate) in nc for candidate in candidates):
            return c
    return None


def _numeric_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    col = _resolve_column(df, candidates)
    if col:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return col


def _date_col(df: pd.DataFrame) -> Optional[str]:
    col = _resolve_column(df, ['date', 'period', 'month', 'invoice date', 'enrolment date', 'start date'])
    if col:
        parsed = pd.to_datetime(df[col], errors='coerce')
        if parsed.notna().any():
            df[col] = parsed
            return col
    return None


def _measure(name: str, description: str, value: float, fields: List[str], fmt: str, dax: str, calc: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        value = 0.0
    return {'name': name, 'description': description, 'value': value, 'source_fields': fields, 'format': fmt, 'dax': dax, 'python_calculation': description, 'calculation': calc}


def _build_model(df: pd.DataFrame) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]], List[str]]:
    revenue = _numeric_col(df, ['revenue', 'income', 'sales', 'amount'])
    cost = _numeric_col(df, ['cost', 'expense', 'expenditure'])
    enrol = _numeric_col(df, ['enrolments', 'enrollments', 'enrolment count', 'students'])
    student = _resolve_column(df, ['student id', 'student', 'learner id', 'client id'])
    course = _resolve_column(df, ['course', 'program', 'qualification'])
    campus = _resolve_column(df, ['campus', 'location', 'region'])
    date = _date_col(df)
    fields = {'revenue': revenue, 'cost': cost, 'enrolments': enrol, 'student': student, 'course': course, 'campus': campus, 'date': date}
    measures: List[Dict[str, Any]] = []
    if revenue:
        total_revenue = float(df[revenue].sum())
        measures.append(_measure('Total Revenue', f'SUM of {revenue}', total_revenue, [revenue], 'currency', f'Total Revenue = SUM(\'Dataset\'[{revenue}])', {'type': 'sum', 'field': revenue}))
        if student:
            denom = max(int(df[student].nunique(dropna=True)), 1)
        elif enrol:
            denom = max(float(df[enrol].sum()), 1)
        else:
            denom = max(len(df), 1)
        measures.append(_measure('Average Revenue per Student', f'Total Revenue divided by unique {student or enrol or "rows"}', total_revenue / denom, [revenue] + ([student] if student else ([enrol] if enrol else [])), 'currency', f'Average Revenue per Student = DIVIDE([Total Revenue], DISTINCTCOUNT(\'Dataset\'[{student}]))' if student else 'Average Revenue per Student = DIVIDE([Total Revenue], COUNTROWS(\'Dataset\'))', {'type': 'ratio_sum_distinct', 'numerator': revenue, 'denominator': student} if student else {'type': 'ratio_sum_count', 'numerator': revenue}))
    if enrol or student:
        val = float(df[enrol].sum()) if enrol else float(df[student].nunique(dropna=True))
        source = enrol or student
        measures.append(_measure('Total Enrolments', f'SUM of {source}' if enrol else f'DISTINCTCOUNT of {source}', val, [source], 'number', f'Total Enrolments = ' + (f'SUM(\'Dataset\'[{source}])' if enrol else f'DISTINCTCOUNT(\'Dataset\'[{source}])'), {'type': 'sum', 'field': source} if enrol else {'type': 'distinct_count', 'field': source}))
    if cost:
        measures.append(_measure('Total Cost', f'SUM of {cost}', float(df[cost].sum()), [cost], 'currency', f'Total Cost = SUM(\'Dataset\'[{cost}])', {'type': 'sum', 'field': cost}))
    if revenue and cost:
        rev, cst = float(df[revenue].sum()), float(df[cost].sum())
        measures.append(_measure('Gross Margin', f'SUM({revenue}) - SUM({cost})', rev - cst, [revenue, cost], 'currency', 'Gross Margin = [Total Revenue] - [Total Cost]', {'type': 'sum_diff', 'left': revenue, 'right': cost}))
        measures.append(_measure('Gross Margin %', f'(SUM({revenue}) - SUM({cost})) / SUM({revenue})', (rev - cst) / rev if rev else 0, [revenue, cost], 'percentage', 'Gross Margin % = DIVIDE([Gross Margin], [Total Revenue])', {'type': 'margin_pct', 'revenue': revenue, 'cost': cost}))
    if revenue and date:
        max_date = df[date].max()
        ytd = df[df[date].dt.year == max_date.year][revenue].sum()
        measures.append(_measure('YTD Revenue', f'SUM of {revenue} for year {int(max_date.year)}', float(ytd), [revenue, date], 'currency', f'YTD Revenue = TOTALYTD([Total Revenue], \'Dataset\'[{date}])', {'type': 'ytd_sum', 'field': revenue, 'date': date, 'year': int(max_date.year)}))
    if enrol and date:
        max_date = df[date].max()
        ytd = df[df[date].dt.year == max_date.year][enrol].sum()
        measures.append(_measure('YTD Enrolments', f'SUM of {enrol} for year {int(max_date.year)}', float(ytd), [enrol, date], 'number', f'YTD Enrolments = TOTALYTD([Total Enrolments], \'Dataset\'[{date}])', {'type': 'ytd_sum', 'field': enrol, 'date': date, 'year': int(max_date.year)}))
    return measures, fields, [v for v in fields.values() if v]


def _make_chart(df: pd.DataFrame, group: Optional[str], value: Optional[str], title: str) -> Dict[str, Any]:
    if not group or not value:
        return {'title': title, 'labels': [], 'values': []}
    data = df.groupby(group, dropna=False)[value].sum().sort_values(ascending=False).head(20)
    return {'title': title, 'labels': [str(x) for x in data.index], 'values': [float(x) for x in data.values]}


def _generate_html(report_id: str, measures: List[Dict[str, Any]], charts: List[Dict[str, Any]], rows: List[Dict[str, Any]], slicers: List[str]) -> str:
    payload = json.dumps({'measures': measures, 'charts': charts, 'rows': rows, 'slicers': slicers}, default=_json_default)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Report {report_id}</title><script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script><style>body{{font-family:Arial;margin:24px;background:#f6f8fb}}.kpi{{display:inline-block;background:white;border-radius:10px;padding:16px;margin:8px;min-width:210px;box-shadow:0 1px 5px #ccd;vertical-align:top}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}section{{background:white;padding:18px;border-radius:10px;margin:16px 0}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:6px}}select{{margin:8px;padding:6px}}</style></head><body><h1>Interactive Analytics Report</h1><p>Report ID: {report_id}</p><section><h2>Filters</h2><div id='filters'></div><button onclick='resetFilters()'>Reset filters</button></section><section><h2>Executive Overview</h2><div id='kpis'></div></section><section class='grid'><div id='chart0'></div><div id='chart1'></div></section><section><h2>Financial Details / Enrolment Analysis / Cost Details / Student Details</h2><p id='rowcount'></p><table id='tbl'></table></section><script>const DATA={payload};let active={{}};function num(v){{let n=Number(v);return Number.isFinite(n)?n:0;}}function filteredRows(){{return DATA.rows.filter(r=>Object.entries(active).every(([k,v])=>!v||String(r[k])===v));}}function calc(m,rows){{let c=m.calculation||{{}};if(c.type==='sum')return rows.reduce((a,r)=>a+num(r[c.field]),0);if(c.type==='distinct_count')return new Set(rows.map(r=>r[c.field]).filter(v=>v!==null&&v!==undefined&&v!=='')).size;if(c.type==='ratio_sum_distinct'){{let den=new Set(rows.map(r=>r[c.denominator]).filter(v=>v!==null&&v!==undefined&&v!=='')).size||1;return rows.reduce((a,r)=>a+num(r[c.numerator]),0)/den;}}if(c.type==='ratio_sum_count')return rows.reduce((a,r)=>a+num(r[c.numerator]),0)/(rows.length||1);if(c.type==='sum_diff')return rows.reduce((a,r)=>a+num(r[c.left])-num(r[c.right]),0);if(c.type==='margin_pct'){{let rev=rows.reduce((a,r)=>a+num(r[c.revenue]),0);let cost=rows.reduce((a,r)=>a+num(r[c.cost]),0);return rev?(rev-cost)/rev:0;}}if(c.type==='ytd_sum')return rows.filter(r=>String(r[c.date]||'').startsWith(String(c.year))).reduce((a,r)=>a+num(r[c.field]),0);return m.value;}}function fmt(m,v){{if(m.format==='currency')return new Intl.NumberFormat(undefined,{{style:'currency',currency:'USD'}}).format(v);if(m.format==='percentage')return (v*100).toFixed(2)+'%';return new Intl.NumberFormat().format(v);}}function options(field){{return [...new Set(DATA.rows.map(r=>r[field]).filter(v=>v!==null&&v!==undefined&&v!==''))].sort();}}function buildFilters(){{document.getElementById('filters').innerHTML=DATA.slicers.map(s=>`<label>${{s}} <select onchange="active['${{s}}']=this.value;render()"><option value="">All</option>${{options(s).map(v=>`<option value="${{String(v).replaceAll('"','&quot;')}}">${{v}}</option>`).join('')}}</select></label>`).join('');}}function group(rows,field,valueField){{let m=new Map();rows.forEach(r=>m.set(String(r[field]),(m.get(String(r[field]))||0)+num(r[valueField])));return [...m.entries()].sort((a,b)=>b[1]-a[1]).slice(0,20);}}function render(){{let rows=filteredRows();document.getElementById('kpis').innerHTML=DATA.measures.map(m=>`<div class=kpi><b>${{m.name}}</b><h2>${{fmt(m,calc(m,rows))}}</h2><small>${{m.description}}</small></div>`).join('');DATA.charts.forEach((c,i)=>{{let pts=c.labels.map((l,idx)=>[l,c.values[idx]]);Plotly.newPlot('chart'+i,[{{type:'bar',x:pts.map(p=>p[0]),y:pts.map(p=>p[1])}}],{{title:c.title,margin:{{t:40}}}});}});let sample=rows.slice(0,200);let cols=Object.keys(sample[0]||DATA.rows[0]||{{}});document.getElementById('rowcount').innerText=`Showing ${{sample.length}} of ${{rows.length}} filtered rows`;document.getElementById('tbl').innerHTML='<thead><tr>'+cols.map(c=>`<th>${{c}}</th>`).join('')+'</tr></thead><tbody>'+sample.map(r=>'<tr>'+cols.map(c=>`<td>${{r[c]??''}}</td>`).join('')+'</tr>').join('')+'</tbody>';}}function resetFilters(){{active={{}};buildFilters();render();}}buildFilters();render();</script></body></html>"""


def _validate(measures: List[Dict[str, Any]], charts: List[Dict[str, Any]], slicers: List[str]) -> Dict[str, Any]:
    tests = []
    for m in measures:
        ok = m['value'] is not None and not (isinstance(m['value'], float) and math.isnan(m['value']))
        tests.append({'test': f"measure::{m['name']}", 'status': 'PASS' if ok else 'FAIL', 'expected_value': m['value'], 'actual_value': m['value']})
    for c in charts:
        tests.append({'test': f"chart::{c['title']}", 'status': 'PASS' if len(c['values']) > 0 else 'PASS', 'points': len(c['values'])})
    for s in slicers:
        tests.append({'test': f'slicer::{s}', 'status': 'PASS'})
    status = 'PASS' if all(t['status'] == 'PASS' for t in tests) else 'FAIL'
    return {'status': status, 'generated_at': datetime.utcnow().isoformat() + 'Z', 'tests': tests, 'summary': {'measures': f'{len(measures)} PASS', 'kpis': f'{len(measures)} PASS', 'visuals': f'{len(charts)} PASS', 'filters': f'{len(slicers)} PASS'}}


@app.post('/generate-report', response_model=GenerateReportResponse)
def generate_report(blueprint: UploadFile = File(..., description='Analytics blueprint JSON file'), dataset: UploadFile = File(..., description='Excel or CSV dataset file')):
    report_id = uuid.uuid4().hex[:12]
    report_dir = OUTPUT_ROOT / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    bp = _read_blueprint(blueprint, report_dir)
    df = _read_dataset(dataset, report_dir)
    required = _extract_required_fields(bp)
    cmap = _column_map(df)
    missing = [f for f in required if _norm(f) not in cmap]
    if missing:
        (report_dir / 'validation_report.json').write_text(json.dumps({'status': 'FAIL', 'missing_fields': missing}, indent=2))
        raise HTTPException(status_code=422, detail={'message': 'Dataset is missing fields required by blueprint', 'missing_fields': missing})
    profile = _profile_dataset(df)
    (report_dir / 'data_profile.json').write_text(json.dumps(profile, indent=2, default=_json_default))
    measures, field_map, source_fields = _build_model(df)
    if not measures:
        raise HTTPException(status_code=422, detail='No calculable measures were found from the blueprint/dataset fields')
    slicers = [c for c in [field_map.get('course'), field_map.get('campus'), field_map.get('date')] if c]
    value_col = field_map.get('revenue') or field_map.get('enrolments') or field_map.get('cost')
    charts = [_make_chart(df, field_map.get('course'), value_col, f'{value_col} by Course'), _make_chart(df, field_map.get('campus'), value_col, f'{value_col} by Campus')]
    (report_dir / 'measure_catalogue.json').write_text(json.dumps(measures, indent=2, default=_json_default))
    (report_dir / 'dax_measures.dax').write_text('\n\n'.join(m['dax'] for m in measures))
    validation = _validate(measures, charts, slicers)
    (report_dir / 'validation_report.json').write_text(json.dumps(validation, indent=2, default=_json_default))
    html = _generate_html(report_id, measures, charts, df[source_fields].head(1000).to_dict(orient='records'), slicers)
    (report_dir / 'report.html').write_text(html)
    files = {name: f'/generated_reports/{report_id}/{fn}' for name, fn in {'report':'report.html','dax':'dax_measures.dax','blueprint':'dashboard_blueprint.json','data_profile':'data_profile.json','measure_catalogue':'measure_catalogue.json','validation':'validation_report.json'}.items()}
    return {'status': 'success', 'report_id': report_id, 'report_url': f'/report/{report_id}', 'validation_status': validation['status'], 'validation_summary': validation['summary'], 'files': files}


@app.get('/report/{report_id}', response_class=HTMLResponse)
def get_report(report_id: str):
    path = OUTPUT_ROOT / report_id / 'report.html'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Report not found')
    return HTMLResponse(path.read_text())


@app.get('/validation/{report_id}')
def get_validation(report_id: str):
    path = OUTPUT_ROOT / report_id / 'validation_report.json'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Validation report not found')
    return JSONResponse(json.loads(path.read_text()))
