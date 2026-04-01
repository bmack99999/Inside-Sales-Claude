function renderKPIs(data) {
  if (!data || data.length === 0) {
    document.getElementById('kpi-table-body').innerHTML =
      '<tr><td colspan="8" class="table-empty">No KPI data logged yet. Fill in the form on the Daily Plan page.</td></tr>';
    return;
  }

  // Sort by date ascending
  data.sort((a, b) => a.date.localeCompare(b.date));

  // Last 30 days
  const today = new Date();
  const cutoff = new Date(today);
  cutoff.setDate(cutoff.getDate() - 30);
  const recent = data.filter(d => new Date(d.date) >= cutoff);

  // MTD
  const now = new Date();
  const mtd = data.filter(d => {
    const dd = new Date(d.date);
    return dd.getFullYear() === now.getFullYear() && dd.getMonth() === now.getMonth();
  });

  const sum = (arr, key) => arr.reduce((s, r) => s + (r[key] || 0), 0);
  const pct = (n, d) => d > 0 ? ((n / d) * 100).toFixed(1) + '%' : '—';

  const mtdDials = sum(mtd, 'dials');
  const mtdConnects = sum(mtd, 'connects');
  const mtdDemos = sum(mtd, 'demos_set');
  const mtdCloses = sum(mtd, 'closes');

  // Summary cards
  setEl('mtd-dials', mtdDials);
  setEl('mtd-connects', mtdConnects);
  setEl('connect-rate', pct(mtdConnects, mtdDials));
  setEl('mtd-demos', mtdDemos);
  setEl('demo-rate', pct(mtdDemos, mtdConnects));
  setEl('mtd-closes', mtdCloses);

  // Funnel (last 30 days)
  const r30Dials = sum(recent, 'dials');
  const r30Connects = sum(recent, 'connects');
  const r30Demos = sum(recent, 'demos_set');
  const r30Closes = sum(recent, 'closes');
  const maxVal = Math.max(r30Dials, 1);

  renderFunnelStage('funnel-dials', r30Dials, maxVal);
  renderFunnelStage('funnel-connects', r30Connects, maxVal);
  renderFunnelStage('funnel-demos', r30Demos, maxVal);
  renderFunnelStage('funnel-closes', r30Closes, maxVal);

  const labels = recent.map(d => formatDate(d.date));

  // Chart defaults
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = '#7f8c8d';

  const navy = '#1B3A6B';
  const orange = '#d35400';
  const green = '#1e8449';
  const red = '#c0392b';

  makeBarChart('dialsChart', labels, recent.map(d => d.dials || 0), 'Dials', navy);
  makeLineChart('connectRateChart', labels,
    recent.map(d => d.dials > 0 ? +((d.connects / d.dials) * 100).toFixed(1) : 0),
    'Connect %', orange);
  makeBarChart('demosChart', labels, recent.map(d => d.demos_set || 0), 'Demos Set', green);
  makeBarChart('closesChart', labels, recent.map(d => d.closes || 0), 'Closes', red);

  // Log table
  const tbody = document.getElementById('kpi-table-body');
  const rows = [...data].reverse().map(d => {
    const cRate = d.dials > 0 ? ((d.connects / d.dials) * 100).toFixed(1) + '%' : '—';
    return `<tr>
      <td>${d.date}</td>
      <td>${d.dials || 0}</td>
      <td>${d.connects || 0}</td>
      <td>${d.voicemails || 0}</td>
      <td>${cRate}</td>
      <td>${d.demos_set || 0}</td>
      <td>${d.applications || 0}</td>
      <td>${d.closes || 0}</td>
    </tr>`;
  });
  tbody.innerHTML = rows.join('');
}

function renderFunnelStage(id, value, maxVal) {
  const el = document.getElementById(id);
  if (!el) return;
  const pct = Math.max(8, Math.round((value / maxVal) * 100));
  el.querySelector('.funnel-bar').style.opacity = (pct / 100 * 0.7 + 0.3);
  el.querySelector('.funnel-bar').style.background = `hsl(${220 - pct}, 60%, 35%)`;
  el.querySelector('.funnel-num').textContent = value;
}

function makeBarChart(canvasId, labels, data, label, color) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label, data, backgroundColor: color + 'cc', borderRadius: 3 }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  });
}

function makeLineChart(canvasId, labels, data, label, color) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label,
        data,
        borderColor: color,
        backgroundColor: color + '22',
        tension: 0.3,
        fill: true,
        pointRadius: 3
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { callback: v => v + '%' } }
      }
    }
  });
}

function setEl(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function formatDate(str) {
  const d = new Date(str + 'T00:00:00');
  return (d.getMonth() + 1) + '/' + d.getDate();
}
