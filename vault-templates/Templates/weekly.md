---
week: <% tp.date.now("GGGG-[W]WW") %>
date_start: <% tp.date.weekday("YYYY-MM-DD", 0) %>
date_end: <% tp.date.weekday("YYYY-MM-DD", 6) %>
tags: [weekly]
projects: []
total_ai_sessions: 0
total_file_changes: 0
---

# <% tp.date.now("GGGG-[W]WW") %> 주간 작업 요약
**기간**: <% tp.date.weekday("YYYY-MM-DD", 0) %> (월) ~ <% tp.date.weekday("YYYY-MM-DD", 6) %> (일)

## 요약

- **0개** 프로젝트에서 작업
- AI 상호작용: **0건** 총계
- 생성/수정 파일: **0개**

## 프로젝트별 활동

## 이번 주 Daily Notes

- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 0) %>|월요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 1) %>|화요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 2) %>|수요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 3) %>|목요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 4) %>|금요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 5) %>|토요일]]
- [[Daily/<% tp.date.weekday("YYYY-MM-DD", 6) %>|일요일]]
