/* SPHEREx Blink — foro de hallazgos. Vanilla JS, sin dependencias. */
(() => {
  "use strict";
  const API = "/api/v1/forum";
  const $ = (id) => document.getElementById(id);
  const PAGE = 12;
  const MODE_NAME = { blink: "Blink", diff: "Diferencia", coadd: "Coadición" };

  // ------------------------------------------------------------------ identidad local
  // El voto va asociado a un identificador aleatorio de este navegador, y el borrado
  // al token que devolvio el servidor al publicar (no hay cuentas).
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
  };
  let voter = store.get("spherex.voter", null);
  if (!voter || !/^[A-Za-z0-9_-]{8,64}$/.test(voter)) {
    const rnd = crypto.getRandomValues(new Uint8Array(18));
    voter = btoa(String.fromCharCode(...rnd)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    store.set("spherex.voter", voter);
  }
  const owned = () => store.get("spherex.owned", {});
  function headers(extra) {
    const tokens = Object.values(owned()).slice(-200).join(",");
    return { "X-Voter": voter, ...(tokens ? { "X-Owner-Tokens": tokens } : {}), ...(extra || {}) };
  }
  async function api(path, opts = {}) {
    const r = await fetch(API + path, { ...opts, headers: headers(opts.headers) });
    let body = null;
    try { body = await r.json(); } catch { /* sin cuerpo */ }
    if (!r.ok) throw new Error((body && body.detail) || `Error ${r.status}`);
    return body;
  }

  // ------------------------------------------------------------------ formato
  function timeAgo(t) {
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return "hace un momento";
    if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
    if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
    if (s < 86400 * 30) { const d = Math.floor(s / 86400); return `hace ${d} ${d === 1 ? "día" : "días"}`; }
    return new Date(t * 1000).toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" });
  }
  const fmtCoord = (ra, dec) => `${ra.toFixed(4)}°, ${dec >= 0 ? "+" : ""}${dec.toFixed(4)}°`;
  const pct = (v) => (v == null ? null : (v * 100).toFixed(1).replace(".", ",") + " %");

  function viewerUrl(p) {
    const v = p.view, q = new URLSearchParams();
    q.set("ra", v.ra.toFixed(5)); q.set("dec", v.dec.toFixed(5));
    q.set("fov", Math.round(v.fov)); q.set("band", v.band); q.set("mode", v.mode);
    q.set("stretch", v.stretch); q.set("speed", v.speed);
    if (v.invert) q.set("invert", "1");
    if (v.mode === "diff") q.set("ref", v.ref);
    if (!v.common) q.set("common", "0");
    if (!v.cross) q.set("cross", "0");
    if (p.object) { q.set("mra", p.object.ra.toFixed(5)); q.set("mdec", p.object.dec.toFixed(5)); }
    return "/?" + q.toString();
  }
  const postUrl = (p) => `${location.origin}/forum.html#p/${p.id}`;

  // ------------------------------------------------------------------ graficas (perezosas)
  const specCache = new Map();
  const drawn = new WeakMap();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting) { io.unobserve(en.target); loadSpectrum(en.target); } });
  }, { rootMargin: "300px" });
  const ro = new ResizeObserver((entries) => entries.forEach((en) => {
    const s = drawn.get(en.target);
    if (s) window.SpherexChart.draw(en.target, s);
  }));
  async function loadSpectrum(canvas) {
    const url = canvas.dataset.src;
    const cap = canvas.parentElement.querySelector("figcaption");
    try {
      if (!specCache.has(url)) specCache.set(url, fetch(url).then((r) => { if (!r.ok) throw new Error(); return r.json(); }));
      const s = await specCache.get(url);
      drawn.set(canvas, s);
      window.SpherexChart.draw(canvas, s);
      ro.observe(canvas);
      canvas.classList.remove("loading");
    } catch {
      specCache.delete(url);
      canvas.classList.remove("loading");
      cap.textContent = "No se pudo cargar el espectro";
    }
  }

  // ------------------------------------------------------------------ tarjeta
  const pending = new Map();   // id -> temporizador de sondeo de las que se estan generando

  function card(p, full) {
    const el = $("postTpl").content.firstElementChild.cloneNode(true);
    el.dataset.id = p.id;
    if (full) el.classList.add("full");
    const v = p.view;

    const a = el.querySelector(".post-title a");
    a.textContent = p.title; a.href = `#p/${p.id}`;
    const target = p.target ? `${p.target} · ` : "";
    el.querySelector(".post-meta").textContent =
      `${p.author || "Anónimo"} · ${timeAgo(p.created)} · ${target}${fmtCoord(v.ra, v.dec)}`;
    el.querySelector(".post-meta").title = new Date(p.created * 1000).toLocaleString("es-ES");

    const chips = [
      v.band.startsWith("ch") ? `Canal ${v.band.slice(2)}` : v.band, MODE_NAME[v.mode] || v.mode,
      `${Math.round(v.fov)}″`, v.stretch,
    ];
    if (v.invert) chips.push("negativo");
    const ul = el.querySelector(".post-chips");
    chips.forEach((c) => { const li = document.createElement("li"); li.textContent = c; ul.appendChild(li); });
    const d = p.diagnostics || {};
    [[pct(d["h2o_ice_3.05um_depth"]), "Hielo H₂O"], [pct(d["co2_4.27um_depth"]), "CO₂"]].forEach(([val, name]) => {
      if (val == null) return;
      const li = document.createElement("li"); li.className = "diag-chip"; li.textContent = `${name} ${val}`; ul.appendChild(li);
    });

    const body = el.querySelector(".post-body");
    if (p.body) body.textContent = p.body; else body.remove();

    const vurl = viewerUrl(p);
    el.querySelector(".analyze").href = vurl;
    el.querySelector(".gif-link").href = vurl;
    el.querySelector(".permalink").href = `#p/${p.id}`;
    el.querySelector(".copy").onclick = async (ev) => {
      const b = ev.currentTarget;
      try { await navigator.clipboard.writeText(postUrl(p)); b.textContent = "Copiado"; }
      catch { prompt("Copia este enlace:", postUrl(p)); }
      setTimeout(() => (b.textContent = "Copiar enlace"), 1500);
    };
    const del = el.querySelector(".delete");
    if (p.owner) {
      del.hidden = false;
      del.onclick = async () => {
        if (!confirm("¿Borrar esta publicación? No se puede deshacer.")) return;
        try {
          await api(`/posts/${p.id}`, { method: "DELETE", headers: { "X-Owner-Token": owned()[p.id] || "" } });
          const o = owned(); delete o[p.id]; store.set("spherex.owned", o);
          if (route().id) location.hash = ""; else el.remove();
        } catch (e) { alert(e.message); }
      };
    }

    paintVotes(el, p);
    paintMedia(el, p);
    return el;
  }

  function paintMedia(el, p) {
    const media = el.querySelector(".post-media");
    const img = el.querySelector(".post-gif img"), gcap = el.querySelector(".post-gif figcaption");
    const canvas = el.querySelector(".post-spec canvas"), scap = el.querySelector(".post-spec figcaption");
    media.classList.toggle("processing", p.status === "processing");
    media.classList.toggle("failed", p.status === "failed");
    el.querySelector(".vote").classList.toggle("disabled", p.status !== "ready");
    const old = media.querySelector(".media-state");
    if (old) old.remove();
    if (p.status !== "ready") {
      const box = document.createElement("div");
      box.className = "media-state";
      if (p.status === "processing") {
        box.innerHTML = '<span class="spin" aria-hidden="true"></span><b></b><small></small>';
        box.querySelector("b").textContent = "Generando la animación y midiendo las 102 bandas…";
        box.querySelector("small").textContent = "Suele tardar unos segundos si el autor ya tenía la vista cargada.";
        poll(p.id);
      } else {
        box.innerHTML = "<b></b><small></small>";
        box.querySelector("b").textContent = "No se pudo generar esta publicación";
        box.querySelector("small").textContent = p.error || "";
      }
      media.appendChild(box);
      return;
    }
    img.src = p.gif;
    img.alt = `Animación de ${p.frames || ""} fotogramas de SPHEREx en ${p.view.band}: ${p.title}`;
    gcap.textContent = `${p.frames || "?"} ${p.view.mode === "coadd" ? "fotograma" : "épocas"} · ${p.view.speed} ms · clic para analizar`;
    if (p.spectrum) {
      canvas.dataset.src = p.spectrum;
      canvas.classList.add("loading");
      const at = p.object ? "objeto marcado " + fmtCoord(p.object.ra, p.object.dec) : "centro de la vista";
      scap.textContent = `102 canales · ${p.n_exposures || "?"} exposiciones · ${at}`;
      io.observe(canvas);
    } else {
      el.querySelector(".post-spec").classList.add("empty");
      scap.textContent = p.error || "Espectro no disponible";
    }
  }

  function poll(id) {
    if (pending.has(id)) return;
    let n = 0;
    const tick = async () => {
      n++;
      try {
        const p = await api(`/posts/${id}`);
        const el = document.querySelector(`.post[data-id="${id}"]`);
        if (!el) { pending.delete(id); return; }
        if (p.status !== "processing") {
          pending.delete(id);
          el.replaceWith(card(p, el.classList.contains("full")));
          return;
        }
      } catch { /* reintenta */ }
      pending.set(id, setTimeout(tick, Math.min(6000, 1500 + n * 250)));
    };
    pending.set(id, setTimeout(tick, 1500));
  }

  // ------------------------------------------------------------------ votos
  function paintVotes(el, p) {
    const up = el.querySelector(".up"), down = el.querySelector(".down"), sc = el.querySelector(".score");
    sc.textContent = p.score;
    sc.title = `${p.ups} a favor · ${p.downs} en contra`;
    up.setAttribute("aria-pressed", String(p.my_vote === 1));
    down.setAttribute("aria-pressed", String(p.my_vote === -1));
    el.querySelector(".vote").classList.toggle("voted-up", p.my_vote === 1);
    el.querySelector(".vote").classList.toggle("voted-down", p.my_vote === -1);
    const send = async (dir) => {
      if (p.status !== "ready") return;
      const value = p.my_vote === dir ? 0 : dir;           // pulsar otra vez quita el voto
      const before = { ...p };
      // se pinta al momento y se corrige con la respuesta del servidor
      p.ups += (value === 1) - (p.my_vote === 1);
      p.downs += (value === -1) - (p.my_vote === -1);
      p.score = p.ups - p.downs; p.my_vote = value;
      paintVotes(el, p);
      try {
        const r = await api(`/posts/${p.id}/vote`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }),
        });
        Object.assign(p, r); paintVotes(el, p);
      } catch (e) {
        Object.assign(p, before); paintVotes(el, p);
        flash(e.message);
      }
    };
    up.onclick = () => send(1);
    down.onclick = () => send(-1);
  }

  let flashTimer = null;
  function flash(text) {
    const m = $("feedMsg");
    m.textContent = text; m.classList.add("err");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { m.textContent = ""; m.classList.remove("err"); }, 4000);
  }

  // ------------------------------------------------------------------ lista y detalle
  let sort = ["hot", "new", "top"].includes(store.get("spherex.forumSort", "hot")) ? store.get("spherex.forumSort", "hot") : "hot";
  let offset = 0, loading = false, listToken = 0;

  function route() {
    const m = location.hash.match(/^#p\/(\d+)$/);
    return { id: m ? +m[1] : null };
  }
  function clearFeed() {
    pending.forEach((t) => clearTimeout(t)); pending.clear();
    $("feed").textContent = ""; $("feedMsg").textContent = ""; $("feedMsg").classList.remove("err");
    $("more").hidden = true;
  }
  function skeletons(n) {
    for (let i = 0; i < n; i++) {
      const s = document.createElement("div"); s.className = "post skeleton"; s.setAttribute("aria-hidden", "true");
      s.innerHTML = '<div class="vote"></div><div class="post-main"><i></i><i></i><div class="sk-media"><span></span><span></span></div></div>';
      $("feed").appendChild(s);
    }
  }

  async function loadList(reset) {
    if (loading && !reset) return;
    const token = ++listToken;
    loading = true;
    if (reset) { offset = 0; clearFeed(); skeletons(3); }
    $("more").disabled = true;
    try {
      const r = await api(`/posts?sort=${sort}&limit=${PAGE}&offset=${offset}`);
      if (token !== listToken) return;
      document.querySelectorAll(".skeleton").forEach((s) => s.remove());
      $("forumCount").textContent = `${r.total} ${r.total === 1 ? "publicación" : "publicaciones"}`;
      if (!r.posts.length && offset === 0) {
        $("feed").innerHTML = '<div class="empty-state"><b>Aún no hay hallazgos publicados.</b>' +
          '<span>Abre el visor, busca algo que se mueva entre épocas y pulsa «Publicar en el foro».</span>' +
          '<a class="publish-btn" href="/?publish=1">Publicar el primero</a></div>';
      }
      r.posts.forEach((p) => $("feed").appendChild(card(p, false)));
      offset += r.posts.length;
      $("more").hidden = !r.more;
    } catch (e) {
      if (token !== listToken) return;
      document.querySelectorAll(".skeleton").forEach((s) => s.remove());
      $("feedMsg").textContent = "No se pudo cargar el foro: " + e.message; $("feedMsg").classList.add("err");
    } finally {
      if (token === listToken) { loading = false; $("more").disabled = false; }
    }
  }

  async function loadOne(id) {
    const token = ++listToken;
    clearFeed(); skeletons(1);
    try {
      const p = await api(`/posts/${id}`);
      if (token !== listToken) return;
      $("feed").textContent = "";
      $("feed").appendChild(card(p, true));
      document.title = `${p.title} · Foro SPHEREx Blink`;
    } catch (e) {
      if (token !== listToken) return;
      $("feed").textContent = "";
      $("feedMsg").textContent = e.message; $("feedMsg").classList.add("err");
    }
  }

  function render() {
    const { id } = route();
    $("sortTabs").hidden = !!id;
    $("backLink").hidden = !id;
    [...$("sortTabs").children].forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sort === sort)));
    if (id) { loadOne(id); window.scrollTo(0, 0); }
    else { document.title = "Foro de hallazgos · SPHEREx Blink"; loadList(true); }
  }

  [...$("sortTabs").children].forEach((b) => (b.onclick = () => {
    sort = b.dataset.sort; store.set("spherex.forumSort", sort); render();
  }));
  $("backLink").onclick = (ev) => { ev.preventDefault(); history.pushState(null, "", location.pathname); render(); };
  $("more").onclick = () => loadList(false);
  window.addEventListener("hashchange", render);
  render();
})();
