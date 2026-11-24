/* Piano Sync — 악보 플레이 모드 (vanilla JS, no build step) */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v, d = 2) => Number(v || 0).toFixed(d);

  const state = {
    active: false,
    status: null,
    notes: [],
    current: -1,
    jobStream: null,
    booted: false,
  };

  async function api(path, options) {
    const res = await fetch(path, options);
    const type = res.headers.get("content-type") || "";
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return type.includes("application/json") ? res.json() : res.text();
  }
  const post = (path, body) => api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function toast(message, kind = "ok") {
    const host = $("#toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast" + (kind === "err" ? " is-error" : "");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }

  function setBusy(message, progress) {
    const bar = $("#scoreBar");
    if (bar) bar.style.width = `${Math.round((progress || 0) * 100)}%`;
    if (message) $("#scoreMsg").textContent = message;
  }

  function watchJob(jobId, handlers) {
    if (state.jobStream) clearInterval(state.jobStream);
    state.jobStream = setInterval(async () => {
      try {
        const job = await api(`/api/jobs/${jobId}`);
        if (!job.finished) {
          if (handlers.onProgress) handlers.onProgress(job);
          return;
        }
        clearInterval(state.jobStream);
        state.jobStream = null;
        if (job.status === "done") handlers.onDone(job);
        else handlers.onError(job);
      } catch (err) {
        clearInterval(state.jobStream);
        state.jobStream = null;
        handlers.onError({ error: err.message });
      }
    }, 220);
  }

  async function upload(file) {
    const form = new FormData();
    form.append("file", file);
    return api("/api/upload", { method: "POST", body: form });
  }

  function applyStatus(status) {
    state.status = status;
    state.notes = status.notes || [];
    state.current = -1;
    $("#scoreBadge").textContent = status.ready ? "정렬 완료" : "준비";
    if (!status.ready) return;

    const score = status.score || {};
    const sync = status.sync || {};
    const stats = [
      ["곡", score.title || "—"],
      ["음표", `${score.playable_count ?? 0}`, "개"],
      ["마디", `${score.measures ?? 0}`, ""],
      ["길이", fmt(status.source?.duration, 1), "s"],
      ["신뢰도", `${Math.round((sync.confidence || 0) * 100)}`, "%"],
    ];
    $("#scoreStats").innerHTML = stats.map(([k, v, unit]) =>
      `<div class="stat"><span class="k">${esc(k)}</span><span class="v">${esc(v)}${unit ? `<small>${unit}</small>` : ""}</span></div>`
    ).join("");
    const warnings = [...(score.warnings || []), ...(sync.warnings || [])];
    $("#scoreWarnings").textContent = warnings.join(" ");
    $("#scoreWarnings").hidden = warnings.length === 0;
    $("#scoreInfoCard").hidden = false;
    $("#scoreRenderCard").hidden = false;
    $("#scoreAnalyzeBtn").disabled = false;
    $("#scoreAnalyzeBtn").textContent = "다시 정렬";

    const preview = $("#scorePreviewVideo");
    if (preview.getAttribute("src") !== "/api/score/media") {
      preview.src = "/api/score/media?_=" + Date.now();
    }
  }

  async function loadSheet() {
    if (!state.status?.ready) return;
    try {
      const svg = await api("/api/score/svg");
      $("#scoreSheet").innerHTML = svg;
      state.current = -1;
    } catch (err) {
      toast("악보를 표시하지 못했습니다: " + err.message, "err");
    }
  }

  function currentIndexAt(time) {
    const notes = state.notes;
    let lo = 0;
    let hi = notes.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (Number(notes[mid].video_time) <= time) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  function syncPlayback() {
    if (!state.status?.ready) return;
    const video = $("#scorePreviewVideo");
    const time = Number(video.currentTime || 0);
    $("#scoreTime").textContent = fmt(time) + "s";
    const index = currentIndexAt(time);
    if (index === state.current) return;
    state.current = index;
    $("#scoreSheet").querySelectorAll("[data-note-index]").forEach((el) => {
      el.classList.toggle("is-current", Number(el.dataset.noteIndex) === index);
    });
    const active = $("#scoreSheet").querySelector(".score-note.is-current");
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  async function runAnalyze() {
    const videoFile = $("#scoreVideoFile").files[0];
    const scoreFile = $("#scoreFile").files[0];
    if (!videoFile || !scoreFile) {
      toast("연주 영상과 악보 파일을 모두 선택하세요", "err");
      return;
    }
    const btn = $("#scoreAnalyzeBtn");
    btn.disabled = true;
    try {
      setBusy("파일을 업로드하는 중…", 0.05);
      const video = await upload(videoFile);
      const score = await upload(scoreFile);
      const res = await post("/api/score/analyze", {
        video_path: video.path,
        score_path: score.path,
        quality: $("#scoreQuality").value,
      });
      setBusy("악보를 정렬하는 중…", 0.1);
      watchJob(res.job_id, {
        onProgress: (job) => setBusy(job.message || "악보를 정렬하는 중…", job.progress || 0.1),
        onDone: async (job) => {
          btn.disabled = false;
          applyStatus(job.result);
          setBusy("정렬 완료", 1);
          await loadSheet();
          toast("악보 정렬이 완료되었습니다", "ok");
        },
        onError: (job) => {
          btn.disabled = false;
          setBusy("정렬 실패: " + (job.error || "알 수 없는 오류"), 0);
          toast("악보 정렬 실패: " + (job.error || ""), "err");
        },
      });
    } catch (err) {
      btn.disabled = false;
      setBusy("정렬 실패: " + err.message, 0);
      toast("정렬 실패: " + err.message, "err");
    }
  }

  async function runRender() {
    if (!state.status?.ready) {
      toast("먼저 악보를 정렬하세요", "err");
      return;
    }
    const btn = $("#scoreRenderBtn");
    btn.disabled = true;
    setBusy("악보 영상을 렌더링하는 중…", 0.05);
    try {
      const res = await post("/api/score/render", {
        filename: $("#scoreFilename").value || "score-play.mp4",
        position: $("#scorePosition").value,
        overlay_enabled: $("#scoreOverlay").checked,
        width: 0,
        height: 0,
      });
      watchJob(res.job_id, {
        onProgress: (job) => setBusy(job.message || "렌더링 중…", job.progress || 0.05),
        onDone: async () => {
          btn.disabled = false;
          const status = await api("/api/score/status");
          applyStatus(status);
          const stamp = Date.now();
          $("#scoreResultVideo").src = `/api/score/result?_=${stamp}`;
          $("#scoreDownload").href = `/api/score/download?_=${stamp}`;
          $("#scoreResultCard").hidden = false;
          setBusy("렌더링 완료", 1);
          toast("악보 영상이 완성되었습니다", "ok");
        },
        onError: (job) => {
          btn.disabled = false;
          setBusy("렌더링 실패: " + (job.error || ""), 0);
          toast("렌더링 실패: " + (job.error || ""), "err");
        },
      });
    } catch (err) {
      btn.disabled = false;
      setBusy("렌더링 실패: " + err.message, 0);
      toast("렌더링 실패: " + err.message, "err");
    }
  }

  async function reset() {
    if (state.jobStream) clearInterval(state.jobStream);
    state.jobStream = null;
    await post("/api/score/reset", {});
    state.status = null;
    state.notes = [];
    state.current = -1;
    $("#scoreBadge").textContent = "준비";
    $("#scoreStats").innerHTML = "";
    $("#scoreInfoCard").hidden = true;
    $("#scoreRenderCard").hidden = true;
    $("#scoreResultCard").hidden = true;
    $("#scoreSheet").innerHTML = '<p class="score-empty">영상과 악보를 올리고 정렬을 시작하세요.</p>';
    $("#scorePreviewVideo").removeAttribute("src");
    $("#scoreResultVideo").removeAttribute("src");
    $("#scoreAnalyzeBtn").disabled = false;
    $("#scoreAnalyzeBtn").textContent = "악보 정렬";
    setBusy("초기화되었습니다", 0);
  }

  async function activate() {
    try {
      const status = await api("/api/score/status");
      applyStatus(status);
      if (status.ready) await loadSheet();
    } catch (_) {}
  }

  function boot() {
    if (state.booted) return;
    state.booted = true;
    $("#scoreAnalyzeBtn").addEventListener("click", runAnalyze);
    $("#scoreResetBtn").addEventListener("click", reset);
    $("#scoreRenderBtn").addEventListener("click", runRender);
    $("#scorePreviewVideo").addEventListener("timeupdate", syncPlayback);
    $("#scorePreviewVideo").addEventListener("seeked", syncPlayback);
    window.addEventListener("pianosync:mode", (event) => {
      state.active = event.detail?.mode === "score";
      if (state.active) activate();
    });
    if (new URLSearchParams(location.search).get("mode") === "score") {
      state.active = true;
      activate();
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
