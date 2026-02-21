---
name: summary
description: 특정 날짜 또는 기간의 작업을 요약하여 Obsidian 볼트에 리포트 노트를 생성한다. "이번 주 요약해줘", "지난주 보고서 만들어줘" 같은 요청에 사용.
argument-hint: "[today|week|month|YYYY-MM-DD|YYYY-Www]"
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Write, Bash, Glob
---

# 작업 요약 리포트 생성

## 작업 순서

1. `$ARGUMENTS`로 요약 범위를 결정한다.
   - 없거나 `today` → 오늘 Daily Note 갱신
   - `week` → 이번 주 (Weekly Review 노트 생성)
   - `month` → 이번 달 (Monthly Review 노트 생성)
   - `YYYY-MM-DD` → 특정 날짜 Daily Note 재생성
   - `YYYY-Www` (예: `2026-W08`) → 특정 주 Weekly Review
2. PLAN.md와 PROGRESS.md를 읽어 컨텍스트를 파악한다.
3. `data/worklog.db`에서 해당 기간 데이터를 쿼리한다.
   - 프로젝트별 활동 집계
   - AI 세션 목록 및 주요 프롬프트
   - 파일 변경 통계
4. config.yaml에서 vault_path를 읽는다.
5. 기간에 맞는 노트를 생성한다.
   - `today/날짜` → `{vault}/Daily/YYYY-MM-DD.md`
   - `week` → `{vault}/Weekly/YYYY-Www.md`
   - `month` → `{vault}/Monthly/YYYY-MM.md`
6. 생성된 노트 경로를 보고한다.

## 노트 포함 내용

- 기간 요약 (총 작업 시간, 프로젝트 수, AI 세션 수)
- 프로젝트별 상세 (변경 파일, AI 세션 목록)
- 주요 성과 및 다음 계획 섹션
