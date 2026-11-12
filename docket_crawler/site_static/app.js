'use strict';

// ── Storage (per browser; every access may throw in private mode) ───────────
const STORE_STARS = 'gpsc-explorer.stars.v1';
const STORE_VIEW = 'gpsc-explorer.view.v1';
function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}

// ── State ───────────────────────────────────────────────────────────────────
const S = {
  dockets: [],            // [{id, label, subtitle, party_label}]
  requests: [],           // one per (docket, doc_id), responses in filing order
  byKey: new Map(),       // "docket|doc_id" -> request
  docket: null,
  party: null,            // null = all staff
  set: null,              // null = all sets of the party
  query: '',
  terms: [],
  starredOnly: false,
  stars: new Set(load(STORE_STARS, [])),
  selected: null,         // request key
  visible: [],            // requests currently listed, in reading order
  built: '', latest: '',
};

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const fmtDate = iso => {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
};
const splitList = s => (s || '').split(';').map(x => x.trim()).filter(Boolean);
const setName = r => `STF-${r.party}-${r.set_no}`;
const docketOf = id => S.dockets.find(d => d.id === id) || { id, label: `Docket ${id}`, subtitle: '', party_label: 'Staff' };

const ICON_STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6-5.4-2.9-5.4 2.9 1.1-6-4.5-4.2 6.1-.8z"/></svg>';
const ICON_CLIP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg>';

// ── Data ────────────────────────────────────────────────────────────────────
function buildRequests(rows) {
  const map = new Map();
  for (const r of rows) {
    const key = `${r.docket_id}|${r.doc_id}`;
    if (!map.has(key)) {
      map.set(key, {
        key, docket: r.docket_id, doc_id: r.doc_id, party: r.party,
        set_no: r.set_no, item_no: r.item_no, responses: [],
      });
    }
    map.get(key).responses.push(r);
  }
  for (const req of map.values()) {
    req.responses.sort((a, b) => a.response_seq - b.response_seq);
    const first = req.responses[0];
    req.question = first.question;
    req.filed = first.filed_date;
    req.pd = req.responses.some(r => r.is_pd === 'yes');
    req.ts = req.responses.some(r => r.has_trade_secret === 'yes');
    req.supps = req.responses.length - 1;
    req.attachments = new Set(req.responses.flatMap(r => splitList(r.attachments))).size;
    req.hay = [req.doc_id, req.question, ...req.responses.map(r => r.response), ...req.responses.map(r => r.attachments)]
      .join('\n').toLowerCase();
  }
  const order = new Map(S.dockets.map((d, i) => [d.id, i]));
  return [...map.values()].sort((a, b) =>
    (order.get(a.docket) - order.get(b.docket))
    || a.party.localeCompare(b.party) || a.set_no - b.set_no || a.item_no - b.item_no);
}

const partiesCache = new Map();
function partiesOf(docket) {
  if (partiesCache.has(docket)) return partiesCache.get(docket);
  const parties = new Map();
  for (const r of S.requests) {
    if (r.docket !== docket) continue;
    if (!parties.has(r.party)) parties.set(r.party, new Map());
    const sets = parties.get(r.party);
    if (!sets.has(r.set_no)) sets.set(r.set_no, { n: 0, filed: r.filed });
    const s = sets.get(r.set_no);
    s.n += 1;
    if (r.filed < s.filed) s.filed = r.filed;
  }
  partiesCache.set(docket, parties);
  return parties;
}

// ── Search ──────────────────────────────────────────────────────────────────
function parseQuery(q) {
  return (q.match(/"[^"]+"|\S+/g) || [])
    .map(t => t.replace(/^"|"$/g, '').toLowerCase().trim())
    .filter(t => t.length >= 2);
}
const matches = (req, terms) => terms.every(t => req.hay.includes(t));
const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Escape text and wrap search terms in <mark>. */
function hl(text, terms = S.terms) {
  if (!terms.length || !text) return esc(text);
  const re = new RegExp(`(${terms.map(reEscape).join('|')})`, 'gi');
  return String(text).split(re).map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join('');
}

function snippet(text, terms, width = 150) {
  const lower = text.toLowerCase();
  let at = -1;
  for (const t of terms) { const i = lower.indexOf(t); if (i >= 0 && (at < 0 || i < at)) at = i; }
  if (at < 0) return null;
  const start = Math.max(0, at - 50);
  const s = text.slice(start, start + width).replace(/\s+/g, ' ');
  return (start > 0 ? '…' : '') + s + (start + width < text.length ? '…' : '');
}

// ── Filtering ───────────────────────────────────────────────────────────────
function computeVisible() {
  return S.requests.filter(r => {
    if (S.starredOnly) { if (!S.stars.has(r.key)) return false; }
    else {
      if (r.docket !== S.docket) return false;
      if (S.party && r.party !== S.party) return false;
      if (S.party && S.set != null && r.set_no !== S.set) return false;
    }
    return !S.terms.length || matches(r, S.terms);
  });
}

function saveView() {
  save(STORE_VIEW, { docket: S.docket, party: S.party, set: S.set, selected: S.selected });
}

// ── Rendering: top bar ──────────────────────────────────────────────────────
function renderDocketSwitch() {
  const el = $('docketSwitch');
  el.hidden = S.dockets.length < 2;
  el.innerHTML = S.dockets.map(d => {
    const n = S.requests.filter(r => r.docket === d.id).length;
    return `<button type="button" data-docket="${esc(d.id)}" aria-pressed="${!S.starredOnly && d.id === S.docket}">
      <span class="num">${esc(d.id)}</span><span class="sub">${n.toLocaleString()} requests</span></button>`;
  }).join('');
}

function renderStarCount() {
  $('starCount').textContent = S.stars.size;
  $('starredToggle').setAttribute('aria-pressed', String(S.starredOnly));
}

function latestFiling(docket) {
  let latest = '';
  for (const r of S.requests) {
    if (r.docket !== docket) continue;
    for (const x of r.responses) if (x.filed_date > latest) latest = x.filed_date;
  }
  return latest || S.latest;
}

// ── Rendering: sidebar ──────────────────────────────────────────────────────
function renderSidebar() {
  const d = docketOf(S.docket);
  const parties = partiesOf(S.docket);
  const total = S.requests.filter(r => r.docket === S.docket).length;
  const partyItems = [...parties.keys()].sort().map(p => {
    const sets = parties.get(p);
    const n = [...sets.values()].reduce((a, s) => a + s.n, 0);
    const open = S.party === p;
    const setItems = open ? `<div class="side-sets">${[...sets.keys()].sort((a, b) => a - b).map(no => {
      const s = sets.get(no);
      return `<button type="button" class="side-item" data-party="${esc(p)}" data-set="${no}" aria-current="${S.set === no}">
        <span class="label">Set ${no}</span><span class="date">${esc(fmtDate(s.filed))}</span><span class="n">${s.n}</span></button>`;
    }).join('')}</div>` : '';
    return `<button type="button" class="side-item" data-party="${esc(p)}" aria-current="${open && S.set == null}" aria-expanded="${open}">
      <span class="code">${esc(p)}</span><span class="n">${n}</span></button>${setItems}`;
  }).join('');

  $('sidebar').innerHTML = `
    <div class="side-docket"><h2>${esc(d.label)}</h2><p>${esc(d.subtitle)}</p></div>
    <div class="side-list">
      <button type="button" class="side-item" data-party="" aria-current="${!S.party}"><span class="label">All ${esc(d.party_label.toLowerCase())}</span><span class="n">${total}</span></button>
    </div>
    <div class="side-label">${esc(d.party_label)} member</div>
    <div class="side-list">${partyItems}</div>
    <div class="side-foot">Latest filing ${esc(fmtDate(latestFiling(S.docket)))}<br>Updated ${esc(fmtDate(S.built))}<br>Source: <a href="https://psc.ga.gov" target="_blank" rel="noopener">psc.ga.gov</a></div>`;
}

// ── Rendering: list ─────────────────────────────────────────────────────────
function renderListHead() {
  const parts = [];
  if (S.starredOnly) {
    parts.push(`<div class="list-title"><h2>Starred requests</h2><span class="n">${S.visible.length}</span>
      <span class="actions">${S.stars.size ? '<button type="button" class="link-btn" data-action="copy-stars">Copy link to this list</button>' : ''}</span></div>`);
  } else {
    const d = docketOf(S.docket);
    const parties = partiesOf(S.docket);
    const title = S.party ? (S.set != null ? `STF-${S.party}-${S.set}` : `${d.party_label} ${S.party}`) : `All ${d.party_label.toLowerCase()}`;
    parts.push(`<div class="list-title"><h2 class="${S.party ? 'mono' : ''}">${esc(title)}</h2><span class="n">${S.visible.length} request${S.visible.length === 1 ? '' : 's'}</span></div>`);
    const partyOpts = [...parties.keys()].sort().map(p => `<option value="${esc(p)}" ${p === S.party ? 'selected' : ''}>${esc(p)}</option>`).join('');
    const setOpts = S.party ? [...parties.get(S.party).keys()].sort((a, b) => a - b)
      .map(no => `<option value="${no}" ${no === S.set ? 'selected' : ''}>Set ${no}</option>`).join('') : '';
    parts.push(`<div class="list-filters">
      <select id="partySelect" aria-label="${esc(d.party_label)} member"><option value="">All ${esc(d.party_label.toLowerCase())}</option>${partyOpts}</select>
      <select id="setSelect" aria-label="Request set" ${S.party ? '' : 'disabled'}><option value="">All sets</option>${setOpts}</select></div>`);
  }
  if (S.terms.length && !S.starredOnly) {
    const others = S.dockets.filter(d => d.id !== S.docket).map(d => ({
      d, n: S.requests.filter(r => r.docket === d.id && matches(r, S.terms)).length,
    })).filter(x => x.n);
    for (const { d, n } of others) {
      parts.push(`<div class="notice">${n} match${n === 1 ? '' : 'es'} in ${esc(d.label)}
        <button type="button" class="link-btn" data-docket-switch="${esc(d.id)}">Show</button></div>`);
    }
  }
  if (S.importStars?.length) {
    parts.push(`<div class="notice">This link includes ${S.importStars.length} starred request${S.importStars.length === 1 ? '' : 's'}.
      <button type="button" class="link-btn" data-action="import-stars">Add to my stars</button>
      <button type="button" class="link-btn" data-action="dismiss-import">Dismiss</button></div>`);
  }
  $('listHead').innerHTML = parts.join('');
}

function chipsFor(req) {
  const c = [];
  if (req.pd) c.push('<span class="chip chip-pd" title="Public disclosure: redacted version; a trade-secret version was filed confidentially">PD</span>');
  else if (req.ts) c.push('<span class="chip chip-ts" title="Some material for this response was filed as trade secret">TS</span>');
  if (req.supps) c.push(`<span class="chip chip-sp" title="${req.supps} supplemental response${req.supps === 1 ? '' : 's'}">+${req.supps} supp</span>`);
  if (req.attachments) c.push(`<span class="chip chip-att" title="${req.attachments} attachment${req.attachments === 1 ? '' : 's'}">${ICON_CLIP.replace('width="14" height="14"', 'width="11" height="11"')}${req.attachments}</span>`);
  return c.join('');
}

function rowText(req) {
  if (!S.terms.length) return esc(req.question);
  const q = req.question.toLowerCase();
  if (S.terms.some(t => q.includes(t))) return hl(req.question);
  for (const r of req.responses) {
    const s = snippet(r.response, S.terms);
    if (s) return `<span class="where">Response:</span> ${hl(s)}`;
  }
  return esc(req.question);
}

function renderList() {
  const body = $('listBody');
  if (!S.visible.length) {
    body.innerHTML = `<div class="empty">${S.starredOnly
      ? (S.stars.size ? 'No starred requests match this search.' : 'No starred requests yet. Star a request with ☆ or press <kbd>s</kbd> while reading it.')
      : 'No requests match. Try fewer words, or search all staff.'}</div>`;
    return;
  }
  const multiDocket = S.starredOnly && new Set(S.visible.map(r => r.docket)).size > 1;
  const out = [];
  let group = null;
  for (const r of S.visible) {
    const g = `${r.docket}|${r.party}|${r.set_no}`;
    if (g !== group) {
      group = g;
      const sets = partiesOf(r.docket).get(r.party);
      const info = sets && sets.get(r.set_no);
      out.push(`<div class="group" role="presentation">
        ${multiDocket || S.starredOnly ? `<span class="chip chip-docket">${esc(r.docket)}</span>` : ''}
        <span class="set">${esc(setName(r))}</span>
        <span class="meta">${info ? `${info.n} requests · filed ${esc(fmtDate(info.filed))}` : ''}</span></div>`);
    }
    const starred = S.stars.has(r.key);
    out.push(`<div class="req" role="option" id="req-${cssId(r.key)}" data-key="${esc(r.key)}" aria-selected="${r.key === S.selected}">
      <button type="button" class="star-btn" data-star="${esc(r.key)}" aria-pressed="${starred}" aria-label="${starred ? 'Unstar' : 'Star'} ${esc(r.doc_id)}">${ICON_STAR}</button>
      <div class="req-top"><span class="req-id">${hl(r.doc_id)}</span>${chipsFor(r)}<span class="req-date">${esc(r.filed)}</span></div>
      <div class="req-text">${rowText(r)}</div></div>`);
  }
  body.innerHTML = out.join('');
}

const cssId = key => key.replace(/[^A-Za-z0-9-]/g, '_');

function scrollSelectedIntoView() {
  const el = S.selected && document.getElementById(`req-${cssId(S.selected)}`);
  if (el) el.scrollIntoView({ block: 'nearest' });
}

// ── Rendering: reader ───────────────────────────────────────────────────────
/** Render parsed text: blank-free lines become paragraphs; runs of "a | b" lines become tables. */
function richText(text) {
  const lines = String(text || '').split('\n');
  const out = [];
  let table = [];
  const flush = () => {
    if (!table.length) return;
    out.push(`<div class="table-wrap"><table>${table.map(row =>
      `<tr>${row.split(' | ').map(c => `<td>${hl(c)}</td>`).join('')}</tr>`).join('')}</table></div>`);
    table = [];
  };
  for (const line of lines) {
    if (line.includes(' | ')) { table.push(line); continue; }
    flush();
    if (line.trim()) out.push(`<p>${hl(line)}</p>`);
  }
  flush();
  return out.join('');
}

function neighbours() {
  const i = S.visible.findIndex(r => r.key === S.selected);
  return { i, prev: i > 0 ? S.visible[i - 1] : null, next: i >= 0 && i < S.visible.length - 1 ? S.visible[i + 1] : null };
}

function renderReader() {
  const reader = $('reader');
  const req = S.byKey.get(S.selected);
  if (!req) {
    reader.innerHTML = `<div class="reader-inner"><div class="empty">Choose a request from the list to read it here.</div></div>`;
    return;
  }
  const d = docketOf(req.docket);
  const { i, prev, next } = neighbours();
  const starred = S.stars.has(req.key);
  const setInfo = partiesOf(req.docket).get(req.party)?.get(req.set_no);

  const responses = req.responses.map((r, idx) => {
    const label = idx === 0 ? 'Response' : `Supplemental response ${idx}`;
    const atts = splitList(r.attachments);
    const conf = splitList(r.confidential_files);
    return `<section class="response" aria-label="${esc(label)}">
      <div class="response-head"><strong>${esc(label)}</strong><span>filed ${esc(fmtDate(r.filed_date))}</span>
        ${r.is_pd === 'yes' ? '<span class="chip chip-pd">Public disclosure · redacted</span>' : ''}
        ${r.is_pd !== 'yes' && r.has_trade_secret === 'yes' ? '<span class="chip chip-ts">Trade-secret material withheld</span>' : ''}
        <span class="links"><a href="${esc(r.filing_url)}" target="_blank" rel="noopener">Filing on psc.ga.gov ↗</a>
        ${r.source_url ? `<a href="${esc(r.source_url)}" rel="noopener" title="${esc(r.source_file)}">Download files</a>` : ''}</span></div>
      <div class="prose">${r.response ? richText(r.response) : '<p class="empty-response">No text in this response (it may be an image or attachment only).</p>'}</div>
      ${atts.length || conf.length ? `<div class="files">
        ${atts.map(f => `<div class="file">${ICON_CLIP}<span>${hl(f)}</span></div>`).join('')}
        ${conf.map(f => `<div class="file">${ICON_CLIP}<span>${esc(f)} <span class="files-note">(trade secret, not public)</span></span></div>`).join('')}
        <div class="files-note">Attachments are inside the filing download.</div></div>` : ''}
    </section>`;
  }).join('');

  reader.innerHTML = `<div class="reader-inner">
    <div class="reader-nav">
      <button type="button" class="btn back" data-action="back" aria-label="Back to list">← List</button>
      <div class="crumbs">
        <span>${esc(d.label)}</span><span class="sep">/</span>
        <span>${esc(d.party_label)} ${esc(req.party)}</span><span class="sep">/</span>
        <span>Set ${req.set_no}</span>
        ${setInfo ? `<span class="sep">·</span><span>item ${req.item_no} of ${setInfo.n}</span>` : ''}
      </div>
      <div class="pager">
        <button type="button" class="btn" data-go="${prev ? esc(prev.key) : ''}" ${prev ? '' : 'disabled'} title="Previous (↑ or k)">↑ <span class="id">${prev ? esc(prev.doc_id) : ''}</span></button>
        <button type="button" class="btn" data-go="${next ? esc(next.key) : ''}" ${next ? '' : 'disabled'} title="Next (↓ or j)">↓ <span class="id">${next ? esc(next.doc_id) : ''}</span></button>
      </div>
    </div>
    <div class="reader-title">
      <h2>${esc(req.doc_id)}</h2>
      <button type="button" class="star-btn" data-star="${esc(req.key)}" aria-pressed="${starred}" aria-label="${starred ? 'Unstar' : 'Star'} this request" title="${starred ? 'Unstar' : 'Star'} (s)">${ICON_STAR}</button>
      <div class="reader-chips">${req.supps ? `<span class="chip chip-sp">${req.supps} supplemental</span>` : ''}</div>
    </div>
    <section class="question" aria-label="Question"><div class="section-label">Question</div><div class="prose">${richText(req.question)}</div></section>
    ${responses}
    <div class="kbd-help"><kbd>↑</kbd> <kbd>↓</kbd> or <kbd>k</kbd> <kbd>j</kbd> previous / next · <kbd>s</kbd> star · <kbd>/</kbd> search${i < 0 ? ' · This request is outside the current list.' : ''}</div>
  </div>`;
}

// ── Actions ─────────────────────────────────────────────────────────────────
function refresh({ keepScroll = false } = {}) {
  S.visible = computeVisible();
  if (!S.byKey.has(S.selected) || (!S.visible.some(r => r.key === S.selected) && S.visible.length && !keepScroll)) {
    S.selected = S.visible[0]?.key ?? S.selected;
  }
  renderDocketSwitch();
  renderStarCount();
  renderSidebar();
  renderListHead();
  renderList();
  renderReader();
  if (!keepScroll) scrollSelectedIntoView();
  saveView();
}

function select(key, { openOnPhone = true } = {}) {
  if (!S.byKey.has(key)) return;
  const prevSel = document.querySelector('.req[aria-selected="true"]');
  if (prevSel) prevSel.setAttribute('aria-selected', 'false');
  S.selected = key;
  const el = document.getElementById(`req-${cssId(key)}`);
  if (el) { el.setAttribute('aria-selected', 'true'); el.scrollIntoView({ block: 'nearest' }); }
  renderReader();
  $('reader').scrollTop = 0;
  const req = S.byKey.get(key);
  history.replaceState(null, '', `#${req.responses[0].row_id}`);
  if (openOnPhone) $('layout').classList.add('reading');
  saveView();
}

function step(delta) {
  const { i } = neighbours();
  const target = S.visible[i < 0 ? 0 : i + delta];
  if (target) select(target.key, { openOnPhone: false });
}

function toggleStar(key) {
  if (S.stars.has(key)) S.stars.delete(key); else S.stars.add(key);
  save(STORE_STARS, [...S.stars]);
  const on = S.stars.has(key);
  document.querySelectorAll(`[data-star="${CSS.escape(key)}"]`).forEach(b => {
    b.setAttribute('aria-pressed', String(on));
    b.setAttribute('aria-label', `${on ? 'Unstar' : 'Star'} ${S.byKey.get(key)?.doc_id || ''}`);
  });
  renderStarCount();
  if (S.starredOnly) refresh({ keepScroll: true });
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2200);
}

async function copyStarsLink() {
  const list = [...S.stars].map(k => k.replace('|', ':')).join(',');
  const url = `${location.origin}${location.pathname}#stars=${encodeURIComponent(list)}`;
  try {
    await navigator.clipboard.writeText(url);
    toast(`Copied a link to ${S.stars.size} starred request${S.stars.size === 1 ? '' : 's'}`);
  } catch {
    window.prompt('Copy this link:', url);
  }
}

function setDocket(id) {
  S.docket = id; S.party = null; S.set = null; S.starredOnly = false;
  refresh();
}

// ── Events ──────────────────────────────────────────────────────────────────
function wire() {
  $('docketSwitch').addEventListener('click', e => {
    const b = e.target.closest('[data-docket]');
    if (b) setDocket(b.dataset.docket);
  });

  $('sidebar').addEventListener('click', e => {
    const b = e.target.closest('.side-item');
    if (!b) return;
    const party = b.dataset.party || null;
    const set = b.dataset.set != null ? Number(b.dataset.set) : null;
    if (set == null && party && S.party === party && S.set == null) {
      S.party = null;  // clicking the open staff member again collapses it
    } else {
      S.party = party; S.set = set;
    }
    refresh();
  });

  $('listHead').addEventListener('change', e => {
    if (e.target.id === 'partySelect') { S.party = e.target.value || null; S.set = null; refresh(); }
    if (e.target.id === 'setSelect') { S.set = e.target.value === '' ? null : Number(e.target.value); refresh(); }
  });
  $('listHead').addEventListener('click', e => {
    const sw = e.target.closest('[data-docket-switch]');
    if (sw) { S.docket = sw.dataset.docketSwitch; S.party = null; S.set = null; refresh(); return; }
    const a = e.target.closest('[data-action]');
    if (!a) return;
    if (a.dataset.action === 'copy-stars') copyStarsLink();
    if (a.dataset.action === 'import-stars') {
      S.importStars.forEach(k => { if (S.byKey.has(k)) S.stars.add(k); });
      save(STORE_STARS, [...S.stars]);
      toast(`Added ${S.importStars.length} starred request${S.importStars.length === 1 ? '' : 's'}`);
      S.importStars = null; S.starredOnly = true; refresh();
    }
    if (a.dataset.action === 'dismiss-import') { S.importStars = null; renderListHead(); }
  });

  $('listBody').addEventListener('click', e => {
    const star = e.target.closest('[data-star]');
    if (star) { toggleStar(star.dataset.star); return; }
    const row = e.target.closest('.req');
    if (row) select(row.dataset.key);
  });

  $('reader').addEventListener('click', e => {
    const star = e.target.closest('[data-star]');
    if (star) { toggleStar(star.dataset.star); return; }
    const go = e.target.closest('[data-go]');
    if (go && go.dataset.go) { select(go.dataset.go, { openOnPhone: false }); return; }
    if (e.target.closest('[data-action="back"]')) $('layout').classList.remove('reading');
  });

  $('starredToggle').addEventListener('click', () => {
    S.starredOnly = !S.starredOnly;
    refresh();
  });

  let timer;
  $('search').addEventListener('input', e => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      S.query = e.target.value;
      S.terms = parseQuery(S.query);
      refresh();
    }, 120);
  });

  document.addEventListener('keydown', e => {
    const typing = e.target.matches('input, select, textarea');
    if (e.key === 'Escape') {
      if (typing && e.target.value) { e.target.value = ''; e.target.dispatchEvent(new Event('input')); }
      else if ($('layout').classList.contains('reading')) $('layout').classList.remove('reading');
      else if (typing) e.target.blur();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); $('search').focus(); }
    else if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1); }
    else if (e.key === 's' && S.selected) toggleStar(S.selected);
  });
}

// ── Start ───────────────────────────────────────────────────────────────────
function readHash(rows) {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith('stars=')) {
    S.importStars = h.slice(6).split(',').map(s => s.replace(':', '|')).filter(k => S.byKey.has(k) && !S.stars.has(k));
    history.replaceState(null, '', location.pathname + location.search);
    return null;
  }
  const row = h && rows.find(r => r.row_id === h);
  return row ? `${row.docket_id}|${row.doc_id}` : null;
}

function start(data) {
  S.dockets = data.dockets;
  S.built = data.built;
  S.latest = data.latest_filing;
  S.requests = buildRequests(data.rows);
  S.byKey = new Map(S.requests.map(r => [r.key, r]));
  for (const k of [...S.stars]) if (!S.byKey.has(k)) S.stars.delete(k);

  const view = load(STORE_VIEW, {});
  const fromHash = readHash(data.rows);
  const target = fromHash || (S.byKey.has(view.selected) ? view.selected : null);
  S.docket = (target && S.byKey.get(target).docket)
    || (S.dockets.some(d => d.id === view.docket) ? view.docket : S.dockets[0]?.id);
  if (!fromHash && view.docket === S.docket) { S.party = view.party ?? null; S.set = view.set ?? null; }
  S.selected = target;

  const q = new URLSearchParams(location.search).get('q');
  if (q) { $('search').value = q; S.query = q; S.terms = parseQuery(q); }

  wire();
  refresh();
  if (fromHash) $('layout').classList.add('reading');
}

fetch('data.json')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(start)
  .catch(err => {
    $('listBody').innerHTML = `<div class="empty">Could not load the data (${esc(err.message)}). Reload the page to try again.</div>`;
    console.error(err);
  });
