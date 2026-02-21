---
name: project
description: 특정 프로젝트의 Obsidian 페이지를 최신 활동으로 업데이트하거나 새로 생성한다. "daytracker 프로젝트 페이지 업데이트해줘" 같은 요청에 사용.
argument-hint: "[프로젝트명]"
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Glob
---

# 프로젝트 노트 생성/업데이트

## 작업 순서

1. `$ARGUMENTS`에서 프로젝트명을 파악한다.
   - 인자가 없으면 현재 cwd의 폴더명을 프로젝트명으로 사용한다.
2. `data/worklog.db`의 `projects` 테이블에서 해당 프로젝트를 조회한다.
   - 없으면 현재 디렉토리 경로로 새 프로젝트를 등록한다.
3. config.yaml에서 vault_path를 읽는다.
4. `{vault}/Projects/{프로젝트명}.md` 파일을 확인한다.
   - 없으면 CLAUDE.md의 Project Note 형식으로 새로 생성한다.
   - 있으면 "최근 활동" 섹션의 Dataview 쿼리를 최신 상태로 검토한다.
5. 해당 프로젝트의 최근 AI 세션 5개를 AI Sessions 섹션에 반영한다.
6. 결과를 보고한다.

## 생성되는 노트 형식

CLAUDE.md의 "Project Note" 형식 규칙을 따른다.
- YAML frontmatter: type, name, status, started, path, tags
- 최근 활동 Dataview 쿼리
- AI 세션 목록 Dataview 쿼리
