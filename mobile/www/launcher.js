const KEY = "pianosync_server_url";
const DEFAULT = "http://127.0.0.1:8765";

const $ = (id) => document.getElementById(id);

function normalizeUrl(raw) {
  const s = (raw || "").trim();
  if (!s) return "";
  const withScheme = /^https?:\/\//i.test(s) ? s : `http://${s}`;
  const u = new URL(withScheme);
  if (u.protocol !== "http:" && u.protocol !== "https:") return "";
  u.pathname = u.pathname.replace(/\/+$/, "") || "";
  return u.toString().replace(/\/$/, "");
}

function loadSaved() {
  try {
    return localStorage.getItem(KEY) || DEFAULT;
  } catch {
    return DEFAULT;
  }
}

function save(url) {
  try {
    localStorage.setItem(KEY, url);
  } catch { /* private mode */ }
}

async function probe(url) {
  const health = `${url}/api/health`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(health, { signal: ctrl.signal, cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function go(url) {
  save(url);
  window.location.replace(`${url}/`);
}

async function boot() {
  const input = $("serverUrl");
  const err = $("err");
  const saved = loadSaved();
  input.value = saved;

  const localBtn = $("localBtn");
  if (localBtn) {
    localBtn.hidden = false;
    localBtn.addEventListener("click", () => {
      input.value = DEFAULT;
      connect();
    });
  }

  async function connect() {
    err.textContent = "";
    const url = normalizeUrl(input.value);
    if (!url) {
      err.textContent = "http:// 또는 https:// 로 시작하는 주소를 입력하세요.";
      return;
    }
    $("connectBtn").disabled = true;
    err.textContent = "서버 확인 중…";
    const ok = await probe(url);
    $("connectBtn").disabled = false;
    if (!ok) {
      err.textContent = "서버에 연결할 수 없습니다. PC에서 pianosync.web 실행 및 Wi‑Fi를 확인하세요.";
      return;
    }
    await go(url);
  }

  $("connectBtn").addEventListener("click", connect);

  const auto = normalizeUrl(saved);
  if (auto && auto !== DEFAULT) {
    const ok = await probe(auto);
    if (ok) await go(auto);
  }
}

boot();
