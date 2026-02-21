/**
 * DayTracker - ChatGPT Content Script
 * MutationObserver로 대화 완료를 감지해 프롬프트+응답을 캡처.
 */
(function () {
  "use strict";

  let lastSentId = null;

  // 스트리밍 완료 여부: "Stop generating" 버튼 없음 = 완료
  function isResponseComplete() {
    return !document.querySelector('[data-testid="stop-button"]');
  }

  // 마지막 user 메시지와 assistant 메시지 추출
  function extractLastTurn() {
    const userMsgs = document.querySelectorAll('[data-message-author-role="user"]');
    const asstMsgs = document.querySelectorAll('[data-message-author-role="assistant"]');
    if (!userMsgs.length || !asstMsgs.length) return null;

    const lastUser = userMsgs[userMsgs.length - 1];
    const lastAsst = asstMsgs[asstMsgs.length - 1];

    const prompt   = lastUser.innerText.trim();
    const response = lastAsst.innerText.trim();
    if (!prompt || !response) return null;

    // 메시지 ID를 dedup key로 사용
    const messageId = lastAsst.closest("[data-message-id]")?.getAttribute("data-message-id")
      || `${prompt.slice(0, 40)}_${response.slice(0, 40)}`;

    return { prompt, response, messageId };
  }

  function sendTurn(turn) {
    chrome.runtime.sendMessage({
      type:     "AI_SESSION",
      tool:     "chatgpt",
      prompt:   turn.prompt,
      response: turn.response,
      url:      window.location.href,
    }, (resp) => {
      if (resp?.success) {
        console.debug("[DayTracker] ChatGPT turn saved:", turn.messageId);
      }
    });
    lastSentId = turn.messageId;
  }

  // 디바운스 타이머
  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      if (!isResponseComplete()) return;
      const turn = extractLastTurn();
      if (turn && turn.messageId !== lastSentId) {
        sendTurn(turn);
      }
    }, 1500);
  });

  observer.observe(document.body, { childList: true, subtree: true });
  console.debug("[DayTracker] ChatGPT logger active.");
})();
