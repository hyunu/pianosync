/* Piano Sync — Solo Cinema (vanilla JS, no build step) */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const STYLE_DESC = {
    cinematic: "따뜻한 대비 · 부드러운 블룸",
    noir: "흑백 대비 · 깊은 비네트",
    neon: "차가운 색번짐 · 강한 채도",
    warm: "황금빛 온도 · 필름 질감",
    clean: "절제된 보정 · 원본에 가깝게",
  };

  const state = {
    config: null,
    source: null,
    ready: false,
    status: null,
    wave: null,
    selected: new Set(),
    style: "cinematic",
    step: 1,
    jobStream: null,
    result: null,
    direction: null,
    hoverT: null,
    canvasW: 0,
    canvasH: 0,
  };

  /* ── helpers ─────────────────────────────────────────────────────── */
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

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.innerHTML = `<svg class="ic"><use href="#i-${kind === "err" ? "alert" : "check"}"/></svg><span>${esc(msg)}</span>`;
    $("#toasts").appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .2s, transform .2s";
      el.style.opacity = "0";
      el.style.transform = "translateY(4px)";
      setTimeout(() => el.remove(), 220);
    }, 4200);
  }

  function fillSelect(el, items, value) {
    if (!el) return;
    el.innerHTML = "";
    items.forEach((it) => {
      const o = document.createElement("option");
      o.value = it.value;
      o.textContent = it.label;
      if (it.value === value) o.selected = true;
      el.appendChild(o);
    });
  }

  function fmt(t, d = 2) {
    const n = Number(t) || 0;
    const m = Math.floor(n / 60);
    const s = n - m * 60;
    return m > 0 ? `${m}:${s.toFixed(d).padStart(d ? 4 + d : 2, "0")}` : `${s.toFixed(d)}s`;
  }

  function accentFor(style) {
    const item = (state.config?.solo?.styles || []).find((s) => s.value === style);
    return item?.accent || "#3ecf8e";
  }

  /* ── theme ───────────────────────────────────────────────────────── */
  function resolveTheme(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function applyTheme(pref) {
    localStorage.setItem("pianosync-theme", pref);
    const resolved = resolveTheme(pref);
    document.documentElement.dataset.theme = resolved;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", resolved === "light" ? "#f7f8fa" : "#0a0b0d");
    const btn = $("#themeBtn");
    if (btn) {
      btn.title = `테마: ${{ auto: "자동", dark: "다크", light: "라이트" }[pref] || "자동"}`;
      btn.setAttribute("aria-label", btn.title);
    }
  }

  function cycleTheme() {
    const order = ["auto", "dark", "light"];
    const current = localStorage.getItem("pianosync-theme") || "auto";
    applyTheme(order[(order.indexOf(current) + 1) % order.length]);
    if (state.wave) drawWave();
  }

  /* ── status bar ──────────────────────────────────────────────────── */
  function setStatus(stateName, msg, progress) {
    const el = $("#statusState");
    el.classList.remove("is-busy", "is-ok", "is-err");
    const labels = { idle: "대기", busy: "작업 중", ok: "완료", err: "오류" };
    el.classList.add(stateName === "busy" ? "is-busy" : stateName === "ok" ? "is-ok" : stateName === "err" ? "is-err" : "");
    el.innerHTML = `<i class="dot-idle"></i>${labels[stateName] || "대기"}`;
    $("#statusMsg").textContent = msg || "";
    if (progress == null) {
      $("#statusProgress").hidden = true;
    } else {
      $("#statusProgress").hidden = false;
      $("#statusBar").style.width = `${clamp(progress, 0, 1) * 100}%`;
    }
  }

  function updateStatusBar() {
    const s = state.status;
    $("#statusDur").textContent = s?.source ? fmt(s.source.duration, 1) : "—";
    const on = state.selected.size;
    const total = s?.plan?.moments?.length || 0;
    $("#statusMoments").textContent = state.ready ? `${on}/${total}개` : "—";
    const label = (state.config?.solo?.styles || []).find((x) => x.value === state.style)?.label;
    $("#statusStyle").textContent = state.ready ? (label || state.style) : "—";
    $("#railMomentCount").textContent = total ? `${on}/${total}` : "0";
    if (total) {
      $("#railMoments").hidden = false;
      $("#railMeterBar").style.width = `${(on / total) * 100}%`;
      $("#railMeterText").textContent = on ? `${on}개 켜짐` : "모두 꺼짐";
    }
  }

  /* ── source ──────────────────────────────────────────────────────── */
  function renderSources() {
    const list = $("#sourceList");
    list.innerHTML = "";
    const src = state.source;
    $("#railCount").textContent = src ? "1/1" : "0/1";
    $("#railSources").innerHTML = src
      ? `<div class="rail-src"><span class="swatch" style="background:${accentFor(state.style)}"></span><span class="name">${esc(src.name)}</span></div>`
      : `<p class="rail-empty">아직 추가된 영상이 없습니다.</p>`;
    if (!src) return;

    const card = document.createElement("div");
    card.className = "source is-ref";
    const uploading = !!src.uploading;
    card.innerHTML = `
      <span class="source-badge" style="background:${accentFor(state.style)}">1</span>
      <div class="source-info">
        <span class="source-name">${esc(src.name)}</span>
        <span class="source-meta">${uploading ? "업로드 중…" : [src.size ? formatBytes(src.size) : "", state.status?.source?.duration ? fmt(state.status.source.duration, 1) : ""].filter(Boolean).join(" · ")}</span>
      </div>
      <div class="source-tags">${state.ready ? '<span class="tag tag-ok">분석됨</span>' : ""}</div>
      <button class="source-remove" type="button" title="영상 제거" ${uploading ? "disabled" : ""}>
        <svg class="ic"><use href="#i-x"/></svg>
      </button>
      ${uploading ? '<div class="source-progress"><i></i></div>' : ""}`;
    card.querySelector(".source-remove")?.addEventListener("click", resetAll);
    list.appendChild(card);
  }

  function formatBytes(n) {
    if (!n) return "";
    const units = ["B", "KB", "MB", "GB"];
    let v = Number(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  async function uploadOne(file) {
    if (!file) return;
    if (!(file.type || "").startsWith("video/") && !/\.(mp4|mov|mkv|m4v|webm)$/i.test(file.name)) {
      toast("영상 파일을 선택해 주세요", "err");
      return;
    }
    await post("/api/solo/reset").catch(() => {});
    state.source = { name: file.name, size: file.size, uploading: true };
    state.ready = false;
    state.status = null;
    state.wave = null;
    state.selected = new Set();
    state.result = null;
    showDirection(null);
    updateAiAvailability();
    $("#analyzeBtn").disabled = true;
    $("#renderBtn").disabled = true;
    $("#timelineCard").hidden = true;
    $("#summaryCard").hidden = true;
    $("#nextRow").hidden = true;
    $("#resultBox").hidden = true;
    $("#resultEmpty").hidden = false;
    renderSources();
    setStatus("busy", "업로드 중…", null);

    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api("/api/upload", { method: "POST", body: fd });
      state.source = { path: res.path, name: res.name, size: res.size, uploading: false };
      renderSources();
      $("#analyzeBtn").disabled = false;
      setStatus("ok", "영상 준비 완료", null);
      toast("영상이 추가되었습니다", "ok");
    } catch (err) {
      state.source = null;
      renderSources();
      setStatus("err", "업로드 실패", null);
      toast("업로드 실패: " + err.message, "err");
    }
  }

  /* ── analysis ────────────────────────────────────────────────────── */
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
    if (!state.source?.path) return;
    $("#runCard").hidden = false;
    $("#runMsg").textContent = "분석을 준비하는 중…";
    setStatus("busy", "분석 준비 중…", 0);
    let jobId;
    try {
      const res = await post("/api/solo/analyze", {
        path: state.source.path,
        quality: $("#soloQuality").value,
        max_moments: Number($("#soloMoments").value) || 8,
      });
      jobId = res.job_id;
    } catch (err) {
      $("#runCard").hidden = true;
      setStatus("err", "분석 요청 실패", null);
      toast("분석 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => {
        setStatus("busy", job.message || "분석 중…", job.progress);
        $("#runMsg").textContent = job.message || "분석 중…";
      },
      onDone: async (job) => {
        $("#runCard").hidden = true;
        applyStatus(job.result);
        await loadWaveform();
        setStep(1);
        setStatus("ok", "임팩트 분석 완료", null);
        toast("임팩트 지점을 찾았습니다", "ok");
      },
      onError: (err) => {
        $("#runCard").hidden = true;
        if (err.cancelled) setStatus("idle", "분석 취소됨", null);
        else {
          setStatus("err", "분석 실패", null);
          toast("분석 실패: " + err.message, "err");
        }
      },
    });
    $("#cancelBtn").onclick = () => jobId && post(`/api/jobs/${jobId}/cancel`).catch(() => {});
  }

  /* ── AI director ─────────────────────────────────────────────────── */
  function updateAiAvailability() {
    const configured = !!state.config?.solo?.ai_configured;
    $("#aiDirectBtn").disabled = !configured;
    $("#aiHint").textContent = configured ? "Gemini 연결됨" : "API 키 필요";
    $("#aiDirectNote").textContent = configured
      ? "로컬 분석에서 찾은 임팩트만 골라 스타일·강도·카메라를 제안합니다. 타이밍과 오디오는 바꾸지 않습니다."
      : "설정에서 Gemini API 키를 추가하면 AI 연출 제안을 사용할 수 있습니다. 분석·렌더는 지금도 정상 동작합니다.";
  }

  function showDirection(d) {
    state.direction = d || null;
    const box = $("#aiDirectReason");
    if (!d) {
      box.hidden = true;
      box.textContent = "";
      return;
    }
    const kept = Array.isArray(d.keep_moments) ? d.keep_moments.length : null;
    const parts = [];
    if (d.reason) parts.push(d.reason);
    else parts.push("AI 연출이 적용되었습니다.");
    if (kept != null) parts.push(`임팩트 ${kept}개 선택`);
    if (d.model) parts.push(d.model);
    box.hidden = false;
    box.textContent = "AI: " + parts.join(" · ");
  }

  async function runAiDirect() {
    if (!state.ready) {
      toast("먼저 영상을 분석하세요", "err");
      return;
    }
    const btn = $("#aiDirectBtn");
    btn.disabled = true;
    setStatus("busy", "AI 연출 제안을 요청하는 중…", null);
    let jobId;
    try {
      const res = await post("/api/solo/direct", { note: $("#aiMood").value || "" });
      jobId = res.job_id;
    } catch (err) {
      btn.disabled = false;
      setStatus("err", "AI 연출 요청 실패", null);
      toast("AI 연출 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => {
        setStatus("busy", job.message || "AI 연출 제안 중…", job.progress);
      },
      onDone: async (job) => {
        btn.disabled = false;
        applyStatus(job.result);
        await loadWaveform();
        setStatus("ok", "AI 연출 적용 완료", null);
        toast("AI 연출을 적용했습니다", "ok");
      },
      onError: (err) => {
        btn.disabled = false;
        if (err.cancelled) setStatus("idle", "AI 연출 취소됨", null);
        else {
          setStatus("err", "AI 연출 실패", null);
          toast("AI 연출 실패: " + err.message, "err");
        }
      },
    });
  }

  function applyStatus(status) {
    if (!status) return;
    state.status = status;
    state.ready = !!status.ready;
    if (status.source) {
      state.source = {
        path: status.source.path,
        name: status.source.name,
        size: state.source?.size || 0,
        uploading: false,
      };
    }
    if (status.settings) applySettings(status.settings);
    showDirection(status.direction);

    if (!state.ready) {
      $("#summaryCard").hidden = true;
      $("#timelineCard").hidden = true;
      $("#nextRow").hidden = true;
      $("#renderBtn").disabled = true;
      renderSources();
      updateStatusBar();
      return;
    }

    const plan = status.plan || {};
    const moments = plan.moments || [];
    const selected = Array.isArray(status.selected) ? status.selected : moments.map((m) => m.id);
    state.selected = new Set(selected);

    const rows = [
      ["길이", fmt(status.source?.duration || plan.duration, 1)],
      ["템포", plan.tempo_bpm ? `${plan.tempo_bpm}` : "—", plan.tempo_bpm ? "BPM" : ""],
      ["임팩트", `${moments.length}`, "개"],
      ["켜짐", `${state.selected.size}`, "개"],
      ["해상도", status.source ? `${status.source.width}×${status.source.height}` : "—"],
    ];
    $("#statGrid").innerHTML = rows.map(([k, v, s]) =>
      `<div class="stat"><span class="k">${k}</span><span class="v">${esc(v)}${s ? `<small>${s}</small>` : ""}</span></div>`).join("");

    $("#summaryCard").hidden = false;
    $("#timelineCard").hidden = false;
    $("#nextRow").hidden = false;
    $("#renderBtn").disabled = false;
    $("#momentCount").textContent = `${state.selected.size}/${moments.length}`;
    const note = plan.moment_count
      ? `에너지 ${Math.round((plan.peak_energy || 0) * 100)}% 피크 · 평균 ${Math.round((plan.mean_energy || 0) * 100)}% · 프레이즈 ${plan.phrase_times?.length || 0}개 구간`
      : "";
    $("#soloNote").hidden = !note;
    $("#soloNote").textContent = note;

    renderSources();
    renderMomentList();
    updateStatusBar();
    if (status.render) showResult(status.render);
    requestAnimationFrame(() => { resizeWave(); drawWave(); });
  }

  async function loadWaveform() {
    try {
      state.wave = await api("/api/solo/waveform");
    } catch (err) {
      state.wave = null;
      toast("타임라인을 불러오지 못했습니다: " + err.message, "err");
      return;
    }
    if (state.wave?.ready) {
      state.selected = new Set(state.wave.selected || []);
      $("#momentCount").textContent = `${state.selected.size}/${(state.wave.moments || []).length}`;
    }
    requestAnimationFrame(() => { resizeWave(); drawWave(); });
    renderMomentList();
    updateStatusBar();
  }

  /* ── moments ─────────────────────────────────────────────────────── */
  function momentKind(kind) {
    return kind === "climax" ? "에너지 클라이맥스" : "타건 임팩트";
  }

  function renderMomentList() {
    const list = $("#momentList");
    const moments = state.wave?.moments || state.status?.plan?.moments || [];
    list.innerHTML = "";
    if (!moments.length) return;
    moments.forEach((m) => {
      const on = state.selected.has(m.id);
      const row = document.createElement("button");
      row.type = "button";
      row.className = "solo-moment " + (on ? "is-on" : "is-off");
      row.dataset.id = m.id;
      row.setAttribute("aria-pressed", on ? "true" : "false");
      row.style.setProperty("--accent", accentFor(state.style));
      row.innerHTML = `
        <span class="check"><svg class="ic"><use href="#i-check"/></svg></span>
        <span class="sm-time">${fmt(m.time)}</span>
        <span class="sm-body">
          <span class="sm-kind">${momentKind(m.kind)}</span>
          <span class="sm-bar"><i style="width:${Math.round(clamp(m.strength, 0, 1) * 100)}%"></i></span>
        </span>
        <span class="sm-strength">${Math.round(clamp(m.strength, 0, 1) * 100)}%</span>`;
      row.addEventListener("click", () => toggleMoment(m.id));
      list.appendChild(row);
    });
  }

  function toggleMoment(id) {
    if (state.selected.has(id)) state.selected.delete(id);
    else state.selected.add(id);
    renderMomentList();
    drawWave();
    $("#momentCount").textContent = `${state.selected.size}/${(state.wave?.moments || state.status?.plan?.moments || []).length}`;
    updateStatusBar();
    post("/api/solo/moments", { moments: [...state.selected] }).catch(() => {});
  }

  /* ── canvas ──────────────────────────────────────────────────────── */
  const PAD_T = 20, PAD_B = 24, PAD_L = 44, PAD_R = 16;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function resizeWave() {
    const canvas = $("#soloWave");
    if (!canvas || $("#timelineCard").hidden) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = window.devicePixelRatio || 1;
    state.canvasW = rect.width;
    state.canvasH = canvas.clientHeight || 260;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(state.canvasH * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawWave();
  }

  function waveGeom() {
    return {
      left: PAD_L,
      right: state.canvasW - PAD_R,
      top: PAD_T,
      bottom: state.canvasH - PAD_B,
      width: Math.max(1, state.canvasW - PAD_L - PAD_R),
      height: Math.max(1, state.canvasH - PAD_T - PAD_B),
    };
  }

  function drawWave() {
    const canvas = $("#soloWave");
    if (!canvas || !state.wave?.ready || !state.canvasW) return;
    const ctx = canvas.getContext("2d");
    const g = waveGeom();
    const duration = Math.max(0.001, state.wave.duration || 1);
    const tToX = (t) => g.left + clamp(t / duration, 0, 1) * g.width;

    ctx.clearRect(0, 0, state.canvasW, state.canvasH);

    const fg2 = cssVar("--fg-2") || "#a2a8b0";
    const fg3 = cssVar("--fg-3") || "#828a93";
    const border = cssVar("--border") || "#23272e";
    const accent = accentFor(state.style);

    // Phrase grid.
    ctx.strokeStyle = border;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    (state.wave.phrase_times || []).forEach((t) => {
      const x = Math.round(tToX(t)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, g.top);
      ctx.lineTo(x, g.bottom);
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // Energy area.
    const pts = state.wave.energy || { t: [], v: [] };
    if (pts.t?.length) {
      const grad = ctx.createLinearGradient(0, g.top, 0, g.bottom);
      grad.addColorStop(0, "rgba(162,168,176,0.36)");
      grad.addColorStop(1, "rgba(162,168,176,0.04)");
      ctx.beginPath();
      ctx.moveTo(tToX(pts.t[0]), g.bottom);
      for (let i = 0; i < pts.t.length; i += 1) {
        const v = clamp(pts.v[i], 0, 1);
        ctx.lineTo(tToX(pts.t[i]), g.bottom - v * g.height);
      }
      ctx.lineTo(tToX(pts.t[pts.t.length - 1]), g.bottom);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      for (let i = 0; i < pts.t.length; i += 1) {
        const v = clamp(pts.v[i], 0, 1);
        const x = tToX(pts.t[i]);
        const y = g.bottom - v * g.height;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = fg2;
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // Onset ticks along the bottom.
    ctx.strokeStyle = fg3;
    ctx.globalAlpha = 0.35;
    (state.wave.onsets || []).forEach((t) => {
      const x = Math.round(tToX(t)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, g.bottom);
      ctx.lineTo(x, g.bottom - Math.min(14, g.height * 0.16));
      ctx.stroke();
    });
    ctx.globalAlpha = 1;

    // Impact moments.
    const moments = state.wave.moments || [];
    const energyAt = (t) => {
      if (!pts.t?.length) return 0;
      let lo = 0, hi = pts.t.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (pts.t[mid] < t) lo = mid + 1; else hi = mid;
      }
      const i = clamp(lo, 0, pts.v.length - 1);
      return clamp(pts.v[i], 0, 1);
    };

    moments.forEach((m) => {
      const on = state.selected.has(m.id);
      const x = Math.round(tToX(m.time)) + 0.5;
      const y = g.bottom - energyAt(m.time) * g.height;
      ctx.strokeStyle = on ? accent : fg3;
      ctx.globalAlpha = on ? 0.9 : 0.45;
      ctx.lineWidth = on ? 1.6 : 1;
      ctx.beginPath();
      ctx.moveTo(x, g.top);
      ctx.lineTo(x, g.bottom);
      ctx.stroke();

      ctx.fillStyle = on ? accent : fg3;
      ctx.globalAlpha = on ? 1 : 0.55;
      ctx.beginPath();
      ctx.arc(x, y, on ? 4.2 : 3.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;

      // Index label above the marker; skip when markers crowd the timeline.
      if (moments.length <= 16 || on) {
        ctx.font = `10px ${cssVar("--mono") || "monospace"}`;
        ctx.textAlign = "center";
        ctx.fillStyle = on ? accent : fg3;
        ctx.globalAlpha = on ? 1 : 0.6;
        ctx.fillText(String(m.id + 1), x, g.top + 10);
        ctx.globalAlpha = 1;
      }
    });

    // Hover playhead.
    if (state.hoverT != null) {
      const x = Math.round(tToX(state.hoverT)) + 0.5;
      ctx.strokeStyle = cssVar("--fg") || "#e7e9ec";
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, g.top);
      ctx.lineTo(x, g.bottom);
      ctx.stroke();
      ctx.globalAlpha = 1;
      $("#timeReadout").textContent = fmt(state.hoverT);
    }
  }

  function timeFromEvent(ev) {
    const rect = $("#soloWave").getBoundingClientRect();
    const g = waveGeom();
    const x = clamp(ev.clientX - rect.left - g.left, 0, g.width);
    return (x / g.width) * (state.wave?.duration || 0);
  }

  function nearestMoment(clientX) {
    if (!state.wave?.ready) return null;
    const rect = $("#soloWave").getBoundingClientRect();
    const g = waveGeom();
    const moments = state.wave.moments || [];
    let best = null;
    let bestPx = 14;
    moments.forEach((m) => {
      const x = g.left + clamp(m.time / Math.max(0.001, state.wave.duration), 0, 1) * g.width;
      const px = Math.abs(clientX - rect.left - x);
      if (px < bestPx) { bestPx = px; best = m; }
    });
    return best;
  }

  function wireWave() {
    const canvas = $("#soloWave");
    if (!canvas) return;
    canvas.addEventListener("pointermove", (ev) => {
      if (!state.wave?.ready) return;
      state.hoverT = timeFromEvent(ev);
      drawWave();
    });
    canvas.addEventListener("pointerleave", () => {
      state.hoverT = null;
      $("#timeReadout").textContent = "0.00s";
      drawWave();
    });
    canvas.addEventListener("click", (ev) => {
      if (!state.wave?.ready) return;
      const near = nearestMoment(ev.clientX);
      if (near) toggleMoment(near.id);
      else {
        state.hoverT = timeFromEvent(ev);
        drawWave();
      }
    });
  }

  /* ── styles / settings ───────────────────────────────────────────── */
  function renderStyles() {
    const grid = $("#styleGrid");
    grid.innerHTML = "";
    (state.config?.solo?.styles || []).forEach((s) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "solo-style-card" + (s.value === state.style ? " is-active" : "");
      card.dataset.style = s.value;
      card.style.setProperty("--accent", s.accent);
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", s.value === state.style ? "true" : "false");
      card.innerHTML = `
        <span class="swatch-band"></span>
        <span class="label">${esc(s.label)}</span>
        <span class="desc">${esc(STYLE_DESC[s.value] || "")}</span>
        <span class="check"><svg class="ic"><use href="#i-check"/></svg></span>`;
      card.addEventListener("click", () => applyStyle(s.value));
      grid.appendChild(card);
    });
  }

  function applyStyle(style) {
    state.style = style;
    $$("#styleGrid .solo-style-card").forEach((card) => {
      const on = card.dataset.style === style;
      card.classList.toggle("is-active", on);
      card.setAttribute("aria-checked", on ? "true" : "false");
    });
    const item = (state.config?.solo?.styles || []).find((s) => s.value === style);
    $("#styleHint").textContent = item?.label || style;
    $("#legOn").style.background = accentFor(style);
    renderSources();
    renderMomentList();
    drawWave();
    updateStatusBar();
  }

  function applySettings(s) {
    if (!s) return;
    state.style = s.style || state.style;
    applyStyle(state.style);
    $("#intensity").value = Math.round((s.intensity ?? 0.6) * 100);
    $("#intensityVal").textContent = $("#intensity").value;
    $("#fxPunch").checked = s.punch !== false;
    $("#fxCamera").checked = s.camera !== false;
    $("#fxFlash").checked = s.flash !== false;
    $("#fxGlow").checked = s.glow !== false;
    $("#fxVignette").checked = s.vignette !== false;
    $("#fxGrain").checked = s.grain !== false;
    $("#fxLetterbox").checked = !!s.letterbox;
    $("#introText").value = s.intro_text || "";
    $("#introSubtitle").value = s.intro_subtitle || "";
    $("#introSeconds").value = s.intro_seconds ?? 2.5;
    $("#normalizeAudio").checked = s.normalize_audio !== false;
  }

  function collectSettings() {
    return {
      style: state.style,
      intensity: (Number($("#intensity").value) || 0) / 100,
      camera: $("#fxCamera").checked,
      punch: $("#fxPunch").checked,
      flash: $("#fxFlash").checked,
      glow: $("#fxGlow").checked,
      vignette: $("#fxVignette").checked,
      grain: $("#fxGrain").checked,
      letterbox: $("#fxLetterbox").checked,
      intro_text: $("#introText").value,
      intro_subtitle: $("#introSubtitle").value,
      intro_seconds: Number($("#introSeconds").value) || 0,
      normalize_audio: $("#normalizeAudio").checked,
      include_audio: true,
      moments: [...state.selected],
    };
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
    if (!state.ready) return;
    if (state.selected.size === 0 && !$("#introText").value.trim() && !$("#introSubtitle").value.trim()) {
      toast("임팩트를 하나 이상 켜거나 인트로 타이틀을 입력하세요", "err");
      return;
    }
    $("#renderProg").hidden = false;
    setRenderProgress("렌더링을 준비하는 중…", 0);
    setStatus("busy", "렌더링 중…", 0);
    let jobId;
    try {
      const res = await post("/api/solo/render", {
        ...collectSettings(),
        filename: $("#filename").value,
        size: $("#resolution").value,
        fps: Number($("#fps").value) || 30,
        crf: Number($("#crf").value),
        preset: "medium",
      });
      jobId = res.job_id;
    } catch (err) {
      $("#renderProg").hidden = true;
      setStatus("err", "렌더 요청 실패", null);
      toast("렌더 요청 실패: " + err.message, "err");
      return;
    }
    watchJob(jobId, {
      onProgress: (job) => {
        setStatus("busy", job.message || "렌더링 중…", job.progress);
        setRenderProgress(job.message || "렌더링 중…", job.progress);
      },
      onDone: (job) => {
        $("#renderProg").hidden = true;
        setRenderProgress(null, 0);
        showResult(job.result);
        setStatus("ok", "연출 영상 완성", null);
        toast("연출 영상이 완성되었습니다", "ok");
      },
      onError: (err) => {
        $("#renderProg").hidden = true;
        if (err.cancelled) setStatus("idle", "렌더 취소됨", null);
        else {
          setStatus("err", "렌더 실패", null);
          toast("렌더 실패: " + err.message, "err");
        }
      },
    });
  }

  function showResult(r) {
    if (!r) return;
    state.result = r;
    $("#resultEmpty").hidden = true;
    $("#resultBox").hidden = false;
    const label = (state.config?.solo?.styles || []).find((x) => x.value === r.style)?.label || r.style;
    const rows = [
      ["파일", r.name],
      ["길이", `${r.duration}`, "초"],
      ["해상도", `${r.width}×${r.height}`],
      ["임팩트", `${r.moments}`, "개"],
      ["스타일", label],
      ["인트로", r.intro_seconds ? `${r.intro_seconds}` : "없음", r.intro_seconds ? "초" : ""],
    ];
    $("#renderStats").innerHTML = rows.map(([k, v, s]) =>
      `<div class="stat"><span class="k">${k}</span><span class="v"${String(v).length > 12 ? ' style="font-size:12px"' : ""}>${esc(v)}${s ? `<small>${s}</small>` : ""}</span></div>`).join("");
    $("#resultVideo").src = `/api/solo/result?_=${Date.now()}`;
    $("#downloadLink").href = `/api/solo/download?_=${Date.now()}`;
    $("#resultEmpty").hidden = true;
  }

  /* ── steps / reset ───────────────────────────────────────────────── */
  function setStep(n) {
    n = Number(n);
    if (n === 2 && !state.ready) {
      toast("먼저 영상을 분석하세요", "err");
      return;
    }
    state.step = n;
    $$(".step").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.step === String(n));
      el.classList.toggle("is-done", stepDone(Number(el.dataset.step)));
    });
    $$(".panel").forEach((el) => el.classList.toggle("is-active", el.dataset.panel === String(n)));
    if (n === 1) requestAnimationFrame(() => { resizeWave(); drawWave(); });
  }

  function stepDone(n) {
    if (n === 1) return state.ready;
    if (n === 2) return !!state.result;
    return false;
  }

  async function resetAll() {
    if (state.jobStream) { state.jobStream.close(); state.jobStream = null; }
    await post("/api/solo/reset").catch(() => {});
    state.source = null;
    state.status = null;
    state.ready = false;
    state.wave = null;
    state.selected = new Set();
    state.result = null;
    showDirection(null);
    updateAiAvailability();
    $("#analyzeBtn").disabled = true;
    $("#renderBtn").disabled = true;
    $("#timelineCard").hidden = true;
    $("#summaryCard").hidden = true;
    $("#nextRow").hidden = true;
    $("#resultBox").hidden = true;
    $("#resultEmpty").hidden = false;
    $("#railMoments").hidden = true;
    $("#momentList").innerHTML = "";
    $("#resultVideo").removeAttribute("src");
    $("#resultVideo").load?.();
    renderSources();
    setStep(1);
    setStatus("idle", "", null);
    updateStatusBar();
    setRenderProgress(null, 0);
  }

  /* ── restore ─────────────────────────────────────────────────────── */
  async function restore() {
    try {
      const status = await api("/api/solo/status");
      if (status.ready) {
        applyStatus(status);
        await loadWaveform();
        $("#resultEmpty").hidden = !!state.result;
      } else {
        renderSources();
        updateStatusBar();
      }
    } catch (_) {
      renderSources();
      updateStatusBar();
    }
  }

  /* ── events ──────────────────────────────────────────────────────── */
  function wireEvents() {
    $$(".step").forEach((el) => el.addEventListener("click", () => setStep(el.dataset.step)));
    $("#analyzeBtn").addEventListener("click", runAnalyze);
    $("#aiDirectBtn").addEventListener("click", runAiDirect);
    $("#toRenderBtn").addEventListener("click", () => setStep(2));
    $("#renderBtn").addEventListener("click", runRender);
    $("#themeBtn").addEventListener("click", cycleTheme);

    $("#addSourceBtn").addEventListener("click", () => $("#hiddenFile").click());
    $("#hiddenFile").addEventListener("change", (e) => {
      if (e.target.files.length) uploadOne(e.target.files[0]);
      e.target.value = "";
    });
    const zone = $("#soloDrop");
    ["dragover", "dragenter"].forEach((ev) => zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("is-dragging");
    }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-dragging")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("is-dragging");
      if (e.dataTransfer.files.length) uploadOne(e.dataTransfer.files[0]);
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => e.preventDefault());

    $("#soloMoments").addEventListener("input", (e) => {
      $("#momentCountVal").textContent = e.target.value;
    });
    $("#intensity").addEventListener("input", (e) => {
      $("#intensityVal").textContent = e.target.value;
    });
    $("#crf").addEventListener("input", (e) => {
      $("#crfVal").textContent = e.target.value;
    });
    wireWave();
    window.addEventListener("resize", () => {
      if (state.step === 1) { resizeWave(); }
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && state.hoverT != null) {
        state.hoverT = null;
        $("#timeReadout").textContent = "0.00s";
        drawWave();
      }
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
    const solo = c.solo || {};
    const defaults = solo.defaults || {};
    state.style = defaults.style || "cinematic";

    fillSelect($("#soloQuality"), c.quality || [], c.defaults?.quality || "balanced");
    fillSelect($("#resolution"), c.resolution || [], c.defaults?.size || "original");
    $("#soloMoments").value = solo.max_moments || 8;
    $("#momentCountVal").textContent = $("#soloMoments").value;
    $("#intensity").value = Math.round((defaults.intensity ?? 0.6) * 100);
    $("#intensityVal").textContent = $("#intensity").value;
    $("#introSeconds").value = defaults.intro_seconds ?? 2.5;
    $("#crf").value = c.defaults?.crf ?? 20;
    $("#crfVal").textContent = $("#crf").value;

    renderStyles();
    applyStyle(state.style);
    updateAiAvailability();
    renderMomentList();
    renderSources();
    updateStatusBar();

    if (document.fonts && document.fonts.ready) {
      try { await document.fonts.ready; } catch (_) { /* offline / blocked CDN */ }
    }
    wireEvents();
    applyTheme(localStorage.getItem("pianosync-theme") || "auto");
    resizeWave();
    await restore();

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
