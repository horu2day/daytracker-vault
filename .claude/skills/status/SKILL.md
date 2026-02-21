---
name: status
description: 오늘 수집된 작업 데이터 현황을 터미널에 요약 출력한다. "오늘 뭐 했어", "오늘 작업 현황 보여줘" 같은 요청에 사용.
argument-hint: "[today|week|YYYY-MM-DD]"
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Bash
---

# 작업 현황 조회

## 작업 순서

1. `$ARGUMENTS`로 조회 범위를 결정한다.
   - 없거나 `today` → 오늘
   - `week` → 이번 주 (월요일 ~ 오늘)
   - `YYYY-MM-DD` → 특정 날짜
2. `data/worklog.db`가 존재하면 아래 항목을 쿼리한다.
   - 작업한 프로젝트 목록 및 각 프로젝트별 파일 변경 수
   - AI 세션 수 (도구별: claude-code, chatgpt, gemini)
   - 수정/생성된 파일 목록 (최근 10개)
   - 첫 활동 시각 ~ 마지막 활동 시각
3. DB가 없으면 (Phase 1 미완성 상태):
   - `~/.claude/projects/` 폴더에서 오늘 대화 기록을 직접 파악한다.
   - 현재 프로젝트의 git log에서 오늘 커밋을 확인한다.
4. 결과를 아래 형식으로 출력한다.

## 출력 형식

```
📅 2026-02-21 작업 현황
━━━━━━━━━━━━━━━━━━━━━━━━
🗂  프로젝트
  • daytracker-vault  파일 변경 12건
  • other-project     파일 변경 3건

🤖 AI 세션
  • claude-code  8건
  • chatgpt      2건

📁 최근 수정 파일
  • scripts/config.py        14:32
  • scripts/init_db.py       15:10

⏱  작업 시간: 09:15 ~ 17:45
━━━━━━━━━━━━━━━━━━━━━━━━
```
