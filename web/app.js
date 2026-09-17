/**
 * The AICFD page: check the model, set the parameters, start the run, watch it.
 *
 * The order matters. Everything you need to catch a wrong dimension is on
 * screen before the Run button does anything, because a mistake found after a
 * twenty-minute solve has already cost the twenty minutes.
 */

import { VIEWS, drawView, sheetScale } from './drawing.js';

const PARAMS = [
  { key: 'rack_count', label: 'Racks', unit: 'un', step: 1 },
  { key: 'rack_load_kw', label: 'Carga por rack', unit: 'kW', step: 0.5 },
  { key: 'airflow_m3h', label: 'Vazão do fan wall', unit: 'm³/h', step: 100 },
  { key: 'supply_temp_c', label: 'Temperatura de insuflamento', unit: '°C', step: 0.5 },
  { key: 'fan_height', label: 'Altura do fan wall', unit: 'm', step: 0.1 },
  { key: 'cold_aisle', label: 'Corredor frio', unit: 'm', step: 0.1 },
  { key: 'hot_aisle', label: 'Corredor quente', unit: 'm', step: 0.1 },
  { key: 'ceiling', label: 'Altura do forro', unit: 'm', step: 0.1 },
  { key: 'gallery_depth', label: 'Profundidade da galeria', unit: 'm', step: 0.1 },
  { key: 'cell_size', label: 'Tamanho de célula', unit: 'm', step: 0.01 },
  { key: 'max_iterations', label: 'Iterações (teto)', unit: 'un', step: 50 },
];

const SPEC_PATH = {
  rack_count: ['racks', 'count'],
  rack_load_kw: ['racks', 'load_kw'],
  airflow_m3h: ['fanwall', 'airflow_m3h'],
  supply_temp_c: ['fanwall', 'supply_temp_c'],
  fan_height: ['fanwall', 'height'],
  cold_aisle: ['aisles', 'cold'],
  hot_aisle: ['aisles', 'hot'],
  ceiling: ['hall', 'ceiling'],
  gallery_depth: ['gallery', 'depth'],
  cell_size: ['mesh', 'cell_size'],
  max_iterations: ['solver', 'max_iterations'],
  containment: ['containment', 'enabled'],
};

const RESIDUAL_ORDER = ['Ux', 'Uy', 'Uz', 'h', 'p_rgh', 'k', 'epsilon'];

let model = null;
let pollTimer = null;

main();

async function main() {
  setupTheme();
  try {
    model = await fetchJson('/api/model');
  } catch (error) {
    document.getElementById('root').innerHTML =
      `<div class="error"><strong>Não consegui carregar o modelo.</strong>
       <p>${error.message}</p>
       <p>Esta página precisa do servidor do AICFD. Rode <code>aicfd view</code>.</p></div>`;
    return;
  }
  if (model.error) {
    document.getElementById('root').innerHTML =
      `<div class="error"><strong>Erro no spec.</strong><p>${model.error}</p></div>`;
    return;
  }
  render();
  poll();
}

// --- rendering ---------------------------------------------------------------

function render() {
  document.getElementById('case-name').textContent =
    `${model.name} · ${model.cells.toLocaleString('pt-BR')} células · ` +
    `${model.divisions.join(' × ')}`;
  setStage(model.run);

  document.getElementById('root').innerHTML = `
<main class="layout">
  <div class="column">
    <section class="card">
      <div class="card-head">
        <span class="card-title">Modelo</span>
        <span class="card-sub">desenhado a partir da geometria que vai ser discretizada</span>
      </div>
      <div class="views" id="views"></div>
      <div class="legend">
        <span><i class="swatch" style="background:var(--cold)"></i>fan wall / ar frio</span>
        <span><i class="swatch" style="background:var(--hot)"></i>grelhas e retorno</span>
        <span><i class="swatch" style="background:var(--containment)"></i>enclausuramento</span>
        <span><i class="swatch" style="background:var(--rack);border:1px solid var(--text-primary)"></i>racks</span>
        <span><i class="swatch" style="background:var(--series-4)"></i>sensores</span>
        <span>tracejado = além do plano de corte</span>
      </div>
    </section>

    <section class="card" id="sensors-card">
      <div class="card-head">
        <span class="card-title">Sensores</span>
        <span class="card-sub">média de 3 pontos por local, a cada
          ${model.spec?.solver?.sensor_interval ?? 100} iterações</span>
      </div>
      <div id="sensors"></div>
    </section>

    <section class="card">
      <div class="card-head">
        <span class="card-title">Convergência</span>
        <span class="card-sub" id="progress-note">${
          model.blocked ? `não dá para rodar: ${model.blocked}` : 'nada rodando'
        }</span>
        <span class="spacer" style="flex:1"></span>
        ${
          model.has_results
            ? `<a class="linkbutton" href="./results.html?case=${model.name}">Ver resultados</a>`
            : ''
        }
        <button id="run" class="primary" type="button">Rodar simulação</button>
      </div>
      <div id="progress"></div>
    </section>
  </div>

  <aside class="column">
    <section class="card">
      <div class="card-head"><span class="card-title">Parâmetros</span></div>
      <div class="params" id="params"></div>
      <div class="actions">
        <button id="apply" class="primary" type="button">Aplicar</button>
        <button id="reset" type="button">Desfazer</button>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><span class="card-title">Verificação</span>
        <span class="card-sub">números derivados</span></div>
      <div id="summary"></div>
    </section>

    <section class="card" id="warnings-card" hidden>
      <div class="card-head"><span class="card-title">Ajustes à malha</span></div>
      <ul class="notes" id="warnings"></ul>
    </section>
  </aside>
</main>`;

  drawViews();
  renderSensors(null);
  renderParams();
  renderSummary();
  renderWarnings();
  drawProgress({ iterations: [], series: {} });

  document.getElementById('apply').addEventListener('click', applyChanges);
  document.getElementById('reset').addEventListener('click', () => renderParams());
  document.getElementById('run').addEventListener('click', startRun);
  window.addEventListener('resize', debounce(drawViews, 150));
}

function drawViews() {
  const host = document.getElementById('views');
  if (!host) return;
  host.replaceChildren();
  // Lay the cells out first, then pick one scale they can all live with: the
  // three views are meant to be read against each other.
  const cells = VIEWS.map((view) => {
    const cell = document.createElement('div');
    cell.className = 'view';
    cell.innerHTML = `<h3>${view.title}</h3><p>${view.subtitle}</p>`;
    host.append(cell);
    return cell;
  });
  const scale = sheetScale(model, VIEWS, cells.map((c) => c.clientWidth - 24));
  VIEWS.forEach((view, i) => cells[i].append(drawView(model, view, scale)));
}

function renderParams() {
  const host = document.getElementById('params');
  host.innerHTML =
    PARAMS.map(
      (p) => `<div class="param">
        <label for="p-${p.key}">${p.label} <span class="unit">${p.unit}</span></label>
        <input type="number" id="p-${p.key}" step="${p.step}"
               value="${specValue(p.key)}" />
      </div>`,
    ).join('') +
    `<div class="param">
       <label for="p-containment">Enclausurar corredor quente</label>
       <input type="checkbox" id="p-containment" ${specValue('containment') ? 'checked' : ''} />
     </div>`;
}

function specValue(key) {
  const path = SPEC_PATH[key];
  let node = model.spec;
  for (const step of path) node = node?.[step];
  return node ?? '';
}

function renderSummary() {
  // Label, the dimension, and what it implies -- stacked rather than in three
  // columns, because "7,20 m² · 0,19 m/s" wrapped mid-number in a side panel.
  document.getElementById('summary').innerHTML = model.summary
    .map(
      ([label, a, b]) => `<div class="fact">
        <span class="fact-label">${label}</span>
        <span class="fact-a">${a}</span>
        <span class="fact-b">${b}</span>
      </div>`,
    )
    .join('');
}

function renderWarnings() {
  const card = document.getElementById('warnings-card');
  const list = document.getElementById('warnings');
  const items = model.warnings || [];
  card.hidden = items.length === 0;
  list.innerHTML = items
    .map((w) => `<li><span class="mark">!</span><span>${w}</span></li>`)
    .join('');
}

function renderSensors(history) {
  const host = document.getElementById('sensors');
  if (!host) return;
  const groups = history?.groups?.length ? history.groups : null;
  const iterations = history?.iterations || [];
  const last = iterations.length - 1;

  const placed = (model.sensors || []).map((group) => {
    const live = groups?.find((g) => g.name === group.name);
    return { ...group, live };
  });

  const balance = history?.balance?.length
    ? history.balance[history.balance.length - 1]
    : null;

  host.innerHTML = `${balance ? balanceStrip(balance) : ''}
  <table class="sensors">
    <thead><tr>
      <th>Local</th><th>Temp.</th><th>Δ entre pontos</th>
      <th>Pressão</th><th>Velocidade</th>
    </tr></thead>
    <tbody>${placed
      .map(({ label, note, live }) => {
        const t = live?.temp_c?.[last];
        const spread = live?.spread_k?.[last];
        const dp = live?.pressure_pa?.[last];
        const v = live?.speed_ms?.[last];
        return `<tr>
          <td><span class="sensor-label">${label}</span>
              <span class="sensor-note">${note}</span></td>
          <td>${t == null ? '—' : `${fmt(t, 1)} °C`}</td>
          <td class="muted">${spread == null ? '—' : `${fmt(spread, 1)} K`}</td>
          <td>${dp == null ? '—' : `${dp > 0 ? '+' : ''}${fmt(dp, 1)} Pa`}</td>
          <td>${v == null ? '—' : `${fmt(v, 2)} m/s`}</td>
        </tr>`;
      })
      .join('')}</tbody></table>
    <p class="sensor-foot">Pressão relativa à tomada do fan wall, com a coluna
      hidrostática removida — é a diferença que um manômetro leria e que empurra
      o ar pelo circuito.</p>
    ${iterations.length ? drawSensorChart(history) :
      `<p class="empty">as medidas aparecem a partir da primeira leitura</p>`}`;
}

/**
 * The two numbers that say whether the run means anything yet.
 *
 * Energy closure first, because it is the one that cannot be satisfied by
 * accident: every watt installed has to leave as warmer air. A run still
 * filling shows it climbing; residuals show nothing at all.
 */
function balanceStrip(b) {
  const closure = b.closure == null ? null : b.closure * 100;
  const state =
    closure == null ? '' : closure >= 90 && closure <= 110 ? 'good' : 'warn';
  return `<div class="balance">
    <span class="balance-item" data-state="${state}">
      <b>${closure == null ? '—' : `${fmt(closure, 0)}%`}</b>
      <i>da carga no ar de retorno</i></span>
    <span class="balance-item"><b>${fmt(b.return_temp_c, 1)} °C</b>
      <i>retorno no fan wall</i></span>
    <span class="balance-item"><b>${fmt(b.peak_speed_ms, 2)} m/s</b>
      <i>velocidade máxima</i></span>
    ${b.fan_rise_pa == null ? '' : `<span class="balance-item">
      <b>${fmt(b.fan_rise_pa, 1)} Pa</b><i>pressão do fan wall</i></span>`}
    <span class="balance-item" data-state="${b.backflow_kg_s > 0.01 ? 'warn' : ''}">
      <b>${fmt(b.backflow_kg_s, 3)} kg/s</b><i>refluxo na tomada</i></span>
  </div>`;
}

/** Temperature at each place, against iteration. Linear -- these are degrees. */
function drawSensorChart(history) {
  const W = 640;
  const H = 180;
  const pad = { left: 44, right: 96, top: 10, bottom: 26 };
  const its = history.iterations;
  const series = history.groups.filter((g) => g.temp_c?.length);
  if (!series.length || its.length < 2) return '';

  const values = series.flatMap((g) => g.temp_c).filter((v) => Number.isFinite(v));
  const lo = Math.floor(Math.min(...values) - 0.5);
  const hi = Math.ceil(Math.max(...values) + 0.5);
  const X = (i) => pad.left + (i / (its.length - 1)) * (W - pad.left - pad.right);
  const Y = (v) => pad.top + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.top - pad.bottom);

  const ticks = [lo, (lo + hi) / 2, hi];
  const grid = ticks
    .map(
      (v) => `<line class="chart-grid" x1="${pad.left}" x2="${W - pad.right}"
                y1="${Y(v)}" y2="${Y(v)}"/>
              <text class="chart-tick" x="${pad.left - 6}" y="${Y(v) + 4}"
                text-anchor="end">${fmt(v, 0)}</text>`,
    )
    .join('');

  const lines = series
    .map((g, i) => {
      const d = g.temp_c
        .map((v, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)},${Y(v).toFixed(1)}`)
        .join('');
      const colour = `var(--series-${(i % 8) + 1})`;
      const end = g.temp_c.length - 1;
      return `<path class="chart-line" d="${d}" stroke="${colour}"/>
        <text class="chart-tick" x="${X(end) + 6}" y="${Y(g.temp_c[end]) + 4}"
          fill="${colour}">${g.label}</text>`;
    })
    .join('');

  return `<svg class="sensor-chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="temperatura por local ao longo das iterações">
    ${grid}${lines}
    <text class="chart-tick" x="${(pad.left + W - pad.right) / 2}" y="${H - 6}"
      text-anchor="middle">iteração ${its[its.length - 1]}</text>
  </svg>`;
}

const fmt = (v, places) =>
  Number(v).toLocaleString('pt-BR', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });

// --- actions -----------------------------------------------------------------

async function applyChanges() {
  const button = document.getElementById('apply');
  button.disabled = true;
  const changes = {};
  for (const p of PARAMS) {
    const input = document.getElementById(`p-${p.key}`);
    if (input.value !== '') changes[p.key] = Number(input.value);
  }
  changes.containment = document.getElementById('p-containment').checked;

  try {
    const next = await fetchJson('/api/model', { method: 'POST', body: changes });
    if (next.error) throw new Error(next.error);
    model = next;
    render();
    if (next.rejected?.length) {
      alert(`Não aceito:\n${next.rejected.join('\n')}`);
    }
  } catch (error) {
    alert(`Falha ao aplicar: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function startRun() {
  const button = document.getElementById('run');
  button.disabled = true;
  try {
    const response = await fetchJson('/api/run', { method: 'POST', body: {} });
    if (response.blocked) throw new Error(response.blocked);
    setStage(response.run);
  } catch (error) {
    alert(`Não consegui iniciar: ${error.message}`);
    button.disabled = false;
  }
}

function setStage(run) {
  if (!run) return;
  const badge = document.getElementById('stage');
  badge.dataset.stage = run.stage;
  const labels = {
    idle: 'pronto para rodar',
    meshing: `malhando${run.step ? ` · ${run.step}` : ''}`,
    solving: 'resolvendo',
    done: 'resolvido',
    failed: 'falhou',
  };
  badge.textContent = labels[run.stage] || run.stage;
  const button = document.getElementById('run');
  if (button) {
    const busy = run.stage === 'meshing' || run.stage === 'solving';
    button.disabled = busy || Boolean(model.blocked);
    button.title = model.blocked || '';
  }
  const note = document.getElementById('progress-note');
  if (note && run.stage === 'failed') note.textContent = run.message;
}

// --- progress ----------------------------------------------------------------

async function poll() {
  clearTimeout(pollTimer);
  try {
    const status = await fetchJson('/api/progress');
    setStage(status.run);
    drawProgress(status.residuals);
    renderSensors(status.sensors);
    const note = document.getElementById('progress-note');
    if (note && !model.blocked && status.residuals.total) {
      note.textContent = `${status.residuals.total.toLocaleString('pt-BR')} iterações`;
    }
  } catch {
    /* the server may be mid-restart; try again on the next tick */
  }
  const busy = ['meshing', 'solving'].includes(
    document.getElementById('stage')?.dataset.stage,
  );
  pollTimer = setTimeout(poll, busy ? 2000 : 8000);
}

function drawProgress(residuals) {
  const host = document.getElementById('progress');
  if (!host) return;
  const width = host.clientWidth;
  const height = host.clientHeight;
  host.replaceChildren();
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  const names = RESIDUAL_ORDER.filter((n) => residuals.series?.[n]?.length);
  if (!names.length) {
    svg.append(
      text(width / 2, height / 2, 'sem dados ainda — a curva aparece durante a simulação',
        'chart-empty'),
    );
    host.append(svg);
    return;
  }

  const M = { top: 12, right: 16, bottom: 30, left: 50 };
  const plotW = width - M.left - M.right;
  const plotH = height - M.top - M.bottom;
  const values = names.flatMap((n) => residuals.series[n]).filter((v) => v > 0);
  const loMag = Math.floor(Math.log10(Math.min(...values)));
  const hiMag = Math.ceil(Math.log10(Math.max(...values)));
  const n = residuals.iterations.length;
  const X = (i) => M.left + (i / Math.max(n - 1, 1)) * plotW;
  const Y = (v) => M.top + ((hiMag - Math.log10(v)) / Math.max(hiMag - loMag, 1)) * plotH;

  for (let mag = loMag; mag <= hiMag; mag += 1) {
    svg.append(line(M.left, Y(10 ** mag), M.left + plotW, Y(10 ** mag), 'chart-grid'));
    svg.append(text(M.left - 8, Y(10 ** mag) + 4, `1e${mag}`, 'chart-tick', 'end'));
  }
  svg.append(line(M.left, M.top + plotH, M.left + plotW, M.top + plotH, 'chart-axis'));
  for (let i = 0; i < 4; i += 1) {
    const index = Math.round((i / 3) * (n - 1));
    svg.append(
      text(X(index), M.top + plotH + 18, String(residuals.iterations[index]), 'chart-tick',
        'middle'),
    );
  }
  names.forEach((name, slot) => {
    const points = [];
    residuals.series[name].forEach((v, i) => {
      if (v > 0) points.push(`${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    });
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', points.join(' '));
    poly.setAttribute('class', 'chart-line');
    poly.setAttribute('style', `stroke: var(--series-${slot + 1})`);
    svg.append(poly);
  });
  host.append(svg);
}

// --- helpers -----------------------------------------------------------------

function line(x1, y1, x2, y2, cls) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  node.setAttribute('x1', x1); node.setAttribute('y1', y1);
  node.setAttribute('x2', x2); node.setAttribute('y2', y2);
  node.setAttribute('class', cls);
  return node;
}

function text(x, y, content, cls, anchor) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  node.setAttribute('x', x); node.setAttribute('y', y);
  node.setAttribute('class', cls);
  if (anchor) node.setAttribute('text-anchor', anchor);
  node.textContent = content;
  return node;
}

async function fetchJson(url, { method = 'GET', body } = {}) {
  const response = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function setupTheme() {
  const button = document.getElementById('theme-toggle');
  const current = () =>
    document.documentElement.dataset.theme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const apply = () => {
    button.textContent = current() === 'dark' ? 'Claro' : 'Escuro';
    if (model) drawViews();
  };
  button.addEventListener('click', () => {
    document.documentElement.dataset.theme = current() === 'dark' ? 'light' : 'dark';
    apply();
  });
  apply();
}
