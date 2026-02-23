/**
 * DayTracker - Gemini Content Script
 */
(function () {
  "use strict";

  let lastSentKey = null;

  function isResponseComplete() {
    // 스트리밍 중엔 loading indicator가 표시됨
    return !document.querySelector(".loading-indicator, .pending-response");
  }

  function extractLastTurn() {
    // Gemini의 user 쿼리: .query-text 또는 .user-query-text
    const userEls = document.querySelectorAll(".query-text, .user-query-text");
    // Gemini의 응답: .model-response-text, .response-container
    const asstEls = document.querySelectorAll(
      ".model-response-text, .response-container message-content"
    );
    if (!userEls.length || !asstEls.length) return null;

    const prompt = userEls[userEls.length - 1].innerText.trim();
    const response = asstEls[asstEls.length - 1].innerText.trim();
    if (!prompt || !response) return null;

    const key = `${prompt.slice(0, 40)}_${response.slice(0, 40)}`;
    return { prompt, response, key };
  }

  function sendTurn(turn) {
    chrome.runtime.sendMessage({
      type: "AI_SESSION",
      tool: "gemini",
      prompt: turn.prompt,
      response: turn.response,
      url: window.location.href,
    });
    lastSentKey = turn.key;
  }

  let debounceTimer = null;
  function attemptCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      if (!isResponseComplete()) return;
      const turn = extractLastTurn();
      if (turn && turn.key !== lastSentKey) {
        sendTurn(turn);
      }
    }, 1500);
  }

  const observer = new MutationObserver(attemptCapture);
  observer.observe(document.body, { childList: true, subtree: true });

  // SPA 환경 대응 및 주기적 체크
  let lastUrl = window.location.href;
  setInterval(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      lastSentKey = null;
      attemptCapture();
    }
    if (isResponseComplete()) {
      attemptCapture();
    }
  }, 2000);

  console.debug("[DayTracker] Gemini logger active (with polling backup).");
})();
