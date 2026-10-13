/* Piano Sync Switcher — web UI */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const fmt = (t, d = 2) => `${Number(t).toFixed(d)}s`;

  const state = {
    pathA: null,
    pathB: null,
    config: null,
    wave: null,
    ready: false,
    scrubTime: 0,
    anchors: [],
    jobStream: null,
    previewTimer: null,
  };

  const COLORS = { a: "#a78bfa", b: "#67e8f9", low: "#fb7185", warn: "#fbbf24" };

  /* ---------------- utilities ---------------- */
  function interp(xs, ys, x) {
    if (!xs || xs.length === 0) return 0;
    if (x <= xs[0]) return ys[0];
    if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] <= x) lo = mid; else hi = mid;
    }
    const span = xs[hi] - xs[lo] || 1;
    return ys[lo] + (ys[hi] - ys[lo]) * ((x - xs[lo]) / span);
  }

  function toast(message, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4600);
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.headers.get("content-type")?.includes("application/json") ? res.json() : res;
  }

  /* ---------------- config / bootstrap ---------------- */
  function fillSelect(el, items, value) {
    el.innerHTML = "";
    items.forEach((it) => {
      const opt = document.createElement("option");
      opt.value = it.value;
      opt.textContent = it.label;
      if (it.value === value) opt.selected = true;
      el.appendChild(opt);
    });
  }

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
    fillSelect($("#effect4"), c.effect, c.defaults.effect);
    fillSelect($("#audioMode4"), c.audio_mode, c.defaults.audio_mode);
    fillSelect($("#resolution"), c.resolution, "1920x1080");
    $("#interval").value = c.defaults.interval_seconds;
    $("#crf").value = c.defaults.crf;
    $("#crfVal").textContent = c.defaults.crf;

    const chip = $("#aiChip");
    if (c.defaults.ai_configured) {
      chip.className = "chip on";
      $("#aiChipText").textContent = `Gemini · ${c.defaults.gemini_model}`;
    } else {
      chip.className = "chip off";
      $("#aiChipText").textContent = "Gemini 키 없음";
    }

    wireEvents();
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
        setStep(2);
      }
    } catch (_) { /* fresh workspace */ }
  }

  /* ---------------- uploads ---------------- */
  function wireDrop(dropId, inputId, nameId, side) {
    const drop = $(dropId), input = $(inputId), nameEl = $(nameId);
    drop.addEventListener("click", () => input.click());
    ["dragover", "dragenter"].forEach((e) =>
      drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((e) =>
      drop.addEventListener(e, () => drop.classList.remove("drag")));
    drop.addEventListener("drop", (ev) => {
      ev.preventDefault();
      if (ev.dataTransfer.files[0]) handleFile(ev.dataTransfer.files[0], nameEl, side);
    });
    input.addEventListener("change", () => {
      if (input.files[0]) handleFile(input.files[0], nameEl, side);
    });
  }

  async function handleFile(file, nameEl, side) {
    const prog = side === "a" ? $("#upProgA > i") : $("#upProgB > i");
    nameEl.textContent = `${file.name} · 업로드 중…`;
    prog.style.right = "70%";
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/upload", { method: "POST", body: fd });
      if (side === "a") state.pathA = res.path; else state.pathB = res.path;
      nameEl.textContent = file.name;
      prog.style.right = "0%";
      $("#analyzeBtn").disabled = !(state.pathA && state.pathB);
    } catch (err) {
      nameEl.textContent = "";
      prog.style.right = "100%";
      toast("업로드 실패: " + err.message, "err");
    }
  }

  /* ---------------- jobs ---------------- */
  function watchJob(jobId, { onProgress, onDone, onError }) {
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    state.jobStream = es;
    es.onmessage = (ev) => {
      const job = JSON.parse(ev.data);
      if (onProgress) onProgress(job);
      if (job.finished) {
        es.close();
        state.jobStream = null;
        if (job.status === "done") onDone && onDone(job);
        else if (job.status === "cancelled") onError && onError({ cancelled: true });
        else onError && onError({ message: job.error || "알 수 없는 오류" });
      }
    };
    es.onerror = () => { es.close(); state.jobStream = null; };
    return es;
  }

  async function runAnalyze() {
    const card = $("#runCard");
    card.hidden = false;
    $("#cancelBtn").hidden = false;
    setBar("#runBar", 0);
    $("#runMsg").textContent = "분석을 시작하는 중…";
    let jobId;
    try {
      const res = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path_a: state.pathA,
          path_b: state.pathB,
          quality: $("#quality").value,
          interval_seconds: Number($("#interval").value) || 5,
          use_ai: $("#useAi").checked,
        }),
      });
      jobId = res.job_id;
    } catch (err) {
      card.hidden = true;
      toast("분석 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => {
        setBar("#runBar", job.progress);
        $("#runMsg").textContent = job.message || "처리 중…";
      },
      onDone: async (job) => {
        card.hidden = true;
        applyStatus(job.result);
        await loadWaveform();
        await loadPlan();
        toast("정렬 완료", "ok");
        setStep(2);
      },
      onError: (err) => {
        card.hidden = true;
        if (!err.cancelled) toast("분석 실패: " + err.message, "err");
      },
    });
    $("#cancelBtn").onclick = () => jobId && api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  }

  function setBar(sel, fraction) {
    $(sel).style.right = `${100 - Math.max(0, Math.min(1, fraction)) * 100}%`;
  }

  /* ---------------- status / summary ---------------- */
  function applyStatus(status) {
    state.ready = true;
    const grid = $("#statGrid");
    grid.innerHTML = "";
    const a = status.a, b = status.b, al = status.alignment;
    const stat = (k, v, sub = "") =>
      grid.insertAdjacentHTML("beforeend",
        `<div class="stat"><div class="k">${k}</div><div class="v">${v}${sub ? ` <small>${sub}</small>` : ""}</div></div>`);
    if (a) stat("영상 A", a.name, `${a.duration}s`);
    if (b) stat("영상 B", b.name, `${b.duration}s`);
    if (a) stat("템포 A / B", `${a.tempo_bpm}`, `/ ${b.tempo_bpm} BPM`);
    if (al) stat("평균 신뢰도", al.mean_confidence, `${(al.low_fraction * 100).toFixed(1)}% 저신뢰`);
    if (status.plan) stat("전환 구간", `${status.plan.segments.length}`, "개");
    $("#summaryCard").hidden = false;
    $("#summaryBadge").textContent = "완료";
    $("#aiNote").textContent = status.ai_note || "";
    $("#renderBtn").disabled = false;
    if (status.render) showResult(status.render);
  }

  /* ---------------- waveform ---------------- */
  function resizeWave() {
    const canvas = $("#wave");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(300, rect.width) * dpr;
    canvas.height = 320 * dpr;
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    drawWave();
  }

  async function loadWaveform() {
    try {
      state.wave = await api("/api/waveform");
      if (state.wave && state.wave.ready) {
        state.anchors = state.wave.anchors.map((p) => [...p]);
        $("#waveEmpty").hidden = true;
      }
    } catch (_) { state.wave = null; }
    drawWave();
  }

  function waveGeom() {
    const canvas = $("#wave");
    const W = canvas.clientWidth, H = 320;
    const padX = 18, padTop = 16, padBottom = 26;
    const innerW = W - padX * 2;
    const innerH = H - padTop - padBottom;
    const confH = 10, gap = 10;
    const laneH = (innerH - confH - gap * 2) / 2;
    const aTop = padTop, aBot = padTop + laneH;
    const confTop = aBot + gap, confBot = confTop + confH;
    const bTop = confBot + gap, bBot = bTop + laneH;
    const aMax = (state.wave && state.wave.a_duration) || 1;
    const bMax = (state.wave && state.wave.b_duration) || 1;
    return {
      W, H, padX, innerW, aTop, aBot, confTop, confBot, bTop, bBot, aMax, bMax,
      xA: (t) => padX + (t / aMax) * innerW,
      xB: (t) => padX + (t / bMax) * innerW,
      tA: (x) => ((x - padX) / innerW) * aMax,
    };
  }

  function laneFill(ctx, xs, vs, geom, xFn, top, bot, color, alpha) {
    if (!xs.length) return;
    const grad = ctx.createLinearGradient(0, top, 0, bot);
    grad.addColorStop(0, hexA(color, alpha));
    grad.addColorStop(1, hexA(color, 0.04));
    ctx.beginPath();
    ctx.moveTo(geom.padX, bot);
    for (let i = 0; i < xs.length; i++) {
      const x = xFn(xs[i]);
      const y = bot - Math.min(1, vs[i]) * (bot - top) * 0.94;
      ctx.lineTo(x, y);
    }
    ctx.lineTo(geom.padX + geom.innerW, bot);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < xs.length; i++) {
      const x = xFn(xs[i]);
      const y = bot - Math.min(1, vs[i]) * (bot - top) * 0.94;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.stroke();
  }

  function hexA(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  function drawWave() {
    const canvas = $("#wave");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const g = waveGeom();
    ctx.clearRect(0, 0, g.W, g.H);
    drawGrid(ctx, g);
    if (!state.wave || !state.wave.ready) return;
    const w = state.wave;

    // segment tints + transition lines
    w.segments.forEach((s) => {
      const x0 = g.xA(s.a_start), x1 = g.xA(s.a_end);
      const col = s.source === "A" ? COLORS.a : COLORS.b;
      ctx.fillStyle = hexA(col, 0.06);
      ctx.fillRect(x0, g.aTop, x1 - x0, g.bBot - g.aTop);
      ctx.fillStyle = col;
      ctx.fillRect(x0, g.aTop, 2, g.bBot - g.aTop);
      ctx.font = "600 11px Inter, sans-serif";
      ctx.fillStyle = hexA(col, 0.85);
      ctx.fillText(s.source, x0 + 7, g.aTop + 13);
    });

    // low-confidence shading
    w.low_segments.forEach(([t0, t1]) => {
      const x0 = g.xA(t0), x1 = g.xA(t1);
      ctx.fillStyle = hexA(COLORS.low, 0.16);
      ctx.fillRect(x0, g.aTop, Math.max(1.5, x1 - x0), g.confBot - g.aTop);
    });

    // onsets (ticks on A lane top)
    ctx.strokeStyle = hexA("#ffffff", 0.28);
    ctx.lineWidth = 1;
    ctx.beginPath();
    w.onsets.forEach((t) => { const x = g.xA(t); ctx.moveTo(x, g.aTop); ctx.lineTo(x, g.aTop + 7); });
    ctx.stroke();

    // envelopes
    laneFill(ctx, w.env_a.t, w.env_a.v, g, g.xA, g.aTop, g.aBot, COLORS.a, 0.55);
    laneFill(ctx, w.env_b.t, w.env_b.v, g, g.xB, g.bTop, g.bBot, COLORS.b, 0.5);

    // confidence curve
    const grad = ctx.createLinearGradient(g.padX, 0, g.padX + g.innerW, 0);
    grad.addColorStop(0, COLORS.a); grad.addColorStop(1, COLORS.b);
    ctx.beginPath();
    for (let i = 0; i < w.confidence.t.length; i++) {
      const x = g.xA(w.confidence.t[i]);
      const y = g.confBot - w.confidence.v[i] * (g.confBot - g.confTop);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = grad; ctx.lineWidth = 1.6; ctx.stroke();
    ctx.fillStyle = hexA("#ffffff", 0.35);
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.fillText("신뢰도", g.padX + 2, g.confTop - 2);

    // mapping curve A -> B
    ctx.beginPath();
    const mc = w.map;
    for (let i = 0; i < mc.a.length; i++) {
      const xA = g.xA(mc.a[i]);
      const xB = g.xB(mc.b[i]);
      const midY = (g.aTop + g.bBot) / 2;
      const y = midY + (xB - xA) * 0.12;
      i ? ctx.lineTo(xA, y) : ctx.moveTo(xA, y);
    }
    const mg = ctx.createLinearGradient(0, 0, g.W, 0);
    mg.addColorStop(0, hexA(COLORS.a, 0.5)); mg.addColorStop(1, hexA(COLORS.b, 0.5));
    ctx.strokeStyle = mg; ctx.lineWidth = 1.2; ctx.stroke();

    // anchors
    w.anchors.forEach(([a, b]) => {
      const x = g.xA(a), y = (g.aTop + g.bBot) / 2;
      ctx.fillStyle = COLORS.warn;
      ctx.beginPath();
      ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 6, y);
      ctx.closePath(); ctx.fill();
    });

    // playhead
    if (state.ready) {
      const x = g.xA(state.scrubTime);
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x, g.aTop); ctx.lineTo(x, g.bBot); ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.moveTo(x, g.aTop); ctx.lineTo(x + 5, g.aTop + 6); ctx.lineTo(x - 5, g.aTop + 6); ctx.closePath(); ctx.fill();
    }
  }

  function drawGrid(ctx, g) {
    ctx.strokeStyle = hexA("#ffffff", 0.05);
    ctx.fillStyle = hexA("#ffffff", 0.35);
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.lineWidth = 1;
    const step = niceStep(g.aMax);
    for (let t = 0; t <= g.aMax; t += step) {
      const x = g.xA(t);
      ctx.beginPath(); ctx.moveTo(x, g.aTop); ctx.lineTo(x, g.bBot); ctx.stroke();
      ctx.fillText(`${t.toFixed(0)}s`, x + 3, g.H - 8);
    }
    [g.aBot, g.confBot, g.bBot].forEach((y) => {
      ctx.beginPath(); ctx.moveTo(g.padX, y); ctx.lineTo(g.padX + g.innerW, y); ctx.stroke();
    });
  }

  function niceStep(max) {
    const target = max / 8;
    const pow = Math.pow(10, Math.floor(Math.log10(target)));
    const n = target / pow;
    const mult = n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10;
    return Math.max(1, mult * pow);
  }

  function waveTimeFromEvent(ev) {
    const canvas = $("#wave");
    const rect = canvas.getBoundingClientRect();
    const g = waveGeom();
    return Math.max(0, Math.min(g.aMax, g.tA(ev.clientX - rect.left)));
  }

  function nearestAnchor(x) {
    if (!state.wave) return -1;
    const g = waveGeom();
    let best = -1, bestD = 12;
    state.wave.anchors.forEach(([a], i) => {
      const d = Math.abs(g.xA(a) - x);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  function wireWave() {
    const canvas = $("#wave");
    canvas.addEventListener("mousemove", (ev) => {
      if (!(state.wave && state.wave.ready)) return;
      const t = waveTimeFromEvent(ev);
      $("#timeReadout").textContent = fmt(t);
      if (ev.buttons === 0) { state.scrubTime = t; drawWave(); }
    });
    canvas.addEventListener("click", async (ev) => {
      if (!(state.wave && state.wave.ready)) return;
      const t = waveTimeFromEvent(ev);
      if (ev.ctrlKey || ev.metaKey) {
        await addAnchor(t);
      } else {
        state.scrubTime = t;
        syncScrub();
        drawWave();
        schedulePreview();
      }
    });
    canvas.addEventListener("contextmenu", async (ev) => {
      ev.preventDefault();
      if (!(state.wave && state.wave.ready)) return;
      const rect = canvas.getBoundingClientRect();
      const idx = nearestAnchor(ev.clientX - rect.left);
      if (idx >= 0) await removeAnchor(idx);
    });
  }

  async function addAnchor(aTime) {
    try {
      const res = await api("/api/anchors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", a: aTime }),
      });
      await refreshPlan(res);
      toast(`앵커 추가 @ ${fmt(aTime)}`, "ok");
    } catch (err) { toast("앵커 추가 실패: " + err.message, "err"); }
  }

  async function removeAnchor(index) {
    try {
      const res = await api("/api/anchors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", index }),
      });
      await refreshPlan(res);
      toast("앵커 제거", "ok");
    } catch (err) { toast("앵커 제거 실패: " + err.message, "err"); }
  }

  /* ---------------- plan ---------------- */
  async function loadPlan() {
    try {
      const res = await api("/api/transitions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectTransitionSettings()),
      });
      await refreshPlan(res);
    } catch (err) { toast("전환 생성 실패: " + err.message, "err"); }
  }

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

  async function refreshPlan(res) {
    if (res && res.plan) renderPlan(res.plan);
    await loadWaveform();
    if (state.wave && state.wave.plan === undefined) { /* noop */ }
  }

  function renderPlan(plan) {
    const list = $("#segList");
    list.innerHTML = "";
    $("#segCount").textContent = plan.segments.length;
    plan.segments.forEach((s) => {
      const conf = Math.round(s.confidence * 100);
      const color = s.confidence < 0.4 ? COLORS.low : s.confidence < 0.65 ? COLORS.warn : "#34d399";
      const el = document.createElement("div");
      el.className = "seg";
      el.innerHTML = `
        <div class="idx ${s.source.toLowerCase()}">${s.source}</div>
        <div><div class="meta"><b>${fmt(s.a_start)} – ${fmt(s.a_end)}</b> · B ${fmt(s.b_start)}</div>
        <div class="meta">${s.reason}</div></div>
        <div class="bar" title="신뢰도 ${conf}%"><i style="width:${conf}%;background:${color}"></i></div>`;
      el.addEventListener("click", () => seekTo(s.a_start));
      list.appendChild(el);
    });
    const warns = $("#warnList");
    warns.innerHTML = "";
    (plan.warnings || []).slice(0, 6).forEach((wd) => {
      warns.insertAdjacentHTML("beforeend", `<div class="warn">${wd}</div>`);
    });
    const jump = $("#segJump");
    jump.innerHTML = "";
    plan.segments.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = `#${i + 1} · ${s.source} · ${fmt(s.a_start)}`;
      jump.appendChild(opt);
    });
    drawWave();
  }

  /* ---------------- preview ---------------- */
  function syncScrub() {
    const max = state.wave ? state.wave.a_duration : 1;
    $("#scrub").value = Math.round((state.scrubTime / max) * 1000);
    $("#scrubTime").textContent = fmt(state.scrubTime);
  }

  function schedulePreview() {
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(refreshPreview, 120);
  }

  function seekTo(t) {
    state.scrubTime = Math.max(0, t);
    syncScrub();
    drawWave();
    schedulePreview();
    const sel = $("#segJump");
    if (state.wave) {
      const idx = state.wave.segments.findIndex((s) => t >= s.a_start && t < s.a_end);
      if (idx >= 0) sel.value = String(idx);
    }
  }

  async function refreshPreview() {
    if (!(state.wave && state.wave.ready)) return;
    const aTime = state.scrubTime;
    const bTime = interp(state.wave.map.a, state.wave.map.b, aTime);
    $("#prevTimeA").textContent = fmt(aTime);
    $("#prevTimeB").textContent = fmt(bTime);
    const stamp = Date.now();
    $("#prevA").src = `/api/frame?side=a&t=${aTime.toFixed(3)}&w=520&_=${stamp}`;
    $("#prevB").src = `/api/frame?side=b&t=${bTime.toFixed(3)}&w=520&_=${stamp}`;
  }

  /* ---------------- render ---------------- */
  async function runRender() {
    const [w, h] = ($("#resolution").value || "1920x1080").split("x").map(Number);
    $("#renderProg").hidden = false;
    setBar("#renderBar", 0);
    let jobId;
    try {
      const res = await api("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: $("#filename").value,
          width: w, height: h,
          fps: Number($("#fps").value) || 30,
          crf: Number($("#crf").value),
          effect: $("#effect4").value,
          audio_mode: $("#audioMode4").value,
          include_audio: $("#audioMode4").value !== "none",
        }),
      });
      jobId = res.job_id;
    } catch (err) {
      $("#renderProg").hidden = true;
      toast("렌더 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => setBar("#renderBar", job.progress),
      onDone: (job) => {
        $("#renderProg").hidden = true;
        showResult(job.result);
        toast("영상 생성 완료", "ok");
      },
      onError: (err) => {
        $("#renderProg").hidden = true;
        if (!err.cancelled) toast("렌더 실패: " + err.message, "err");
      },
    });
  }

  function showResult(r) {
    if (!r) return;
    $("#resultEmpty").hidden = true;
    $("#resultBox").hidden = false;
    $("#renderStats").innerHTML = `
      <div class="stat"><div class="k">파일</div><div class="v" style="font-size:13px">${r.name}</div></div>
      <div class="stat"><div class="k">길이</div><div class="v">${r.duration}<small>s</small></div></div>
      <div class="stat"><div class="k">해상도</div><div class="v">${r.width}×${r.height}</div></div>
      <div class="stat"><div class="k">구간</div><div class="v">${r.segments}<small>개</small></div></div>`;
    $("#resultVideo").src = `/api/download?_=${Date.now()}`;
  }

  /* ---------------- steps ---------------- */
  function setStep(n) {
    $$(".step").forEach((el) => el.classList.toggle("is-active", el.dataset.step === String(n)));
    $$(".panel").forEach((el) => el.classList.toggle("is-active", el.dataset.panel === String(n)));
    if (n === "3" || n === 3) schedulePreview();
    if (n === "2" || n === 2) resizeWave();
  }

  /* ---------------- events ---------------- */
  function wireEvents() {
    wireDrop("#dropA", "#fileA", "#fileAName", "a");
    wireDrop("#dropB", "#fileB", "#fileBName", "b");
    wireWave();
    $$(".step").forEach((el) => el.addEventListener("click", () => setStep(el.dataset.step)));
    $("#analyzeBtn").addEventListener("click", runAnalyze);
    $("#applyBtn").addEventListener("click", async () => {
      try {
        const res = await api("/api/transitions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectTransitionSettings()),
        });
        await refreshPlan(res);
        toast("전환 지점을 갱신했습니다", "ok");
      } catch (err) { toast("갱신 실패: " + err.message, "err"); }
    });
    $("#renderBtn").addEventListener("click", runRender);
    $("#crf").addEventListener("input", (e) => { $("#crfVal").textContent = e.target.value; });
    $("#effect4").addEventListener("change", (e) => { $("#effect").value = e.target.value; });
    $("#audioMode4").addEventListener("change", (e) => { $("#audioMode").value = e.target.value; });
    $("#scrub").addEventListener("input", (e) => {
      const max = state.wave ? state.wave.a_duration : 1;
      state.scrubTime = (e.target.value / 1000) * max;
      $("#scrubTime").textContent = fmt(state.scrubTime);
      drawWave();
      schedulePreview();
    });
    $("#prevSeg").addEventListener("click", () => step_(-1));
    $("#nextSeg").addEventListener("click", () => step_(1));
    $("#segJump").addEventListener("change", (e) => {
      const s = state.wave?.segments[Number(e.target.value)];
      if (s) seekTo(s.a_start);
    });
    $("#downloadLink").addEventListener("click", () => setTimeout(() => {}, 0));
    $("#newBtn").addEventListener("click", async () => {
      await api("/api/new", { method: "POST" });
      location.reload();
    });
  }

  function step_(dir) {
    if (!state.wave) return;
    const segs = state.wave.segments;
    let idx = segs.findIndex((s) => state.scrubTime < s.a_start);
    idx = idx < 0 ? segs.length - 1 : Math.max(0, idx - 1);
    const target = segs[Math.max(0, Math.min(segs.length - 1, idx + dir))];
    if (target) seekTo(target.a_start);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
