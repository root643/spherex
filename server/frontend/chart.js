/* SPHEREx Blink — grafica del espectro de 102 canales.
   La comparten el visor (app.js) y el foro (forum.js) para que se vea igual. */
(() => {
  "use strict";
  const DET = { 1: [0.75, 1.10], 2: [1.10, 1.63], 3: [1.63, 2.42], 4: [2.42, 3.83], 5: [3.83, 4.42], 6: [4.42, 5.0] };
  const DET_COLOR = ["#7aa2f7", "#7fd1b9", "#c3e88d", "#ffb454", "#ff8a65", "#f07178"];

  function draw(c, s) {
    if (!c || !s) return;
    const g = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    const W = Math.max(180, c.clientWidth || 240), H = Math.round(W * 0.7);
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
    g.fillText("wavelength (µm)", (L + W - R) / 2, H - 6);
    g.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      g.fillText(Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(Math.abs(hi) < 10 ? 2 : 0), L - 6, Y(v) + 4);
      g.strokeStyle = "rgba(255,255,255,.05)"; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - R, Y(v)); g.stroke();
    }
    g.save(); g.translate(12, (T + H - B) / 2); g.rotate(-Math.PI / 2); g.textAlign = "center"; g.fillText("flux (µJy)", 0, 0); g.restore();
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
    g.strokeStyle = "#5ec8f2"; g.lineWidth = 1.5; g.beginPath();
    ch.forEach((p, i) => (i ? g.lineTo(X(p.wavelength), Y(p.flux_ujy)) : g.moveTo(X(p.wavelength), Y(p.flux_ujy))));
    g.stroke();
    ch.forEach((p) => { g.fillStyle = DET_COLOR[p.detector - 1]; g.beginPath(); g.arc(X(p.wavelength), Y(p.flux_ujy), 2.2, 0, 2 * Math.PI); g.fill(); });
    g.restore();
  }

  window.SpherexChart = { draw, DET, DET_COLOR };
})();
