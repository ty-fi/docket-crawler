"""
Generates index.html with AG Grid and embedded data from the SQLite database.
Output: output/index.html
"""

import json
import sqlite3
from pathlib import Path

DB = Path("output/stf_pia_qa.sqlite")
OUT = Path("output/index.html")


def load_data():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT
            filing_api_id,
            filing_description,
            filing_date,
            staff,
            doc_id,
            is_protective_disclosure,
            question,
            response,
            attachment_filenames,
            confidentiality_files
        FROM qa_responses
        ORDER BY staff, filing_date, doc_id
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Georgia PSC Docket 55378 — Staff Data Requests</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/styles/ag-grid.css"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/styles/ag-theme-quartz.css"/>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --color-bg:       #f8f9fb;
    --color-surface:  #ffffff;
    --color-border:   #e2e6ea;
    --color-text:     #1a2332;
    --color-muted:    #6b7a8d;
    --color-accent:   #1d4ed8;
    --color-row-hover:#f0f4ff;
    --color-selected: #e8effe;

    --badge-DEA: #2563eb; --badge-DEA-bg: #dbeafe;
    --badge-GS:  #059669; --badge-GS-bg:  #d1fae5;
    --badge-JKA: #7c3aed; --badge-JKA-bg: #ede9fe;
    --badge-LA:  #d97706; --badge-LA-bg:  #fef3c7;
    --badge-PIA: #dc2626; --badge-PIA-bg: #fee2e2;

    --panel-width: 480px;
    --header-height: 64px;
  }

  html, body { height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--color-bg); color: var(--color-text); }

  /* ── Header ── */
  header {
    height: var(--header-height);
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border);
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 0 20px;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
  }
  .header-title {
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }
  .header-title h1 { font-size: 15px; font-weight: 700; color: var(--color-text); letter-spacing: -.2px; }
  .header-title span { font-size: 11px; color: var(--color-muted); margin-top: 1px; }

  .search-wrap {
    flex: 1;
    max-width: 400px;
    position: relative;
  }
  .search-wrap svg {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--color-muted);
    pointer-events: none;
  }
  #quickFilter {
    width: 100%;
    padding: 7px 12px 7px 34px;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    font-size: 13px;
    background: var(--color-bg);
    color: var(--color-text);
    outline: none;
    transition: border-color .15s;
  }
  #quickFilter:focus { border-color: var(--color-accent); background: #fff; }

  .staff-filters { display: flex; gap: 6px; }
  .staff-btn {
    padding: 4px 10px;
    border-radius: 20px;
    border: 1.5px solid transparent;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity .15s, border-color .15s;
    background: transparent;
  }
  .staff-btn.active { border-color: currentColor; }
  .staff-btn:not(.active) { opacity: .4; }
  .staff-btn[data-staff="DEA"] { color: var(--badge-DEA); background: var(--badge-DEA-bg); }
  .staff-btn[data-staff="GS"]  { color: var(--badge-GS);  background: var(--badge-GS-bg);  }
  .staff-btn[data-staff="JKA"] { color: var(--badge-JKA); background: var(--badge-JKA-bg); }
  .staff-btn[data-staff="LA"]  { color: var(--badge-LA);  background: var(--badge-LA-bg);  }
  .staff-btn[data-staff="PIA"] { color: var(--badge-PIA); background: var(--badge-PIA-bg); }

  .record-count { font-size: 12px; color: var(--color-muted); white-space: nowrap; margin-left: auto; }

  /* ── Main layout ── */
  .main {
    display: flex;
    height: calc(100vh - var(--header-height));
    overflow: hidden;
  }

  .grid-wrap {
    flex: 1;
    min-width: 0;
    padding: 16px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  #myGrid {
    flex: 1;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--color-border);
  }

  /* ── Detail panel ── */
  .detail-panel {
    width: 0;
    flex-shrink: 0;
    background: var(--color-surface);
    border-left: 1px solid var(--color-border);
    overflow: hidden;
    transition: width .2s ease;
    display: flex;
    flex-direction: column;
  }
  .detail-panel.open {
    width: var(--panel-width);
  }
  .detail-inner {
    width: var(--panel-width);
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .detail-header {
    padding: 16px 20px 14px;
    border-bottom: 1px solid var(--color-border);
    display: flex;
    align-items: flex-start;
    gap: 10px;
    flex-shrink: 0;
  }
  .detail-meta { flex: 1; min-width: 0; }
  .detail-doc-id { font-size: 16px; font-weight: 700; letter-spacing: -.3px; }
  .detail-sub { font-size: 12px; color: var(--color-muted); margin-top: 3px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .detail-close {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-muted);
    padding: 4px;
    border-radius: 4px;
    line-height: 0;
    flex-shrink: 0;
  }
  .detail-close:hover { background: var(--color-bg); color: var(--color-text); }

  .detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .qa-section { display: flex; flex-direction: column; gap: 8px; }
  .qa-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .8px;
    text-transform: uppercase;
    color: var(--color-muted);
  }
  .qa-text {
    font-size: 13.5px;
    line-height: 1.65;
    color: var(--color-text);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .qa-text.question {
    background: #f6f8ff;
    border-left: 3px solid var(--color-accent);
    padding: 12px 14px;
    border-radius: 0 6px 6px 0;
  }
  .qa-text.response {
    background: #f9fafb;
    border-left: 3px solid #94a3b8;
    padding: 12px 14px;
    border-radius: 0 6px 6px 0;
  }

  .attachments-list { display: flex; flex-direction: column; gap: 4px; }
  .attachment-item {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    color: var(--color-muted);
    padding: 5px 8px;
    background: var(--color-bg);
    border-radius: 4px;
  }
  .attachment-item svg { flex-shrink: 0; }

  /* ── AG Grid overrides ── */
  .ag-theme-quartz {
    --ag-font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --ag-font-size: 13px;
    --ag-row-hover-color: var(--color-row-hover);
    --ag-selected-row-background-color: var(--color-selected);
    --ag-header-background-color: #f1f4f8;
    --ag-border-color: var(--color-border);
    --ag-cell-horizontal-padding: 12px;
  }
  .ag-row { cursor: pointer; }

  /* Staff badge */
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .3px;
  }
  .badge-DEA { color: var(--badge-DEA); background: var(--badge-DEA-bg); }
  .badge-GS  { color: var(--badge-GS);  background: var(--badge-GS-bg);  }
  .badge-JKA { color: var(--badge-JKA); background: var(--badge-JKA-bg); }
  .badge-LA  { color: var(--badge-LA);  background: var(--badge-LA-bg);  }
  .badge-PIA { color: var(--badge-PIA); background: var(--badge-PIA-bg); }

  /* PD tag */
  .pd-tag {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .4px;
    color: #92400e;
    background: #fef3c7;
    border: 1px solid #fcd34d;
  }

  .clip-icon { color: #94a3b8; font-size: 14px; }

  /* preview text truncation handled by AG Grid */
  .preview { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--color-text); }
  .preview-muted { color: var(--color-muted); }
</style>
</head>
<body>

<header>
  <div class="header-title">
    <h1>Georgia PSC Docket 55378</h1>
    <span>Staff Data Request Responses — Georgia Power Company</span>
  </div>

  <div class="search-wrap">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
    </svg>
    <input type="text" id="quickFilter" placeholder="Search questions and responses…" autocomplete="off">
  </div>

  <div class="staff-filters" id="staffFilters"></div>

  <div class="record-count" id="recordCount"></div>
</header>

<div class="main">
  <div class="grid-wrap">
    <div id="myGrid" class="ag-theme-quartz"></div>
  </div>
  <div class="detail-panel" id="detailPanel">
    <div class="detail-inner" id="detailInner"></div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4/dist/ag-grid-community.min.noStyle.js"></script>
<script>
const RAW_DATA = __DATA_PLACEHOLDER__;

// ── Staff filter state ──────────────────────────────────────────────────────
const STAFFS = ['DEA', 'GS', 'JKA', 'LA', 'PIA'];
const activeStaff = new Set(STAFFS);

function buildStaffButtons() {
  const container = document.getElementById('staffFilters');
  STAFFS.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'staff-btn active';
    btn.dataset.staff = s;
    btn.textContent = s;
    btn.addEventListener('click', () => toggleStaff(s, btn));
    container.appendChild(btn);
  });
}

function toggleStaff(staff, btn) {
  if (activeStaff.has(staff)) {
    if (activeStaff.size === 1) return; // keep at least one
    activeStaff.delete(staff);
    btn.classList.remove('active');
  } else {
    activeStaff.add(staff);
    btn.classList.add('active');
  }
  gridApi.onFilterChanged();
}

// ── Cell renderers ──────────────────────────────────────────────────────────
function staffRenderer(params) {
  return `<span class="badge badge-${params.value}">${params.value}</span>`;
}

function pdRenderer(params) {
  return params.value === 'yes' ? '<span class="pd-tag">PD</span>' : '';
}

function previewRenderer(params) {
  const text = (params.value || '').replace(/\\s+/g, ' ').trim();
  return `<span class="preview">${escHtml(text)}</span>`;
}

function attachRenderer(params) {
  return params.value ? '<span class="clip-icon">📎</span>' : '';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Column definitions ──────────────────────────────────────────────────────
const columnDefs = [
  {
    field: 'staff',
    headerName: 'Staff',
    width: 84,
    pinned: 'left',
    cellRenderer: staffRenderer,
    filter: false, // handled by our custom buttons
    sortable: true,
  },
  {
    field: 'doc_id',
    headerName: 'Doc ID',
    width: 148,
    pinned: 'left',
    sortable: true,
    filter: true,
  },
  {
    field: 'filing_date',
    headerName: 'Filed',
    width: 100,
    sortable: true,
    filter: true,
  },
  {
    field: 'is_protective_disclosure',
    headerName: 'PD',
    width: 58,
    cellRenderer: pdRenderer,
    filter: true,
    sortable: false,
  },
  {
    field: 'question',
    headerName: 'Question',
    flex: 1,
    minWidth: 220,
    cellRenderer: previewRenderer,
    sortable: false,
    filter: true,
    tooltipField: 'question',
  },
  {
    field: 'attachment_filenames',
    headerName: '',
    width: 44,
    cellRenderer: attachRenderer,
    sortable: false,
    filter: false,
    cellStyle: { textAlign: 'center', paddingLeft: '4px', paddingRight: '4px' },
  },
];

// ── External filter (staff buttons) ────────────────────────────────────────
function isExternalFilterPresent() {
  return activeStaff.size < STAFFS.length;
}
function doesExternalFilterPass(node) {
  return activeStaff.has(node.data.staff);
}

// ── Grid init ───────────────────────────────────────────────────────────────
const gridOptions = {
  columnDefs,
  rowData: RAW_DATA,
  defaultColDef: { resizable: true, suppressMovable: false },
  rowSelection: 'single',
  suppressCellFocus: true,
  isExternalFilterPresent,
  doesExternalFilterPass,
  onRowClicked: e => showDetail(e.data),
  onFilterChanged: updateCount,
  onGridReady: updateCount,
  rowHeight: 36,
  headerHeight: 38,
  tooltipShowDelay: 400,
};

const gridApi = agGrid.createGrid(document.getElementById('myGrid'), gridOptions);

// ── Quick filter ────────────────────────────────────────────────────────────
document.getElementById('quickFilter').addEventListener('input', e => {
  gridApi.setGridOption('quickFilterText', e.target.value);
});

// ── Record count ────────────────────────────────────────────────────────────
function updateCount() {
  let shown = 0;
  gridApi.forEachNodeAfterFilter(() => shown++);
  document.getElementById('recordCount').textContent =
    shown === RAW_DATA.length ? `${RAW_DATA.length} responses` : `${shown} of ${RAW_DATA.length}`;
}

// ── Detail panel ────────────────────────────────────────────────────────────
let currentDocId = null;

function showDetail(row) {
  if (currentDocId === row.doc_id + row.zip_source) {
    closeDetail();
    return;
  }
  currentDocId = row.doc_id + row.zip_source;

  const attachments = (row.attachment_filenames || '')
    .split(';').map(s => s.trim()).filter(Boolean);

  const confFiles = (row.confidentiality_files || '')
    .split(';').map(s => s.trim()).filter(Boolean);

  const allAttach = [...attachments, ...confFiles];

  const panel = document.getElementById('detailPanel');
  const inner = document.getElementById('detailInner');

  inner.innerHTML = `
    <div class="detail-header">
      <div class="detail-meta">
        <div class="detail-doc-id">${escHtml(row.doc_id)}</div>
        <div class="detail-sub">
          <span class="badge badge-${row.staff}">${row.staff}</span>
          <span>${row.filing_date}</span>
          ${row.is_protective_disclosure === 'yes' ? '<span class="pd-tag">Protective Disclosure</span>' : ''}
        </div>
      </div>
      <button class="detail-close" onclick="closeDetail()" title="Close">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M18 6 6 18M6 6l12 12"/>
        </svg>
      </button>
    </div>
    <div class="detail-body">
      <div class="qa-section">
        <div class="qa-label">Question</div>
        <div class="qa-text question">${escHtml(row.question || '—')}</div>
      </div>
      <div class="qa-section">
        <div class="qa-label">Response</div>
        <div class="qa-text response">${escHtml(row.response || '—')}</div>
      </div>
      ${allAttach.length ? `
      <div class="qa-section">
        <div class="qa-label">Attachments</div>
        <div class="attachments-list">
          ${allAttach.map(f => `
            <div class="attachment-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
              </svg>
              ${escHtml(f)}
            </div>`).join('')}
        </div>
      </div>` : ''}
    </div>
  `;

  panel.classList.add('open');
}

function closeDetail() {
  currentDocId = null;
  document.getElementById('detailPanel').classList.remove('open');
  gridApi.deselectAll();
}

// ── Keyboard close ──────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDetail();
});

buildStaffButtons();
</script>
</body>
</html>
"""


def main():
    data = load_data()
    json_str = json.dumps(data, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json_str)
    OUT.write_text(html, encoding="utf-8")
    print(f"Generated {OUT} ({OUT.stat().st_size:,} bytes, {len(data)} records)")


if __name__ == "__main__":
    main()
