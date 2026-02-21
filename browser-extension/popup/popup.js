const SERVER_URL = "http://127.0.0.1:7331";

const dot        = document.getElementById("dot");
const statusText = document.getElementById("status-text");
const countEl    = document.getElementById("session-count");
const toggle     = document.getElementById("enable-toggle");

// 서버 상태 + 오늘 세션 수 확인
async function checkServer() {
  try {
    const res  = await fetch(`${SERVER_URL}/status`, { signal: AbortSignal.timeout(2000) });
    const data = await res.json();

    dot.className        = "status-dot online";
    statusText.textContent = "연결됨";
    countEl.textContent  = data.today_sessions ?? "-";
  } catch {
    dot.className        = "status-dot offline";
    statusText.textContent = "연결 안 됨";
    countEl.textContent  = "-";
  }
}

// enabled 상태 로드
chrome.storage.local.get("enabled", ({ enabled }) => {
  toggle.checked = enabled !== false;
});

// 토글 변경 저장
toggle.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: toggle.checked });
});

checkServer();
