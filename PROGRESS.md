# PROGRESS.md - DayTracker 진행 상황

## 마지막 업데이트

- 날짜: 2026-02-21 23:55
- 작업 내용: collector 에이전트 - Phase 2 자동 수집 데몬 전체 구현 완료

---

## 완료된 작업

- [x] 시스템 조사 및 기술 선택 (2026-02-21)
- [x] CLAUDE.md 작성 - 프로젝트 개요, 구조, 규칙 정의 (2026-02-21)
- [x] PLAN.md 작성 - Phase 1~8 상세 구현 계획 (2026-02-21)
- [x] .claude/ 구조 생성 - skills, agents, hooks, settings (2026-02-21)
- [x] explore 에이전트로 실제 환경 탐색 완료 (2026-02-21)
- [x] auto-allow hook 활성화 및 테스트 완료 (2026-02-21)
- [x] **Phase 1 기반 인프라 구현 완료 (2026-02-21)**
  - `scripts/`, `scripts/collectors/`, `scripts/processors/`, `scripts/obsidian/` 디렉토리 생성
  - `data/` 디렉토리 생성 (gitignored)
  - `vault-templates/Templates/` 디렉토리 생성
  - `.gitignore` 작성 (data/, config.yaml, .env, \*.db, `__pycache__`/ 등)
  - `requirements.txt` 작성 (pyyaml, sqlite-utils, watchdog 등)
  - `config.example.yaml` 작성 (전체 필드 + 주석)
  - `scripts/__init__.py` + 하위 패키지 `__init__.py` 파일 생성
  - `scripts/config.py` - Config 클래스, get\_vault\_path(), get\_claude\_history\_path(), .env 오버라이드
  - `scripts/init_db.py` - 4개 테이블 + 5개 인덱스, 멱등성 보장
  - `scripts/setup_vault.py` - 볼트 디렉토리 생성, 템플릿 복사, config.yaml 저장
  - `scripts/processors/project_mapper.py` - 경로→프로젝트명, get\_or\_create\_project()
- [x] **Obsidian 노트 생성기 구현 완료 (2026-02-21)**
  - `scripts/obsidian/writer.py` - write\_note(), update\_section() 유틸리티
  - `scripts/obsidian/ai_session.py` - AI Session Note 생성기 (YYYY-MM-DD-NNN.md, 멱등)
  - `scripts/obsidian/daily_note.py` - Daily Note 생성/업데이트 (섹션별 병합)
  - `scripts/obsidian/project_note.py` - Project Note 생성/업데이트 (Dataview 쿼리 포함)
  - `scripts/daily_summary.py` - 전체 파이프라인 오케스트레이터 (4단계)
  - `vault-templates/Templates/daily.md` - Templater 템플릿
  - `vault-templates/Templates/ai-session.md` - AI 세션 템플릿
  - `vault-templates/Templates/project.md` - 프로젝트 노트 템플릿 (Dataview 쿼리 포함)
- [x] **Phase 2 자동 수집 데몬 구현 완료 (2026-02-21)**
  - `scripts/collectors/file_watcher.py` - watchdog 기반 파일시스템 감시
  - `scripts/collectors/window_poller.py` - pywinctl 기반 활성 창 폴러 (30초 간격)
  - `scripts/collectors/browser_history.py` - Chrome/Edge 히스토리 수집
  - `scripts/watcher_daemon.py` - 통합 데몬 (4개 스레드, 5분 주기 상태 출력, graceful shutdown)
  - `scripts/install_windows.py` - Windows Task Scheduler 등록 스크립트

---

## 진행 중

없음.

---

## 다음 할 일 (Phase 3)

- [ ] VSCode 연동 (Wakapi 또는 Extension)
- [ ] Git post-commit hook 연동 (`scripts/install_git_hook.py`)

---

## 알게 된 사실 / 결정 사항

### Obsidian 볼트

- 볼트는 이 프로젝트 외부에 위치 (사용자가 setup 시 경로 지정)
- 이 프로젝트는 소스코드 + vault-templates 포함
- 볼트 쓰기: 직접 파일 쓰기 기본, Obsidian Local REST API 보완

### Claude Code 기록 구조 (탐색 완료)

- 경로: `C:/Users/hyund/.claude/projects/c--MYCLAUDE-PROJECT-daytracker-vault/`
- 형식: **JSONL** (한 줄 = 하나의 JSON 객체)
- `cwd` 필드: **존재함** → 프로젝트 자동 매핑 가능
- `sessionId` 필드: **존재함** (UUID) → 중복 방지 가능
- `timestamp` 필드: **ISO 8601** (`2026-02-21T11:56:31.382Z`)
- 메시지 타입: `user`, `assistant`, `queue-operation`, `file-history-snapshot`
- 현재 등록된 프로젝트 폴더들: apa, araiagent, docuConverter, md2hml, primenumber, saleslicense 등

### Chrome 히스토리 (탐색 완료)

- 경로: `C:/Users/hyund/AppData/Local/Google/Chrome/User Data/Default/History`
- 형식: SQLite, `urls` 테이블
- 타임스탬프: 마이크로초, 1601-01-01 기준 (Windows FILETIME)
- 변환식: `(ts - 11644473600 * 1000000) / 1000000` → Unix timestamp

### Python 환경 (탐색 완료)

- 버전: Python 3.11.9
- 설치됨: `requests` 2.32.5, `python-dotenv` 1.2.1
- **Phase 2 설치 완료**: `watchdog` 6.0.0, `pywinctl` 0.4.1, `schedule` 1.2.2, `browser-history` 0.5.0, `pyperclip` 1.11.0, `sqlite-utils` 3.39

### auto-allow hook (활성화 완료)

- `settings.local.json`에 PermissionRequest hook 등록됨
- exit 0 = 자동 승인, exit 2 = 차단
- 위험 명령(`rm -rf /` 등) 자동 차단 로직 포함
- 테스트 완료: 정상 명령 exit 0, 위험 명령 exit 2 확인

### Phase 1 인프라 테스트 결과 (2026-02-21)

- `python scripts/init_db.py` → 4개 테이블 + 5개 인덱스 생성 성공
- `python scripts/setup_vault.py --vault-path "..."` → 볼트 디렉토리 생성, config.yaml 저장 성공
- `from scripts.config import Config; c.get_vault_path()` → test-vault 경로 반환 성공
- `python -m scripts.processors.project_mapper --path "..."` → `daytracker-vault` 반환, DB 등록 성공

### Phase 1 통합 파이프라인 테스트 결과 (2026-02-21)

`python scripts/daily_summary.py --date 2026-02-21` 전체 통과:
- Step 1 Claude Code sync: 22개 세션 파싱 (daytracker-vault, 20:56~23:31 KST)
- Step 2 AI Session notes: 22개 노트 생성 (`test-vault/AI-Sessions/`)
- Step 3 Daily Note: `test-vault/Daily/2026-02-21.md` 생성
- Step 4 Project Note: `test-vault/Projects/daytracker-vault.md` 생성 (Dataview 쿼리 포함)
- 2회차 실행 idempotency 확인: 모든 단계 정상 스킵/업데이트

### Phase 2 수집 데몬 테스트 결과 (2026-02-21)

모든 테스트 통과:
- **file_watcher**: `start_watching(dry_run=True)` → 10초 실행 후 정상 종료. `C:\MYCLAUDE_PROJECT` watch 확인
- **window_poller**: `start_polling(interval=5, dry_run=True)` → 7초 실행, 활성 창(Brave/YouTube) 감지 성공
- **browser_history**: `--dry-run --hours 1000` → 14개 항목 발견 (2026-01-11 Chrome 기록), `--hours 24`는 최신 기록 없어 정상적으로 0건 반환
- **daemon**: 5초 dry-run → 4개 스레드(file_watcher, window_poller, browser_sync, scheduler) 정상 시작·종료, graceful shutdown 확인
- **install_windows**: `--dry-run` → 설치 계획 출력 성공, `--status` → 미설치 상태 정상 응답

### Phase 2 구현 세부사항

- **stdout 중복 래핑 버그 수정**: 여러 모듈이 동시 import될 때 `sys.stdout`을 중복 래핑하면 `I/O operation on closed file` 오류 발생 → `_daytracker_wrapped` 플래그로 중복 래핑 방지
- **debounce**: 동일 파일 2초 이내 중복 이벤트 무시 (파일 저장 스파이크 대응)
- **graceful shutdown**: `threading.Event`로 모든 스레드에 종료 신호 전달, `observer.stop() + join()` 순서 보장
- **Browser history**: Chrome 잠금 파일 대응 → `tempfile`로 복사 후 읽기, 항상 `finally`에서 삭제

---

## 막힌 부분 / 이슈

없음.
