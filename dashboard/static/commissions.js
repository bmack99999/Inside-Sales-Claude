/* Commissions — inside sales deal tracker (client). One JSON payload from the
   server drives tiles, forecast chart, deal table, and the edit drawer. Every
   mutation returns a fresh payload so the page never goes stale. */
(function () {
  'use strict';

  var view = JSON.parse(document.getElementById('cx-data').textContent);
  var state = { filter: 'inflight', q: '', sort: 'signed_desc', openId: null, mode: null, dirty: false, month: null };
  var chart = null;

  // ── helpers ──────────────────────────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function money(v, opts) {
    opts = opts || {};
    if (v == null || isNaN(v)) return '—';
    var neg = v < 0, a = Math.abs(v);
    var s = opts.cents ? a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                       : Math.round(a).toLocaleString('en-US');
    return (neg ? '−$' : '$') + s;
  }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDate(iso, withYear) {
    if (!iso) return '—';
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return iso;
    var s = MONTHS[+m[2] - 1] + ' ' + (+m[3]);
    if (withYear || m[1] !== view.today.slice(0, 4)) s += ', ' + m[1];
    return s;
  }
  function fmtMonth(key) { var p = key.split('-'); return MONTHS[+p[1] - 1] + (p[0] !== view.today.slice(0, 4) ? ' ' + p[0].slice(2) : ''); }
  function fmtWhen(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  }
  function daysAgo(n) { if (n == null) return ''; if (n === 0) return 'today'; if (n === 1) return '1 day ago'; return n + ' days ago'; }
  function toast(msg, err) {
    var t = $('cx-toast'); t.textContent = msg; t.hidden = false; t.className = 'cx-toast' + (err ? ' err' : '');
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.hidden = true; }, err ? 4200 : 2200);
  }
  function api(url, method, body) {
    return fetch(url, { method: method || 'GET', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function dealById(id) { for (var i = 0; i < view.deals.length; i++) if (view.deals[i].id === id) return view.deals[i]; return null; }
  function stageLabel(s) { for (var i = 0; i < view.options.stages.length; i++) if (view.options.stages[i].value === s) return view.options.stages[i].label; return s; }

  var STEPS = [
    { key: 'signed', label: 'Signed', idx: 0 },
    { key: 'onboarding', label: 'Onboard', idx: 1 },
    { key: 'install', label: 'Install', idx: 2 },
    { key: 'live', label: 'Live', idx: 4 },
    { key: 'paid', label: 'Paid', idx: 5 },
  ];
  function progressHTML(d) {
    var si = d.stage_index; // 0..6; -1 stalled/cancelled
    var cls = d.stage === 'stalled' ? ' stalled' : (d.stage === 'complete' ? ' complete' : '');
    var fill = si < 0 ? (d.stage === 'stalled' ? 1 : 0) : si;
    var out = '<div class="cx-progress' + cls + '" title="' + esc(d.stage_label) + '">';
    var thresholds = [0, 1, 2, 4, 5];
    for (var i = 0; i < 5; i++) out += '<i class="' + (fill >= thresholds[i] && (si >= 0 || d.stage === 'stalled') ? 'on' : '') + '"></i>';
    return out + '</div>';
  }

  // ── tiles ────────────────────────────────────────────────────────────────
  function renderTiles() {
    var s = view.summary;
    var tiles = [
      { label: 'Paid this year', value: money(s.paid_ytd), sub: money(s.paid_total) + ' lifetime on ' + s.paid_deal_count + ' paid deal' + (s.paid_deal_count === 1 ? '' : 's'), tone: 'good' },
      { label: 'Projected next 90 days', value: money(s.projected_90d), sub: money(s.projected_total) + ' still to come overall', tone: 'accent', filter: 'inflight' },
      { label: 'Deals tracked', value: s.deals, sub: s.signed_this_month + ' signed this month · ' + s.in_flight + ' in flight', tone: '' },
      { label: 'Waiting on install', value: s.awaiting_install, sub: s.awaiting_payout + ' installed, waiting on payout', tone: '', filter: 'preinstall' },
      { label: 'Needs attention', value: s.at_risk, sub: s.stalled + ' stalled · ' + s.missing_mid + ' missing MID · ' + s.missing_devices + ' missing devices', tone: s.at_risk ? 'warn' : '', filter: 'risk' },
    ];
    $('cx-tiles').innerHTML = tiles.map(function (t) {
      return '<div class="cx-tile' + (t.tone ? ' tone-' + t.tone : '') + (t.filter ? ' clickable' : '') + '"' + (t.filter ? ' data-filter="' + t.filter + '"' : '') + '>' +
        '<div class="cx-tile-label">' + esc(t.label) + '</div><div class="cx-tile-value">' + esc(t.value) + '</div><div class="cx-tile-sub">' + esc(t.sub) + '</div></div>';
    }).join('');
    $('cx-meta').textContent = s.deals + ' deals · ' + money(s.paid_total) + ' paid · ' + money(s.projected_total) + ' projected';
  }

  // ── forecast chart ───────────────────────────────────────────────────────
  function hatch(color) {
    var c = document.createElement('canvas'); c.width = 8; c.height = 8;
    var x = c.getContext('2d');
    x.fillStyle = '#ffffff'; x.fillRect(0, 0, 8, 8);
    x.strokeStyle = color; x.lineWidth = 2;
    x.beginPath(); x.moveTo(-2, 10); x.lineTo(10, -2); x.moveTo(-2, 2); x.lineTo(2, -2); x.moveTo(6, 10); x.lineTo(10, 6); x.stroke();
    return x.createPattern(c, 'repeat');
  }
  var valueLabels = {
    id: 'cxValueLabels',
    afterDatasetsDraw: function (ch) {
      var ctx = ch.ctx; ctx.save();
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.fillStyle = '#55657c'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ch.data.datasets.forEach(function (ds, di) {
        var meta = ch.getDatasetMeta(di);
        meta.data.forEach(function (bar, i) {
          var v = ds.data[i]; if (!v) return;
          ctx.fillText(money(v), bar.x, bar.y - 3);
        });
      });
      ctx.restore();
    }
  };
  function renderChart() {
    var f = view.forecast;
    var labels = f.months.map(fmtMonth);
    var accent = '#2a78d6';
    var ctx = $('cx-chart').getContext('2d');
    var total90 = view.summary.projected_90d;
    $('cx-forecast-sub').textContent = 'Actual payouts for the last 3 months and where the remaining ' + money(view.summary.projected_total) + ' should land. Click a month for the breakdown.';
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          { label: 'Paid', data: f.paid, backgroundColor: accent, borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 34 },
          { label: 'Projected', data: f.projected, backgroundColor: hatch(accent), borderColor: accent, borderWidth: 1, borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 34 },
        ]
      },
      plugins: [valueLabels],
      options: {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: 18 } },
        onClick: function (evt, els) {
          if (!els.length) return;
          var i = els[0].index; state.month = (state.month === f.months[i]) ? null : f.months[i]; renderForecastDetail();
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#16233a', padding: 10, displayColors: false,
            callbacks: {
              title: function (items) { return fmtMonth(f.months[items[0].dataIndex]) + (f.months[items[0].dataIndex] === f.current_month ? ' (this month)' : ''); },
              label: function (item) { return item.dataset.label + ': ' + money(item.raw, { cents: true }); },
              afterBody: function (items) {
                var k = f.months[items[0].dataIndex]; var det = f.detail[k] || [];
                if (!det.length || items[0].datasetIndex !== 1) return '';
                return det.slice(0, 5).map(function (d) { return '  ' + d.site + ' · ' + d.what + ' ' + money(d.amount); }).concat(det.length > 5 ? ['  +' + (det.length - 5) + ' more'] : []);
              }
            }
          }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#8b98ab', font: { size: 11 } }, border: { display: false } },
          y: { grid: { color: '#eef1f6' }, border: { display: false }, ticks: { color: '#8b98ab', font: { size: 11 }, callback: function (v) { return money(v); }, maxTicksLimit: 5 }, beginAtZero: true }
        }
      }
    });
    renderForecastDetail();
  }
  function renderForecastDetail() {
    var el = $('cx-forecast-detail');
    if (!state.month) { el.innerHTML = ''; return; }
    var det = view.forecast.detail[state.month] || [];
    var i = view.forecast.months.indexOf(state.month);
    var paid = view.forecast.paid[i], proj = view.forecast.projected[i];
    var head = '<div class="fd-head">' + fmtMonth(state.month) + ' · paid ' + money(paid) + ' · projected ' + money(proj) + '</div>';
    if (!det.length) { el.innerHTML = head + '<span>Nothing projected for this month.</span>'; return; }
    el.innerHTML = head + det.map(function (d) { return '<span><strong>' + esc(d.site) + '</strong> ' + esc(d.what) + ' ' + money(d.amount) + '</span>'; }).join('');
  }

  // ── filters + table ──────────────────────────────────────────────────────
  var FILTERS = [
    { key: 'inflight', label: 'In flight', test: function (d) { return d.stage !== 'complete' && d.stage !== 'cancelled'; } },
    { key: 'all', label: 'All', test: function () { return true; } },
    { key: 'preinstall', label: 'Pre install', test: function (d) { return ['signed', 'onboarding', 'install_scheduled'].indexOf(d.stage) >= 0; } },
    { key: 'installed', label: 'Installed', test: function (d) { return ['installed', 'live'].indexOf(d.stage) >= 0; } },
    { key: 'upfront_paid', label: 'Upfront paid', test: function (d) { return d.stage === 'upfront_paid'; } },
    { key: 'complete', label: 'Trued up', test: function (d) { return d.stage === 'complete'; } },
    { key: 'stalled', label: 'Stalled', test: function (d) { return d.stage === 'stalled'; } },
    { key: 'risk', label: 'Needs attention', risk: true, test: function (d) { return d.at_risk; } },
    { key: 'missing', label: 'Missing info', test: function (d) { return d.stage !== 'cancelled' && (!d.mid || !d.devices_total || !d.mo_volume); } },
    { key: 'cancelled', label: 'Cancelled', test: function (d) { return d.stage === 'cancelled'; } },
  ];
  function renderChips() {
    $('cx-chips').innerHTML = FILTERS.map(function (f) {
      var n = view.deals.filter(f.test).length;
      if (!n && f.key !== 'inflight' && f.key !== 'all') return '';
      return '<button type="button" class="cx-chip' + (f.risk ? ' risk' : '') + (state.filter === f.key ? ' on' : '') + '" data-filter="' + f.key + '">' + esc(f.label) + ' <b>' + n + '</b></button>';
    }).join('');
  }
  function visibleDeals() {
    var f = FILTERS.filter(function (x) { return x.key === state.filter; })[0] || FILTERS[0];
    var q = state.q.toLowerCase().trim();
    var rows = view.deals.filter(f.test).filter(function (d) {
      if (!q) return true;
      var hay = [d.site, d.mid, d.mid_raw, d.contact_name, d.contact_email, d.product, d.rate_structure, d.specialist_name, d.notes].join(' ').toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    var nextDate = function (d) { return (d.next_event && d.next_event.date) || '9999'; };
    var sorters = {
      signed_desc: function (a, b) { return (b.sign_date || '').localeCompare(a.sign_date || '') || a.site.localeCompare(b.site); },
      signed_asc: function (a, b) { return (a.sign_date || '').localeCompare(b.sign_date || '') || a.site.localeCompare(b.site); },
      next: function (a, b) { return nextDate(a).localeCompare(nextDate(b)) || (b.remaining.total - a.remaining.total); },
      remaining: function (a, b) { return b.remaining.total - a.remaining.total; },
      paid: function (a, b) { return b.paid.total - a.paid.total; },
      name: function (a, b) { return a.site.localeCompare(b.site); },
    };
    rows.sort(sorters[state.sort] || sorters.signed_desc);
    return rows;
  }
  function devicesText(d) {
    var parts = [];
    if (d.terminals) parts.push(d.terminals + ' term');
    if (d.handhelds) parts.push(d.handhelds + ' hh');
    if (d.kds) parts.push(d.kds + ' kds');
    if (d.other_devices) parts.push(d.other_devices + ' other');
    return parts.join(' · ');
  }
  function renderTable() {
    var rows = visibleDeals();
    $('cx-empty').hidden = rows.length > 0;
    $('cx-empty').textContent = view.deals.length ? 'No deals match.' : 'No deals yet. Click "+ New deal" to add your first one.';
    $('cx-tbody').innerHTML = rows.map(function (d) {
      var next = d.next_event;
      var nextHTML = next ? ('<div>' + esc(next.label) + (next.amount ? ' <span class="cx-money">' + money(next.amount) + '</span>' : '') + '</div><div class="cx-sub">' + (next.date ? fmtDate(next.date) : (d.stage === 'complete' ? '' : 'no date yet')) + '</div>') : '<span class="cx-sub">—</span>';
      var rem = d.remaining.total;
      var remCls = 'cx-money' + (!rem ? ' zero' : (rem < 0 ? ' neg' : ''));
      var paidCls = 'cx-money' + (!d.paid.total ? ' zero' : (d.paid.total < 0 ? ' neg' : ''));
      var risk = d.at_risk ? '<span class="cx-flag" title="' + esc(d.risks.join(' · ')) + '">!</span>' : '';
      return '<tr data-id="' + d.id + '" class="stage-' + d.stage + (d.at_risk ? ' at-risk' : '') + '">' +
        '<td><div class="cx-deal-name">' + esc(d.site) + risk + '</div><div class="cx-deal-meta">' +
          (d.product ? '<span>' + esc(d.product) + '</span>' : '') +
          (d.mid ? '<span class="cx-mono">' + esc(d.mid_raw || d.mid) + '</span>' : '<span class="cx-warn-txt">no MID</span>') +
        '</div></td>' +
        '<td><span class="cx-stage st-' + d.stage + '">' + esc(d.stage_label) + '</span>' + progressHTML(d) + '</td>' +
        '<td>' + fmtDate(d.sign_date) + '<div class="cx-sub">' + daysAgo(d.days_since_sign) + '</div></td>' +
        '<td>' + esc(d.rate_display || '—') + '<div class="cx-sub">' + esc(d.rate_structure || '') + (d.mo_volume ? ' · ' + money(d.mo_volume) + '/mo' : '') + '</div></td>' +
        '<td class="num">' + (d.devices_total ? d.devices_total : '<span class="cx-warn-txt">—</span>') + '<div class="cx-sub">' + esc(devicesText(d)) + '</div></td>' +
        '<td class="num"><span class="' + paidCls + '">' + money(d.paid.total) + '</span>' + (d.paid.total ? '<div class="cx-sub">' + fmtDate(d.paid.last_paid) + '</div>' : '') + '</td>' +
        '<td class="num"><span class="' + remCls + '">' + money(rem) + '</span>' + (d.volume_assumed && rem ? '<div class="cx-sub">volume assumed</div>' : '') + '</td>' +
        '<td>' + nextHTML + '</td>' +
      '</tr>';
    }).join('');
  }

  function renderAll() { renderTiles(); renderChart(); renderChips(); renderTable(); }

  // ── drawer ───────────────────────────────────────────────────────────────
  function opt(list, val, labelKey) {
    return list.map(function (o) {
      var v = typeof o === 'string' ? o : o.value, l = typeof o === 'string' ? o : o[labelKey || 'label'];
      return '<option value="' + esc(v) + '"' + (v === val ? ' selected' : '') + '>' + esc(l) + '</option>';
    }).join('');
  }
  function field(name, label, value, type, extra) {
    extra = extra || {};
    var id = 'f-' + name;
    var inner;
    if (type === 'select') inner = '<select id="' + id + '" name="' + name + '">' + (extra.blank ? '<option value="">' + esc(extra.blank) + '</option>' : '') + opt(extra.options, value || '', extra.labelKey) + '</select>';
    else if (type === 'textarea') inner = '<textarea id="' + id + '" name="' + name + '" placeholder="' + esc(extra.placeholder || '') + '">' + esc(value || '') + '</textarea>';
    else {
      var input = '<input id="' + id + '" name="' + name + '" type="' + (type || 'text') + '" value="' + esc(value == null ? '' : value) + '" placeholder="' + esc(extra.placeholder || '') + '"' + (extra.step ? ' step="' + extra.step + '"' : '') + (extra.min != null ? ' min="' + extra.min + '"' : '') + (extra.inputmode ? ' inputmode="' + extra.inputmode + '"' : '') + '>';
      if (extra.prefix) inner = '<div class="with-prefix"><span>' + esc(extra.prefix) + '</span>' + input + '</div>';
      else if (extra.suffix) inner = '<div class="with-suffix">' + input + '<span>' + esc(extra.suffix) + '</span></div>';
      else inner = input;
    }
    return '<div class="cx-f' + (extra.span2 ? ' span2' : '') + (extra.hidden ? ' hidden' : '') + '" data-field="' + name + '"><label for="' + id + '">' + esc(label) + '</label>' + inner + '</div>';
  }

  function moneyCard(label, expected, paidAmt, paidLines, sub, tag) {
    var isPaid = paidLines > 0;
    var v = isPaid ? paidAmt : expected;
    return '<div class="cx-mc' + (isPaid ? ' paid' : '') + '"><div class="cx-mc-label"><span>' + esc(label) + '</span><span class="tag">' + esc(isPaid ? 'paid' : (tag || 'expected')) + '</span></div>' +
      '<div class="cx-mc-value' + (v < 0 ? ' neg' : '') + '">' + (v == null ? '—' : money(v, { cents: false })) + '</div><div class="cx-mc-sub">' + esc(sub || '') + '</div></div>';
  }

  function drawerHTML(d, mode) {
    var isNew = mode === 'new';
    d = d || { status: 'signed', product: 'Dine', rate_structure: 'Dual Pricing', terminals: 0, handhelds: 0, kds: 0, other_devices: 0, paid: { total: 0, lines: [], upfront: 0, true_up: 0, saas: 0, true_up_lines: 0, saas_lines: 0 }, expected: {}, remaining: { total: 0 }, dates: {}, risks: [], events: [], stage: 'signed', stage_index: 0, stage_label: 'Signed' };
    var o = view.options;
    var sfLink = d.sf_opp_url ? '<a href="' + esc(d.sf_opp_url) + '" target="_blank" rel="noopener">Open in Salesforce ↗</a>' : '';

    // stepper
    var stepCls = d.stage === 'stalled' ? ' stalled' : (d.stage === 'complete' ? ' complete' : '');
    var stepper = '<div class="cx-stepper' + stepCls + '">' + STEPS.map(function (s) {
      var cls = '';
      if (d.stage === 'complete') cls = 'done';
      else if (d.stage_index >= 0) { if (d.stage_index > s.idx || (s.key === 'install' && d.stage_index === 3)) cls = 'done'; else if (d.stage_index === s.idx || (s.key === 'install' && d.stage_index === 2)) cls = 'now'; }
      else if (d.stage === 'stalled') cls = s.idx === 0 ? 'done' : (s.idx === 1 ? 'now' : '');
      return '<div class="cx-step ' + cls + '">' + s.label + '</div>';
    }).join('') + '</div>';
    var risks = d.risks && d.risks.length ? '<ul class="cx-risk-list">' + d.risks.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>' : '';

    // money
    var e = d.expected || {}, p = d.paid || {}, dt = d.dates || {};
    var moneySec = '';
    if (!isNew) {
      var trueUpSub = p.true_up_lines ? ('paid ' + fmtDate(lastLineDate(p.lines, 'true_up'))) : (e.true_up == null ? 'enter a rate to estimate' : ('est. ' + fmtDate(dt.true_up_pay_est) + (d.volume_assumed ? ' · volume assumed' : '')));
      moneySec =
        '<div class="cx-sec"><div class="cx-sec-title">Money <span class="hint">' + (d.mid ? 'linked to MID ' + esc(d.mid_raw || d.mid) : 'add a MID to link real payouts') + '</span></div>' +
        '<div class="cx-money-grid">' +
          moneyCard('Upfront', e.upfront, p.upfront, p.upfront > 0 ? 1 : 0, p.upfront > 0 ? ('paid ' + fmtDate(lastLineDate(p.lines, 'upfront'))) : ('est. ' + fmtDate(dt.upfront_pay_est) + (dt.go_live_basis ? ' · from ' + dt.go_live_basis : ''))) +
          moneyCard('True up', e.true_up, p.true_up, p.true_up_lines, trueUpSub) +
          moneyCard('SaaS ×' + (view.assumptions.saas_months || 2), e.saas, p.saas, p.saas_lines, p.saas_lines ? ('paid ' + fmtDate(lastLineDate(p.lines, 'saas'))) : (d.devices_total ? (d.devices_total + ' devices × ' + money(d.saas_monthly_est, { cents: true }) + '/mo') : 'add device counts')) +
        '</div>' +
        '<div class="cx-total-row"><span>Paid to date</span><b>' + money(p.total, { cents: true }) + '</b></div>' +
        '<div class="cx-total-row"><span>Still projected</span><b>' + money(d.remaining.total, { cents: true }) + '</b></div>' +
        (d.profit_monthly_est != null ? '<div class="cx-help">Estimated monthly processing profit ' + money(d.profit_monthly_est) + ' → 2 months = ' + money(2 * d.profit_monthly_est) + ', minus the upfront, capped at ' + money(view.assumptions.bonus_cap) + ' total. Tune in Assumptions.</div>' : '') +
        (p.lines && p.lines.length ? '<table class="cx-lines">' + p.lines.map(function (l) { return '<tr><td><span class="cx-pill ' + l.type + '">' + esc(typeLabel(l.type)) + '</span></td><td>' + fmtDate(l.date_paid, true) + '</td><td class="cx-sub">' + esc(l.department || '') + (l.pay_cycle ? ' · ' + esc(l.pay_cycle) : '') + '</td><td class="' + (l.amount < 0 ? 'neg' : '') + '">' + money(l.amount, { cents: true }) + '</td></tr>'; }).join('') + '</table>' : '') +
        '</div>';
    }

    var sfPaste = isNew ? '<div class="cx-sfpaste"><label>Paste the Salesforce opportunity link to autofill</label><div class="row"><input id="sf-paste" type="url" placeholder="https://crmcredorax.lightning.force.com/lightning/r/Opportunity/006…/view"><button type="button" class="cx-btn ghost sm" id="sf-paste-btn">Fill</button></div><div class="status" id="sf-paste-status"></div></div>' : '';

    var statusSec =
      '<div class="cx-sec"><div class="cx-sec-title">Status</div>' + (isNew ? '' : stepper + risks) +
      '<div class="cx-grid">' +
        field('status', 'Status', d.status, 'select', { options: o.statuses }) +
        field('sign_date', 'Signed', d.sign_date, 'date') +
        field('install_scheduled_date', 'Install scheduled', d.install_scheduled_date, 'date') +
        field('install_date', 'Installed', d.install_date, 'date') +
        field('go_live_date', 'Go live (status 700)', d.go_live_date, 'date') +
        field('stall_reason', 'Stall reason', d.stall_reason, 'text', { placeholder: 'e.g. waiting on internet, owner unresponsive', hidden: d.status !== 'stalled' && d.status !== 'cancelled' }) +
      '</div></div>';

    var dealSec =
      '<div class="cx-sec"><div class="cx-sec-title">Deal</div><div class="cx-grid">' +
        field('sf_opp_url', 'Salesforce opportunity URL', d.sf_opp_url, 'url', { span2: true, placeholder: 'https://crmcredorax.lightning.force.com/lightning/r/Opportunity/…' }) +
        field('mid_raw', 'MID', d.mid_raw || d.mid, 'text', { placeholder: '0023xxxxxx', inputmode: 'numeric' }) +
        field('product', 'Product', d.product, 'select', { options: o.products }) +
        field('rate_structure', 'Rate structure', d.rate_structure, 'select', { options: o.rate_structures }) +
        field('mo_volume', 'Monthly card volume', d.mo_volume, 'number', { prefix: '$', step: '1000', min: 0 }) +
        field('rate_pct', 'Rate', d.rate_pct, 'number', { suffix: '%', step: '0.01', min: 0 }) +
        field('per_item', 'Per transaction', d.per_item, 'number', { prefix: '$', step: '0.01', min: 0 }) +
      '</div></div>';

    var devSec =
      '<div class="cx-sec"><div class="cx-sec-title">Devices <span class="hint">SaaS pays on device count</span></div><div class="cx-grid-4">' +
        field('terminals', 'Terminals', d.terminals, 'number', { min: 0, step: '1' }) +
        field('handhelds', 'Handhelds', d.handhelds, 'number', { min: 0, step: '1' }) +
        field('kds', 'KDS', d.kds, 'number', { min: 0, step: '1' }) +
        field('other_devices', 'Other', d.other_devices, 'number', { min: 0, step: '1' }) +
      '</div><div class="cx-grid" style="margin-top:10px">' +
        field('saas_monthly', 'Monthly SaaS override', d.saas_monthly, 'number', { prefix: '$', step: '0.01', min: 0, placeholder: 'blank = devices × ' + money(view.assumptions.saas_per_device, { cents: true }) }) +
      '</div></div>';

    var peopleSec =
      '<div class="cx-sec"><div class="cx-sec-title">People</div><div class="cx-grid">' +
        field('contact_name', 'Owner / contact', d.contact_name, 'text') +
        field('contact_phone', 'Phone', d.contact_phone, 'tel') +
        field('contact_email', 'Email', d.contact_email, 'email', { span2: true }) +
        field('specialist_name', 'Pre launch specialist', d.specialist_name, 'text') +
        field('specialist_email', 'Specialist email', d.specialist_email, 'email') +
        field('notes', 'Notes', d.notes, 'textarea', { span2: true, placeholder: 'Anything worth remembering about this deal' }) +
      '</div></div>';

    var timelineSec = isNew ? '' :
      '<div class="cx-sec"><div class="cx-sec-title">Timeline</div>' +
      '<div class="cx-addnote"><input id="note-input" type="text" placeholder="Add a note… (Enter to save)"><button type="button" class="cx-btn ghost sm" id="note-btn">Add</button></div>' +
      (d.events && d.events.length ? '<ul class="cx-timeline">' + d.events.map(function (ev) {
        return '<li class="k-' + esc(ev.kind) + '"><div>' + esc(ev.note) + '</div><div class="when">' + fmtWhen(ev.at) + (ev.source && ev.source !== 'manual' ? '<span class="src">' + esc(ev.source.replace('_', ' ')) + '</span>' : '') + '</div></li>';
      }).join('') + '</ul>' : '<div class="cx-help">No activity yet.</div>') +
      '</div>';

    return '<div class="cx-dr-head"><div style="flex:1;min-width:0">' +
        '<div class="cx-dr-title"><input id="f-site" name="site" value="' + esc(d.site || '') + '" placeholder="Business name" ' + (isNew ? 'autofocus' : '') + '></div>' +
        '<div class="cx-dr-sub">' + (isNew ? '<span>New inside sales deal</span>' : '<span class="cx-stage st-' + d.stage + '">' + esc(d.stage_label) + '</span>' + (d.mid ? '<span class="cx-mono">MID ' + esc(d.mid_raw || d.mid) + '</span>' : '') + sfLink) + '</div>' +
      '</div><button type="button" class="cx-dr-close" id="dr-close" aria-label="Close">×</button></div>' +
      '<div class="cx-dr-body"><form id="deal-form" onsubmit="return false">' + sfPaste + statusSec + moneySec + dealSec + devSec + peopleSec + '</form>' + timelineSec + '</div>' +
      '<div class="cx-dr-foot"><div>' + (isNew ? '' : '<button type="button" class="cx-btn danger sm" id="dr-delete">Delete</button>') + '</div>' +
        '<div class="right"><button type="button" class="cx-btn ghost" id="dr-cancel">Close</button><button type="button" class="cx-btn primary" id="dr-save">' + (isNew ? 'Add deal' : 'Save changes') + '</button></div></div>';
  }
  function lastLineDate(lines, type) { var d = null; (lines || []).forEach(function (l) { if (l.type === type && (!d || l.date_paid > d)) d = l.date_paid; }); return d; }
  function typeLabel(t) { return { upfront: 'Upfront', true_up: 'True up', saas: 'SaaS', adjustment: 'Adjustment', upgrade: 'Upgrade' }[t] || t; }

  function openDrawer(id, mode) {
    state.openId = id; state.mode = mode || 'edit'; state.dirty = false;
    var d = id ? dealById(id) : null;
    var dr = $('cx-drawer');
    dr.innerHTML = drawerHTML(d, state.mode);
    dr.hidden = false; $('cx-scrim').hidden = false;
    document.body.style.overflow = 'hidden';
    wireDrawer();
    var site = $('f-site'); if (state.mode === 'new' && site) setTimeout(function () { site.focus(); }, 30);
  }
  function closeDrawer(force) {
    if (state.dirty && !force && !confirm('Discard unsaved changes?')) return;
    $('cx-drawer').hidden = true; $('cx-scrim').hidden = true; document.body.style.overflow = '';
    state.openId = null; state.mode = null; state.dirty = false;
  }
  function formPayload() {
    var f = $('deal-form'); var out = {};
    Array.prototype.forEach.call(f.querySelectorAll('input[name],select[name],textarea[name]'), function (el) { out[el.name] = el.value; });
    out.site = $('f-site').value;
    return out;
  }
  function wireDrawer() {
    var dr = $('cx-drawer');
    $('dr-close').onclick = function () { closeDrawer(); };
    $('dr-cancel').onclick = function () { closeDrawer(); };
    dr.querySelector('#deal-form').addEventListener('input', function () { state.dirty = true; });
    $('f-site').addEventListener('input', function () { state.dirty = true; });
    var statusSel = $('f-status');
    if (statusSel) statusSel.addEventListener('change', function () {
      var v = statusSel.value;
      dr.querySelector('[data-field="stall_reason"]').classList.toggle('hidden', v !== 'stalled' && v !== 'cancelled');
      var today = view.today;
      if (v === 'installed' && !$('f-install_date').value) $('f-install_date').value = today;
      if (v === 'live' && !$('f-go_live_date').value) $('f-go_live_date').value = today;
      if (v === 'install_scheduled') $('f-install_scheduled_date').focus();
    });
    ['f-install_scheduled_date', 'f-install_date', 'f-go_live_date'].forEach(function (id) {
      var el = $(id); if (!el) return;
      el.addEventListener('change', function () {
        if (!el.value || !statusSel) return;
        var order = ['signed', 'onboarding', 'install_scheduled', 'installed', 'live'];
        var implied = { 'f-install_scheduled_date': 'install_scheduled', 'f-install_date': 'installed', 'f-go_live_date': 'live' }[id];
        if (order.indexOf(statusSel.value) >= 0 && order.indexOf(statusSel.value) < order.indexOf(implied)) statusSel.value = implied;
      });
    });
    $('dr-save').onclick = saveDrawer;
    var del = $('dr-delete');
    if (del) del.onclick = function () {
      var d = dealById(state.openId);
      if (!confirm('Delete ' + d.site + ' from the tracker? Payout history from the sheets is not affected.')) return;
      api('/api/tracked_deals/' + state.openId + '/delete', 'POST', {}).then(function (j) { view = j.view; closeDrawer(true); renderAll(); toast('Deleted'); }).catch(function (e) { toast(e.message, true); });
    };
    var noteBtn = $('note-btn'), noteIn = $('note-input');
    if (noteBtn) {
      var addNote = function () {
        var t = noteIn.value.trim(); if (!t) return;
        api('/api/tracked_deals/' + state.openId + '/events', 'POST', { note: t }).then(function (j) { view = j.view; var keep = state.dirty; refreshDrawerKeepingForm(keep); renderAll(); toast('Note added'); }).catch(function (e) { toast(e.message, true); });
      };
      noteBtn.onclick = addNote;
      noteIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addNote(); } });
    }
    var paste = $('sf-paste');
    if (paste) {
      var doFill = function () {
        var url = paste.value.trim(); if (!url) return;
        var st = $('sf-paste-status'); st.className = 'status'; st.textContent = 'Looking up…';
        api('/api/tracked_deals/sf_prefill?url=' + encodeURIComponent(url)).then(function (j) {
          $('f-sf_opp_url').value = j.sf_opp_url || url;
          if (j.already_tracked) { st.className = 'status warn'; st.textContent = 'Heads up: this opp is already tracked.'; }
          if (j.found) {
            if (j.site && !$('f-site').value) $('f-site').value = j.site;
            if (j.contact_name) $('f-contact_name').value = j.contact_name;
            if (j.contact_email) $('f-contact_email').value = j.contact_email;
            if (j.contact_phone) $('f-contact_phone').value = j.contact_phone;
            if (j.close_date && !$('f-sign_date').value) $('f-sign_date').value = j.close_date;
            if (!j.already_tracked) { st.className = 'status ok'; st.textContent = 'Filled from ' + j.site + (j.stage ? ' (' + j.stage + ')' : ''); }
          } else if (!j.already_tracked) { st.className = 'status warn'; st.textContent = 'Opp not in the dashboard cache. Link saved, fill the rest by hand.'; }
          state.dirty = true;
        }).catch(function (e) { st.className = 'status err'; st.textContent = e.message; });
      };
      $('sf-paste-btn').onclick = doFill;
      paste.addEventListener('paste', function () { setTimeout(doFill, 20); });
      paste.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); doFill(); } });
    }
  }
  function refreshDrawerKeepingForm(keepDirty) {
    var payload = keepDirty ? formPayload() : null;
    var scroll = $('cx-drawer').querySelector('.cx-dr-body').scrollTop;
    $('cx-drawer').innerHTML = drawerHTML(dealById(state.openId), state.mode);
    wireDrawer();
    if (payload) { Object.keys(payload).forEach(function (k) { var el = $('f-' + k); if (el) el.value = payload[k]; }); state.dirty = true; }
    $('cx-drawer').querySelector('.cx-dr-body').scrollTop = scroll;
  }
  function saveDrawer() {
    var payload = formPayload();
    if (!payload.site.trim()) { toast('Business name is required', true); $('f-site').focus(); return; }
    var btn = $('dr-save'); btn.disabled = true;
    var req = state.mode === 'new' ? api('/api/tracked_deals', 'POST', payload) : api('/api/tracked_deals/' + state.openId, 'POST', payload);
    req.then(function (j) {
      view = j.view; state.dirty = false;
      if (state.mode === 'new') { state.openId = j.id; state.mode = 'edit'; }
      renderAll();
      refreshDrawerKeepingForm(false);
      toast(state.mode === 'new' ? 'Deal added' : 'Saved');
    }).catch(function (e) { toast(e.message, true); }).then(function () { var b = $('dr-save'); if (b) b.disabled = false; });
  }

  // ── assumptions ──────────────────────────────────────────────────────────
  var ASSUMP = [
    ['upfront_dine', 'Upfront: Dine / SkyTab POS', '$'], ['upfront_other', 'Upfront: terminal / Solo / processing', '$'],
    ['bonus_cap', 'Upfront + true up cap', '$'], ['cost_basis_pct', 'Cost basis (interchange + network)', '%'],
    ['cost_per_item', 'Cost per transaction', '$'], ['avg_ticket', 'Average ticket', '$'],
    ['saas_per_device', 'SaaS per device per month', '$'], ['saas_months', 'SaaS months paid', ''],
    ['default_volume', 'Volume when none entered', '$'], ['days_sign_to_install', 'Days sign → install', 'd'],
    ['days_install_to_live', 'Days install → go live', 'd'], ['stall_after_days', 'Flag as at risk after (days)', 'd'],
  ];
  function openAssumptions() {
    var a = view.assumptions;
    $('cx-assump-modal').innerHTML =
      '<div class="cx-dr-head"><div><div class="cx-dr-title">Projection assumptions</div><div class="cx-dr-sub">These drive every estimate on the page. Real payouts always win once they land.</div></div><button type="button" class="cx-dr-close" id="as-close">×</button></div>' +
      '<div class="cx-dr-body"><form id="as-form" onsubmit="return false"><div class="cx-grid">' +
      ASSUMP.map(function (r) { return field(r[0], r[1], a[r[0]], 'number', r[2] === '$' ? { prefix: '$', step: 'any' } : r[2] === '%' ? { suffix: '%', step: '0.01' } : { step: '1' }); }).join('') +
      '</div></form><div class="cx-help" style="margin-top:12px">Profit per month = volume × (rate − cost basis) for dual pricing style programs; flat rate also earns (per item − cost per item) on each transaction; interchange plus treats the rate as pure markup. True up = 2 × profit − upfront, capped. SaaS = devices × per device rate × months.</div></div>' +
      '<div class="cx-dr-foot"><div></div><div class="right"><button type="button" class="cx-btn ghost" id="as-cancel">Cancel</button><button type="button" class="cx-btn primary" id="as-save">Save</button></div></div>';
    $('cx-assump-modal').hidden = false; $('cx-assump-scrim').hidden = false;
    var close = function () { $('cx-assump-modal').hidden = true; $('cx-assump-scrim').hidden = true; };
    $('as-close').onclick = close; $('as-cancel').onclick = close; $('cx-assump-scrim').onclick = close;
    $('as-save').onclick = function () {
      var out = {}; ASSUMP.forEach(function (r) { out[r[0]] = $('f-' + r[0]).value; });
      api('/api/tracked_deals/assumptions', 'POST', out).then(function (j) { view = j.view; renderAll(); close(); toast('Assumptions saved'); }).catch(function (e) { toast(e.message, true); });
    };
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  $('cx-tiles').addEventListener('click', function (e) { var t = e.target.closest('.cx-tile[data-filter]'); if (!t) return; state.filter = t.dataset.filter; renderChips(); renderTable(); $('cx-table').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  $('cx-chips').addEventListener('click', function (e) { var b = e.target.closest('.cx-chip'); if (!b) return; state.filter = b.dataset.filter; renderChips(); renderTable(); });
  $('cx-search').addEventListener('input', function (e) { state.q = e.target.value; renderTable(); });
  $('cx-sort').addEventListener('change', function (e) { state.sort = e.target.value; renderTable(); });
  $('cx-tbody').addEventListener('click', function (e) { var tr = e.target.closest('tr[data-id]'); if (tr) openDrawer(tr.dataset.id, 'edit'); });
  $('cx-new-btn').addEventListener('click', function () { openDrawer(null, 'new'); });
  $('cx-assump-btn').addEventListener('click', openAssumptions);
  $('cx-scrim').addEventListener('click', function () { closeDrawer(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { if (!$('cx-assump-modal').hidden) { $('cx-assump-modal').hidden = true; $('cx-assump-scrim').hidden = true; } else if (!$('cx-drawer').hidden) closeDrawer(); }
    if ((e.metaKey || e.ctrlKey) && e.key === 's' && !$('cx-drawer').hidden) { e.preventDefault(); saveDrawer(); }
    if (e.key === 'n' && $('cx-drawer').hidden && $('cx-assump-modal').hidden && !/input|textarea|select/i.test(document.activeElement.tagName)) { openDrawer(null, 'new'); }
  });

  renderAll();
  var hash = /^#deal=(\w+)/.exec(location.hash); if (hash && dealById(hash[1])) openDrawer(hash[1], 'edit');
})();
