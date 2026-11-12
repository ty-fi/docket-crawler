'use strict';

const PALETTE = [
  { color: '#2563eb', bg: '#dbeafe' },
  { color: '#059669', bg: '#d1fae5' },
  { color: '#7c3aed', bg: '#ede9fe' },
  { color: '#d97706', bg: '#fef3c7' },
  { color: '#dc2626', bg: '#fee2e2' },
  { color: '#db2777', bg: '#fce7f3' },
  { color: '#0891b2', bg: '#cffafe' },
  { color: '#65a30d', bg: '#ecfccb' },
  { color: '#475569', bg: '#e2e8f0' },
  { color: '#b45309', bg: '#ffedd5' },
];

const state = {
  rows: [],
  dockets: [],
  parties: [],
  activeDockets: new Set(),
  activeParties: new Set(),
  latestOnly: false,
  latestSeq: new Map(),   // "docket|doc_id" -> highest response_seq
  byRequest: new Map(),   // "docket|doc_id" -> rows, in filing order
  currentRowId: null,
  colors: new Map(),      // "docket:<id>" / "party:<code>" -> palette entry
};
let gridApi;

// ── helpers ─────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}
const splitList = s => (s || '').split(';').map(x => x.trim()).filter(Boolean);
const requestKey = r => `${r.docket_id}|${r.doc_id}`;

function chipStyle(key) {
  const c = state.colors.get(key) || PALETTE[PALETTE.length - 1];
  return `--chip-color:${c.color};--chip-bg:${c.bg}`;
}
function badge(key, text) {
  return `<span class="badge" style="${chipStyle(key)}">${esc(text)}</span>`;
}
function tags(r, long) {
  const out = [];
  if (r.is_pd === 'yes') {
    out.push(`<span class="tag tag-pd" title="Public disclosure: this response text is redacted; the trade-secret version was filed confidentially">${long ? 'Public disclosure (redacted)' : 'PD'}</span>`);
  } else if (r.has_trade_secret === 'yes') {
    out.push(`<span class="tag tag-ts" title="Some material for this response (e.g. an attachment) was filed as trade secret and is not public">${long ? 'Trade-secret material withheld' : 'TS'}</span>`);
  }
  if (r.is_supplemental === 'yes') out.push(`<span class="tag tag-supp" title="Response #${r.response_seq} to this request">${long ? 'Supplemental #' : 'S'}${r.response_seq}</span>`);
  return out.join('');
}

// ── filters ─────────────────────────────────────────────────────────────────
function buildChips(containerId, items, active, keyPrefix) {
  const container = document.getElementById(containerId);
  container.replaceChildren();
  if (items.length < 2) return;
  items.forEach(({ value, label }) => {
    const btn = document.createElement('button');
    btn.className = 'chip-btn active';
    btn.textContent = label;
    btn.style.cssText = chipStyle(`${keyPrefix}:${value}`);
    btn.addEventListener('click', e => {
      // Alt/Option-click shows only this one; plain click toggles.
      if (e.altKey) {
        active.clear();
        active.add(value);
      } else if (active.has(value)) {
        if (active.size === 1) return;
        active.delete(value);
      } else {
        active.add(value);
      }
      [...container.children].forEach((b, i) => b.classList.toggle('active', active.has(items[i].value)));
      gridApi.onFilterChanged();
    });
    container.appendChild(btn);
  });
}

function isExternalFilterPresent() {
  return state.latestOnly
    || state.activeDockets.size < state.dockets.length
    || state.activeParties.size < state.parties.length;
}
function doesExternalFilterPass(node) {
  const r = node.data;
  if (!state.activeDockets.has(r.docket_id)) return false;
  if (!state.activeParties.has(r.party)) return false;
  if (state.latestOnly && r.response_seq !== state.latestSeq.get(requestKey(r))) return false;
  return true;
}

// ── grid ────────────────────────────────────────────────────────────────────
function columnDefs(multiDocket, partyLabel) {
  const preview = p => `<span class="preview">${esc((p.value || '').replace(/\s+/g, ' ').trim())}</span>`;
  return [
    {
      field: 'docket_id', headerName: 'Docket', width: 100, pinned: 'left', hide: !multiDocket,
      cellRenderer: p => badge(`docket:${p.value}`, p.value),
    },
    {
      field: 'party', headerName: partyLabel, width: 88, pinned: 'left',
      cellRenderer: p => badge(`party:${p.value}`, p.value),
    },
    {
      field: 'doc_id', headerName: 'Request', width: 150, pinned: 'left', filter: true,
      // Sort numerically by set then item, so STF-JKA-1-10 follows STF-JKA-1-9.
      comparator: (a, b, na, nb) =>
        na.data.party.localeCompare(nb.data.party)
        || na.data.set_no - nb.data.set_no
        || na.data.item_no - nb.data.item_no
        || na.data.response_seq - nb.data.response_seq,
    },
    { field: 'filed_date', headerName: 'Filed', width: 108, filter: 'agDateColumnFilter',
      filterParams: { comparator: (d, cell) => { const c = new Date(cell + 'T00:00'); return c < d ? -1 : c > d ? 1 : 0; } } },
    { colId: 'tags', headerName: '', width: 70, sortable: false, cellRenderer: p => `<span class="tags">${tags(p.data, false)}</span>`,
      getQuickFilterText: () => '' },
    { field: 'question', headerName: 'Question', flex: 1, minWidth: 200, sortable: false, filter: true, cellRenderer: preview },
    { field: 'response', headerName: 'Response', flex: 1, minWidth: 200, sortable: false, filter: true, cellRenderer: preview },
    {
      field: 'attachments', headerName: '', width: 44, sortable: false,
      cellRenderer: p => (p.value ? '<span class="clip-icon" title="Has attachments">📎</span>' : ''),
      cellStyle: { textAlign: 'center', paddingLeft: '4px', paddingRight: '4px' },
    },
  ];
}

function updateCount() {
  let shown = 0;
  gridApi.forEachNodeAfterFilter(() => shown++);
  const total = state.rows.length;
  document.getElementById('recordCount').textContent =
    shown === total ? `${total} responses` : `${shown} of ${total} responses`;
}

// ── detail panel ────────────────────────────────────────────────────────────
const CLIP_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';

function fileList(label, files) {
  if (!files.length) return '';
  return `<div class="qa-section"><div class="qa-label">${esc(label)}</div><div class="file-list">
    ${files.map(f => `<div class="file-item">${CLIP_SVG}${esc(f)}</div>`).join('')}
  </div></div>`;
}

function showDetail(row, { toggle = true } = {}) {
  if (toggle && state.currentRowId === row.row_id) { closeDetail(); return; }
  state.currentRowId = row.row_id;
  history.replaceState(null, '', `#${row.row_id}`);

  const multi = state.dockets.length > 1;
  const siblings = state.byRequest.get(requestKey(row)) || [row];
  const related = siblings.length > 1 ? `
    <div class="qa-section"><div class="qa-label">All responses to this request</div><div class="file-list">
      ${siblings.map(s => `<button class="related-item${s.row_id === row.row_id ? ' current' : ''}" data-row="${esc(s.row_id)}">
        #${s.response_seq} · filed ${esc(s.filed_date)} · ${esc(s.source_file)}</button>`).join('')}
    </div></div>` : '';

  const inner = document.getElementById('detailInner');
  inner.innerHTML = `
    <div class="detail-header">
      <div class="detail-meta">
        <div class="detail-doc-id">${esc(row.doc_id)}</div>
        <div class="detail-sub">
          ${multi ? badge(`docket:${row.docket_id}`, `Docket ${row.docket_id}`) : ''}
          ${badge(`party:${row.party}`, row.party)}
          <span>Filed ${esc(row.filed_date)}</span>
          ${tags(row, true)}
        </div>
        <div class="detail-links">
          <a href="${esc(row.filing_url)}" target="_blank" rel="noopener">View filing on PSC site ↗</a>
          ${row.source_url ? `<a href="${esc(row.source_url)}" rel="noopener">Download ${esc(row.source_file)}</a>` : ''}
        </div>
      </div>
      <button class="detail-close" title="Close (Esc)" aria-label="Close">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="detail-body">
      <div class="qa-section"><div class="qa-label">Question</div><div class="qa-text question">${esc(row.question || '—')}</div></div>
      <div class="qa-section"><div class="qa-label">Response</div><div class="qa-text response">${esc(row.response || '—')}</div></div>
      ${related}
      ${fileList('Attachments (in the filing download)', splitList(row.attachments))}
      ${fileList('Confidential / trade secret files', splitList(row.confidential_files))}
    </div>`;

  inner.querySelector('.detail-close').addEventListener('click', closeDetail);
  inner.querySelectorAll('.related-item:not(.current)').forEach(b =>
    b.addEventListener('click', () => selectRow(b.dataset.row)));
  inner.querySelector('.detail-body').scrollTop = 0;
  document.getElementById('detailPanel').classList.add('open');
}

function selectRow(rowId) {
  const node = gridApi.getRowNode(rowId);
  if (!node) return;
  node.setSelected(true, true);
  if (node.displayed) gridApi.ensureNodeVisible(node, 'middle');
  showDetail(node.data, { toggle: false });
}

function closeDetail() {
  state.currentRowId = null;
  history.replaceState(null, '', location.pathname + location.search);
  document.getElementById('detailPanel').classList.remove('open');
  gridApi.deselectAll();
}

// ── init ────────────────────────────────────────────────────────────────────
function init(data) {
  state.rows = data.rows;
  state.dockets = data.dockets;
  state.parties = [...new Set(data.rows.map(r => r.party))].sort();
  state.activeDockets = new Set(state.dockets.map(d => d.id));
  state.activeParties = new Set(state.parties);

  state.dockets.forEach((d, i) => state.colors.set(`docket:${d.id}`, PALETTE[i % PALETTE.length]));
  state.parties.forEach((p, i) => state.colors.set(`party:${p}`, PALETTE[i % PALETTE.length]));

  for (const r of state.rows) {
    const k = requestKey(r);
    state.latestSeq.set(k, Math.max(state.latestSeq.get(k) || 0, r.response_seq));
    if (!state.byRequest.has(k)) state.byRequest.set(k, []);
    state.byRequest.get(k).push(r);
  }
  for (const list of state.byRequest.values()) list.sort((a, b) => a.response_seq - b.response_seq);

  const multiDocket = state.dockets.length > 1;
  const partyLabels = [...new Set(state.dockets.map(d => d.party_label))];
  document.getElementById('subtitle').textContent = multiDocket
    ? state.dockets.map(d => d.label).join(' · ')
    : (state.dockets[0] && (state.dockets[0].subtitle || state.dockets[0].label)) || '';
  document.getElementById('footer').textContent =
    `Latest filing ${data.latest_filing || '—'} · built ${data.built} · source: psc.ga.gov`;

  gridApi = agGrid.createGrid(document.getElementById('myGrid'), {
    columnDefs: columnDefs(multiDocket, partyLabels.length === 1 ? partyLabels[0] : 'Party'),
    rowData: state.rows,
    getRowId: p => p.data.row_id,
    defaultColDef: { resizable: true, sortable: true },
    rowSelection: 'single',
    suppressCellFocus: true,
    isExternalFilterPresent,
    doesExternalFilterPass,
    onRowClicked: e => showDetail(e.data),
    onFilterChanged: updateCount,
    onGridReady: updateCount,
    rowHeight: 108,
    headerHeight: 38,
    tooltipShowDelay: 400,
  });

  buildChips('docketFilters', state.dockets.map(d => ({ value: d.id, label: d.label })), state.activeDockets, 'docket');
  buildChips('partyFilters', state.parties.map(p => ({ value: p, label: p })), state.activeParties, 'party');

  document.getElementById('quickFilter').addEventListener('input', e =>
    gridApi.setGridOption('quickFilterText', e.target.value));
  document.getElementById('latestOnly').addEventListener('change', e => {
    state.latestOnly = e.target.checked;
    gridApi.onFilterChanged();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });

  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash) selectRow(hash);
}

fetch('data.json')
  .then(r => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
  .then(init)
  .catch(err => {
    document.getElementById('myGrid').textContent = `Could not load data: ${err.message}`;
    console.error(err);
  });
