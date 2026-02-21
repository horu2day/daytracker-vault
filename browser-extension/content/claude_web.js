/**
 * DayTracker - Claude.ai Content Script
 */
(function () {
  "use strict";

  let lastSentKey = null;

  function isResponseComplete() {
    // 스트리밍 중에는 data-is-streaming="true" 속성이 있음
    return !document.querySelector('[data-is-streaming="true"]');
  }

  function extractLastTurn() {
    // Claude.ai user 메시지
    const userEls = document.querySelectorAll(
      '.font-user-message, [data-testid="user-message"]'
    );
    // Claude.ai 응답
    const asstEls = document.querySelectorAll(
      '.font-claude-message, [data-testid="assistant-message"]'
    );
    if (!userEls.length || !asstEls.length) return null;

    const prompt   = userEls[userEls.length - 1].innerText.trim();
    const response = asstEls[asstEls.length - 1].innerText.trim();
    if (!prompt || !response) return null;

    const key = `${prompt.slice(0, 40)}_${response.slice(0, 40)}`;
    return { prompt, response, key };
  }

  function sendTurn(turn) {
    chrome.runtime.sendMessage({
      type:     "AI_SESSION",
      tool:     "claude",
      prompt:   turn.prompt,
      response: turn.response,
      url:      window.location.href,
    });
    lastSentKey = turn.key;
  }

  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      if (!isResponseComplete()) return;
      const turn = extractLastTurn();
      if (turn && turn.key !== lastSentKey) {
        sendTurn(turn);
      }
    }, 1500);
  });

  observer.observe(document.body, { childList: true, subtree: true });
  console.debug("[DayTracker] Claude.ai logger active.");
})();
