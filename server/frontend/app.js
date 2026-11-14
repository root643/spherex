/* SPHEREx Blink — cliente. Vanilla JS, sin dependencias. */
(() => {
  "use strict";
  const API = "/api/v1";
  const $ = (id) => document.getElementById(id);
  const TARGETS = [
    { name: "Barnard", q: "Barnard's Star", ra: 269.4521, dec: 4.6934, fov: 240, band: "D2" },
    { name: "WISE 0855", q: "WISE 0855-0714", ra: 133.7951, dec: -7.2451, fov: 240, band: "D5" },
    { name: "ε Indi B", q: "Epsilon Indi B", ra: 331.044, dec: -56.783, fov: 300, band: "D3" },
    { name: "RW Aur", q: "RW Aurigae", ra: 76.956, dec: 30.401, fov: 300, band: "D2" },
    { name: "R Dra", q: "R Draconis", ra: 248.168, dec: 66.755, fov: 300, band: "D2" },
  ];
  const DET = { 1: [0.75, 1.10], 2: [1.10, 1.63], 3: [1.63, 2.42], 4: [2.42, 3.83], 5: [3.83, 4.42], 6: [4.42, 5.0] };
  const DET_COLOR = ["#7aa2f7", "#7fd1b9", "#c3e88d", "#ffb454", "#ff8a65", "#f07178"];

  // ------------------------------------------------------------------ estado <-> URL
  const P = new URLSearchParams(location.search);
  const num = (k, d) => (P.has(k) && isFinite(parseFloat(P.get(k))) ? parseFloat(P.get(k)) : d);
  const state = {
    ra: num("ra", null), dec: num("dec", null),
    fov: Math.min(1800, Math.max(60, num("fov", 300))),
    band: /^(D[1-6]|ch\d{1,3})$/.test(P.get("band") || "") ? P.get("band") : "D4",
    mode: ["blink", "diff", "coadd"].includes(P.get("mode")) ? P.get("mode") : "blink",
    stretch: ["asinh", "log", "sqrt", "linear"].includes(P.get("stretch")) ? P.get("stretch") : "asinh",
    invert: P.get("invert") === "1",
    speed: Math.min(1500, Math.max(100, num("speed", 500))),
    ref: Math.max(0, Math.round(num("ref", 0))),
    common: P.get("common") !== "0",
    cross: P.get("cross") !== "0",
  };
  let epochs = [];          // epocas devueltas por /observations
  let frames = [];          // [{url, label, meta}]
  let idx = 0, timer = null, playing = true, loadToken = 0;
  let grid = { size: 0, scale: 0 };  // rejilla del fotograma actual (px, arcsec/px)
  let marker = null;        // {ra, dec} del ultimo clic

  function syncUrl() {
    const q = new URLSearchParams();
    if (state.ra != null) { q.set("ra", state.ra.toFixed(5)); q.set("dec", state.dec.toFixed(5)); }
    q.set("fov", state.fov); q.set("band", state.band); q.set("mode", state.mode);
    q.set("stretch", state.stretch); q.set("speed", state.speed);
    if (state.invert) q.set("invert", "1");
    if (state.mode === "diff") q.set("ref", state.ref);
    if (!state.common) q.set("common", "0");
    if (!state.cross) q.set("cross", "0");
    history.replaceState(null, "", "?" + q.toString());
  }

  const detOf = (band) => band.startsWith("D") ? +band[1] : Math.ceil(+band.slice(2) / 17);
  function chRange(ch) {
    const d = Math.ceil(ch / 17), k = (ch - 1) % 17, [lo, hi] = DET[d], s = (hi - lo) / 17;
    return [lo + k * s, lo + (k + 1) * s];
  }

  // ------------------------------------------------------------------ controles
  function setMsg(text, err) { const m = $("msg"); m.textContent = text || ""; m.classList.toggle("err", !!err); }

  function paintControls() {
    $("ra").value = state.ra ?? ""; $("dec").value = state.dec ?? "";
    $("fov").value = state.fov; $("fovOut").textContent = state.fov + "″ (" + (state.fov / 60).toFixed(1) + "′)";
    $("stretch").value = state.stretch; $("invert").checked = state.invert;
    $("speed").value = state.speed; $("speedOut").textContent = state.speed + " ms";
    $("common").checked = state.common; $("cross").checked = state.cross;
    const fine = state.band.startsWith("ch");
    $("useCh").checked = fine; $("chWrap").hidden = !fine;
    if (fine) {
      const ch = +state.band.slice(2), [a, b] = chRange(ch);
      $("ch").value = ch; $("chOut").textContent = `${ch} · ${a.toFixed(2)}–${b.toFixed(2)} µm`;
    }
    const det = detOf(state.band);
    [...$("bands").children].forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.det === det)));
    [...$("modes").children].forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)));
    $("refWrap").hidden = state.mode !== "diff";
    [...$("targets").children].forEach((b, i) => {
      const t = TARGETS[i];
      b.setAttribute("aria-pressed", String(state.ra != null && Math.abs(t.ra - state.ra) < 1e-3 && Math.abs(t.dec - state.dec) < 1e-3));
    });
  }

  function buildControls() {
    TARGETS.forEach((t) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = t.name; b.title = t.q;
      b.onclick = () => { Object.assign(state, { ra: t.ra, dec: t.dec, fov: t.fov, band: t.band }); marker = null; reload(); };
      $("targets").appendChild(b);
    });
    for (let d = 1; d <= 6; d++) {
      const b = document.createElement("button");
      b.type = "button"; b.dataset.det = d; b.textContent = "D" + d;
      b.title = `Detector ${d}: ${DET[d][0]}–${DET[d][1]} µm`;
      b.onclick = () => { state.band = "D" + d; reload(); };
      $("bands").appendChild(b);
    }
    [...$("modes").children].forEach((b) => (b.onclick = () => { state.mode = b.dataset.mode; reload(); }));
    $("fov").oninput = () => { state.fov = +$("fov").value; paintControls(); };
    $("fov").onchange = reload;
    $("useCh").onchange = () => {
      const det = detOf(state.band);
      state.band = $("useCh").checked ? "ch" + ((det - 1) * 17 + 9) : "D" + det;
      reload();
    };
    $("ch").oninput = () => { state.band = "ch" + $("ch").value; paintControls(); };
    $("ch").onchange = reload;
    $("stretch").onchange = () => { state.stretch = $("stretch").value; reload(); };
    $("invert").onchange = () => { state.invert = $("invert").checked; reload(); };
    $("common").onchange = () => { state.common = $("common").checked; reload(); };
    $("cross").onchange = () => { state.cross = $("cross").checked; syncUrl(); drawOverlay(); };
    $("ref").onchange = () => { state.ref = +$("ref").value; reload(); };
    $("speed").oninput = () => { state.speed = +$("speed").value; paintControls(); syncUrl(); if (playing) play(); };
    $("apply").onclick = () => {
      const ra = parseFloat($("ra").value), dec = parseFloat($("dec").value);
      if (!(ra >= 0 && ra < 360 && dec >= -90 && dec <= 90)) return setMsg("Coordenadas no válidas (RA 0–360°, Dec ±90°).", true);
      state.ra = ra; state.dec = dec; marker = null; reload();
    };
    $("share").onclick = async () => {
      try { await navigator.clipboard.writeText(location.href); $("share").textContent = "Enlace copiado"; }
      catch { prompt("Copia este enlace:", location.href); }
      setTimeout(() => ($("share").textContent = "Copiar enlace"), 1500);
    };
    $("search").onsubmit = async (ev) => {
      ev.preventDefault();
      const q = $("q").value.trim();
      if (!q) return;
      setMsg("Resolviendo «" + q + "»…");
      try {
        const r = await getJson(`${API}/targets/search?q=${encodeURIComponent(q)}`);
        if (!r.results.length) return setMsg("No se encontró «" + q + "».", true);
        const t = r.results[0];
        state.ra = t.ra; state.dec = t.dec; marker = null;
        reload();
      } catch (e) { setMsg(e.message, true); }
    };
    $("prev").onclick = () => { pause(); show(idx - 1); };
    $("next").onclick = () => { pause(); show(idx + 1); };
    $("play").onclick = () => (playing ? pause() : play());
    $("scrub").oninput = () => { pause(); show(+$("scrub").value); };
    document.addEventListener("keydown", (ev) => {
      if (/INPUT|SELECT|TEXTAREA/.test(ev.target.tagName)) return;
      if (ev.code === "Space") { ev.preventDefault(); playing ? pause() : play(); }
      if (ev.code === "ArrowLeft") { pause(); show(idx - 1); }
      if (ev.code === "ArrowRight") { pause(); show(idx + 1); }
    });
    const stage = $("stage");
    stage.addEventListener("mousemove", (ev) => {
      const s = skyAt(ev);
      $("cursor").textContent = s ? `RA ${s.ra.toFixed(5)}°  Dec ${s.dec >= 0 ? "+" : ""}${s.dec.toFixed(5)}°` : "—";
    });
    let clickTimer = null;
    stage.addEventListener("click", (ev) => {
      const s = skyAt(ev);
      if (!s) return;
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => { marker = s; drawOverlay(); loadSpectrum(s.ra, s.dec); }, 230);
    });
    stage.addEventListener("dblclick", (ev) => {
      clearTimeout(clickTimer);
      const s = skyAt(ev);
      if (!s) return;
      state.ra = +s.ra.toFixed(5); state.dec = +s.dec.toFixed(5); marker = null; reload();
    });
    new ResizeObserver(drawOverlay).observe(stage);
  }

  // ------------------------------------------------------------------ red
  async function getJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(await errText(r));
    return r.json();
  }
  async function errText(r) {
    try { const j = await r.json(); return j.detail ? String(j.detail) : `Error ${r.status}`; }
    catch { return `Error ${r.status}`; }
  }
  function frameUrl(kind, extra) {
    const q = new URLSearchParams({ ra: state.ra, dec: state.dec, fov: state.fov, band: state.band, stretch: state.stretch });
    if (state.invert) q.set("invert", "true");
    Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v));
    return `${API}/cutout/${kind}?${q}`;
  }
  async function fetchFrame(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(await errText(r));
    const h = (k) => r.headers.get("X-Spherex-" + k);
    const blob = await r.blob();
    return {
      url: URL.createObjectURL(blob),
      meta: { vmin: +h("Vmin"), vmax: +h("Vmax"), n: +h("N"), date: h("Date"), size: +h("Size"), scale: +h("Scale"), wl: h("Wavelength") },
    };
  }
  async function mapLimit(items, limit, fn) {
    const out = new Array(items.length);
    let i = 0;
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (i < items.length) { const k = i++; out[k] = await fn(items[k], k).catch((e) => ({ error: e })); }
    }));
    return out;
  }

  // ------------------------------------------------------------------ carga principal
  async function reload() {
    paintControls(); syncUrl();
    if (state.ra == null) return;
    const token = ++loadToken;
    pause(true);
    frames.forEach((f) => URL.revokeObjectURL(f.url));
    frames = []; $("img").removeAttribute("src"); $("frameLabel").textContent = ""; $("frameMeta").textContent = "";
    setMsg("Consultando el archivo de SPHEREx en IRSA…");
    try {
      const obs = await getJson(`${API}/observations?ra=${state.ra}&dec=${state.dec}`);
      if (token !== loadToken) return;
      epochs = obs.epochs;
      // encolar la construccion de todas las epocas en el servidor: las peticiones
      // de fotogramas que vengan despues caeran en Redis en vez de reproyectar en frio
      fetch(`${API}/cutout/prewarm?ra=${state.ra}&dec=${state.dec}&fov=${state.fov}&band=${"D" + detOf(state.band)}`)
        .catch(() => {});
      const det = "D" + detOf(state.band);
      [...$("bands").children].forEach((b) => (b.disabled = !(obs.detectors["D" + b.dataset.det] > 0)));
      const usable = epochs.filter((e) => e.detectors[det] > 0);
      paintEpochs(det);
      if (!usable.length) return setMsg(obs.n_exposures ? `Sin imágenes en ${det} para esta posición. Prueba otro detector.` : "SPHEREx aún no tiene imágenes públicas de esta posición.", true);

      let jobs;
      if (state.mode === "coadd") {
        jobs = [{ label: `Coadición · ${usable.length} épocas`, kind: "coadd", extra: {} }];
      } else if (state.mode === "diff") {
        if (usable.length < 2) return setMsg("Hace falta más de una época para restar.", true);
        if (!usable.some((e) => e.epoch === state.ref)) state.ref = usable[0].epoch;
        paintRef(usable);
        jobs = usable.filter((e) => e.epoch !== state.ref)
          .map((e) => ({ label: `${e.date} − ref`, kind: "frame", epoch: e.epoch, extra: { epoch: e.epoch, diff: "true", ref: state.ref } }));
      } else {
        jobs = usable.map((e) => ({ label: e.date, kind: "frame", epoch: e.epoch, extra: { epoch: e.epoch } }));
      }
      syncUrl();

      // ---- carga progresiva: primero baja resolucion (se ensena segun llega),
      // ---- despues alta resolucion en segundo plano reemplazando cada fotograma
      let done = 0, failed = 0, showing = false;
      const progress = () => setMsg(done < jobs.length - failed
        ? `Épocas listas: ${done}/${jobs.length - failed} (la primera vez tarda; después va desde caché)` : "");
      progress();

      const byIdx = new Map();   // indice del job -> fotograma
      const rebuild = () => {
        frames = jobs.map((j, k) => {
          const f = byIdx.get(k);
          return f ? { ...j, idx0: k, ...f } : null;
        }).filter(Boolean);
        if (!frames.length) return;
        $("scrub").max = frames.length - 1;
      };
      const applyFrame = (i, f) => {
        if (token !== loadToken) { if (f.url) URL.revokeObjectURL(f.url); return; }
        if (f.error) failed++; else byIdx.set(i, f);
        done++;
        rebuild();
        progress();
        if (!showing) {
          showing = true;
          show(0);
          if (frames.length > 1) play(); else pause(true);
        } else if (frames.length > 1 && playing) {
          play();   // rearma el temporizador al llegar mas epocas
        }
      };
      const replaceFrame = (i, g) => {
        if (token !== loadToken) { if (g.url) URL.revokeObjectURL(g.url); return; }
        const old = byIdx.get(i);
        byIdx.set(i, g);
        rebuild();
        if (old) URL.revokeObjectURL(old.url);
        if (frames[idx] && frames[idx].idx0 === i) show(idx);
      };

      // fase 1: baja resolucion
      await mapLimit(jobs, 6, async (j, i) => {
        try {
          applyFrame(i, await fetchFrame(frameUrl(j.kind, { ...j.extra, res: "low" })));
        } catch (e) { applyFrame(i, { error: e }); }
      });
      if (token !== loadToken) return;
      const lows = [...byIdx.entries()].sort((a, b) => a[0] - b[0]).map(([, f]) => f);
      if (!lows.length) return setMsg("Sin datos válidos en esta banda para esta posición.", true);
      setMsg(failed ? `${failed} época(s) sin datos válidos.` : "");

      // escala de grises comun para todas las epocas (se aplica al pedir la alta resolucion)
      let lim = {};
      if (state.common && lows.length > 1) {
        lim = { vmin: Math.min(...lows.map((f) => f.meta.vmin)), vmax: Math.max(...lows.map((f) => f.meta.vmax)) };
      }

      // fase 2 (sin esperar): alta resolucion, cada fotograma sustituye a su version baja
      mapLimit(lows, 4, async (f, i) => {
        if (token !== loadToken) return;
        try {
          replaceFrame(f.idx0, await fetchFrame(frameUrl(f.kind, { ...f.extra, ...lim })));
        } catch { /* se conserva la baja resolucion */ }
      });
    } catch (e) {
      if (token === loadToken) setMsg(e.message, true);
    }
  }

  function paintEpochs(det) {
    const ol = $("epochs"); ol.textContent = "";
    epochs.forEach((e) => {
      const li = document.createElement("li");
      const n = e.detectors[det] || 0;
      li.dataset.epoch = e.epoch;
      li.className = n ? "" : "off";
      li.innerHTML = `<span>${e.date_start} → ${e.date_end}</span><span>${n} img</span>`;
      if (n) li.onclick = () => { const k = frames.findIndex((f) => f.epoch === e.epoch); if (k >= 0) { pause(); show(k); } };
      ol.appendChild(li);
    });
  }
  function paintRef(usable) {
    const s = $("ref"); s.textContent = "";
    usable.forEach((e) => { const o = document.createElement("option"); o.value = e.epoch; o.textContent = e.date; s.appendChild(o); });
    s.value = state.ref;
  }

  // ------------------------------------------------------------------ reproductor
  function show(k) {
    if (!frames.length) return;
    idx = (k + frames.length) % frames.length;
    const f = frames[idx];
    grid = { size: f.meta.size, scale: f.meta.scale };   // la geometria sigue al fotograma mostrado
    $("img").src = f.url;
    $("scrub").value = idx;
    $("frameLabel").textContent = `${idx + 1}/${frames.length} · ${f.label}`;
    $("frameMeta").textContent = `${f.meta.n} exposiciones` + (f.meta.wl ? ` · λ≈${(+f.meta.wl).toFixed(2)} µm` : "") + ` · ${f.meta.scale}″/px`;
    [...$("epochs").children].forEach((li) => li.classList.toggle("on", +li.dataset.epoch === f.epoch));
    drawOverlay();
  }
  function play() {
    clearInterval(timer);
    playing = true; $("play").textContent = "❚❚";
    if (frames.length > 1) timer = setInterval(() => show(idx + 1), state.speed);
  }
  function pause(silent) {
    clearInterval(timer); timer = null;
    if (!silent) { playing = false; $("play").textContent = "▶"; }
  }

  // ------------------------------------------------------------------ geometria (TAN)
  const D2R = Math.PI / 180;
  function skyAt(ev) {
    if (!grid.size || state.ra == null) return null;
    const r = $("stage").getBoundingClientRect();
    const fx = (ev.clientX - r.left) / r.width - 0.5, fy = (ev.clientY - r.top) / r.height - 0.5;
    const span = grid.size * grid.scale / 3600 * D2R;       // ancho total en radianes
    const xi = -fx * span, eta = -fy * span;                // este a la izquierda, norte arriba
    const rho = Math.hypot(xi, eta), d0 = state.dec * D2R;
    if (rho === 0) return { ra: state.ra, dec: state.dec };
    const c = Math.atan(rho);
    const dec = Math.asin(Math.cos(c) * Math.sin(d0) + eta * Math.sin(c) * Math.cos(d0) / rho);
    const ra = state.ra * D2R + Math.atan2(xi * Math.sin(c), rho * Math.cos(d0) * Math.cos(c) - eta * Math.sin(d0) * Math.sin(c));
    return { ra: ((ra / D2R) % 360 + 360) % 360, dec: dec / D2R };
  }
  function pixelOf(ra, dec) {   // inversa: cielo -> fraccion [0,1] del visor
    const a = (ra - state.ra) * D2R, d = dec * D2R, d0 = state.dec * D2R;
    const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a);
    const xi = Math.cos(d) * Math.sin(a) / cosc, eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a)) / cosc;
    const span = grid.size * grid.scale / 3600 * D2R;
    return { x: 0.5 - xi / span, y: 0.5 - eta / span };
  }
  function drawOverlay() {
    const c = $("overlay"), r = $("stage").getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
    const g = c.getContext("2d"); g.scale(dpr, dpr);
    const w = r.width, h = r.height;
    if (state.cross && frames.length) {
      g.strokeStyle = "rgba(255,180,84,.85)"; g.lineWidth = 1;
      const cx = w / 2, cy = h / 2, a = 8, b = 22;
      g.beginPath();
      [[-b, 0, -a, 0], [a, 0, b, 0], [0, -b, 0, -a], [0, a, 0, b]].forEach(([x1, y1, x2, y2]) => { g.moveTo(cx + x1, cy + y1); g.lineTo(cx + x2, cy + y2); });
      g.stroke();
    }
    if (marker && grid.size) {
      const p = pixelOf(marker.ra, marker.dec);
      g.strokeStyle = "#7fd1b9"; g.lineWidth = 1.5;
      g.beginPath(); g.arc(p.x * w, p.y * h, 9, 0, 2 * Math.PI); g.stroke();
    }
  }

  // ------------------------------------------------------------------ espectro
  async function loadSpectrum(ra, dec) {
    $("sedInfo").textContent = `Midiendo los 102 canales en ${ra.toFixed(5)}°, ${dec.toFixed(5)}°… (puede tardar la primera vez)`;
    $("diag").textContent = "";
    try {
      const s = await getJson(`${API}/spectrum?ra=${ra.toFixed(5)}&dec=${dec.toFixed(5)}`);
      $("sedInfo").textContent = `${s.n_exposures} exposiciones · apertura r = ${s.aperture_radius_px} px · ${ra.toFixed(5)}°, ${dec.toFixed(5)}°`;
      drawChart(s);
      const fmt = (v) => (v == null ? "sin datos" : (v * 100).toFixed(1) + " %");
      $("diag").innerHTML = `<dt>Hielo de agua (3,05 µm)</dt><dd>${fmt(s.diagnostics["h2o_ice_3.05um_depth"])}</dd>` +
        `<dt>CO₂ (4,27 µm)</dt><dd>${fmt(s.diagnostics["co2_4.27um_depth"])}</dd>`;
    } catch (e) { $("sedInfo").textContent = e.message; }
  }
  function drawChart(s) {
    const c = $("chart"), g = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    const W = Math.max(280, c.clientWidth), H = Math.round(W * 0.7);
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const L = 50, R = 10, T = 14, B = 34, x0 = 0.75, x1 = 5.0;
    g.clearRect(0, 0, W, H);
    const ch = s.channels.filter((p) => p.flux_ujy != null);
    const vals = ch.length ? ch.map((p) => p.flux_ujy) : s.points.map((p) => p.flux_ujy);
    let lo = Math.min(0, ...vals), hi = Math.max(...vals);
    if (!(hi > lo)) hi = lo + 1;
    const pad = (hi - lo) * 0.12; hi += pad; if (lo < 0) lo -= pad;
    const X = (v) => L + (v - x0) / (x1 - x0) * (W - L - R), Y = (v) => H - B - (v - lo) / (hi - lo) * (H - T - B);
    // detectores
    for (let d = 1; d <= 6; d++) {
      g.fillStyle = d % 2 ? "rgba(255,255,255,.03)" : "rgba(255,255,255,0)";
      g.fillRect(X(DET[d][0]), T, X(DET[d][1]) - X(DET[d][0]), H - T - B);
      g.fillStyle = DET_COLOR[d - 1]; g.font = "10px ui-monospace, monospace"; g.textAlign = "center";
      g.fillText("D" + d, (X(DET[d][0]) + X(DET[d][1])) / 2, T + 10);
    }
    // ejes
    g.strokeStyle = "#3a434e"; g.fillStyle = "#8a94a1"; g.lineWidth = 1; g.font = "11px ui-monospace, monospace";
    g.beginPath(); g.moveTo(L, T); g.lineTo(L, H - B); g.lineTo(W - R, H - B); g.stroke();
    g.textAlign = "center";
    for (let v = 1; v <= 5; v++) { g.fillText(v + "", X(v), H - B + 14); g.beginPath(); g.moveTo(X(v), H - B); g.lineTo(X(v), H - B + 4); g.stroke(); }
    g.fillText("longitud de onda (µm)", (L + W - R) / 2, H - 6);
    g.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      g.fillText(Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(Math.abs(hi) < 10 ? 2 : 0), L - 6, Y(v) + 4);
      g.strokeStyle = "rgba(255,255,255,.05)"; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - R, Y(v)); g.stroke();
    }
    g.save(); g.translate(12, (T + H - B) / 2); g.rotate(-Math.PI / 2); g.textAlign = "center"; g.fillText("flujo (µJy)", 0, 0); g.restore();
    // marcas diagnosticas
    [[3.05, "H₂O"], [4.27, "CO₂"]].forEach(([wl, name]) => {
      g.strokeStyle = "rgba(127,209,185,.6)"; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(X(wl), T + 14); g.lineTo(X(wl), H - B); g.stroke(); g.setLineDash([]);
      g.fillStyle = "#7fd1b9"; g.textAlign = "center"; g.fillText(name, X(wl), T + 24);
    });
    // puntos individuales
    g.save(); g.beginPath(); g.rect(L, T, W - L - R, H - T - B); g.clip();
    s.points.forEach((p) => { g.fillStyle = DET_COLOR[p.detector - 1] + "55"; g.fillRect(X(p.wavelength) - 1, Y(p.flux_ujy) - 1, 2, 2); });
    // medianas por canal
    g.strokeStyle = "#ffb454"; g.lineWidth = 1.5; g.beginPath();
    ch.forEach((p, i) => (i ? g.lineTo(X(p.wavelength), Y(p.flux_ujy)) : g.moveTo(X(p.wavelength), Y(p.flux_ujy))));
    g.stroke();
    ch.forEach((p) => { g.fillStyle = DET_COLOR[p.detector - 1]; g.beginPath(); g.arc(X(p.wavelength), Y(p.flux_ujy), 2.2, 0, 2 * Math.PI); g.fill(); });
    g.restore();
  }

  // ------------------------------------------------------------------ estadisticas
  async function stats() {
    try {
      const s = await getJson(`${API}/cache/stats`), r = s.redis, d = s.disk || {};
      const gb = (b) => (b / 1073741824).toFixed(2) + " GB";
      let t = r.backend === "redis" && !r.error
        ? `Redis ${r.used_memory_human}${r.maxmemory_bytes ? " / " + gb(r.maxmemory_bytes) : ""} · ${r.keys} claves · aciertos ${r.hit_rate == null ? "—" : (r.hit_rate * 100).toFixed(0) + " %"} · ${r.ops_per_sec} ops/s`
        : "Redis no disponible (caché local)";
      if (d.fits_bytes != null) t += ` · NVMe ${gb(d.fits_bytes)} en ${d.fits_files} FITS`;
      $("stats").textContent = "caché: " + t;
    } catch { $("stats").textContent = "caché: sin conexión con la API"; }
  }

  buildControls(); paintControls();
  if (state.ra == null) { Object.assign(state, { ra: TARGETS[0].ra, dec: TARGETS[0].dec, fov: TARGETS[0].fov, band: TARGETS[0].band }); }
  reload(); stats(); setInterval(stats, 20000);
})();
