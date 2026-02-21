---
name: log
description: 지금 하고 있는 작업을 수동으로 activity_log에 기록한다. "지금 작업 기록해줘", "이 작업 로그 남겨줘" 같은 요청에 사용.
argument-hint: "[작업 내용 설명]"
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Bash, Write
---

# 수동 작업 로그 기록

## 작업 순서

1. `$ARGUMENTS`에서 기록할 내용을 파악한다.
   - 인자가 없으면 현재 작업 내용을 사용자에게 물어본다.
2. 현재 열린 프로젝트(cwd 기준)를 프로젝트명으로 식별한다.
3. 현재 타임스탬프를 기록한다.
4. `data/worklog.db`의 `activity_log` 테이블에 아래 내용으로 INSERT한다.
   - `event_type`: `'manual'`
   - `timestamp`: 현재 ISO 8601 로컬 시간
   - `project_id`: 식별된 프로젝트 ID
   - `summary`: `$ARGUMENTS` 내용
5. DB가 없으면 오늘 Daily Note 파일에 직접 추가한다.
6. 기록 완료 메시지를 출력한다.

## 예시

```
/log config.yaml 설계 완료, setup_vault.py 구현 시작
/log ChatGPT로 SQLite 스키마 설계 논의
/log 오늘 목표: Phase 1 기반 구축 완료
```
