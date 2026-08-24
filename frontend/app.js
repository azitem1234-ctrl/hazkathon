"use strict";

/* ---------- Helpers ---------- */
const $ = (id) => document.getElementById(id);

// Try the origin serving the page first; if it is not our API (e.g. VS Code
// Live Server or a plain static server, which reject POSTs with 405),
// automatically fall back to the backend on port 8000.
const LOCAL_API = "http://127.0.0.1:8000";

function apiBases() {
  const bases = new Set();
  if (location.protocol.startsWith("http")) bases.add(location.origin);
  bases.add(LOCAL_API);
  return [...bases];
}

async function apiPost(path, opts = {}) {
  const bases = apiBases();
  for (let i = 0; i < bases.length; i++) {
    try {
      const res = await fetch(bases[i] + path, opts);
      const wrongBackend = res.status === 404 || res.status === 405;
      if (!wrongBackend || i === bases.length - 1) return res;
    } catch (err) {
      if (i === bases.length - 1) throw err;
    }
  }
}

const fmt = (n, digits = 0) =>
  Number(n).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

const shortDate = (iso) => {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
};

function showStatus(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.className = `status ${isError ? "error" : ""}`;
}

function setLoading(on) {
  $("loading").classList.toggle("hidden", !on);
  if (on) showStatus("");
  else hideStatus();
}

function hideStatus() {
  $("status").className = "status hidden";
}

/* ---------- Data fetching ---------- */
function reqTimeout(ms) {
  // AbortSignal.timeout is unavailable in older Safari; degrade gracefully.
  try {
    return AbortSignal.timeout(ms);
  } catch {
    return undefined;
  }
}

let currentMultiplier = 1.5;

async function analyze(formData, label) {
  $("results").classList.add("hidden");
  $("loading-text").textContent = label
    ? `Анализируем «${label}»…`
    : "Анализируем данные по энергопотреблению…";
  setLoading(true);
  try {
    const tariff = encodeURIComponent($("tariff").value);
    const multiplier = encodeURIComponent(currentMultiplier);
    const res = await apiPost(`/api/analyze?tariff=${tariff}&multiplier=${multiplier}`, {
      method: "POST",
      body: formData,
      signal: reqTimeout(30000),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error(data?.detail || `Ошибка запроса (${res.status})`);
    render(data);
  } catch (err) {
    showStatus(
      `${err.message} — откройте дашборд на http://127.0.0.1:8000 (не через Live Server).`,
      true,
    );
  } finally {
    setLoading(false); // ALWAYS stop the spinner — success or failure
  }
}

/* ---------- Rendering ---------- */
let lastAnalysis = null; // reused for the AI Copilot and tariff re-runs
let lastInsight = null; // last Gemini/offline recommendation, reused by the report download

function render(data) {
  const { summary: s, series } = data;

  $("source-name").textContent =
    data.source === "sample_data.csv"
      ? "образец данных за месяц"
      : data.source;
  $("baseline-chip").textContent = `${fmt(s.baseline_kwh, 1)} кВт·ч`;
  $("days-analyzed").textContent = `из ${s.days_analyzed} дней проанализировано`;
  $("multiplier-chip").textContent = fmt(s.multiplier, 1);
  $("multiplier-value").textContent = fmt(s.multiplier, 1);
  $("multiplier-slider").value = s.multiplier;
  currentMultiplier = s.multiplier;

  $("kpi-excess").textContent = fmt(s.total_excess_kwh, 1);
  $("kpi-kzt").textContent = fmt(s.savings_kzt);
  $("kpi-co2").textContent = fmt(s.co2_saved_kg, 1);
  $("kpi-days").textContent = s.anomaly_days;
  $("kpi-quarterly").textContent = fmt(s.savings_kzt * 3);
  $("kpi-yearly").textContent = fmt(s.savings_kzt * 12);

  renderInsight(data);
  renderChart(series, s.baseline_kwh * s.multiplier, s.baseline_kwh);
  renderTable(series, s);

  lastAnalysis = data;
  $("results").classList.remove("hidden");
  resetCopilot();
  showTab("overview");
}

function renderInsight({ summary, worst_day, first_anomaly, last_anomaly }) {
  const box = $("insight");
  if (!worst_day) {
    box.textContent =
      "Хорошие новости: в каждый нерабочий день потребление оставалось близко к базовому уровню — потерь не обнаружено.";
    box.classList.remove("hidden");
    return;
  }
  const span =
    first_anomaly === last_anomaly
      ? `<strong>${shortDate(first_anomaly)}</strong>`
      : `в период с <strong>${shortDate(first_anomaly)} по ${shortDate(last_anomaly)}</strong>`;
  box.innerHTML =
    `⚠ Здание потребляло энергию так, будто было занято людьми, в <strong>${summary.anomaly_days}</strong> нерабочий(их) день(дней) ${span}. ` +
    `Самый затратный день: <strong>${shortDate(worst_day)}</strong> — за весь период это обошлось примерно в ` +
    `<strong>${fmt(summary.savings_kzt)} тенге</strong>. Проверьте таймеры отопления и освещения.`;
  box.classList.remove("hidden");
}

function renderTable(series, s) {
  const rows = series.filter((d) => d.is_anomaly);
  const tbody = $("anomaly-table").querySelector("tbody");
  tbody.innerHTML = "";
  $("no-anomalies").classList.toggle("hidden", rows.length > 0);

  for (const d of [...rows].sort((a, b) => b.excess_kwh - a.excess_kwh)) {
    tbody.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td>${d.date}</td>
        <td class="num">${fmt(d.consumption_kwh, 1)} кВт·ч</td>
        <td class="num excess">+${fmt(d.excess_kwh, 1)} кВт·ч</td>
        <td class="num">${fmt(d.excess_kwh * s.tariff_kzt_per_kwh)} тенге</td>
        <td class="num">${fmt(d.excess_kwh * s.co2_factor, 1)} кг</td>
      </tr>`,
    );
  }
}

/* ---------- Charts: SVG with TradingView-style mouse analysis ----------
   wheel = zoom at cursor · drag = pan · shift+drag = select range · dbl-click = reset */

const W = 1000;
const H = 380;
const MARGIN = { top: 24, right: 22, bottom: 46, left: 64 };
const COLORS = { workday: "#58a6ff", offday: "#484f58", anomaly: "#f85149" };
const MIN_SPAN = 4;

const chartState = {
  series: [], threshold: 0, view: "bar",
  i0: 0, i1: null,
  anim: true,
};

function niceCeil(v) {
  const pow = 10 ** Math.floor(Math.log10(v));
  const d = v / pow;
  const m = d <= 1 ? 1 : d <= 2 ? 2 : d <= 2.5 ? 2.5 : d <= 5 ? 5 : 10;
  return m * pow;
}

function defsGradients() {
  return `<defs>${Object.entries(COLORS)
    .map(([name, hex]) =>
      `<linearGradient id="g-${name}" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0%" stop-color="${hex}" stop-opacity="0.95"/>` +
      `<stop offset="100%" stop-color="${hex}" stop-opacity="0.45"/></linearGradient>`)
    .join("")}<linearGradient id="g-area" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0%" stop-color="#58a6ff" stop-opacity="0.3"/>` +
    `<stop offset="100%" stop-color="#58a6ff" stop-opacity="0"/></linearGradient></defs>`;
}

function makeY(maxV) {
  const innerH = H - MARGIN.top - MARGIN.bottom;
  const maxKwh = niceCeil(maxV * 1.06);
  const yScale = (v) => MARGIN.top + innerH - (v / maxKwh) * innerH;
  const invert = (py) => ((MARGIN.top + innerH - py) / innerH) * maxKwh;
  let grid = "";
  for (let t = 0; t <= 4; t++) {
    const v = (maxKwh * t) / 4;
    const yy = yScale(v).toFixed(1);
    grid += `<line x1="${MARGIN.left}" y1="${yy}" x2="${W - MARGIN.right}" y2="${yy}" stroke="#232a33"/>` +
      `<text x="${MARGIN.left - 10}" y="${+yy + 4}" fill="#9aa4b2" font-size="11" text-anchor="end">${fmt(v)}</text>`;
  }
  grid += `<text x="12" y="${MARGIN.top - 6}" fill="#9aa4b2" font-size="11">кВт·ч</text>`;
  return { maxKwh, yScale, invert, grid, innerH };
}

function refLines(y, mean, showMean) {
  const tv = chartState.threshold;
  let out =
    `<line x1="${MARGIN.left}" y1="${y.yScale(tv).toFixed(1)}" x2="${W - MARGIN.right}" y2="${y.yScale(tv).toFixed(1)}" stroke="#d29922" stroke-width="2" stroke-dasharray="7 5"/>` +
    `<text x="${W - MARGIN.right}" y="${(y.yScale(tv) - 7).toFixed(1)}" fill="#d29922" font-size="11" text-anchor="end">порог ${fmt(tv, 1)}</text>`;
  if (showMean && mean > 0) {
    out += `<line x1="${MARGIN.left}" y1="${y.yScale(mean).toFixed(1)}" x2="${W - MARGIN.right}" y2="${y.yScale(mean).toFixed(1)}" stroke="#8b949e" stroke-width="1" stroke-dasharray="2 5"/>` +
      `<text x="${MARGIN.left + 4}" y="${(y.yScale(mean) - 6).toFixed(1)}" fill="#8b949e" font-size="10.5">ср. ${fmt(mean, 1)}</text>`;
  }
  return out;
}

const dayColor = (d) =>
  d.is_anomaly ? COLORS.anomaly : d.is_workday ? COLORS.workday : COLORS.offday;

function weeklyItems(series) {
  const map = new Map();
  for (const d of series) {
    const dt = new Date(`${d.date}T00:00:00`);
    const mon = new Date(dt);
    mon.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
    const key = `${mon.getFullYear()}-${String(mon.getMonth() + 1).padStart(2, "0")}-${String(mon.getDate()).padStart(2, "0")}`;
    if (!map.has(key)) map.set(key, { work: 0, base: 0, waste: 0, days: 0 });
    const w = map.get(key);
    if (d.is_workday) w.work += d.consumption_kwh;
    else { w.waste += d.excess_kwh; w.base += d.consumption_kwh - d.excess_kwh; }
    w.days++;
  }
  return [...map.entries()].map(([key, w]) => ({
    label: `нед. ${shortDate(key)}`,
    total: w.work + w.base + w.waste,
    days: w.days,
    segs: [
      { name: "Рабочие дни", v: w.work, color: COLORS.workday },
      { name: "Нерабочие (норма)", v: w.base, color: COLORS.offday },
      { name: "Потери", v: w.waste, color: COLORS.anomaly },
    ],
  }));
}

function cumulativeItems(series) {
  let t = 0, waste = 0;
  return series.map((d) => {
    t += d.consumption_kwh;
    waste += d.excess_kwh;
    return { label: shortDate(d.date), total: t, waste };
  });
}

/* ----- SVG builders (one per view) ----- */
function xLabelsFor(items, xCenter, labels) {
  const n = items.length;
  const step = Math.max(1, Math.ceil(n / 12));
  let out = "";
  items.forEach((_, i) => {
    if (i % step) return;
    const lx = xCenter(i).toFixed(1);
    out += `<text x="${lx}" y="${H - 12}" fill="#9aa4b2" font-size="11" text-anchor="middle" transform="rotate(-40 ${lx} ${H - 12})">${labels[i]}</text>`;
  });
  return out;
}

function drawBarsSvg(items, y) {
  const n = items.length;
  const slot = (W - MARGIN.left - MARGIN.right) / n;
  const barW = Math.min(40, Math.max(3, slot * 0.72));
  const xCenter = (i) => MARGIN.left + i * slot + slot / 2;
  let svg = "";
  const step = Math.max(1, Math.ceil(n / 12));
  items.forEach((d, i) => {
    const cx = xCenter(i);
    const yTop = y.yScale(d.kwh);
    const kind = d.excess > 0 ? "anomaly" : d.closed ? "offday" : "workday";
    svg += `<rect class="bar" style="animation-delay:${Math.min(i * 14, 600)}ms" x="${(cx - barW / 2).toFixed(1)}" y="${yTop.toFixed(1)}" width="${barW.toFixed(1)}" height="${(MARGIN.top + y.innerH - yTop).toFixed(1)}" rx="4" fill="url(#g-${kind})"${d.excess > 0 ? ' stroke="#ff8b85" stroke-width="1"' : ""}/>`;
    if ((chartState.anim || d.excess > 0) && (n <= 21 || d.excess > 0 || i % step === 0)) {
      svg += `<text x="${cx.toFixed(1)}" y="${(yTop - 5).toFixed(1)}" fill="${d.excess > 0 ? COLORS.anomaly : "#9aa4b2"}" font-size="${d.excess > 0 ? 11 : 9.5}" font-weight="${d.excess > 0 ? 700 : 400}" text-anchor="middle">${d.excess > 0 ? "+" + fmt(d.excess) : fmt(d.kwh)}</text>`;
    }
  });
  svg += xLabelsFor(items, xCenter, items.map((d) => shortDate(d.label)));
  return { svg, xCenter };
}

function drawLineSvg(items, y) {
  const n = items.length;
  const slot = (W - MARGIN.left - MARGIN.right) / n;
  const xCenter = (i) => MARGIN.left + i * slot + slot / 2;
  const pts = items.map((d, i) => `${xCenter(i).toFixed(1)},${y.yScale(d.kwh).toFixed(1)}`);
  let svg =
    `<path class="fade-in" d="M${pts[0]} L${pts.join(" L")} L${xCenter(n - 1).toFixed(1)},${(MARGIN.top + y.innerH).toFixed(1)} L${MARGIN.left},${(MARGIN.top + y.innerH).toFixed(1)} Z" fill="url(#g-area)"/>` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="#58a6ff" stroke-width="2.5" stroke-linejoin="round"/>`;
  items.forEach((d, i) => {
    svg += `<circle cx="${xCenter(i).toFixed(1)}" cy="${y.yScale(d.kwh).toFixed(1)}" r="${d.excess > 0 ? 4.5 : 2.6}" fill="${d.color}"${d.excess > 0 ? ' stroke="#fff" stroke-width="1.2"' : ""}/>`;
  });
  svg += xLabelsFor(items, xCenter, items.map((d) => shortDate(d.label)));
  return { svg, xCenter };
}

function drawWeeklySvg(items, y) {
  const n = items.length;
  const slot = (W - MARGIN.left - MARGIN.right) / n;
  const barW = Math.min(70, Math.max(10, slot * 0.66));
  const xCenter = (i) => MARGIN.left + i * slot + slot / 2;
  let svg = "";
  items.forEach((w, wi) => {
    const cx = xCenter(wi);
    let acc = 0;
    for (const sg of w.segs) {
      const y1 = y.yScale(acc + sg.v);
      acc += sg.v;
      if (sg.v <= 0) continue;
      svg += `<rect class="bar" style="animation-delay:${wi * 60}ms" x="${(cx - barW / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${barW.toFixed(1)}" height="${(y.yScale(acc) - y1).toFixed(1)}" rx="2" fill="${sg.color}"/>`;
    }
    svg += `<text x="${cx.toFixed(1)}" y="${(y.yScale(acc) - 6).toFixed(1)}" fill="#e6edf3" font-size="10.5" font-weight="600" text-anchor="middle">${fmt(acc)}</text>`;
  });
  svg += xLabelsFor(items, xCenter, items.map((w) => w.label));
  return { svg, xCenter };
}

function drawCumulativeSvg(items, y) {
  const n = items.length;
  const slot = (W - MARGIN.left - MARGIN.right) / n;
  const xCenter = (i) => MARGIN.left + i * slot + slot / 2;
  const pt = (key) => items.map((d, i) => `${xCenter(i).toFixed(1)},${y.yScale(d[key]).toFixed(1)}`).join(" ");
  let svg =
    `<polyline points="${pt("total")}" fill="none" stroke="#58a6ff" stroke-width="2.6" stroke-linejoin="round"/>` +
    `<polyline points="${pt("waste")}" fill="none" stroke="${COLORS.anomaly}" stroke-width="2.2" stroke-dasharray="5 4"/>` +
    `<text x="${MARGIN.left + 6}" y="${MARGIN.top + 2}" fill="#58a6ff" font-size="11">— накопительно, кВт·ч</text>` +
    `<text x="${MARGIN.left + 168}" y="${MARGIN.top + 2}" fill="${COLORS.anomaly}" font-size="11">-- накопительные потери</text>`;
  items.forEach((d, i) => {
    svg += `<circle cx="${xCenter(i).toFixed(1)}" cy="${y.yScale(d.total).toFixed(1)}" r="2.4" fill="#58a6ff"/><circle cx="${xCenter(i).toFixed(1)}" cy="${y.yScale(d.waste).toFixed(1)}" r="2.2" fill="${COLORS.anomaly}"/>`;
  });
  svg += xLabelsFor(items, xCenter, items.map((d) => d.label));
  return { svg, xCenter };
}

/* ----- TradingView-style mouse interaction ----- */
let plot = null;
let drag = null; // shared across redraws so panning survives re-renders
let lastMouseUp = null;

function attachInteraction(svgEl, geom, describe) {
  svgEl.classList.add("plot");
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag) => document.createElementNS(NS, tag);
  const set = (el, attrs) => { for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); };

  const vLine = mk("line"); set(vLine, { stroke: "#8b949e", "stroke-dasharray": "3 3", opacity: 0 });
  const hLine = mk("line"); set(hLine, { stroke: "#8b949e", "stroke-dasharray": "3 3", opacity: 0 });
  const selRect = mk("rect"); set(selRect, { fill: "rgba(88,166,255,.16)", stroke: "#58a6ff", "stroke-dasharray": "4 3", opacity: 0 });
  const yG = mk("g"), yR = mk("rect"), yT = mk("text");
  set(yR, { fill: "#1c2129", stroke: "#3b4250", rx: 3 }); set(yT, { fill: "#e6edf3", "font-size": "10.5", "text-anchor": "middle" });
  yG.append(yR, yT); set(yG, { opacity: 0 });
  svgEl.append(selRect, vLine, hLine, yG);

  const info = $("chart-info");
  const toLocal = (ev) => {
    const p = svgEl.createSVGPoint();
    p.x = ev.clientX; p.y = ev.clientY;
    return p.matrixTransform(svgEl.getScreenCTM().inverse());
  };
  const idxAt = (mx) => Math.max(0, Math.min(geom.n - 1, Math.floor((mx - MARGIN.left) / geom.slot)));

  if (lastMouseUp) window.removeEventListener("mouseup", lastMouseUp);
  lastMouseUp = (ev) => {
    if (!drag) return;
    if (drag.mode === "select") {
      const a = idxAt(drag.startX), b = idxAt(toLocal(ev).x);
      set(selRect, { opacity: 0 });
      if (Math.abs(b - a) >= MIN_SPAN - 1) zoomTo(a, b + 1);
      else drawChart();
    } else {
      svgEl.style.cursor = "";
    }
    drag = null;
  };
  window.addEventListener("mouseup", lastMouseUp);

  svgEl.onmousemove = (ev) => {
    if (drag && drag.mode === "select") {
      const { x: mx } = toLocal(ev);
      const x0 = Math.min(drag.startX, mx), x1 = Math.max(drag.startX, mx);
      set(selRect, { x: x0, y: MARGIN.top, width: x1 - x0, height: H - MARGIN.top - MARGIN.bottom, opacity: 1 });
      return;
    }
    const { x: mx, y: my } = toLocal(ev);
    const idx = idxAt(mx);
    const cx = geom.xCenter(idx);
    set(vLine, { x1: cx, x2: cx, y1: MARGIN.top, y2: H - MARGIN.bottom, opacity: drag ? 0 : 1 });
    if (my >= MARGIN.top && my <= H - MARGIN.bottom && !drag) {
      set(hLine, { x1: MARGIN.left, x2: W - MARGIN.right, y1: my, y2: my, opacity: 1 });
      yT.textContent = fmt(Math.max(0, geom.y.invert(my)), 1);
      set(yR, { width: yT.textContent.length * 6.2 + 10, height: 16 });
      set(yG, { transform: `translate(${W - MARGIN.right + 2},${my - 8})`, opacity: 1 });
    } else { set(hLine, { opacity: 0 }); set(yG, { opacity: 0 }); }

    info.innerHTML = describe(idx);
    info.classList.remove("hidden");

    if (drag && drag.mode === "pan") {
      const dSlots = Math.round((drag.mx - mx) / geom.slot);
      if (dSlots !== drag.applied) {
        drag.applied = dSlots;
        panTo(drag.startI0 + dSlots);
      }
    } else if (!drag) {
      drag = null;
    }
  };

  svgEl.onmouseleave = () => {
    if (drag && drag.mode === "select") return;
    set(vLine, { opacity: 0 }); set(hLine, { opacity: 0 });
    set(yG, { opacity: 0 });
    info.classList.add("hidden");
  };

  svgEl.onmousedown = (ev) => {
    const { x: mx } = toLocal(ev);
    if (ev.shiftKey) {
      drag = { mode: "select", startX: mx };
      set(selRect, { x: mx, y: MARGIN.top, width: 0, opacity: 1 });
    } else {
      drag = { mode: "pan", mx, startI0: chartState.i0, applied: 0 };
      svgEl.style.cursor = "grabbing";
    }
    ev.preventDefault();
  };

  svgEl.onwheel = (ev) => {
    ev.preventDefault();
    const { x: mx } = toLocal(ev);
    const n = chartState.series.length;
    const span = chartState.i1 ? chartState.i1 - chartState.i0 : n;
    const anchor = chartState.i0 + (mx - MARGIN.left) / ((W - MARGIN.left - MARGIN.right) / span);
    const factor = ev.deltaY > 0 ? 1.2 : 1 / 1.2;
    let newSpan = Math.round(span * factor);
    newSpan = Math.max(MIN_SPAN, Math.min(n, newSpan));
    const frac = (anchor - chartState.i0) / span;
    let i0 = Math.round(anchor - frac * newSpan);
    i0 = Math.max(0, Math.min(n - newSpan, i0));
    chartState.i0 = i0;
    chartState.i1 = i0 + newSpan;
    $("zoom-reset").classList.remove("hidden");
    drawChart();
  };

  svgEl.ondblclick = resetZoom;
}

function panTo(newI0) {
  const n = chartState.series.length;
  const span = chartState.i1 ? chartState.i1 - chartState.i0 : n;
  newI0 = Math.max(0, Math.min(n - span, newI0));
  chartState.i0 = newI0;
  chartState.i1 = newI0 + span;
  drawChart();
}

function zoomTo(a, b) {
  const lo = Math.max(0, Math.min(a, b));
  const hi = Math.min(chartState.series.length, Math.max(a, b));
  if (hi - lo < MIN_SPAN) return;
  chartState.i0 = lo;
  chartState.i1 = hi;
  $("zoom-reset").classList.remove("hidden");
  drawChart();
}

function resetZoom() {
  chartState.i0 = 0;
  chartState.i1 = null;
  $("zoom-reset").classList.add("hidden");
  drawChart();
}

/* ----- Donut (unchanged math, exact split via backend excess_kwh) ----- */
function donutArc(cx, cy, rO, rI, a0, a1) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p = (r, a) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`;
  return `M ${p(rO, a0)} A ${rO} ${rO} 0 ${large} 1 ${p(rO, a1)} L ${p(rI, a1)} A ${rI} ${rI} 0 ${large} 0 ${p(rI, a0)} Z`;
}

function drawDonut(series) {
  let work = 0, base = 0, waste = 0;
  for (const d of series) {
    if (d.is_workday) work += d.consumption_kwh;
    else { waste += d.excess_kwh; base += d.consumption_kwh - d.excess_kwh; }
  }
  const total = work + base + waste;
  if (!total) return;
  const slices = [
    { name: "Потребление в рабочие дни", val: work, color: COLORS.workday },
    { name: "Норма в нерабочие дни", val: base, color: COLORS.offday },
    { name: "Потери энергии", val: waste, color: COLORS.anomaly },
  ].filter((s) => s.val > 0);

  const cx = 175, cy = 172, rO = 120, rI = 76, pad = 0.012;
  let angle = -Math.PI / 2, arcs = "";
  for (const s of slices) {
    const sweep = (s.val / total) * Math.PI * 2;
    arcs += `<path class="slice" d="${donutArc(cx, cy, rO, rI, angle + pad / 2, angle + sweep - pad / 2)}" fill="${s.color}"><title>${s.name}: ${fmt(s.val, 1)} кВт·ч (${((s.val / total) * 100).toFixed(1)}%)</title></path>`;
    angle += sweep;
  }
  const center =
    `<text x="${cx}" y="${cy - 8}" text-anchor="middle" fill="#e6edf3" font-size="24" font-weight="700">${fmt(total)}</text>` +
    `<text x="${cx}" y="${cy + 12}" text-anchor="middle" fill="#9aa4b2" font-size="11">кВт·ч всего · ${series.length} дн.</text>` +
    `<text x="${cx}" y="${cy + 30}" text-anchor="middle" fill="${COLORS.anomaly}" font-size="11.5" font-weight="600">${((waste / total) * 100).toFixed(1)}% — потери</text>`;
  const rows = slices.map((s) =>
    `<div class="row${s.color === COLORS.anomaly ? " waste" : ""}"><span class="swatch" style="background:${s.color}"></span><span class="name">${s.name}</span><span class="val">${fmt(s.val, 1)} кВт·ч</span><span class="pct">${((s.val / total) * 100).toFixed(1)}%</span></div>`).join("");
  $("chart").innerHTML =
    `<div class="donut-wrap fade-in"><svg viewBox="0 0 350 344" style="width:350px" role="img">${arcs}${center}</svg><div class="dlegend">${rows}</div></div>`;
}

/* ----- orchestrator ----- */
function renderChart(series, threshold) {
  Object.assign(chartState, { series, threshold, i0: 0, i1: null, anim: true });
  drawChart();
}

function drawChart() {
  const { series, view } = chartState;
  if (!series.length) return;
  if (view === "donut") { drawDonut(series); return; }

  const full =
    view === "weekly" ? weeklyItems(series)
    : view === "cumulative" ? cumulativeItems(series)
    : series.map((d) => ({
        label: d.date,
        kwh: d.consumption_kwh,
        excess: d.excess_kwh,
        closed: !d.is_workday,
        color: dayColor(d),
      }));

  chartState.i0 = Math.max(0, Math.min(chartState.i0, full.length - 1));
  if (!chartState.i1 || chartState.i1 > full.length) chartState.i1 = full.length;
  const items = full.slice(chartState.i0, chartState.i1);
  const n = items.length;
  if (!n) return;
  const slot = (W - MARGIN.left - MARGIN.right) / n;
  const xCenter = (i) => MARGIN.left + i * slot + slot / 2;

  let maxV, mean = 0, builder, describe;
  if (view === "weekly") {
    maxV = Math.max(...items.map((w) => w.total));
    builder = drawWeeklySvg;
    describe = (i) => {
      const w = items[i];
      const segRows = w.segs.filter((s) => s.v > 0)
        .map((s) => `<div class="ci-row"><i class="dot" style="background:${s.color};display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px"></i>${s.name}: <b>${fmt(s.v, 1)}</b> кВт·ч</div>`).join("");
      return `<div class="ci-date">${w.label}</div><div class="ci-row">всего <b>${fmt(w.total, 1)} кВт·ч</b> · ${w.days} дн.</div>${segRows}`;
    };
  } else if (view === "cumulative") {
    maxV = Math.max(...items.map((d) => d.total));
    builder = drawCumulativeSvg;
    describe = (i) => {
      const d = items[i];
      return `<div class="ci-date">${d.label}</div><div class="ci-row">всего: <b>${fmt(d.total, 1)} кВт·ч</b></div><div class="ci-warn">потери на этот момент: ${fmt(d.waste, 1)} кВт·ч</div>`;
    };
  } else {
    maxV = Math.max(...items.map((d) => d.kwh), chartState.threshold);
    const kwhs = items.map((d) => d.kwh);
    mean = kwhs.reduce((a, b) => a + b, 0) / n;
    builder = view === "line" ? drawLineSvg : drawBarsSvg;
    describe = (i) => {
      const d = items[i];
      return `<div class="ci-date">${d.label}${d.closed ? " · нерабочий день" : " · рабочий день"}</div><div class="ci-row"><b>${fmt(d.kwh, 1)} кВт·ч</b>${mean ? ` · ср. ${fmt(mean, 1)}` : ""}</div>${d.excess > 0 ? `<div class="ci-warn">⚠ +${fmt(d.excess, 1)} кВт·ч сверх базового уровня</div>` : ""}`;
    };
  }

  const y = makeY(maxV);
  const { svg } = builder(items, y);
  const showMean = view === "bar" || view === "line";
  $("chart").innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" class="${chartState.anim ? "fade-in" : ""}">${defsGradients()}${y.grid}${refLines(y, mean, showMean)}${svg}</svg>`;
  chartState.anim = false;

  const svgEl = $("chart").querySelector("svg");
  attachInteraction(svgEl, { n, slot, xCenter, y }, describe);
}
/* ---------- AI Copilot ---------- */
function resetCopilot() {
  $("insight-text").classList.add("hidden");
  $("insight-text").innerHTML = "";
  $("insight-loading").classList.add("hidden");
  lastInsight = null;
}

// Minimal safe Markdown renderer (headings, bullets, bold, code) — input is
// HTML-escaped first so nothing from the model can inject markup.
function mdToHtml(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) =>
    esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "";
  let inList = false;
  for (const line of md.split("\n")) {
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (li) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${inline(li[1])}</li>`;
    } else {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      if (h) html += `<h3>${inline(h[2])}</h3>`;
      else if (line.trim()) html += `<p>${inline(line)}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html;
}

$("insight-btn").addEventListener("click", async () => {
  if (!lastAnalysis) return;
  resetCopilot();
  $("insight-loading").classList.remove("hidden");
  try {
    const res = await apiPost("/api/insight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: lastAnalysis.source,
        summary: lastAnalysis.summary,
        anomalies: lastAnalysis.series.filter((d) => d.is_anomaly),
      }),
      signal: reqTimeout(150000),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error(data?.detail || `Ошибка запроса (${res.status})`);
    const box = $("insight-text");
    box.innerHTML = mdToHtml(data.insight);
    box.classList.remove("hidden");
    lastInsight = data;
  } catch (err) {
    showStatus(`Копилот недоступен — ${err.message}`, true);
    setTimeout(hideStatus, 6000);
  } finally {
    $("insight-loading").classList.add("hidden");
  }
});

/* ---------- Events ---------- */
document.querySelectorAll(".seg-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    document
      .querySelectorAll(".seg-btn")
      .forEach((b) => b.classList.toggle("active", b === btn));
    Object.assign(chartState, { view: btn.dataset.view, i0: 0, i1: null, anim: true });
    $("zoom-reset").classList.add("hidden");
    drawChart();
  }),
);



$("upload-btn").addEventListener("click", () => $("file-input").click());

$("file-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) {
    const formData = new FormData();
    formData.append("file", file);
    // Passing the filename makes the loading state show which file is
    // actually being analyzed, so it's never silently unclear whether an
    // upload was picked up.
    analyze(formData, file.name); // re-renders the dashboard and resets the AI Copilot panel
  }
  e.target.value = "";
});

$("sample-btn").addEventListener("click", () => analyze(new FormData()));

let debounceTimer;
$("tariff").addEventListener("input", () => {
  if ($("results").classList.contains("hidden")) return;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (lastAnalysis && lastAnalysis.source === "sample_data.csv") analyze(new FormData());
    // For uploads we keep the previous result until a new file is chosen,
    // because browsers cannot re-read a File input after it is cleared.
  }, 450);
});

/* ---------- Tabs ---------- */
function showTab(name) {
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `tab-${name}`);
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const active = btn.dataset.tab === name;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
}

document.querySelectorAll(".tab-btn").forEach((btn) =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab)),
);

/* ---------- Anomaly threshold slider (item 5) ----------
   Re-runs the existing /api/analyze with a different multiplier — no
   detection logic is duplicated on the client. */
let multiplierDebounce;
$("multiplier-slider").addEventListener("input", () => {
  const value = Number($("multiplier-slider").value).toFixed(1);
  $("multiplier-value").textContent = value;
  clearTimeout(multiplierDebounce);
  multiplierDebounce = setTimeout(() => {
    currentMultiplier = value;
    // Same limitation as the tariff input: an uploaded File input can't be
    // re-read once cleared, so only the bundled sample recalculates live.
    if (lastAnalysis && lastAnalysis.source === "sample_data.csv") analyze(new FormData());
  }, 300);
});

/* ---------- "Скачать отчёт" — printable HTML report ---------- */
function buildReportHtml() {
  if (!lastAnalysis) return "";
  const { summary: s, series, source } = lastAnalysis;
  const anomalies = series.filter((d) => d.is_anomaly).sort((a, b) => b.excess_kwh - a.excess_kwh);
  const generated = new Date().toLocaleString("ru-RU");
  const tariffNote = s.baseline_reliable
    ? "подтверждённый (данных по нерабочим дням достаточно)"
    : "демо (нерабочих дней в выборке пока мало — предварительный сигнал, не подтверждённый факт)";
  const rows = anomalies
    .map(
      (d) =>
        `<tr><td>${d.date}</td><td>${fmt(d.consumption_kwh, 1)} кВт·ч</td>` +
        `<td>+${fmt(d.excess_kwh, 1)} кВт·ч</td>` +
        `<td>${fmt(d.excess_kwh * s.tariff_kzt_per_kwh)} тенге</td></tr>`,
    )
    .join("");
  const insightBlock = lastInsight
    ? `<h2>Рекомендация ИИ</h2>
       <p class="muted">Источник: ${lastInsight.model === "offline-fallback" ? "офлайн-шаблон (Gemini был недоступен)" : `Gemini (${lastInsight.model})`}</p>
       <div>${mdToHtml(lastInsight.insight)}</div>`
    : "";
  return `<!doctype html>
<html lang="ru"><head><meta charset="utf-8" />
<title>Отчёт EcoBiz Copilot — ${source}</title>
<style>
  body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #111; padding: 24px; max-width: 760px; margin: 0 auto; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin-top: 22px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { border-bottom: 1px solid #ccc; padding: 5px 8px; text-align: left; font-size: 12.5px; }
  .muted { color: #555; font-size: 12px; }
  .summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 20px; margin: 10px 0; }
  @media print { body { padding: 0; } }
</style></head>
<body>
  <h1>EcoBiz Copilot — отчёт по потерям энергии</h1>
  <p class="muted">Сгенерировано: ${generated} · Источник данных: ${source}</p>
  <p class="muted">Тариф: ${s.tariff_kzt_per_kwh} тенге/кВт·ч (${tariffNote})</p>
  <h2>Сводные цифры</h2>
  <div class="summary">
    <div>Потери энергии: <strong>${fmt(s.total_excess_kwh, 1)} кВт·ч</strong></div>
    <div>Потери в тенге: <strong>${fmt(s.savings_kzt)} тенге</strong></div>
    <div>Экономия CO₂: <strong>${fmt(s.co2_saved_kg, 1)} кг</strong></div>
    <div>Аномальных дней: <strong>${s.anomaly_days} из ${s.days_analyzed}</strong></div>
    <div>Прогноз экономии за 3 месяца: <strong>${fmt(s.savings_kzt * 3)} тенге</strong> (проекция, не гарантия)</div>
    <div>Прогноз экономии за год: <strong>${fmt(s.savings_kzt * 12)} тенге</strong> (проекция, не гарантия)</div>
    <div>Базовый уровень: <strong>${fmt(s.baseline_kwh, 1)} кВт·ч</strong> (порог ×${fmt(s.multiplier, 1)})</div>
  </div>
  <h2>Аномальные даты</h2>
  <table><thead><tr><th>Дата</th><th>Потребление</th><th>Превышение</th><th>Потери</th></tr></thead>
  <tbody>${rows || '<tr><td colspan="4">Аномалий не обнаружено</td></tr>'}</tbody></table>
  ${insightBlock}
</body></html>`;
}

$("download-btn").addEventListener("click", () => {
  const html = buildReportHtml();
  if (!html) return;
  const win = window.open("", "_blank");
  if (!win) {
    showStatus("Не удалось открыть окно отчёта — разрешите всплывающие окна для этого сайта.", true);
    setTimeout(hideStatus, 6000);
    return;
  }
  win.document.write(html);
  win.document.close();
  win.focus();
  win.print();
});

// Load something immediately so the screen is never empty.
analyze(new FormData());
