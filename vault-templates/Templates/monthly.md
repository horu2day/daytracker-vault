---
month: <% tp.date.now("YYYY-MM") %>
tags: [monthly]
projects: []
total_ai_sessions: 0
total_file_changes: 0
---

# <% tp.date.now("YYYY") %>년 <% tp.date.now("M") %>월 작업 요약

## 통계

- 작업일: 0일
- 프로젝트: 0개
- AI 세션: 0건
- 파일 변경: 0건

## 프로젝트별 집계

| 프로젝트 | AI 세션 | 파일 변경 | 활동일 |
|---------|---------|---------|------|

## 주간 요약

## 이달의 Daily Notes

```dataview
TABLE work_start, total_ai_sessions
FROM "Daily"
WHERE date >= date("<% tp.date.now("YYYY-MM") %>-01") AND date <= date("<% tp.date.now("YYYY-MM") %>-<% tp.date.now("DD", 0, "YYYY-MM-DD", tp.date.now("YYYY-MM") + "-01", -1) %>")
SORT date ASC
```
