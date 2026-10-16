/* Piano Sync — web studio (vanilla JS, no build step) */
(() => {
  "use strict";

  /* ── helpers ─────────────────────────────────────────────────────── */
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const fmt = (t, d = 2) => `${Number(t).toFixed(d)}s`;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const icon = (n) => `<svg class="ic"><use href="#i-${n}"/></svg>`;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const state = {
    config: null,
    sources: [],        // {id, path, name, size, uploading}
    sourceMeta: [],     // from /api/status: {index,label,name,duration,tempo_bpm,...}
    ready: false,
    wave: null,
    plan: null,
    scrub: 0,
    jobStream: null,
    hoverSnap: null,
    result: null,
    step: 1,
  };

  const color = (k) => {
    const cols = state.config?.source_colors || ["#3ecf8e"];
    return cols[((k % cols.length) + cols.length) % cols.length];
  };

  function interp(xs, ys, x) {
    if (!xs || !xs.length) return 0;
    if (x <= xs[0]) return ys[0];
    if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; xs[m] <= x ? (lo = m) : (hi = m); }
    const span = xs[hi] - xs[lo] || 1;
    return ys[lo] + (ys[hi] - ys[lo]) * ((x - xs[lo]) / span);
  }

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `${kind === "err" ? icon("alert") : kind === "ok" ? icon("check") : icon("layers")}<span>${esc(msg)}</span>`;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 4200);
    setTimeout(() => el.remove(), 4600);
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    const type = res.headers.get("content-type") || "";
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return type.includes("application/json") ? res.json() : res;
  }

  function fillSelect(el, items, value) {
    if (!el) return;
    el.innerHTML = "";
    items.forEach((it) => {
      const o = document.createElement("option");
      o.value = it.value; o.textContent = it.label;
      if (it.value === value) o.selected = true;
      el.appendChild(o);
    });
  }

  /* ── boot ────────────────────────────────────────────────────────── */
  async function boot() {
    try {
      state.config = await api("/api/config");
    } catch (err) {
      toast("설정을 불러오지 못했습니다: " + err.message, "err");
      return;
    }
    const c = state.config;
    fillSelect($("#quality"), c.quality, c.defaults.quality);
    fillSelect($("#mode"), c.mode, c.defaults.mode);
    fillSelect($("#effect"), c.effect, c.defaults.effect);
    fillSelect($("#audioMode"), c.audio_mode, c.defaults.audio_mode);
    fillSelect($("#resolution"), c.resolution, "1920x1080");
    $("#interval").value = c.defaults.interval_seconds;
    $("#crf").value = c.defaults.crf;
    $("#crfVal").textContent = c.defaults.crf;

    const chip = $("#aiChip");
    if (c.defaults.ai_configured) {
      chip.classList.add("on");
      $("#aiChipText").textContent = `Gemini · ${c.defaults.gemini_model}`;
    } else {
      chip.classList.add("off");
      $("#aiChipText").textContent = "Gemini 키 없음";
    }

    wireEvents();
    renderSources();
    resizeWave();
    window.addEventListener("resize", resizeWave);
    await restore();
  }

  async function restore() {
    try {
      const status = await api("/api/status");
      if (status.ready) {
        applyStatus(status);
        await loadWaveform();
        await loadPlan();
        setStep(1);
      } else if (status.sources) {
        applyStatus(status);
      }
    } catch (_) { /* fresh workspace */ }
  }

  /* ── sources ─────────────────────────────────────────────────────── */
  function maxSources() { return state.config?.max_sources || 5; }

  function renderSources() {
    const list = $("#sourceList");
    list.innerHTML = "";
    state.sources.forEach((src, i) => {
      const el = document.createElement("div");
      el.className = "source" + (i === 0 ? " is-ref" : "") + (src.uploading ? " is-uploading" : "");
      el.style.setProperty("--c", color(i));
      el.innerHTML = `
        <span class="source-badge" style="background:${color(i)}">${String.fromCharCode(65 + i)}</span>
        <div class="source-info">
          <span class="source-name">${esc(src.name)}</span>
          <span class="source-meta">${src.uploading ? "업로드 중…" : src.size ? formatBytes(src.size) : ""}${i === 0 ? " · 기준" : ""}</span>
        </div>
        <div class="source-tags">${i === 0 ? `<span class="tag tag-ok">기준</span>` : ""}</div>
        <button class="source-remove" type="button" title="제거" ${src.uploading ? "disabled" : ""}>${icon("x")}</button>
        <div class="source-progress"><i></i></div>`;
      el.querySelector(".source-remove").addEventListener("click", () => removeSource(src.id));
      list.appendChild(el);
    });

    const can = state.sources.length < maxSources();
    $("#addSourceBtn").disabled = !can;
    $("#analyzeBtn").disabled = !(state.sources.length >= 2 && state.sources.every((s) => s.path) && !state.sources.some((s) => s.uploading));
    renderRailSources();
    renderStartWith();
  }

  function renderRailSources() {
    const box = $("#railSources");
    box.innerHTML = "";
    $("#railCount").textContent = `${state.sources.length}/${maxSources()}`;
    if (!state.sources.length) {
      box.innerHTML = `<p class="rail-empty">아직 추가된 영상이 없습니다.</p>`;
      return;
    }
    state.sources.forEach((s, i) => {
      const el = document.createElement("div");
      el.className = "rail-src";
      el.innerHTML = `<span class="swatch" style="background:${color(i)}"></span>
        <span class="name">${String.fromCharCode(65 + i)} · ${esc(s.name)}</span>`;
      el.addEventListener("click", () => setStep(1));
      box.appendChild(el);
    });
  }

  function renderStartWith() {
    const n = Math.max(state.sources.length, 2);
    const items = [];
    for (let i = 0; i < n; i++) items.push({ value: String.fromCharCode(65 + i), label: String.fromCharCode(65 + i) });
    fillSelect($("#startWith"), items, $("#startWith").value || "A");
  }

  function removeSource(id) {
    state.sources = state.sources.filter((s) => s.id !== id);
    renderSources();
  }

  function formatBytes(n) {
    if (!n) return "";
    const mb = n / 1024 / 1024;
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
  }

  async function addFiles(files) {
    for (const file of files) {
      if (state.sources.length >= maxSources()) { toast(`최대 ${maxSources()}개까지 추가할 수 있습니다.`, "err"); break; }
      await uploadOne(file);
    }
  }

  async function uploadOne(file) {
    const src = { id: Math.random().toString(36).slice(2), name: file.name, size: file.size, path: null, uploading: true };
    state.sources.push(src);
    renderSources();
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/upload", { method: "POST", body: fd });
      src.path = res.path; src.size = res.size; src.uploading = false;
      renderSources();
    } catch (err) {
      state.sources = state.sources.filter((s) => s.id !== src.id);
      renderSources();
      toast("업로드 실패: " + err.message, "err");
    }
  }

  /* ── jobs ────────────────────────────────────────────────────────── */
  function watchJob(jobId, { onProgress, onDone, onError }) {
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    state.jobStream = es;
    es.onmessage = (ev) => {
      const job = JSON.parse(ev.data);
      if (onProgress) onProgress(job);
      if (job.finished) {
        es.close(); state.jobStream = null;
        if (job.status === "done") onDone && onDone(job);
        else if (job.status === "cancelled") onError && onError({ cancelled: true });
        else onError && onError({ message: job.error || "알 수 없는 오류" });
      }
    };
    es.onerror = () => { es.close(); state.jobStream = null; };
    return es;
  }

  function setStatus(stateName, msg, progress) {
    const el = $("#statusState");
    el.classList.remove("is-busy", "is-ok", "is-err");
    const dots = { idle: "대기", busy: "작업 중", ok: "완료", err: "오류" };
    el.classList.add(stateName === "busy" ? "is-busy" : stateName === "ok" ? "is-ok" : stateName === "err" ? "is-err" : "");
    el.innerHTML = `<i class="dot-idle"></i>${dots[stateName] || "대기"}`;
    $("#statusMsg").textContent = msg || "";
    if (progress == null) {
      $("#statusProgress").hidden = true;
    } else {
      $("#statusProgress").hidden = false;
      $("#statusBar").style.width = `${clamp(progress, 0, 1) * 100}%`;
    }
  }

  async function runAnalyze() {
    if (!(state.sources.length >= 2)) return;
    $("#runCard").hidden = false;
    setStatus("busy", "분석 준비 중…", 0);
    $("#runMsg").textContent = "분석을 준비하는 중…";
    let jobId;
    try {
      const res = await api("/api/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paths: state.sources.map((s) => s.path),
          quality: $("#quality").value,
          interval_seconds: Number($("#interval").value) || 5,
          use_ai: $("#useAi").checked,
        }),
      });
      jobId = res.job_id;
    } catch (err) {
      $("#runCard").hidden = true; setStatus("err", "분석 요청 실패");
      toast("분석 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => { setStatus("busy", job.message || "분석 중…", job.progress); $("#runMsg").textContent = job.message || "분석 중…"; },
      onDone: async (job) => {
        $("#runCard").hidden = true;
        applyStatus(job.result);
        await loadWaveform(); await loadPlan();
        setStatus("ok", "정렬 완료", null);
        toast("분석이 끝났습니다", "ok");
        setStep(1);
      },
      onError: (err) => {
        $("#runCard").hidden = true;
        if (err.cancelled) { setStatus("idle", "분석 취소됨", null); }
        else { setStatus("err", "분석 실패", null); toast("분석 실패: " + err.message, "err"); }
      },
    });
    $("#cancelBtn").onclick = () => jobId && api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  }

  /* ── status / summary ────────────────────────────────────────────── */
  function applyStatus(status) {
    state.ready = !!status.ready;
    state.sourceMeta = status.sources || state.sourceMeta;
    if (state.sourceMeta.length && state.sources.length === 0) {
      // reflect an already-analyzed workspace into the editable list
      state.sources = state.sourceMeta.map((s, i) => ({
        id: "s" + i, path: s.path || null, name: s.name, size: 0, uploading: false,
      }));
      renderSources();
    }

    if (!state.ready) { return; }

    const grid = $("#statGrid");
    const src = state.sourceMeta;
    const al = status.alignment || {};
    const plan = status.plan;
    const rows = [];
    rows.push(["소스", `${src.length}`, "개"]);
    if (src[0]) rows.push(["기준 길이", `${src[0].duration}`, "초"]);
    const tempos = src.map((s) => s.tempo_bpm).filter((v) => v);
    if (tempos.length) rows.push(["템포", `${Math.min(...tempos)}–${Math.max(...tempos)}`, "BPM"]);
    if (al.mean_confidence != null) rows.push(["평균 신뢰도", (al.mean_confidence * 100).toFixed(0), "%"]);
    if (al.low_fraction != null) rows.push(["저신뢰 비율", (al.low_fraction * 100).toFixed(1), "%"]);
    if (plan) rows.push(["전환 구간", `${plan.segments.length}`, "개"]);
    grid.innerHTML = rows.map(([k, v, s]) =>
      `<div class="stat"><span class="k">${k}</span><span class="v">${v}${s ? `<small>${s}</small>` : ""}</span></div>`).join("");

    $("#summaryCard").hidden = false;
    const note = status.ai_note;
    $("#aiNote").hidden = !note;
    $("#aiNote").textContent = note || "";
    $("#renderBtn").disabled = false;

    // status readouts
    $("#statusDur").textContent = src[0] ? fmt(src[0].duration, 1) : "—";
    $("#statusSegs").textContent = plan ? `${plan.segments.length}구간` : "—";
    $("#statusConf").textContent = al.mean_confidence != null ? `${(al.mean_confidence * 100).toFixed(0)}%` : "—";

    if (status.render) showResult(status.render);
    markSteps();
  }

  function stepDone(n) {
    switch (n) {
      case 1: return state.ready;
      case 2: return !!state.result;
      default: return false;
    }
  }

  function markSteps() {
    $$(".step").forEach((el) => {
      el.classList.toggle("is-done", stepDone(Number(el.dataset.step)));
    });
  }

  /* ── waveform ────────────────────────────────────────────────────── */
  const PAD_T = 14, PAD_B = 26, PAD_L = 46, PAD_R = 16;
  const BAND_H = 20, GAP = 10, SRC_H = 46, ALIGN_H = 92;

  function monoFont() { return getComputedStyle(document.body).getPropertyValue("--mono"); }

  function sourceCount() {
    if (state.wave && state.wave.envs && state.wave.envs.length) return state.wave.envs.length;
    return Math.max(1, state.sourceMeta.length || 2);
  }

  function waveGeom() {
    const canvas = $("#wave");
    const W = canvas.clientWidth || 720;
    const innerW = Math.max(60, W - PAD_L - PAD_R);
    const n = Math.max(1, sourceCount());
    const H = PAD_T + BAND_H + GAP + n * SRC_H + GAP + ALIGN_H + PAD_B;
    const bandTop = PAD_T, bandBot = bandTop + BAND_H;
    const lanes = [];
    let top = bandBot + GAP;
    for (let k = 0; k < n; k++) { lanes.push({ top, bot: top + SRC_H }); top += SRC_H; }
    const alignTop = top + GAP, alignBot = alignTop + ALIGN_H;
    const aMax = (state.wave && state.wave.a_duration) || 1;
    return {
      W, H, innerW, padL: PAD_L, bandTop, bandBot, lanes, alignTop, alignBot, aMax,
      xA: (t) => PAD_L + (t / aMax) * innerW,
      tA: (x) => ((x - PAD_L) / innerW) * aMax,
    };
  }

  function resizeWave() {
    const canvas = $("#wave");
    if (!canvas) return;
    const g = waveGeom();
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.style.height = g.H + "px";
    canvas.width = Math.max(320, rect.width) * dpr;
    canvas.height = g.H * dpr;
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    drawWave();
  }

  async function loadWaveform() {
    try {
      state.wave = await api("/api/waveform");
      if (state.wave && state.wave.ready) { $("#waveEmpty").hidden = true; resizeWave(); }
    } catch (_) { state.wave = null; }
    renderLegend();
    drawWave();
  }

  function renderLegend() {
    const leg = $("#legend");
    if (!(state.wave && state.wave.ready)) { leg.innerHTML = ""; return; }
    const parts = (state.wave.envs || []).map((e) =>
      `<span class="leg"><i class="swatch sq" style="background:${color(e.index)}"></i>${e.label} 파형 레인</span>`);
    parts.push(`<span class="leg"><i class="swatch sq" style="background:var(--bad)"></i>저신뢰 구간</span>`);
    parts.push(`<span class="leg"><i class="swatch" style="background:var(--fg-3)"></i>매핑 곡선 · 신뢰도</span>`);
    leg.innerHTML = parts.join("");
  }

  function drawWave() {
    const canvas = $("#wave");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const g = waveGeom();
    ctx.clearRect(0, 0, g.W, g.H);
    drawAxes(ctx, g);
    const w = state.wave;
    if (!w || !w.ready) { drawPlayhead(ctx, g); return; }

    // low-confidence shading over the reference (A) lane
    const refLane = g.lanes[0];
    if (refLane) {
      (w.low_segments || []).forEach(([t0, t1]) => {
        ctx.fillStyle = "rgba(242,114,123,.12)";
        ctx.fillRect(g.xA(t0), refLane.top, Math.max(1, g.xA(t1) - g.xA(t0)), refLane.bot - refLane.top);
      });
    }

    // one waveform lane per performance
    (w.envs || []).forEach((env, k) => {
      const lane = g.lanes[k];
      if (!lane) return;
      if (k > 0) {
        ctx.strokeStyle = "rgba(231,233,236,.07)";
        ctx.beginPath(); ctx.moveTo(g.padL, lane.top + .5); ctx.lineTo(g.padL + g.innerW, lane.top + .5); ctx.stroke();
      }
      fillLane(ctx, env, g, lane.top, lane.bot, color(env.index), 0.9);
      ctx.fillStyle = hexA(color(env.index), 0.95);
      ctx.font = "600 11px " + monoFont();
      ctx.fillText(env.label, g.padL + 4, lane.top + 13);
    });

    // onset ticks along the top of the alignment lane
    ctx.strokeStyle = "rgba(231,233,236,.28)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    (w.onsets || []).forEach((t) => { const x = g.xA(t); ctx.moveTo(x, g.alignTop); ctx.lineTo(x, g.alignTop + 5); });
    ctx.stroke();

    // mapping curves (secondary, faint)
    (w.maps || []).forEach((m) => {
      const bMax = Math.max(...m.b, 0.001);
      ctx.beginPath();
      for (let i = 0; i < m.a.length; i++) {
        const x = g.xA(m.a[i]);
        const y = g.alignBot - (m.b[i] / bMax) * (g.alignBot - g.alignTop);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.strokeStyle = hexA(color(m.index), 0.45);
      ctx.lineWidth = 1; ctx.stroke();
    });

    // confidence fill
    if (w.confidence && w.confidence.t.length) {
      const span = g.alignBot - g.alignTop;
      ctx.beginPath();
      ctx.moveTo(g.xA(w.confidence.t[0]), g.alignBot);
      for (let i = 0; i < w.confidence.t.length; i++) ctx.lineTo(g.xA(w.confidence.t[i]), g.alignBot - w.confidence.v[i] * span);
      ctx.lineTo(g.xA(w.confidence.t[w.confidence.t.length - 1]), g.alignBot);
      ctx.closePath();
      ctx.fillStyle = "rgba(231,233,236,.06)"; ctx.fill();
      ctx.beginPath();
      for (let i = 0; i < w.confidence.t.length; i++) {
        const x = g.xA(w.confidence.t[i]), y = g.alignBot - w.confidence.v[i] * span;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.strokeStyle = "rgba(231,233,236,.35)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = "rgba(231,233,236,.4)";
      ctx.font = "10px " + monoFont();
      ctx.fillText("신뢰도", g.padL + 4, g.alignTop + 11);
    }

    // segment band
    (w.segments || []).forEach((s) => {
      const x0 = g.xA(s.a_start), x1 = g.xA(s.a_end);
      ctx.fillStyle = color(s.source_index);
      ctx.globalAlpha = 0.85;
      ctx.fillRect(x0, g.bandTop, Math.max(1, x1 - x0 - 1), g.bandBot - g.bandTop);
      ctx.globalAlpha = 1;
      if (x1 - x0 > 18) {
        ctx.fillStyle = "#0a0b0d";
        ctx.font = "600 10px " + monoFont();
        ctx.fillText(s.source, x0 + 5, g.bandTop + 14);
      }
    });
    ctx.strokeStyle = "rgba(231,233,236,.12)";
    ctx.strokeRect(g.padL + .5, g.bandTop + .5, g.innerW - 1, (g.bandBot - g.bandTop) - 1);

    // onset snap guide under the cursor
    if (state.hoverSnap != null) {
      const x = g.xA(state.hoverSnap);
      ctx.strokeStyle = "rgba(227,179,65,.7)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, g.bandTop); ctx.lineTo(x, g.alignBot); ctx.stroke();
      ctx.setLineDash([]);
    }

    drawPlayhead(ctx, g);
  }

  function fillLane(ctx, env, g, top, bot, col, alpha) {
    const { t, v } = env;
    if (!t || !t.length) return;
    ctx.beginPath();
    ctx.moveTo(g.xA(t[0]), bot);
    for (let i = 0; i < t.length; i++) ctx.lineTo(g.xA(t[i]), bot - clamp(v[i], 0, 1) * (bot - top));
    ctx.lineTo(g.xA(t[t.length - 1]), bot);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, top, 0, bot);
    grad.addColorStop(0, hexA(col, alpha * 0.5));
    grad.addColorStop(1, hexA(col, 0.03));
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < t.length; i++) {
      const x = g.xA(t[i]), y = bot - clamp(v[i], 0, 1) * (bot - top);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = hexA(col, alpha); ctx.lineWidth = 1.2; ctx.stroke();
  }

  function drawAxes(ctx, g) {
    ctx.font = "10px " + monoFont();
    ctx.strokeStyle = "rgba(231,233,236,.055)";
    ctx.fillStyle = "rgba(231,233,236,.34)";
    ctx.lineWidth = 1;
    const step = niceStep(g.aMax);
    const bottom = g.alignBot;
    for (let t = 0; t <= g.aMax + 1e-6; t += step) {
      const x = g.xA(t);
      ctx.beginPath(); ctx.moveTo(x, g.bandTop); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.fillText(`${t.toFixed(0)}s`, x + 4, g.H - 9);
    }
    ctx.strokeStyle = "rgba(231,233,236,.1)";
    [g.bandTop, g.bandBot, g.alignTop, g.alignBot, ...g.lanes.map((l) => l.bot)].forEach((y) => {
      ctx.beginPath(); ctx.moveTo(g.padL, y + .5); ctx.lineTo(g.padL + g.innerW, y + .5); ctx.stroke();
    });
  }

  function drawPlayhead(ctx, g) {
    if (!state.ready) return;
    const x = g.xA(state.scrub);
    ctx.strokeStyle = "rgba(231,233,236,.85)";
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(x, g.bandTop); ctx.lineTo(x, g.alignBot); ctx.stroke();
    ctx.fillStyle = "#e7e9ec";
    ctx.beginPath(); ctx.moveTo(x, g.bandTop); ctx.lineTo(x + 5, g.bandTop - 7); ctx.lineTo(x - 5, g.bandTop - 7); ctx.closePath(); ctx.fill();
  }

  function niceStep(max) {
    const target = max / 8;
    const pow = Math.pow(10, Math.floor(Math.log10(Math.max(target, 0.001))));
    const n = target / pow;
    const mult = n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10;
    return Math.max(0.5, mult * pow);
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function waveTimeFromEvent(ev) {
    const rect = $("#wave").getBoundingClientRect();
    return clamp(waveGeom().tA(ev.clientX - rect.left), 0, waveGeom().aMax);
  }

  // Snap a click (pixel position) to the nearest musical onset. Returns the
  // onset time, or null when nothing is close enough.
  const SNAP_PX = 9;
  function nearestOnset(clientX) {
    if (!(state.wave && state.wave.ready)) return null;
    const g = waveGeom();
    const x = clientX - $("#wave").getBoundingClientRect().left;
    let bestT = null, bestD = SNAP_PX;
    (state.wave.onsets || []).forEach((t) => {
      const d = Math.abs(g.xA(t) - x);
      if (d < bestD) { bestD = d; bestT = t; }
    });
    return bestT;
  }

  function wireWave() {
    const canvas = $("#wave");
    let dragging = false, dragMoved = false, dragStartX = 0;

    const onDrag = (ev) => {
      if (!dragging) return;
      if (Math.abs(ev.clientX - dragStartX) > 3) dragMoved = true;
      if (dragMoved) seekTo(waveTimeFromEvent(ev));
    };
    const endDrag = (ev) => {
      if (!dragging) return;
      dragging = false;
      window.removeEventListener("mousemove", onDrag);
      window.removeEventListener("mouseup", endDrag);
      if (!dragMoved) {
        const snapped = nearestOnset(ev.clientX);
        seekTo(snapped == null ? waveTimeFromEvent(ev) : snapped);
      }
    };

    canvas.addEventListener("mousedown", (ev) => {
      if (!(state.wave && state.wave.ready)) return;
      if (ev.button !== 0) return;
      dragging = true; dragMoved = false; dragStartX = ev.clientX;
      if (state.hoverSnap != null) { state.hoverSnap = null; drawWave(); }
      window.addEventListener("mousemove", onDrag);
      window.addEventListener("mouseup", endDrag);
      ev.preventDefault();
    });
    canvas.addEventListener("mousemove", (ev) => {
      if (!(state.wave && state.wave.ready) || dragging) return;
      const snapped = nearestOnset(ev.clientX);
      if (state.hoverSnap !== snapped) { state.hoverSnap = snapped; drawWave(); }
      const t = snapped == null ? waveTimeFromEvent(ev) : snapped;
      $("#timeReadout").textContent = fmt(t) + (snapped == null ? "" : " ⌁");
    });
    canvas.addEventListener("mouseleave", () => {
      if (state.hoverSnap != null) { state.hoverSnap = null; drawWave(); }
    });
  }

  /* ── plan ────────────────────────────────────────────────────────── */
  function collectTransitionSettings() {
    return {
      mode: $("#mode").value,
      effect: $("#effect").value,
      audio_mode: $("#audioMode").value,
      start_with: $("#startWith").value,
      snap_to_onset: $("#snap").checked,
      interval_seconds: Number($("#interval").value) || 5,
    };
  }

  async function loadPlan() {
    try {
      const res = await api("/api/transitions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectTransitionSettings()),
      });
      await refreshPlan(res);
    } catch (err) { toast("전환 생성 실패: " + err.message, "err"); }
  }

  async function refreshPlan(res) {
    if (res && res.plan) renderPlan(res.plan);
    await loadWaveform();
  }

  function renderPlan(plan) {
    state.plan = plan;
    $("#segCount").textContent = plan.segments.length;

    const list = $("#segList");
    list.innerHTML = "";
    plan.segments.forEach((s, i) => {
      const conf = Math.round(s.confidence * 100);
      const c = s.confidence < 0.4 ? "var(--bad)" : s.confidence < 0.65 ? "var(--warn)" : "var(--ok)";
      const el = document.createElement("div");
      el.className = "seg";
      el.innerHTML = `
        <span class="idx" style="background:${color(s.source_index)}">${s.source}</span>
        <div><div class="meta"><b>${fmt(s.a_start)} – ${fmt(s.a_end)}</b> · in ${fmt(s.src_start ?? s.b_start)}</div>
        <div class="reason">${esc(s.reason)}</div></div>
        <div class="confbar" title="신뢰도 ${conf}%"><i style="width:${conf}%;background:${c}"></i></div>`;
      el.addEventListener("click", () => seekTo(s.a_start));
      list.appendChild(el);
    });

    const warns = $("#warnList");
    warns.innerHTML = "";
    (plan.warnings || []).slice(0, 6).forEach((wd) => {
      warns.insertAdjacentHTML("beforeend", `<div class="warn">${icon("alert")}<span>${esc(wd)}</span></div>`);
    });

    // refresh reference readout
    $("#statusSegs").textContent = `${plan.segments.length}구간`;
    drawWave();
    markSteps();
  }

  /* ── preview ─────────────────────────────────────────────────────── */
  function sourceTime(k, refTime) {
    if (!state.wave) return refTime;
    if (k === 0) return refTime;
    const map = (state.wave.maps || []).find((m) => m.index === k);
    return map ? interp(map.a, map.b, refTime) : refTime;
  }

  function seekTo(t) {
    state.scrub = clamp(t, 0, state.wave ? state.wave.a_duration : t);
    drawWave();
    const idx = state.wave
      ? (state.wave.segments || []).findIndex((s) => state.scrub >= s.a_start && state.scrub < s.a_end)
      : -1;
    $$("#segList .seg").forEach((el, i) => el.classList.toggle("is-current", i === idx));
  }


  /* ── render ──────────────────────────────────────────────────────── */
  function setRenderProgress(msg, progress) {
    if (msg != null) $("#renderMsg").textContent = msg;
    const pct = progress == null ? 0 : Math.round(clamp(progress, 0, 1) * 100);
    $("#renderProg").classList.toggle("is-indeterminate", progress == null);
    $("#renderBar").style.width = progress == null ? "40%" : `${pct}%`;
    $("#renderPct").textContent = progress == null ? "" : `${pct}%`;
  }

  async function runRender() {
    const [w, h] = ($("#resolution").value || "1920x1080").split("x").map(Number);
    $("#renderProg").hidden = false;
    setRenderProgress("렌더링을 준비하는 중…", 0);
    setStatus("busy", "렌더링 중…", 0);
    let jobId;
    try {
      try {
        await api("/api/transitions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectTransitionSettings()),
        });
      } catch (_) {}
      const res = await api("/api/render", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: $("#filename").value,
          width: w, height: h,
          fps: Number($("#fps").value) || 30,
          crf: Number($("#crf").value),
          effect: $("#effect").value,
          audio_mode: $("#audioMode").value,
          include_audio: $("#audioMode").value !== "none",
        }),
      });
      jobId = res.job_id;
    } catch (err) {
      $("#renderProg").hidden = true; setStatus("err", "렌더 요청 실패");
      toast("렌더 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => { setStatus("busy", job.message || "렌더링 중…", job.progress); setRenderProgress(job.message || "렌더링 중…", job.progress); },
      onDone: (job) => {
        $("#renderProg").hidden = true; setRenderProgress(null, 0);
        showResult(job.result);
        setStatus("ok", "영상 생성 완료", null);
        toast("영상 생성이 완료되었습니다", "ok");
      },
      onError: (err) => {
        $("#renderProg").hidden = true;
        if (err.cancelled) setStatus("idle", "렌더 취소됨", null);
        else { setStatus("err", "렌더 실패", null); toast("렌더 실패: " + err.message, "err"); }
      },
    });
  }

  function showResult(r) {
    if (!r) return;
    state.result = r;
    $("#resultEmpty").hidden = true;
    $("#resultBox").hidden = false;
    $("#renderStats").innerHTML = `
      <div class="stat"><span class="k">파일</span><span class="v" style="font-size:12px">${esc(r.name)}</span></div>
      <div class="stat"><span class="k">길이</span><span class="v">${r.duration}<small>초</small></span></div>
      <div class="stat"><span class="k">해상도</span><span class="v">${r.width}×${r.height}</span></div>
      <div class="stat"><span class="k">구간</span><span class="v">${r.segments}<small>개</small></span></div>`;
    $("#resultVideo").src = `/api/download?_=${Date.now()}`;
    markSteps();
  }

  /* ── steps ───────────────────────────────────────────────────────── */
  function setStep(n) {
    state.step = Number(n);
    $$(".step").forEach((el) => el.classList.toggle("is-active", el.dataset.step === String(n)));
    $$(".panel").forEach((el) => el.classList.toggle("is-active", el.dataset.panel === String(n)));
    if (Number(n) === 1) { resizeWave(); drawWave(); }
    markSteps();
  }

  /* ── events ──────────────────────────────────────────────────────── */
  function wireEvents() {
    $$(".step").forEach((el) => el.addEventListener("click", () => setStep(el.dataset.step)));
    $("#analyzeBtn").addEventListener("click", runAnalyze);
    $("#addSourceBtn").addEventListener("click", () => $("#hiddenFile").click());
    $("#hiddenFile").addEventListener("change", (e) => { if (e.target.files.length) addFiles(Array.from(e.target.files)); e.target.value = ""; });

    ["dragover", "dragenter"].forEach((ev) => $("#addSourceBtn").addEventListener(ev, (e) => { e.preventDefault(); $("#addSourceBtn").classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => $("#addSourceBtn").addEventListener(ev, () => $("#addSourceBtn").classList.remove("drag")));
    $("#addSourceBtn").addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) addFiles(Array.from(e.dataTransfer.files)); });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => e.preventDefault());

    wireWave();

    // Any transition setting change re-materialises the plan (debounced).
    let planTimer = null;
    const regeneratePlan = () => {
      if (!state.ready) return;
      clearTimeout(planTimer);
      planTimer = setTimeout(async () => {
        try {
          const res = await api("/api/transitions", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(collectTransitionSettings()),
          });
          await refreshPlan(res);
        } catch (err) { toast("전환 지점 재계산 실패: " + err.message, "err"); }
      }, 300);
    };
    ["mode", "effect", "audioMode", "startWith", "snap", "interval"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", regeneratePlan);
    });

    $("#renderBtn").addEventListener("click", runRender);
    $("#crf").addEventListener("input", (e) => { $("#crfVal").textContent = e.target.value; });

    // ← / → move the playhead (10ms, Shift = 100ms)
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      const tag = (ev.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      if (state.step !== 1 || !(state.wave && state.wave.ready)) return;
      ev.preventDefault();
      const delta = (ev.key === "ArrowRight" ? 1 : -1) * (ev.shiftKey ? 0.1 : 0.01);
      seekTo(state.scrub + delta);
    });

    $("#newBtn").addEventListener("click", async () => {
      await api("/api/new", { method: "POST" });
      location.reload();
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
