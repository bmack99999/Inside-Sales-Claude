// Shared mix-adjusted pivot logic. Used by /kpis and /team-view.
// Set window.MIX_VIEW_LABELS = {me: 'Bryce', possessive: "Bryce's"} before load to relabel.
const MIX_LBL = Object.assign({me: 'Me', possessive: 'my'}, window.MIX_VIEW_LABELS || {});

let _mixData = null;
let _mixMonthKey = '';
let _mixSource = '';
let _mixValid = false;
let _mixUW = false;
let _mixSortCol = 'index_pct';
let _mixSortAsc = false;
let _mixExpanded = new Set();
let _mixTrendChart = null;

function _activeMix() {
  if (_mixMonthKey && _mixData.monthly && _mixData.monthly[_mixMonthKey]) {
    return _mixData.monthly[_mixMonthKey];
  }
  return _mixData;
}

function _bs4(arr) { return [arr[0] || 0, arr[1] || 0, arr[2] || 0, arr[3] || 0, arr[4] || 0]; }

function _effRates(mix) {
  // Team close rate per source, honoring the invalid-leads toggle.
  const m = {};
  (mix.source_rates || []).forEach(s => {
    const l = s.leads - (_mixValid ? (s.invalid || 0) : 0);
    const w = s.won + (_mixUW ? (s.uw || 0) : 0);
    m[s.source] = l > 0 ? w / l : 0;
  });
  return m;
}

function _repTotals(r, rates, source) {
  let L = 0, C = 0, W = 0, E = 0, INV = 0;
  const bs = r.by_source || {};
  const keys = source ? (bs[source] ? [source] : []) : Object.keys(bs);
  keys.forEach(s => {
    const [l, c, w, inv, u] = _bs4(bs[s]);
    const le = Math.max(0, l - (_mixValid ? inv : 0));
    const we = w + (_mixUW ? u : 0);
    L += le; C += c; W += we; INV += inv; E += le * (rates[s] || 0);
  });
  return {
    name: r.name, is_me: r.is_me, leads: L, converted: C, won: W, invalid: INV,
    conv_pct: L ? +(C / L * 100).toFixed(1) : 0,
    actual_pct: L ? +(W / L * 100).toFixed(1) : 0,
    expected_won: +E.toFixed(1),
    expected_pct: L ? +(E / L * 100).toFixed(1) : 0,
    index_pct: E > 0 ? +((W / E - 1) * 100).toFixed(1) : 0,
  };
}

function _mixRows() {
  const mix = _activeMix();
  const rates = _effRates(mix);
  return (mix.reps || []).map(r => _repTotals(r, rates, _mixSource))
    .filter(r => r.leads > 0 || r.won > 0);
}

function _pctCell(v, leads) { return leads ? v + '%' : '—'; }

function _repDetailHTML(name) {
  const mix = _activeMix();
  const rates = _effRates(mix);
  const rep = (mix.reps || []).find(r => r.name === name);
  if (!rep) return '';
  const rows = Object.entries(rep.by_source || {}).map(([s, arr]) => {
    const [l, c, w0, inv, u] = _bs4(arr);
    const w = w0 + (_mixUW ? u : 0);
    const le = Math.max(0, l - (_mixValid ? inv : 0));
    const exp = le * (rates[s] || 0);
    return { s, le, c, w, inv, exp };
  }).filter(r => r.le > 0 || r.w > 0).sort((a, b) => b.le - a.le);
  const inner = rows.map(r => `<tr>
    <td style="text-align:left;padding:3px 8px">${r.s}</td>
    <td style="padding:3px 8px">${r.le}</td>
    <td style="padding:3px 8px">${r.c}</td>
    <td style="padding:3px 8px">${_pctCell(r.le ? (r.c / r.le * 100).toFixed(1) : 0, r.le)}</td>
    <td style="padding:3px 8px"><strong>${r.w}</strong></td>
    <td style="padding:3px 8px">${_pctCell(r.le ? (r.w / r.le * 100).toFixed(1) : 0, r.le)}</td>
    <td style="padding:3px 8px;color:#6b7280">${r.exp.toFixed(1)}</td>
    <td style="padding:3px 8px;color:${r.w >= r.exp ? '#059669' : '#dc2626'}">${r.exp > 0 ? ((r.w / r.exp - 1) * 100).toFixed(0) + '%' : '—'}</td>
  </tr>`).join('');
  return `<tr class="mix-detail-row"><td colspan="10" style="background:#f8fafc;padding:8px 16px 12px">
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead><tr style="color:#6b7280;text-align:right">
        <th style="text-align:left;padding:3px 8px">Source</th><th style="padding:3px 8px">Leads</th>
        <th style="padding:3px 8px">Conv</th><th style="padding:3px 8px">Conv%</th>
        <th style="padding:3px 8px">Won</th><th style="padding:3px 8px">Close%</th>
        <th style="padding:3px 8px">Exp</th><th style="padding:3px 8px">Vs Exp</th>
      </tr></thead>
      <tbody style="text-align:right">${inner}</tbody>
    </table>
  </td></tr>`;
}

function renderMixTable() {
  const rows = _mixRows();
  rows.sort((a, b) => {
    if (_mixSortCol === 'name') {
      return _mixSortAsc ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
    }
    const va = a[_mixSortCol] || 0, vb = b[_mixSortCol] || 0;
    return _mixSortAsc ? va - vb : vb - va;
  });

  document.querySelectorAll('.mix-th-sort').forEach(th => {
    const col = th.dataset.col;
    const arrow = col === _mixSortCol ? (_mixSortAsc ? ' ▲' : ' ▼') : ' ⇅';
    th.textContent = th.textContent.replace(/ [▲▼⇅]$/, '') + arrow;
    th.style.color = col === _mixSortCol ? '#1d4ed8' : '';
  });

  document.getElementById('mix-tbody').innerHTML = rows.map((r, i) => {
    const rowCls = r.is_me ? 'background:#e8f0fe;font-weight:600;' : '';
    const pos = r.index_pct >= 0;
    const open = _mixExpanded.has(r.name);
    const caret = open ? '▾' : '▸';
    return `<tr style="${rowCls}cursor:pointer" data-rep="${r.name}" title="Click to expand source breakdown">
      <td style="color:#888;font-size:12px">${i + 1}</td>
      <td style="text-align:left"><span style="color:#9ca3af;font-size:10px">${caret}</span> ${r.is_me ? r.name + ' ◀' : r.name}</td>
      <td>${r.leads}</td>
      <td>${r.converted}</td>
      <td>${_pctCell(r.conv_pct, r.leads)}</td>
      <td><strong>${r.won}</strong></td>
      <td>${_pctCell(r.actual_pct, r.leads)}</td>
      <td style="color:#6b7280">${r.expected_won}</td>
      <td style="color:#6b7280">${_pctCell(r.expected_pct, r.leads)}</td>
      <td style="color:${r.expected_won < 3 ? '#9ca3af' : (pos ? '#059669' : '#dc2626')};font-weight:600" ${r.expected_won < 3 ? 'title="Small sample — read with caution"' : ''}>${r.expected_won > 0 ? (pos ? '+' : '') + r.index_pct + '%' : '—'}</td>
    </tr>` + (open ? _repDetailHTML(r.name) : '');
  }).join('');

  document.querySelectorAll('#mix-tbody tr[data-rep]').forEach(tr => {
    tr.onclick = () => {
      const n = tr.dataset.rep;
      if (_mixExpanded.has(n)) _mixExpanded.delete(n); else _mixExpanded.add(n);
      renderMixTable();
    };
  });

  const tl = rows.reduce((s, r) => s + r.leads, 0);
  const tc = rows.reduce((s, r) => s + r.converted, 0);
  const tw = rows.reduce((s, r) => s + r.won, 0);
  const te = rows.reduce((s, r) => s + r.expected_won, 0);
  document.getElementById('mix-tfoot').innerHTML = `<tr style="border-top:2px solid #d1d5db;font-weight:700;background:#f9fafb">
    <td></td>
    <td style="text-align:left">TEAM</td>
    <td>${tl}</td>
    <td>${tc}</td>
    <td>${tl ? (tc / tl * 100).toFixed(1) : 0}%</td>
    <td>${tw}</td>
    <td>${tl ? (tw / tl * 100).toFixed(1) : 0}%</td>
    <td style="color:#6b7280">${te.toFixed(1)}</td>
    <td style="color:#6b7280">${tl ? (te / tl * 100).toFixed(1) : 0}%</td>
    <td></td>
  </tr>`;
}

function applyMixWindow() {
  const mix = _activeMix();

  // Header window label
  if (_mixMonthKey && mix.month_label) {
    document.getElementById('mix-window').textContent = mix.month_label;
  } else {
    const ws = mix.window_start || '';
    if (ws) {
      const [y, m, d] = ws.split('-').map(Number);
      document.getElementById('mix-window').textContent = 'since ' +
        new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
  }

  // Summary cards (my numbers for the selected window, honoring the toggle).
  // Not every page has the cards — /team-view shows only the tables.
  const meRep = (mix.reps || []).find(r => r.is_me);
  const me = meRep ? _repTotals(meRep, _effRates(mix), '') : null;
  if (!document.getElementById('mix-actual')) {
    // no cards on this page
  } else if (me && me.leads) {
    document.getElementById('mix-actual').textContent = me.actual_pct + '%';
    document.getElementById('mix-actual-sub').textContent = me.won + ' deals / ' + me.leads + ' leads';
    document.getElementById('mix-expected').textContent = me.expected_pct + '%';
    document.getElementById('mix-expected-sub').textContent = '~' + Math.round(me.expected_won) + ' deals for an average closer';
    const idx = document.getElementById('mix-index');
    const pos = me.index_pct >= 0;
    idx.textContent = (pos ? '+' : '') + me.index_pct + '%';
    idx.style.color = pos ? '#059669' : '#dc2626';
    document.getElementById('mix-index-sub').textContent = pos
      ? 'closing above what ' + MIX_LBL.possessive + ' lead mix predicts'
      : 'below what ' + MIX_LBL.possessive + ' lead mix predicts';
  } else {
    document.getElementById('mix-actual').textContent = '—';
    document.getElementById('mix-actual-sub').textContent = 'no activity this period';
    document.getElementById('mix-expected').textContent = '—';
    document.getElementById('mix-expected-sub').textContent = '';
    const idx = document.getElementById('mix-index');
    idx.textContent = '—'; idx.style.color = '';
    document.getElementById('mix-index-sub').textContent = '';
  }

  // Source dropdown options for this window (keep selection if still valid)
  const srcSelect = document.getElementById('mix-source-select');
  const sources = mix.sources || (mix.source_rates || []).map(s => s.source);
  if (!sources.includes(_mixSource)) _mixSource = '';
  srcSelect.innerHTML = '<option value="">All sources</option>' +
    sources.map(s => `<option value="${s}">${s}</option>`).join('');
  srcSelect.value = _mixSource;

  renderMixTable();
  renderMixSrcTable();
}

let _mixSrcMonthKey = '';

function renderMixSrcTable() {
  // Team totals by source. If the card has its own Period selector, it uses
  // that window; otherwise it follows the pivot's Period dropdown.
  const sel = document.getElementById('mix-src-month-select');
  const key = sel ? _mixSrcMonthKey : _mixMonthKey;
  const mix = (key && _mixData.monthly && _mixData.monthly[key])
    ? _mixData.monthly[key] : _mixData;
  document.getElementById('mix-src-tbody').innerHTML = (mix.source_rates || []).map(s => {
    const le = s.leads - (_mixValid ? (s.invalid || 0) : 0);
    const conv = s.converted != null ? s.converted : 0;
    const we = s.won + (_mixUW ? (s.uw || 0) : 0);
    return `<tr>
      <td style="text-align:left">${s.source}</td>
      <td>${le}</td>
      <td style="color:${_mixValid ? '#dc2626' : '#9ca3af'}">${s.invalid || 0}${_mixValid ? ' excl.' : ''}</td>
      <td>${conv}</td>
      <td>${le ? (conv / le * 100).toFixed(1) + '%' : '—'}</td>
      <td>${we}${_mixUW && (s.uw || 0) ? ` <span style="color:#6b7280;font-size:11px">(${s.won}+${s.uw})</span>` : ''}</td>
      <td><strong>${le ? (we / le * 100).toFixed(1) + '%' : '—'}</strong></td>
    </tr>`;
  }).join('');
}

function _mixTrendSeries(metric) {
  const monthly = (_mixData && _mixData.monthly) || {};
  const keys = Object.keys(monthly).sort();
  const labels = [], meVals = [], teamVals = [], meNotes = [], tmNotes = [];
  const isPct = metric !== 'deals' && metric !== 'expdeals';
  keys.forEach(k => {
    const mix = monthly[k];
    const rates = _effRates(mix);
    const rows = (mix.reps || []).map(r => _repTotals(r, rates, ''));
    const meR = rows.find(r => r.is_me);
    const active = rows.filter(r => r.leads > 0);
    const TL = rows.reduce((a, r) => a + r.leads, 0);
    const TW = rows.reduce((a, r) => a + r.won, 0);
    const TE = rows.reduce((a, r) => a + r.expected_won, 0);
    let meV = null, tmV = null, meN = '', tmN = '';
    if (metric === 'deals') {
      meV = meR ? meR.won : null;
      tmV = active.length ? +(TW / active.length).toFixed(1) : null;
      meN = meR ? meR.won + ' deals from ' + meR.leads + ' leads' : '';
      tmN = 'avg per rep · ' + TW + ' team total';
    } else if (metric === 'expdeals') {
      meV = meR && meR.leads ? meR.expected_won : null;
      tmV = active.length ? +(TE / active.length).toFixed(1) : null;
      meN = meR ? 'what ' + meR.leads + ' leads should produce at team avg rates' : '';
      tmN = 'avg per rep · ' + TE.toFixed(1) + ' team total';
    } else if (metric === 'mvf') {
      const meBs = ((mix.reps || []).find(r => r.is_me) || {}).by_source || {};
      const [ml, , , minv] = _bs4(meBs['MVF'] || []);
      const meMvf = Math.max(0, ml - (_mixValid ? minv : 0));
      const sr = (mix.source_rates || []).find(s => s.source === 'MVF');
      const tMvf = sr ? sr.leads - (_mixValid ? (sr.invalid || 0) : 0) : 0;
      meV = meR && meR.leads ? +(meMvf / meR.leads * 100).toFixed(1) : null;
      tmV = TL ? +(tMvf / TL * 100).toFixed(1) : null;
      meN = meMvf + ' of ' + (meR ? meR.leads : 0) + ' leads';
      tmN = tMvf + ' of ' + TL + ' leads';
    } else if (metric === 'mixq') {
      meV = meR && meR.leads ? meR.expected_pct : null;
      tmV = TL ? +(TE / TL * 100).toFixed(1) : null;
      meN = meR ? '~' + Math.round(meR.expected_won) + ' expected deals' : '';
      tmN = '~' + Math.round(TE) + ' expected deals';
    } else if (metric === 'close') {
      meV = meR && meR.leads ? meR.actual_pct : null;
      tmV = TL ? +(TW / TL * 100).toFixed(1) : null;
      meN = meR ? meR.won + ' deals / ' + meR.leads + ' leads' : '';
      tmN = TW + ' deals / ' + TL + ' leads';
    } else if (metric === 'idx') {
      meV = meR && meR.expected_won > 0 ? meR.index_pct : null;
      tmV = TE > 0 ? +((TW / TE - 1) * 100).toFixed(1) : null;
      meN = meR ? meR.won + ' actual vs ' + meR.expected_won + ' expected' : '';
      tmN = TW + ' actual vs ' + TE.toFixed(1) + ' expected';
    }
    labels.push((mix.month_label || k).split(' ')[0]);
    meVals.push(meV);
    teamVals.push(tmV);
    meNotes.push(meN);
    tmNotes.push(tmN);
  });
  return { labels, meVals, teamVals, meNotes, tmNotes, isPct };
}

function renderMixTrend() {
  const canvas = document.getElementById('mix-trend-canvas');
  if (!canvas || typeof Chart === 'undefined') return;
  const metric = document.getElementById('mix-trend-metric').value;
  const { labels, meVals, teamVals, meNotes, tmNotes, isPct } = _mixTrendSeries(metric);
  if (_mixTrendChart) _mixTrendChart.destroy();
  const unit = isPct ? '%' : '';
  _mixTrendChart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: MIX_LBL.me, data: meVals, borderColor: '#1f3a5f', backgroundColor: '#1f3a5f',
          borderWidth: 2.5, tension: 0.25, pointRadius: 4, spanGaps: true },
        { label: 'Team avg', data: teamVals, borderColor: '#9ca3af', backgroundColor: '#9ca3af',
          borderWidth: 2, borderDash: [6, 4], tension: 0.25, pointRadius: 3, spanGaps: true },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: {
          label: c => c.dataset.label + ': ' + (c.parsed.y == null ? '—' : c.parsed.y + unit),
          afterLabel: c => {
            const notes = c.datasetIndex === 0 ? meNotes : tmNotes;
            return notes[c.dataIndex] || '';
          },
        } },
      },
      scales: {
        y: { ticks: { callback: v => v + unit, font: { size: 11 } }, beginAtZero: !isPct },
        x: { ticks: { font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function exportMixCSV() {
  const rows = _mixRows();
  rows.sort((a, b) => _mixSortCol === 'name'
    ? a.name.localeCompare(b.name)
    : (b[_mixSortCol] || 0) - (a[_mixSortCol] || 0));
  const head = ['Rank', 'Rep', 'Leads', 'Converted', 'Conv %', 'Won', 'Close %', 'Expected Won', 'Expected %', 'Vs Expected %'];
  const lines = [head.join(',')];
  rows.forEach((r, i) => {
    lines.push([i + 1, '"' + r.name + '"', r.leads, r.converted, r.conv_pct, r.won,
                r.actual_pct, r.expected_won, r.expected_pct, r.index_pct].join(','));
  });
  const tl = rows.reduce((s, r) => s + r.leads, 0);
  const tc = rows.reduce((s, r) => s + r.converted, 0);
  const tw = rows.reduce((s, r) => s + r.won, 0);
  const te = rows.reduce((s, r) => s + r.expected_won, 0);
  lines.push(['', 'TEAM', tl, tc, tl ? (tc / tl * 100).toFixed(1) : 0, tw,
              tl ? (tw / tl * 100).toFixed(1) : 0, te.toFixed(1),
              tl ? (te / tl * 100).toFixed(1) : 0, ''].join(','));
  const period = _mixMonthKey || 'since-mar-1';
  const src = _mixSource ? _mixSource.replace(/[^A-Za-z0-9]+/g, '-') : 'all-sources';
  const valid = _mixValid ? '_valid-only' : '';
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `mix_pivot_${period}_${src}${valid}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function renderMixAdjusted(mix) {
  if (!mix || !mix.reps || !mix.reps.length) return;
  _mixData = mix;
  document.getElementById('mix-section').style.display = '';

  // Month dropdown (newest month first, cumulative default)
  const monthSelect = document.getElementById('mix-month-select');
  const monthly = mix.monthly || {};
  const keys = Object.keys(monthly).sort().reverse();
  monthSelect.innerHTML = '<option value="">Since Mar 1</option>' +
    keys.map(k => `<option value="${k}">${monthly[k].month_label || k}</option>`).join('');
  monthSelect.value = _mixMonthKey;
  monthSelect.onchange = () => { _mixMonthKey = monthSelect.value; applyMixWindow(); };

  document.getElementById('mix-source-select').onchange = (e) => {
    _mixSource = e.target.value;
    renderMixTable();
  };

  document.getElementById('mix-valid-toggle').onchange = (e) => {
    _mixValid = e.target.checked;
    applyMixWindow();
    renderMixTrend();
  };

  document.getElementById('mix-export-btn').onclick = exportMixCSV;

  const uwToggle = document.getElementById('mix-uw-toggle');
  if (uwToggle) {
    uwToggle.onchange = (e) => {
      _mixUW = e.target.checked;
      applyMixWindow();
      renderMixTrend();
    };
  }
  const trendMetric = document.getElementById('mix-trend-metric');
  if (trendMetric) trendMetric.onchange = renderMixTrend;

  // Optional standalone Period selector on the source-totals card
  const srcMonthSelect = document.getElementById('mix-src-month-select');
  if (srcMonthSelect) {
    srcMonthSelect.innerHTML = '<option value="">Since Mar 1</option>' +
      keys.map(k => `<option value="${k}">${monthly[k].month_label || k}</option>`).join('');
    srcMonthSelect.value = _mixSrcMonthKey;
    srcMonthSelect.onchange = () => { _mixSrcMonthKey = srcMonthSelect.value; renderMixSrcTable(); };
  }

  // Sortable headers
  document.querySelectorAll('.mix-th-sort').forEach(th => {
    th.onclick = () => {
      const col = th.dataset.col;
      if (_mixSortCol === col) { _mixSortAsc = !_mixSortAsc; }
      else { _mixSortCol = col; _mixSortAsc = col === 'name'; }
      renderMixTable();
    };
  });

  applyMixWindow();
  renderMixTrend();
}
