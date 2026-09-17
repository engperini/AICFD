/**
 * Residual history chart -- the answer to "did it actually converge?".
 *
 * One log y-axis for every series, because they all measure the same thing
 * (initial residual per outer iteration) in the same units. A second axis would
 * invent a relationship between fields that does not exist.
 *
 * Seven series is past the point where direct labels help, so identity comes
 * from a legend with written names plus a crosshair tooltip, and every value
 * stays reachable in the table view.
 */

// Fixed slot order. A field keeps its color whether or not other fields are
// present, so "h is the orange one" stays true across cases.
const SERIES_ORDER = ['Ux', 'Uy', 'Uz', 'h', 'p_rgh', 'k', 'epsilon'];

const MARGIN = { top: 14, right: 18, bottom: 34, left: 54 };
const SVG_NS = 'http://www.w3.org/2000/svg';

export class ConvergenceChart {
  constructor(container, residuals) {
    this.container = container;
    this.iterations = residuals.iterations;
    this.series = SERIES_ORDER.filter((name) => residuals.series[name]).map(
      (name, index) => ({
        name,
        slot: index + 1,
        values: residuals.series[name],
      }),
    );
    // Any field the solver logged that is not in SERIES_ORDER still gets drawn,
    // appended after the known ones so the fixed slots never shift.
    for (const name of Object.keys(residuals.series)) {
      if (!SERIES_ORDER.includes(name)) {
        this.series.push({
          name,
          slot: this.series.length + 1,
          values: residuals.series[name],
        });
      }
    }
    this.render();
    window.addEventListener('resize', () => this.render());
  }

  get finalValues() {
    return this.series.map((s) => ({
      name: s.name,
      value: [...s.values].reverse().find((v) => v != null),
      slot: s.slot,
    }));
  }

  render() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height || !this.iterations.length) return;

    this.container.replaceChildren();
    const svg = el('svg', {
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: 'img',
      'aria-label': 'Solver residuals per iteration, logarithmic scale',
    });

    const plotWidth = width - MARGIN.left - MARGIN.right;
    const plotHeight = height - MARGIN.top - MARGIN.bottom;

    const finite = this.series
      .flatMap((s) => s.values)
      .filter((v) => v != null && v > 0);
    if (!finite.length) return;
    const loMag = Math.floor(Math.log10(Math.min(...finite)));
    const hiMag = Math.ceil(Math.log10(Math.max(...finite)));

    const xOf = (i) =>
      MARGIN.left + (i / Math.max(this.iterations.length - 1, 1)) * plotWidth;
    const yOf = (v) =>
      MARGIN.top +
      ((hiMag - Math.log10(v)) / Math.max(hiMag - loMag, 1)) * plotHeight;

    // --- chrome: hairline, solid, recessive ---------------------------------
    const chrome = el('g', { class: 'chart-chrome' });
    for (let mag = loMag; mag <= hiMag; mag += 1) {
      const y = yOf(10 ** mag);
      chrome.append(
        el('line', {
          x1: MARGIN.left,
          x2: MARGIN.left + plotWidth,
          y1: y,
          y2: y,
          class: 'chart-grid',
        }),
        el(
          'text',
          { x: MARGIN.left - 8, y: y + 4, class: 'chart-tick chart-tick-y' },
          `1e${mag}`,
        ),
      );
    }
    for (const index of roundTickIndices(this.iterations, 5)) {
      chrome.append(
        el(
          'text',
          {
            x: xOf(index),
            y: MARGIN.top + plotHeight + 20,
            class: 'chart-tick chart-tick-x',
          },
          this.iterations[index].toLocaleString(),
        ),
      );
    }
    chrome.append(
      el('line', {
        x1: MARGIN.left,
        x2: MARGIN.left + plotWidth,
        y1: MARGIN.top + plotHeight,
        y2: MARGIN.top + plotHeight,
        class: 'chart-axis',
      }),
      el(
        'text',
        {
          x: MARGIN.left + plotWidth / 2,
          y: height - 2,
          class: 'chart-axis-title',
        },
        'iteration',
      ),
    );
    svg.append(chrome);

    // --- marks: 2px lines, round joins --------------------------------------
    for (const series of this.series) {
      const segments = [];
      let current = [];
      series.values.forEach((value, i) => {
        if (value == null || value <= 0) {
          if (current.length) segments.push(current);
          current = [];
        } else {
          current.push(`${xOf(i).toFixed(1)},${yOf(value).toFixed(1)}`);
        }
      });
      if (current.length) segments.push(current);
      for (const points of segments) {
        svg.append(
          el('polyline', {
            points: points.join(' '),
            class: 'chart-line',
            style: `stroke: var(--series-${series.slot})`,
          }),
        );
      }
    }

    // --- crosshair + tooltip -------------------------------------------------
    const crosshair = el('line', {
      class: 'chart-crosshair',
      y1: MARGIN.top,
      y2: MARGIN.top + plotHeight,
      style: 'display: none',
    });
    svg.append(crosshair);
    svg.append(
      el('rect', {
        x: MARGIN.left,
        y: MARGIN.top,
        width: plotWidth,
        height: plotHeight,
        fill: 'transparent',
        class: 'chart-hit',
      }),
    );
    this.container.append(svg);

    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    tooltip.hidden = true;
    this.container.append(tooltip);

    const hit = svg.querySelector('.chart-hit');
    hit.addEventListener('pointermove', (event) => {
      const rect = svg.getBoundingClientRect();
      const t = (event.clientX - rect.left - MARGIN.left) / plotWidth;
      const index = Math.max(
        0,
        Math.min(this.iterations.length - 1, Math.round(t * (this.iterations.length - 1))),
      );
      const x = xOf(index);
      crosshair.setAttribute('x1', x);
      crosshair.setAttribute('x2', x);
      crosshair.style.display = '';
      tooltip.hidden = false;
      tooltip.innerHTML =
        `<div class="chart-tooltip-title">iteration ${this.iterations[index]}</div>` +
        this.series
          .map((s) => {
            const value = s.values[index];
            return (
              `<div class="chart-tooltip-row">` +
              `<span class="swatch" style="background: var(--series-${s.slot})"></span>` +
              `<span class="chart-tooltip-name">${s.name}</span>` +
              `<span class="chart-tooltip-value">${
                value == null ? '--' : value.toExponential(2)
              }</span></div>`
            );
          })
          .join('');
      const right = x > MARGIN.left + plotWidth / 2;
      tooltip.style.left = right ? 'auto' : `${x + 14}px`;
      tooltip.style.right = right ? `${width - x + 14}px` : 'auto';
      tooltip.style.top = `${MARGIN.top}px`;
    });
    hit.addEventListener('pointerleave', () => {
      crosshair.style.display = 'none';
      tooltip.hidden = true;
    });
  }

  legendHtml() {
    return this.series
      .map(
        (s) =>
          `<span class="legend-item"><span class="swatch" style="background: ` +
          `var(--series-${s.slot})"></span>${s.name}</span>`,
      )
      .join('');
  }
}

/**
 * Indices whose iteration numbers land on round values (0, 200, 400...), so the
 * axis reads "400" rather than "401". Falls back to even spacing on short runs.
 */
function roundTickIndices(iterations, count) {
  const last = iterations[iterations.length - 1];
  const raw = last / (count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step =
    [1, 2, 2.5, 5, 10].find((m) => m * magnitude >= raw) * magnitude || raw;

  const indices = new Set([0]);
  const first = Math.ceil(iterations[0] / step) * step;
  for (let value = first; value <= last; value += step) {
    indices.add(nearestIndex(iterations, value));
  }
  indices.add(iterations.length - 1);
  return [...indices].sort((a, b) => a - b);
}

function nearestIndex(iterations, value) {
  let best = 0;
  let bestDistance = Infinity;
  for (let i = 0; i < iterations.length; i += 1) {
    const distance = Math.abs(iterations[i] - value);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  return best;
}

function el(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, value);
  }
  if (text != null) node.textContent = text;
  return node;
}
