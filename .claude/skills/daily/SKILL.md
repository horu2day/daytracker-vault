---
name: daily
description: 오늘(또는 지정 날짜)의 Daily Note를 Obsidian 볼트에 생성하거나 갱신한다. "오늘 일지 만들어줘", "daily note 업데이트해줘" 같은 요청에 사용.
argument-hint: "[YYYY-MM-DD]"
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Glob
---

# Daily Note 생성/갱신

## 작업 순서

1. PLAN.md와 PROGRESS.md를 읽어 현재 프로젝트 상태를 파악한다.
2. 날짜를 확인한다.
   - `$ARGUMENTS`가 있으면 그 날짜 사용 (형식: YYYY-MM-DD)
   - 없으면 오늘 날짜 사용
3. `scripts/obsidian/daily_note.py` 스크립트가 존재하면 실행한다.
   ```
   python scripts/obsidian/daily_note.py --date <날짜>
   ```
4. 스크립트가 아직 없으면 (Phase 1 미완성 상태):
   - `data/worklog.db`에서 해당 날짜 데이터를 직접 쿼리한다.
   - config.yaml에서 vault_path를 읽는다.
   - CLAUDE.md의 노트 형식 규칙에 따라 Daily Note를 직접 생성한다.
5. 생성/갱신 결과를 사용자에게 보고한다.

## 주의사항

- 기존 Daily Note가 있으면 수동 작성 내용을 보존하고 자동 섹션만 업데이트한다.
- vault_path가 설정되지 않았으면 config.yaml 설정을 먼저 안내한다.
- `--dry-run` 인자가 있으면 실제 파일을 쓰지 않고 생성될 내용을 출력한다.
