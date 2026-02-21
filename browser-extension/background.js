/**
 * DayTracker AI Logger - Background Service Worker
 * Content script 에서 AI_SESSION 메시지를 수신해 로컬 서버로 POST.
 */

const SERVER_URL = "http://127.0.0.1:7331";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== "AI_SESSION") return;

  // storage에서 enabled 상태 확인
  chrome.storage.local.get("enabled", ({ enabled }) => {
    if (enabled === false) {
      sendResponse({ success: false, reason: "disabled" });
      return;
    }

    fetch(`${SERVER_URL}/ai-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool:          message.tool,
        prompt_text:   message.prompt,
        response_text: message.response,
        url:           message.url,
        timestamp:     new Date().toISOString(),
      }),
    })
      .then((r) => r.json())
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => {
        console.warn("[DayTracker] Server unreachable:", err.message);
        sendResponse({ success: false, error: err.message });
      });
  });

  return true; // async response
});

// 설치 시 기본값 설정
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ enabled: true });
  console.log("[DayTracker] Extension installed. Logging enabled.");
});
