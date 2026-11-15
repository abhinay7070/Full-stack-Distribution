"""Export a recalculated workbook to apps_script/test/sheet.json for the Apps Script mock harness.

Usage:
  python3 apps_script/test/export_sheet.py master/gtm_master.xlsx     (file must hold cached values: run recalc first)
  TZ=Asia/Kolkata node apps_script/test/run.js apps_script/Code.gs
"""
import datetime
import json
import sys
from pathlib import Path

from openpyxl import load_workbook

src = sys.argv[1]
wv, wf = load_workbook(src, data_only=True), load_workbook(src)
out = {}
for ws in wv.worksheets:
    f = wf[ws.title]
    vals, forms = [], []
    for r in range(1, ws.max_row + 1):
        rv, rf = [], []
        for c in range(1, ws.max_column + 1):
            v, fv = ws.cell(r, c).value, f.cell(r, c).value
            if isinstance(v, (datetime.datetime, datetime.date)):
                v = {"__d": v.isoformat()}
            rv.append("" if v is None else v)
            rf.append(fv if isinstance(fv, str) and fv.startswith("=") else "")
        vals.append(rv)
        forms.append(rf)
    out[ws.title] = {"vals": vals, "forms": forms}
Path(__file__).with_name("sheet.json").write_text(json.dumps(out))
print({k: len(v["vals"]) for k, v in out.items()})
