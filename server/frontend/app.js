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
    gaia: P.get("gaia") === "1",
    wise: P.get("wise") === "1",
  };
  let epochs = [];          // epocas devueltas por /observations
  let frames = [];          // [{url, label, meta}]
  let idx = 0, timer = null, playing = true, loadToken = 0;
  let grid = { size: 0, scale: 0 };  // rejilla del fotograma actual (px, arcsec/px)
  let marker = null;        // {ra, dec} del ultimo clic
  let baseFov = state.fov;  // FOV de los fotogramas cargados en el visor
  let zoomDebounceTimer = null;
  let panOffset = { x: 0, y: 0 };
  let clickTimer = null;
  let spectrumAbortController = null;
  let gaiaSources = [];
  let wiseSources = [];
  const catalogCache = new Map();

  function abortSpectrum() {
    clearTimeout(clickTimer);
    if (spectrumAbortController) {
      spectrumAbortController.abort();
      spectrumAbortController = null;
      if (chartLoader) chartLoader.done("");
      $("sedInfo").textContent = "Haz clic o toca sobre un objeto para analizar su espectro.";
    }
  }

  function applyVisualTransform() {
    const img = $("img");
    if (!img) return;
    const zoomRatio = (baseFov && state.fov) ? (baseFov / state.fov) : 1;
    const hasPan = Math.abs(panOffset.x) > 0.5 || Math.abs(panOffset.y) > 0.5;
    const hasZoom = Math.abs(zoomRatio - 1) >= 0.005;

    if (!hasPan && !hasZoom) {
      img.style.transform = "none";
    } else if (hasPan && hasZoom) {
      img.style.transform = `translate(${panOffset.x.toFixed(1)}px, ${panOffset.y.toFixed(1)}px) scale(${zoomRatio.toFixed(4)})`;
    } else if (hasPan) {
      img.style.transform = `translate(${panOffset.x.toFixed(1)}px, ${panOffset.y.toFixed(1)}px)`;
    } else {
      img.style.transform = `scale(${zoomRatio.toFixed(4)})`;
    }
    drawOverlay();
  }

  function applyVisualZoom(ratio) {
    applyVisualTransform();
  }

  function animateRecenter(targetTx, targetTy) {
    const img = $("img");
    if (!img) return;
    img.classList.add("panning");
    panOffset.x = targetTx;
    panOffset.y = targetTy;
    applyVisualTransform();
    setTimeout(() => {
      img.classList.remove("panning");
    }, 290);
  }

  function changeZoom(delta) {
    clearTimeout(clickTimer);
    abortSpectrum();
    const step = state.fov < 300 ? 30 : (state.fov < 900 ? 60 : 120);
    const nextFov = Math.max(60, Math.min(1800, state.fov + delta * step));
    if (nextFov !== state.fov) {
      state.fov = nextFov;
      $("fov").value = state.fov;
      $("fovOut").textContent = state.fov + "″ (" + (state.fov / 60).toFixed(1) + "′)";
      syncUrl();
      applyVisualTransform();
      clearTimeout(zoomDebounceTimer);
      zoomDebounceTimer = setTimeout(() => reload({ zoomOnly: true }), 260);
    }
  }

  function syncUrl() {
    const q = new URLSearchParams();
    if (state.ra != null) { q.set("ra", state.ra.toFixed(5)); q.set("dec", state.dec.toFixed(5)); }
    q.set("fov", state.fov); q.set("band", state.band); q.set("mode", state.mode);
    q.set("stretch", state.stretch); q.set("speed", state.speed);
    if (state.invert) q.set("invert", "1");
    if (state.mode === "diff") q.set("ref", state.ref);
    if (!state.common) q.set("common", "0");
    if (!state.cross) q.set("cross", "0");
    if (state.gaia) q.set("gaia", "1");
    if (state.wise) q.set("wise", "1");
    history.replaceState(null, "", "?" + q.toString());
  }

  const detOf = (band) => band.startsWith("D") ? +band[1] : Math.ceil(+band.slice(2) / 17);
  function chRange(ch) {
    const d = Math.ceil(ch / 17), k = (ch - 1) % 17, [lo, hi] = DET[d], s = (hi - lo) / 17;
    return [lo + k * s, lo + (k + 1) * s];
  }

  // ------------------------------------------------------------------ buffer loader psicologico con ETA
  function createBufferLoader(wrapEl, textEl) {
    const fillEl = wrapEl ? wrapEl.querySelector(".buffer-fill") : null;
    let startTime = 0;
    let baseText = "";
    let raf = null;
    let finished = true;
    let currentPct = 0;

    function frame(now) {
      if (finished) return;
      const elapsed = now - startTime;

      // Curva psicologica:
      // 0 a 160ms: salta de 0% a 68% (arranque explosivo: percepcion de velocidad inmediata)
      // 160 a 550ms: sube suavemente de 68% a 84%
      // 550ms+: se frena asintoticamente (crawling) hacia el 92% mientras el servidor entrega los bytes
      if (elapsed < 160) {
        currentPct = (elapsed / 160) * 68;
      } else if (elapsed < 550) {
        currentPct = 68 + ((elapsed - 160) / 390) * 16;
      } else {
        const extra = 8 * (1 - Math.exp(-(elapsed - 550) / 900));
        currentPct = 84 + extra;
      }

      if (fillEl) fillEl.style.width = `${currentPct.toFixed(1)}%`;

      // ETA psicologico dinamico
      let eta = "";
      if (currentPct < 65) {
        eta = "~0.5s";
      } else if (currentPct < 80) {
        eta = "~0.2s";
      } else {
        eta = "~0.1s";
      }

      if (textEl && !finished) {
        textEl.innerHTML = `${baseText} <span class="buffer-eta mono">[${Math.round(currentPct)}% · ${eta}]</span>`;
      }

      raf = requestAnimationFrame(frame);
    }

    return {
      start(label) {
        finished = false;
        currentPct = 0;
        baseText = label || "";
        startTime = performance.now();
        if (fillEl) {
          fillEl.className = "buffer-fill";
          fillEl.style.width = "0%";
        }
        if (wrapEl) wrapEl.classList.add("active");
        if (textEl) textEl.classList.remove("err");
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(frame);
      },
      step(pct, label) {
        if (finished) return;
        if (label) baseText = label;
        if (pct > currentPct) currentPct = pct;
        if (fillEl) fillEl.style.width = `${currentPct.toFixed(1)}%`;
      },
      done(finalLabel) {
        finished = true;
        cancelAnimationFrame(raf);
        if (fillEl) {
          fillEl.style.width = "100%";
          fillEl.classList.add("done");
        }
        if (textEl && finalLabel !== undefined) {
          textEl.textContent = finalLabel;
        }
        setTimeout(() => {
          if (wrapEl) wrapEl.classList.remove("active");
          setTimeout(() => {
            if (finished && fillEl) fillEl.style.width = "0%";
          }, 300);
        }, 220);
      },
      fail(errLabel) {
        finished = true;
        cancelAnimationFrame(raf);
        if (fillEl) fillEl.classList.add("err");
        if (textEl && errLabel !== undefined) {
          textEl.textContent = errLabel;
          textEl.classList.add("err");
        }
        setTimeout(() => {
          if (wrapEl) wrapEl.classList.remove("active");
        }, 600);
      }
    };
  }

  let viewerLoader = null;
  let chartLoader = null;

  // ------------------------------------------------------------------ controles
  function setMsg(text, err) { const m = $("msg"); m.textContent = text || ""; m.classList.toggle("err", !!err); }

  const SCIENTIFIC_TARGETS = [
    { name: "Barnard's Star", ra: 269.4521, dec: 4.6934, fov: 240, band: "D2", type: "Mayor movimiento propio conocido (10.39″/año)" },
    { name: "WISE 0855-0714", ra: 133.7951, dec: -7.2451, fov: 240, band: "D5", type: "Enana marrón más fría del cielo (~250 K)" },
    { name: "ε Indi B", ra: 331.0440, dec: -56.7830, fov: 300, band: "D3", type: "Binaria de enanas marrones (4.70″/año)" },
    { name: "Luhman 16", ra: 162.2988, dec: -53.3194, fov: 240, band: "D4", type: "Par de enanas marrones más cercano (6.5 al)" },
    { name: "TRAPPIST-1", ra: 346.6224, dec: -5.0414, fov: 240, band: "D3", type: "Enana roja con 7 planetas templados" },
    { name: "Polo Eclíptico Norte (NEP)", ra: 270.0000, dec: 66.5607, fov: 360, band: "D4", type: "Campo profundo continua SPHEREx" },
    { name: "Polo Eclíptico Sur (SEP)", ra: 90.0000, dec: -66.5607, fov: 360, band: "D4", type: "Campo profundo austral SPHEREx" },
    { name: "Planeta X · Tauro", ra: 62.5000, dec: 18.2500, fov: 300, band: "D5", type: "Zona de búsqueda de Planeta X / TNOs" },
    { name: "Planeta X · Cetus", ra: 42.1500, dec: -8.4500, fov: 300, band: "D5", type: "Corredor predicho de afelio de Planeta X" },
    { name: "Planeta X · Ofiuco", ra: 255.3000, dec: -15.8000, fov: 300, band: "D5", type: "Sondeo infrarrojo de alta inclinación" },
    { name: "Estrella de Teegarden", ra: 43.2537, dec: 16.8813, fov: 240, band: "D2", type: "Enana M de altísimo PM (5.06″/año)" },
    { name: "Wolf 359", ra: 164.1203, dec: 7.0147, fov: 240, band: "D2", type: "Enana roja fulgurante vecina (4.7″/año)" },
    { name: "Ross 128", ra: 176.9350, dec: 0.8045, fov: 240, band: "D2", type: "Enana roja inactiva con exoplaneta" },
    { name: "CW Leonis (IRC +10216)", ra: 146.9892, dec: 13.2787, fov: 300, band: "D6", type: "Estrella de carbono más brillante en IR medio" },
    { name: "RW Aurigae", ra: 76.9560, dec: 30.4010, fov: 300, band: "D2", type: "Variable T Tauri con caídas profundas" },
    { name: "R Draconis", ra: 248.1680, dec: 66.7550, fov: 300, band: "D2", type: "Variable Mira clásica (P ~ 245.6 d)" }
  ];

  function randomCelestialCoord() {
    const ra = +(Math.random() * 360).toFixed(4);
    const u = Math.random() * 2 - 1;
    const dec = +(Math.asin(u) * 180 / Math.PI).toFixed(4);
    const band = "D" + (Math.floor(Math.random() * 6) + 1);
    return { name: `Esfera celeste (${ra}° ${dec >= 0 ? "+" : ""}${dec}°)`, ra, dec, fov: 300, band, type: "Distribución uniforme cos(Dec)" };
  }

  async function handleRandomCoordinates() {
    let t = null;
    try {
      t = await getJson(`${API}/targets/random`);
    } catch {
      t = (Math.random() < 0.75)
        ? SCIENTIFIC_TARGETS[Math.floor(Math.random() * SCIENTIFIC_TARGETS.length)]
        : randomCelestialCoord();
    }
    state.ra = t.ra;
    state.dec = t.dec;
    if (t.fov) state.fov = t.fov;
    if (t.band) state.band = t.band;
    marker = null;
    paintControls();
    syncUrl();
    setMsg(`🎲 Coordenadas aleatorias: ${t.name} (${t.type || "Interés científico"})`);
    reload();
  }

  function updateGaiaUI() {
    if ($("overlayGaia")) $("overlayGaia").checked = state.gaia;
    if ($("stageGaia")) $("stageGaia").classList.toggle("active-gaia", state.gaia);
    if ($("gaiaLegend")) $("gaiaLegend").hidden = !state.gaia;
    if (state.gaia && $("gaiaCount")) {
      $("gaiaCount").textContent = gaiaSources.length;
    }
    if ($("catalogLegend")) $("catalogLegend").hidden = !state.gaia && !state.wise;
  }

  function updateWiseUI() {
    if ($("overlayWise")) $("overlayWise").checked = state.wise;
    if ($("stageWise")) $("stageWise").classList.toggle("active-wise", state.wise);
    if ($("wiseLegend")) $("wiseLegend").hidden = !state.wise;
    if (state.wise && $("wiseCount")) {
      $("wiseCount").textContent = wiseSources.length;
    }
    if ($("catalogLegend")) $("catalogLegend").hidden = !state.gaia && !state.wise;
  }

  async function loadCatalogs(force = false) {
    if (state.ra == null) return;
    const currentRa = state.ra, currentDec = state.dec, currentFov = state.fov;
    const radius = Math.round(currentFov * 0.7);

    if (state.gaia) {
      const gaiaKey = `gaia:${currentRa.toFixed(4)}:${currentDec.toFixed(4)}:${radius}`;
      if (!force && catalogCache.has(gaiaKey)) {
        gaiaSources = catalogCache.get(gaiaKey);
        updateGaiaUI();
      } else {
        if ($("gaiaLegend")) $("gaiaLegend").hidden = false;
        if ($("gaiaCount")) $("gaiaCount").textContent = "…";
        try {
          const res = await getJson(`${API}/catalogs/gaia?ra=${currentRa}&dec=${currentDec}&radius=${radius}&limit=120`);
          if (state.ra === currentRa && state.dec === currentDec) {
            gaiaSources = res.sources || [];
            catalogCache.set(gaiaKey, gaiaSources);
            updateGaiaUI();
          }
        } catch {
          if (state.ra === currentRa && state.dec === currentDec && $("gaiaCount")) {
            $("gaiaCount").textContent = "err";
          }
        }
      }
    } else {
      gaiaSources = [];
      updateGaiaUI();
    }

    if (state.wise) {
      const wiseKey = `wise:${currentRa.toFixed(4)}:${currentDec.toFixed(4)}:${radius}`;
      if (!force && catalogCache.has(wiseKey)) {
        wiseSources = catalogCache.get(wiseKey);
        updateWiseUI();
      } else {
        if ($("wiseLegend")) $("wiseLegend").hidden = false;
        if ($("wiseCount")) $("wiseCount").textContent = "…";
        try {
          const res = await getJson(`${API}/catalogs/wise?ra=${currentRa}&dec=${currentDec}&radius=${radius}&limit=120`);
          if (state.ra === currentRa && state.dec === currentDec) {
            wiseSources = res.sources || [];
            catalogCache.set(wiseKey, wiseSources);
            updateWiseUI();
          }
        } catch {
          if (state.ra === currentRa && state.dec === currentDec && $("wiseCount")) {
            $("wiseCount").textContent = "err";
          }
        }
      }
    } else {
      wiseSources = [];
      updateWiseUI();
    }

    drawOverlay();
  }

  function paintControls() {
    $("ra").value = state.ra ?? ""; $("dec").value = state.dec ?? "";
    $("fov").value = state.fov; $("fovOut").textContent = state.fov + "″ (" + (state.fov / 60).toFixed(1) + "′)";
    $("stretch").value = state.stretch; $("invert").checked = state.invert;
    $("speed").value = state.speed; $("speedOut").textContent = state.speed + " ms";
    $("common").checked = state.common; $("cross").checked = state.cross;
    updateGaiaUI();
    updateWiseUI();
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
    if ($("randCoord")) $("randCoord").onclick = handleRandomCoordinates;
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
    $("fov").oninput = () => {
      state.fov = +$("fov").value;
      paintControls();
      syncUrl();
      applyVisualZoom(baseFov / state.fov);
    };
    $("fov").onchange = () => {
      clearTimeout(zoomDebounceTimer);
      reload({ zoomOnly: true });
    };
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
    if ($("overlayGaia")) {
      $("overlayGaia").onchange = () => {
        state.gaia = $("overlayGaia").checked;
        syncUrl();
        updateGaiaUI();
        loadCatalogs();
      };
    }
    if ($("overlayWise")) {
      $("overlayWise").onchange = () => {
        state.wise = $("overlayWise").checked;
        syncUrl();
        updateWiseUI();
        loadCatalogs();
      };
    }
    if ($("stageGaia")) {
      $("stageGaia").onclick = (e) => {
        e.stopPropagation();
        state.gaia = !state.gaia;
        syncUrl();
        updateGaiaUI();
        loadCatalogs();
      };
    }
    if ($("stageWise")) {
      $("stageWise").onclick = (e) => {
        e.stopPropagation();
        state.wise = !state.wise;
        syncUrl();
        updateWiseUI();
        loadCatalogs();
      };
    }
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
    $("exportGif").onclick = async () => {
      if (state.ra == null || !frames.length) {
        setMsg("Carga primero un campo de SPHEREx para exportar el GIF.", true);
        return;
      }
      const btn = $("exportGif");
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Generando…";
      if (viewerLoader) viewerLoader.start("Generando animación GIF de las épocas…");
      try {
        const q = new URLSearchParams({
          ra: state.ra,
          dec: state.dec,
          fov: Math.round(state.fov),
          band: state.band,
          mode: state.mode === "diff" ? "diff" : "blink",
          stretch: state.stretch,
          speed: state.speed
        });
        if (state.invert) q.set("invert", "true");
        if (state.mode === "diff") q.set("ref", state.ref);
        if (state.common && frames.length) {
          const vmins = frames.map((f) => f.meta && f.meta.vmin).filter((v) => typeof v === "number" && !isNaN(v));
          const vmaxs = frames.map((f) => f.meta && f.meta.vmax).filter((v) => typeof v === "number" && !isNaN(v));
          if (vmins.length && vmaxs.length) {
            q.set("vmin", Math.min(...vmins));
            q.set("vmax", Math.max(...vmaxs));
          }
        }
        const res = await fetch(`${API}/cutout/gif?${q}`);
        if (!res.ok) throw new Error(await errText(res));
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const signDec = state.dec >= 0 ? `+${state.dec.toFixed(4)}` : state.dec.toFixed(4);
        a.download = `spherex_${state.band}_ra${state.ra.toFixed(4)}_dec${signDec}_fov${Math.round(state.fov)}s.gif`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        if (viewerLoader) viewerLoader.done("¡GIF animado descargado con éxito!");
      } catch (e) {
        if (viewerLoader) viewerLoader.fail("Error exportando GIF: " + e.message);
        else setMsg("Error exportando GIF: " + e.message, true);
      } finally {
        btn.disabled = false;
        btn.textContent = origText;
      }
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
      if (ev.key === "+" || ev.key === "=") { ev.preventDefault(); changeZoom(-1); }
      if (ev.key === "-" || ev.key === "_") { ev.preventDefault(); changeZoom(1); }
    });
    const stage = $("stage");
    let isTouching = false;
    let isDragging = false;
    let isPinching = false;
    let touchStartX = 0, touchStartY = 0;
    let startPanX = 0, startPanY = 0;
    let initialPinchDist = 0;
    let initialPinchFov = 0;
    let lastTapTime = 0;
    let lastTapPos = { x: 0, y: 0 };
    let dragDebounceTimer = null;
    let suppressClickUntil = 0;

    // Ratón en desktop: arrastrar para desplazar
    let isMouseDown = false;
    let mouseStartX = 0, mouseStartY = 0;
    let mouseMoved = false;

    stage.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      if (performance.now() < suppressClickUntil) return;
      isMouseDown = true;
      mouseMoved = false;
      mouseStartX = ev.clientX;
      mouseStartY = ev.clientY;
      startPanX = panOffset.x;
      startPanY = panOffset.y;
    });

    stage.addEventListener("mousemove", (ev) => {
      const s = skyAt(ev);
      $("cursor").textContent = s ? `RA ${s.ra.toFixed(5)}°  Dec ${s.dec >= 0 ? "+" : ""}${s.dec.toFixed(5)}°` : "—";
      const tooltipEl = $("tooltip");

      if (isMouseDown) {
        if (tooltipEl) tooltipEl.hidden = true;
        const dx = ev.clientX - mouseStartX;
        const dy = ev.clientY - mouseStartY;
        if (!mouseMoved && Math.hypot(dx, dy) > 5) {
          mouseMoved = true;
          abortSpectrum();
          stage.style.cursor = "grabbing";
        }
        if (mouseMoved) {
          panOffset.x = startPanX + dx;
          panOffset.y = startPanY + dy;
          applyVisualTransform();
        }
        return;
      }

      // Inspección de fuentes de Gaia y WISE con tooltip astrométrico
      let hovered = null;
      if (grid.size && (state.gaia || state.wise)) {
        const r = stage.getBoundingClientRect();
        const mx = ev.clientX - r.left;
        const my = ev.clientY - r.top;
        let bestDist = 13;

        if (state.gaia && gaiaSources.length) {
          for (const src of gaiaSources) {
            const p = pixelOf(src.ra, src.dec);
            const sx = p.x * r.width + panOffset.x;
            const sy = p.y * r.height + panOffset.y;
            const dist = Math.hypot(mx - sx, my - sy);
            if (dist < bestDist) {
              bestDist = dist;
              hovered = { type: "gaia", source: src, x: sx, y: sy };
            }
          }
        }
        if (state.wise && wiseSources.length) {
          for (const src of wiseSources) {
            const p = pixelOf(src.ra, src.dec);
            const sx = p.x * r.width + panOffset.x;
            const sy = p.y * r.height + panOffset.y;
            const dist = Math.hypot(mx - sx, my - sy);
            if (dist < bestDist) {
              bestDist = dist;
              hovered = { type: "wise", source: src, x: sx, y: sy };
            }
          }
        }
      }

      if (hovered && tooltipEl) {
        tooltipEl.hidden = false;
        tooltipEl.style.left = `${Math.min(stage.clientWidth - 260, Math.max(10, hovered.x + 12))}px`;
        tooltipEl.style.top = `${Math.min(stage.clientHeight - 95, Math.max(20, hovered.y))}px`;
        if (hovered.type === "gaia") {
          const src = hovered.source;
          tooltipEl.innerHTML = `<span style="color:#38bdf8;font-weight:600">✦ Gaia DR3</span> <span class="muted">${src.id.slice(-8)}</span><br>` +
            `G: <b>${src.gmag != null ? src.gmag.toFixed(2) : "—"}</b> mag · BP−RP: <b>${src.bp_rp != null ? (src.bp_rp >= 0 ? "+" : "") + src.bp_rp.toFixed(2) : "—"}</b><br>` +
            `PM: <b>${src.pm_total != null ? src.pm_total.toFixed(1) + " mas/a" : "—"}</b>` +
            (src.pmra != null ? ` <span class="muted">(${src.pmra}, ${src.pmdec})</span>` : "") +
            (src.parallax != null ? `<br>Paralaje: <b>${src.parallax.toFixed(2)} mas</b>` : "");
        } else {
          const src = hovered.source;
          const redClass = src.w1_w2 != null && src.w1_w2 > 1.2 ? ` style="color:#ff4081;font-weight:bold"` : "";
          tooltipEl.innerHTML = `<span style="color:#fb923c;font-weight:600">■ AllWISE</span> <span class="muted">${src.id}</span><br>` +
            `W1 (3.4 µm): <b>${src.w1 != null ? src.w1.toFixed(2) : "—"}</b> mag<br>` +
            `W2 (4.6 µm): <b>${src.w2 != null ? src.w2.toFixed(2) : "—"}</b> mag` +
            (src.w1_w2 != null ? `<br><span${redClass}>W1−W2: <b>${src.w1_w2 >= 0 ? "+" : ""}${src.w1_w2.toFixed(2)}</b>${src.w1_w2 > 1.2 ? " ★ (candidato frío)" : ""}</span>` : "");
        }
      } else if (tooltipEl) {
        tooltipEl.hidden = true;
      }
    });

    const finishMouseDrag = () => {
      const tip = $("tooltip");
      if (tip) tip.hidden = true;
      if (!isMouseDown) return;
      isMouseDown = false;
      stage.style.cursor = "crosshair";
      if (mouseMoved) {
        suppressClickUntil = performance.now() + 350;
        const r = stage.getBoundingClientRect();
        const centerEv = { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
        const newCenter = skyAt(centerEv);
        if (newCenter) {
          state.ra = +newCenter.ra.toFixed(5);
          state.dec = +newCenter.dec.toFixed(5);
          paintControls();
          syncUrl();
          clearTimeout(dragDebounceTimer);
          dragDebounceTimer = setTimeout(() => reload({ recenterOnly: true }), 260);
        }
      }
    };
    stage.addEventListener("mouseup", finishMouseDrag);
    stage.addEventListener("mouseleave", finishMouseDrag);

    // Pantallas táctiles (móvil)
    stage.addEventListener("touchstart", (ev) => {
      if (ev.touches.length === 2) {
        isPinching = true;
        isDragging = false;
        isTouching = false;
        abortSpectrum();
        initialPinchDist = Math.hypot(
          ev.touches[0].clientX - ev.touches[1].clientX,
          ev.touches[0].clientY - ev.touches[1].clientY
        );
        initialPinchFov = state.fov;
        return;
      }
      if (ev.touches.length === 1) {
        isTouching = true;
        isDragging = false;
        isPinching = false;
        touchStartX = ev.touches[0].clientX;
        touchStartY = ev.touches[0].clientY;
        startPanX = panOffset.x;
        startPanY = panOffset.y;
      }
    }, { passive: false });

    stage.addEventListener("touchmove", (ev) => {
      if (isPinching && ev.touches.length >= 2) {
        ev.preventDefault();
        abortSpectrum();
        const dist = Math.hypot(
          ev.touches[0].clientX - ev.touches[1].clientX,
          ev.touches[0].clientY - ev.touches[1].clientY
        );
        if (dist > 10 && initialPinchDist > 0) {
          const factor = initialPinchDist / dist;
          const nextFov = Math.max(60, Math.min(1800, Math.round((initialPinchFov * factor) / 30) * 30));
          if (nextFov !== state.fov) {
            state.fov = nextFov;
            paintControls();
            syncUrl();
            applyVisualTransform();
          }
        }
        return;
      }
      if (isTouching && ev.touches.length === 1) {
        const dx = ev.touches[0].clientX - touchStartX;
        const dy = ev.touches[0].clientY - touchStartY;
        const dist = Math.hypot(dx, dy);
        if (!isDragging && dist > 7) {
          isDragging = true;
          abortSpectrum();
        }
        if (isDragging) {
          ev.preventDefault();
          panOffset.x = startPanX + dx;
          panOffset.y = startPanY + dy;
          applyVisualTransform();
        }
      }
    }, { passive: false });

    stage.addEventListener("touchend", (ev) => {
      if (isPinching) {
        if (ev.touches.length < 2) {
          isPinching = false;
          suppressClickUntil = performance.now() + 350;
          clearTimeout(zoomDebounceTimer);
          zoomDebounceTimer = setTimeout(() => reload({ zoomOnly: true }), 260);
        }
        return;
      }
      if (isDragging) {
        isDragging = false;
        isTouching = false;
        suppressClickUntil = performance.now() + 350;
        const r = stage.getBoundingClientRect();
        const centerEv = { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
        const newCenter = skyAt(centerEv);
        if (newCenter) {
          state.ra = +newCenter.ra.toFixed(5);
          state.dec = +newCenter.dec.toFixed(5);
          paintControls();
          syncUrl();
          clearTimeout(dragDebounceTimer);
          dragDebounceTimer = setTimeout(() => reload({ recenterOnly: true }), 260);
        }
        return;
      }
      if (isTouching) {
        isTouching = false;
        const touch = ev.changedTouches[0];
        if (!touch) return;
        const now = performance.now();
        const tapDist = Math.hypot(touch.clientX - lastTapPos.x, touch.clientY - lastTapPos.y);

        if (now - lastTapTime < 320 && tapDist < 30) {
          // Doble toque: centrado suave
          abortSpectrum();
          lastTapTime = 0;
          suppressClickUntil = performance.now() + 350;
          const s = skyAt({ clientX: touch.clientX, clientY: touch.clientY });
          if (s) {
            const r = stage.getBoundingClientRect();
            const clickX = touch.clientX - r.left;
            const clickY = touch.clientY - r.top;
            const targetTx = r.width / 2 - clickX;
            const targetTy = r.height / 2 - clickY;
            state.ra = +s.ra.toFixed(5);
            state.dec = +s.dec.toFixed(5);
            marker = s;
            paintControls();
            syncUrl();
            animateRecenter(targetTx, targetTy);
            reload({ recenterOnly: true });
          }
        } else {
          // Toque simple: seleccionar estrella para 102 bandas
          lastTapTime = now;
          lastTapPos = { x: touch.clientX, y: touch.clientY };
          const s = skyAt({ clientX: touch.clientX, clientY: touch.clientY });
          if (s) {
            suppressClickUntil = performance.now() + 350;
            abortSpectrum();
            clickTimer = setTimeout(() => {
              marker = s;
              drawOverlay();
              loadSpectrum(s.ra, s.dec);
            }, 240);
          }
        }
      }
    }, { passive: false });

    stage.addEventListener("click", (ev) => {
      if (performance.now() < suppressClickUntil) return;
      const s = skyAt(ev);
      if (!s) return;
      abortSpectrum();
      clickTimer = setTimeout(() => {
        marker = s;
        drawOverlay();
        loadSpectrum(s.ra, s.dec);
      }, 240);
    });

    stage.addEventListener("dblclick", (ev) => {
      abortSpectrum();
      suppressClickUntil = performance.now() + 350;
      const s = skyAt(ev);
      if (!s) return;
      const r = stage.getBoundingClientRect();
      const clickX = ev.clientX - r.left;
      const clickY = ev.clientY - r.top;
      const targetTx = r.width / 2 - clickX;
      const targetTy = r.height / 2 - clickY;

      state.ra = +s.ra.toFixed(5);
      state.dec = +s.dec.toFixed(5);
      marker = s;
      paintControls();
      syncUrl();
      animateRecenter(targetTx, targetTy);
      reload({ recenterOnly: true });
    });

    stage.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      abortSpectrum();
      changeZoom(Math.sign(ev.deltaY));
    }, { passive: false });

    $("zoomIn").onclick = (e) => {
      e.stopPropagation();
      abortSpectrum();
      changeZoom(-1);
    };
    $("zoomOut").onclick = (e) => {
      e.stopPropagation();
      abortSpectrum();
      changeZoom(1);
    };

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
  async function reload(opts = {}) {
    const isZoomOnly = Boolean(opts && opts.zoomOnly && frames.length);
    const isRecenterOnly = Boolean(opts && opts.recenterOnly && frames.length);
    const keepExisting = isZoomOnly || isRecenterOnly;
    cancelPrefetch();
    paintControls(); syncUrl();
    if (state.ra == null) return;
    const token = ++loadToken;
    if (state.gaia || state.wise) loadCatalogs();

    if (!keepExisting) {
      panOffset = { x: 0, y: 0 };
      baseFov = state.fov;
      applyVisualTransform();
      pause(true);
      frames.forEach((f) => URL.revokeObjectURL(f.url));
      frames = []; $("img").removeAttribute("src"); $("frameLabel").textContent = ""; $("frameMeta").textContent = "";
      if (viewerLoader) viewerLoader.start("Consultando archivo SPHEREx…");
    } else {
      if (viewerLoader) viewerLoader.start(isRecenterOnly ? "Centrando en nuevas coordenadas…" : "Ajustando nitidez astrométrica…");
    }

    try {
      if (!isZoomOnly || !epochs || !epochs.length) {
        const obs = await getJson(`${API}/observations?ra=${state.ra}&dec=${state.dec}`);
        if (token !== loadToken) return;
        epochs = obs.epochs;
        const det = "D" + detOf(state.band);
        [...$("bands").children].forEach((b) => (b.disabled = !(obs.detectors["D" + b.dataset.det] > 0)));
      }
      const det = "D" + detOf(state.band);
      const usable = epochs.filter((e) => e.detectors[det] > 0);
      paintEpochs(det);
      if (!usable.length) return setMsg(`Sin imágenes en ${det} para esta posición. Prueba otro detector.`, true);

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
      let done = 0, failed = 0, showing = keepExisting;
      const progress = () => setMsg(done < jobs.length - failed
        ? `Épocas listas: ${done}/${jobs.length - failed} (la primera vez tarda; después va desde caché)` : "");
      if (!keepExisting) progress();

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

      // fase 1: baja resolucion (carga rapida para respuesta inmediata)
      const newByIdx = new Map();
      let newFailed = 0;
      await mapLimit(jobs, 4, async (j, i) => {
        try {
          const f = await fetchFrame(frameUrl(j.kind, { ...j.extra, res: "low" }));
          newByIdx.set(i, f);
          if (!keepExisting) {
            applyFrame(i, f);
          }
        } catch (e) {
          if (!keepExisting) applyFrame(i, { error: e }); else newFailed++;
        }
      });
      if (token !== loadToken) return;

      if (keepExisting) {
        const oldFrames = frames;
        byIdx.clear();
        newByIdx.forEach((v, k) => byIdx.set(k, v));
        failed = newFailed;
        rebuild();
        panOffset = { x: 0, y: 0 };
        baseFov = state.fov;
        applyVisualTransform();
        if (frames.length) show(Math.min(idx, frames.length - 1));
        oldFrames.forEach((f) => { if (f.url) URL.revokeObjectURL(f.url); });
        if (viewerLoader) viewerLoader.done("");
      } else {
        if (viewerLoader) viewerLoader.step(82, "Fotogramas listos · Maximizando resolución…");
      }

      const validJobs = [...byIdx.entries()].sort((a, b) => a[0] - b[0]).map(([k, f]) => ({ idx0: k, f, job: jobs[k] }));
      if (!validJobs.length) {
        if (viewerLoader) viewerLoader.fail("Sin datos válidos en esta banda para esta posición.");
        return;
      }

      // escala de grises comun para todas las epocas (se aplica al pedir la alta resolucion)
      let lim = {};
      if (state.common && validJobs.length > 1) {
        lim = { vmin: Math.min(...validJobs.map((x) => x.f.meta.vmin)), vmax: Math.max(...validJobs.map((x) => x.f.meta.vmax)) };
      }

      // fase 2: alta resolucion con concurrencia controlada (2 hilos) para no saturar CPU
      mapLimit(validJobs, 2, async (x) => {
        if (token !== loadToken) return;
        try {
          replaceFrame(x.idx0, await fetchFrame(frameUrl(x.job.kind, { ...x.job.extra, ...lim })));
        } catch { /* se conserva la baja resolucion */ }
      }).then(() => {
        // Solo tras completar la alta resolucion y si el usuario sigue en la misma vista,
        // se programa el precalentamiento especulativo dosificado
        if (token === loadToken) {
          if (!keepExisting && viewerLoader) {
            viewerLoader.done(failed ? `${failed} época(s) sin datos válidos.` : "");
          }
          schedulePrefetch(state.ra, state.dec, state.fov, det);
        }
      });
    } catch (e) {
      if (token === loadToken) {
        if (viewerLoader) viewerLoader.fail(e.message); else setMsg(e.message, true);
      }
    }
  }

  let prefetchTimer = null;
  let prefetchAbort = null;

  function cancelPrefetch() {
    if (prefetchTimer) {
      clearTimeout(prefetchTimer);
      prefetchTimer = null;
    }
    if (prefetchAbort) {
      prefetchAbort.abort();
      prefetchAbort = null;
    }
  }

  function schedulePrefetch(ra, dec, fov, currentDet) {
    cancelPrefetch();
    prefetchTimer = setTimeout(async () => {
      if (state.ra !== ra || state.dec !== dec || state.fov !== fov) return;
      prefetchAbort = new AbortController();
      const signal = prefetchAbort.signal;

      try {
        // 1. Precalentar siguiente nivel de zoom hacia adentro (el paso mas habitual)
        const fovIn = Math.max(60, fov - (fov <= 120 ? 30 : 60));
        if (fovIn !== fov && !signal.aborted) {
          await fetch(`${API}/cutout/prewarm?ra=${ra}&dec=${dec}&fov=${fovIn}&band=${currentDet}`, { signal });
        }
        if (signal.aborted) return;

        // 2. Precalentar banda contigua mas cercana (ej: D1 -> D2; D2 -> D3; D6 -> D5)
        const detNum = parseInt(currentDet.replace(/\D/g, ""), 10) || 1;
        const nextDet = detNum < 6 ? `D${detNum + 1}` : `D${detNum - 1}`;
        if (!signal.aborted) {
          await fetch(`${API}/cutout/prewarm?ra=${ra}&dec=${dec}&fov=${fov}&band=${nextDet}`, { signal });
        }
      } catch {
        // Cancelacion intencionada o fallo silencioso
      }
    }, 2500);
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
    if (!grid.size || state.ra == null || !ev) return null;
    const r = $("stage").getBoundingClientRect();
    const zoomRatio = (baseFov && state.fov) ? (baseFov / state.fov) : 1;
    const cx = ev.clientX != null ? ev.clientX : (ev.touches && ev.touches[0] ? ev.touches[0].clientX : (ev.changedTouches && ev.changedTouches[0] ? ev.changedTouches[0].clientX : 0));
    const cy = ev.clientY != null ? ev.clientY : (ev.touches && ev.touches[0] ? ev.touches[0].clientY : (ev.changedTouches && ev.changedTouches[0] ? ev.changedTouches[0].clientY : 0));
    const fx = ((cx - r.left - panOffset.x) / r.width - 0.5) / zoomRatio;
    const fy = ((cy - r.top - panOffset.y) / r.height - 0.5) / zoomRatio;
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
    const zoomRatio = (baseFov && state.fov) ? (baseFov / state.fov) : 1;
    return { x: 0.5 - (xi / span) * zoomRatio, y: 0.5 - (eta / span) * zoomRatio };
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
      g.beginPath(); g.arc(p.x * w + panOffset.x, p.y * h + panOffset.y, 9, 0, 2 * Math.PI); g.stroke();
    }

    // Superposición Gaia DR3 (estrellas, magnitudes y vectores de movimiento propio)
    if (state.gaia && gaiaSources.length && grid.size) {
      const zoomRatio = (baseFov && state.fov) ? (baseFov / state.fov) : 1;
      const arcsecPerPx = (grid.scale || 6.15) / zoomRatio;

      for (const s of gaiaSources) {
        const p = pixelOf(s.ra, s.dec);
        const sx = p.x * w + panOffset.x;
        const sy = p.y * h + panOffset.y;
        if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;

        const gmag = s.gmag != null ? s.gmag : 15;
        const rad = Math.max(2.5, Math.min(6, (17 - gmag) * 0.45 + 2.5));

        // Marcador estelar de Gaia (anillo cian + punto astrométrico central)
        g.strokeStyle = "#38bdf8";
        g.lineWidth = 1.2;
        g.beginPath();
        g.arc(sx, sy, rad, 0, 2 * Math.PI);
        g.stroke();
        g.fillStyle = "#38bdf8";
        g.fillRect(sx - 0.75, sy - 0.75, 1.5, 1.5);

        // Vector de movimiento propio (PM)
        if (s.pmra != null && s.pmdec != null) {
          const pmTot = s.pm_total != null ? s.pm_total : Math.hypot(s.pmra, s.pmdec);
          if (pmTot > 15) {
            // Desplazamiento sobre línea temporal de 50 años
            let vx = -(s.pmra * 50 / 1000) / arcsecPerPx; // Este hacia la izquierda (-x)
            let vy = -(s.pmdec * 50 / 1000) / arcsecPerPx; // Norte hacia arriba (-y)
            let vlen = Math.hypot(vx, vy);
            if (vlen > 65) { vx = (vx / vlen) * 65; vy = (vy / vlen) * 65; vlen = 65; }
            if (vlen < 6 && pmTot > 25) { vx = (vx / vlen) * 6; vy = (vy / vlen) * 6; vlen = 6; }

            const isFast = pmTot > 120; // Estrella rápida / candidato Planeta X / enana marrón
            g.strokeStyle = isFast ? "#f43f5e" : "rgba(56, 189, 248, 0.85)";
            g.lineWidth = isFast ? 1.8 : 1.2;
            g.beginPath();
            g.moveTo(sx, sy);
            g.lineTo(sx + vx, sy + vy);
            g.stroke();

            // Punta de flecha astrométrica
            const ang = Math.atan2(vy, vx);
            const head = isFast ? 5.5 : 4;
            g.fillStyle = isFast ? "#f43f5e" : "rgba(56, 189, 248, 0.85)";
            g.beginPath();
            g.moveTo(sx + vx, sy + vy);
            g.lineTo(sx + vx - head * Math.cos(ang - Math.PI / 6), sy + vy - head * Math.sin(ang - Math.PI / 6));
            g.lineTo(sx + vx - head * Math.cos(ang + Math.PI / 6), sy + vy - head * Math.sin(ang + Math.PI / 6));
            g.closePath();
            g.fill();
          }
        }

        // Etiqueta de magnitud G
        if (s.gmag != null && (s.gmag < 15.0 || gaiaSources.length <= 25)) {
          g.fillStyle = "rgba(56, 189, 248, 0.9)";
          g.font = "9px ui-monospace, monospace";
          g.textAlign = "left";
          g.textBaseline = "middle";
          g.fillText("G" + s.gmag.toFixed(1), sx + rad + 3, sy - 2);
        }
      }
    }

    // Superposición AllWISE (detecciones infrarrojas W1 3.4 µm, W2 4.6 µm, retículas)
    if (state.wise && wiseSources.length && grid.size) {
      for (const s of wiseSources) {
        const p = pixelOf(s.ra, s.dec);
        const sx = p.x * w + panOffset.x;
        const sy = p.y * h + panOffset.y;
        if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;

        const w1 = s.w1 != null ? s.w1 : 14;
        const boxRad = Math.max(3.5, Math.min(7.5, (16 - w1) * 0.5 + 3.5));
        const isUltraRed = s.w1_w2 != null && s.w1_w2 > 1.2; // Firma térmica de enana Y o Planeta X

        g.strokeStyle = isUltraRed ? "#ff4081" : "#fb923c";
        g.lineWidth = isUltraRed ? 1.8 : 1.3;

        // Retícula cuadrada con mira de comparación infrarroja
        g.strokeRect(sx - boxRad, sy - boxRad, boxRad * 2, boxRad * 2);
        g.beginPath();
        g.moveTo(sx - boxRad - 2, sy); g.lineTo(sx - boxRad, sy);
        g.moveTo(sx + boxRad, sy); g.lineTo(sx + boxRad + 2, sy);
        g.moveTo(sx, sy - boxRad - 2); g.lineTo(sx, sy - boxRad);
        g.moveTo(sx, sy + boxRad); g.lineTo(sx, sy + boxRad + 2);
        g.stroke();

        // Etiquetas infrarrojas W1 / W2 / W1-W2
        if (isUltraRed) {
          g.fillStyle = "#ff4081";
          g.font = "bold 9px ui-monospace, monospace";
          g.textAlign = "left";
          g.textBaseline = "middle";
          g.fillText(`W1:${s.w1.toFixed(1)} W1-W2:+${s.w1_w2.toFixed(1)}★`, sx + boxRad + 3, sy + 2);
        } else if (s.w1 != null && (s.w1 < 14.5 || wiseSources.length <= 25)) {
          g.fillStyle = "rgba(251, 146, 60, 0.9)";
          g.font = "9px ui-monospace, monospace";
          g.textAlign = "left";
          g.textBaseline = "middle";
          const label = s.w2 != null ? `W1:${s.w1.toFixed(1)} W2:${s.w2.toFixed(1)}` : `W1:${s.w1.toFixed(1)}`;
          g.fillText(label, sx + boxRad + 3, sy + 2);
        }
      }
    }
  }

  // ------------------------------------------------------------------ espectro
  async function loadSpectrum(ra, dec) {
    abortSpectrum();
    const controller = new AbortController();
    spectrumAbortController = controller;
    if (chartLoader) chartLoader.start(`Midiendo 102 canales en ${ra.toFixed(4)}°, ${dec.toFixed(4)}°`);
    drawChartLoading(`Midiendo 102 canales en ${ra.toFixed(4)}°, ${dec.toFixed(4)}°…`);
    $("diag").textContent = "";
    try {
      const res = await fetch(`${API}/spectrum?ra=${ra.toFixed(5)}&dec=${dec.toFixed(5)}`, { signal: controller.signal });
      if (!res.ok) throw new Error(await errText(res));
      const s = await res.json();
      if (controller.signal.aborted) return;
      if (chartLoader) chartLoader.done(`${s.n_exposures} exposiciones · apertura r = ${s.aperture_radius_px} px · ${ra.toFixed(5)}°, ${dec.toFixed(5)}°`);
      drawChart(s);
      const fmt = (v) => (v == null ? "sin datos" : (v * 100).toFixed(1) + " %");
      $("diag").innerHTML = `<dt>Hielo de agua (3,05 µm)</dt><dd>${fmt(s.diagnostics["h2o_ice_3.05um_depth"])}</dd>` +
        `<dt>CO₂ (4,27 µm)</dt><dd>${fmt(s.diagnostics["co2_4.27um_depth"])}</dd>`;
    } catch (e) {
      if (e.name === "AbortError" || controller.signal.aborted) {
        return; // Silencioso: usuario priorizó desplazamiento o zoom
      }
      if (chartLoader) chartLoader.fail(e.message); else $("sedInfo").textContent = e.message;
    } finally {
      if (spectrumAbortController === controller) {
        spectrumAbortController = null;
      }
    }
  }

  function drawChartLoading(msg) {
    const c = $("chart"), g = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    const W = Math.max(280, c.clientWidth), H = Math.round(W * 0.7);
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const L = 50, R = 10, T = 14, B = 34, x0 = 0.75, x1 = 5.0;
    g.clearRect(0, 0, W, H);
    const X = (v) => L + (v - x0) / (x1 - x0) * (W - L - R);
    for (let d = 1; d <= 6; d++) {
      g.fillStyle = d % 2 ? "rgba(255,255,255,.03)" : "rgba(255,255,255,0)";
      g.fillRect(X(DET[d][0]), T, X(DET[d][1]) - X(DET[d][0]), H - T - B);
      g.fillStyle = DET_COLOR[d - 1]; g.font = "10px ui-monospace, monospace"; g.textAlign = "center";
      g.fillText("D" + d, (X(DET[d][0]) + X(DET[d][1])) / 2, T + 10);
    }
    g.strokeStyle = "#252b33"; g.lineWidth = 1;
    g.strokeRect(L, T, W - L - R, H - T - B);
    g.fillStyle = "#8a94a1"; g.font = "12px system-ui, sans-serif"; g.textAlign = "center";
    g.fillText(msg || "Extrayendo fotometría de 102 canales…", (L + W - R) / 2, (T + H - B) / 2);
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

  viewerLoader = createBufferLoader($("viewerBuffer"), $("msg"));
  chartLoader = createBufferLoader($("chartBuffer"), $("sedInfo"));
  buildControls(); paintControls();
  if (state.ra == null) { Object.assign(state, { ra: TARGETS[0].ra, dec: TARGETS[0].dec, fov: TARGETS[0].fov, band: TARGETS[0].band }); }
  reload(); stats(); setInterval(stats, 20000);
})();
