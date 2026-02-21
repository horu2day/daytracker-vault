---
type: project
name: <% tp.file.title %>
status: active
started: <% tp.date.now("YYYY-MM-DD") %>
path:
tags: [project]
---

# <% tp.file.title %>

## 최근 활동

```dataview
TABLE work_start, total_ai_sessions, file.link AS "일지"
FROM "Daily"
WHERE contains(projects, "<% tp.file.title %>")
SORT date DESC
LIMIT 14
```

## AI 세션 목록

```dataview
LIST file.link + " (" + tool + ")"
FROM "AI-Sessions"
WHERE project = "<% tp.file.title %>"
SORT date DESC
```
