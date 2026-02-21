# PLAN.md - DayTracker 구현 계획

## 프로젝트 목적

하루 동안 컴퓨터로 수행한 모든 작업(VSCode 활동, AI 프롬프트, 파일 변경, 브라우저 활동)을
자동 수집하여 외부 Obsidian 볼트에 **일별 / 프로젝트별**로 정리하는 시스템.

---

## 핵심 설계 결정

| 결정 사항 | 선택 | 이유 |
|-----------|------|------|
| 코드 저장소와 볼트 분리 | 분리 | 코드는 공개 가능, 볼트는 개인 데이터 |
| 볼트 경로 | 사용자가 setup 시 지정 | 범용성, OS별 경로 차이 대응 |
| 중간 저장소 | SQLite | 쿼리 가능, 로컬, 경량 |
| Obsidian 쓰기 방식 | 직접 파일 쓰기 기본 + REST API 보완 | Obsidian 미실행 시에도 동작 |
| 볼트 템플릿 위치 | `vault-templates/` (이 프로젝트 내) | setup_vault.py가 볼트로 복사 |
| VSCode 프로젝트 인식 | 현재 열린 Root 폴더 기준 | 가장 자연스러운 단위 |
| 스케줄링 | 데몬 내 schedule 라이브러리 | OS 무관, 단일 프로세스로 관리 |

---

## Phase 1 - 기반 구축

**목표**: 수동으로 `daily_summary.py`를 실행하면 Obsidian 볼트에 오늘 일지가 생성된다.

### 1-1. 설정 로더 (`scripts/config.py`)

- `config.yaml` 읽기 / 없으면 `config.example.yaml` 복사 후 안내
- `vault_path`, `watch_roots`, `exclude_patterns`, `claude_history_path` 등 제공
- 환경변수 오버라이드 지원 (`.env`)

### 1-2. SQLite 초기화 (`scripts/init_db.py`)

- `data/worklog.db` 생성
- 테이블: `projects`, `activity_log`, `ai_prompts`, `file_events`
- 인덱스: `timestamp`, `project_id`, `file_path`
- 멱등성 보장 (이미 존재하면 skip)

### 1-3. 볼트 셋업 (`scripts/setup_vault.py`)

- `--vault-path` 인자 없으면 인터랙티브 입력
- 볼트 폴더 및 `Daily/`, `Projects/`, `AI-Sessions/`, `Templates/` 생성
- `vault-templates/` 내용 복사
- `config.yaml`에 `vault_path` 저장
- Obsidian에서 열도록 안내 메시지 출력

### 1-4. 프로젝트 매퍼 (`scripts/processors/project_mapper.py`)

- 파일 경로 → 프로젝트명 변환
- `watch_roots` 하위 첫 번째 폴더명 = 프로젝트명
- 예: `C:/MYCLAUDE_PROJECT/daytracker-vault/scripts/foo.py` → `daytracker-vault`
- `data/worklog.db`의 `projects` 테이블에 자동 등록

### 1-5. Claude Code 파서 (`scripts/collectors/claude_code.py`)

- `~/.claude/projects/` 하위 JSONL 파일 파싱
- 각 대화의 `cwd` 필드로 프로젝트 매핑
- `ai_prompts` 테이블에 upsert (`session_id` 기준 중복 방지)
- `--dry-run` 옵션: 파싱 결과만 출력, DB 쓰기 없음
- **TODO**: 실제 파일 포맷 확인 필요 (`~/.claude/` 구조 탐색)

### 1-6. Daily Note 생성기 (`scripts/obsidian/daily_note.py`)

- `worklog.db` 쿼리 → 오늘(또는 `--date`) 데이터 집계
- `{vault}/Daily/YYYY-MM-DD.md` 생성 또는 섹션별 병합 업데이트
- 기존 파일 있으면 수동 작성 내용 보존, 자동 섹션만 교체
- `--dry-run`: 생성될 내용을 stdout에만 출력

### 1-7. AI Session Note 생성기 (`scripts/obsidian/ai_session.py`)

- `ai_prompts` 테이블 → `{vault}/AI-Sessions/YYYY-MM-DD-NNN.md` 생성
- 이미 존재하는 파일은 skip (중복 생성 방지)

---

## Phase 2 - 자동 수집 데몬

**목표**: 백그라운드에서 자동으로 수집하여 DB에 기록한다.

### 2-1. 파일시스템 감시 (`scripts/collectors/file_watcher.py`)

- `watchdog` 라이브러리 사용
- `watch_roots` 하위 폴더 감시
- `exclude_patterns` 매칭 파일/폴더 제외
- 이벤트: `created`, `modified`, `deleted`
- `file_events` + `activity_log` 테이블에 기록

### 2-2. 활성 창 폴러 (`scripts/collectors/window_poller.py`)

- `pywinctl` 사용, 30초 간격 폴링
- 앱 이름 + 창 제목 → 프로젝트 매핑 시도
- VSCode 창 제목 패턴: `{파일명} - {폴더명}` → 폴더명으로 프로젝트 식별
- `activity_log` 테이블에 기록

### 2-3. 브라우저 히스토리 수집 (`scripts/collectors/browser_history.py`)

- `browser-history` 라이브러리 사용
- Chrome/Edge/Firefox 지원
- 1시간 간격으로 새 항목만 수집 (마지막 수집 시각 기준)
- `activity_log` 테이블에 기록

### 2-4. 데몬 통합 (`scripts/watcher_daemon.py`)

- 위 수집기들을 스레드로 실행
- `schedule` 라이브러리로 매일 `daily_summary_time`에 일일 요약 실행
- 시작/종료 로그 기록
- `Ctrl+C` 또는 SIGTERM으로 graceful shutdown

### 2-5. OS별 자동 시작 등록

- Windows: `scripts/install_windows.py` → Task Scheduler 등록
- Mac: `scripts/install_mac.py` → launchd plist 생성 및 등록

---

## Phase 3 - VSCode 연동

**목표**: VSCode 안에서의 작업(파일 편집, 터미널 명령, git 커밋)을 세밀하게 수집한다.

### 옵션 A: Wakapi 연동 (권장)

- Wakapi 로컬 서버 실행
- VSCode WakaTime 확장을 Wakapi 서버로 연결
- Wakapi REST API로 코딩 시간 데이터 수집 → DB 저장

### 옵션 B: VSCode Extension 직접 개발

- TypeScript로 extension 개발
- `vscode.workspace.onDidSaveTextDocument` → 파일 저장 이벤트
- `vscode.window.onDidChangeActiveTextEditor` → 활성 파일 변경
- 로컬 HTTP 서버(Python)로 이벤트 POST

### 3-3. Git Hook 연동

- `scripts/install_git_hook.py`: watch_roots 내 모든 repo에 post-commit hook 설치
- hook: 커밋 메시지 + 변경 파일 목록 → 로컬 서버로 POST

---

## Phase 4 - 웹 AI 도구 연동

**목표**: ChatGPT, Gemini, Claude.ai 웹 인터페이스에서 입력한 프롬프트를 수집한다.

### 4-1. 로컬 수신 서버 (`scripts/server.py`)

- FastAPI 또는 Flask로 구현
- 포트: config에서 지정 (기본 `7331`)
- 엔드포인트: `POST /ai-session` → `ai_prompts` 테이블 저장

### 4-2. 브라우저 확장 (`browser-extension/`)

- Manifest V3, Chrome/Edge 지원
- Content Script: MutationObserver로 대화 감지
- 대상: ChatGPT (`chat.openai.com`), Gemini (`gemini.google.com`), Claude.ai
- 로컬 수신 서버로 POST

### 4-3. ChatGPT 내보내기 파서 (`scripts/collectors/chatgpt_export.py`)

- ChatGPT 설정 > 데이터 내보내기 → `conversations.json` 파싱
- `ai_prompts` 테이블에 upsert

---

## Phase 5 - 고도화

- 주간/월간 요약 노트 자동 생성
- 민감 정보 필터링 규칙 강화 (정규식 라이브러리 기반)
- Datasette로 `worklog.db` 웹 대시보드
- Project Note 자동 생성/업데이트 (`scripts/obsidian/project_note.py`)

---

## Phase 6 - 업무 보조 에이전트

**목표**: Phase 1~5에서 쌓인 `worklog.db` + Obsidian 볼트 데이터를 활용하여
사용자의 업무를 능동적으로 보조하는 AI 에이전트들을 개발한다.

> `worklog.db`가 에이전트들의 **장기 기억**이 된다.

### Morning Briefing Agent

- 매일 아침 컴퓨터 시작 시 자동 실행
- 어제 Daily Note + 미완성 TODO + 열린 프로젝트 컨텍스트 조합
- "어제 `daytracker-vault`에서 마무리 못한 파일이 3개 있어요. 오늘 이어서 할까요?"
- 오늘 집중할 프로젝트 추천

### Context Agent

- VSCode에서 특정 프로젝트 폴더를 열 때 자동 실행
- 해당 프로젝트의 worklog.db 히스토리 조회
- "이 프로젝트는 2주 전에 마지막으로 작업했어요. 그때 이런 AI 세션이 있었고, 이 파일들을 수정했어요."
- 빠른 컨텍스트 복구를 위한 요약 제공

### Stuck Detector Agent

- 같은 파일을 일정 시간 이상 반복 수정/저장 패턴 감지
- 과거 ai_prompts 테이블에서 유사한 문제 상황과 해결책 검색
- "비슷한 문제를 지난번엔 이렇게 해결했어요." 형태로 제안

### Weekly Review Agent

- 매주 금요일 자동 실행
- 이번 주 작업을 프로젝트별로 집계 및 분석
- "가장 많은 시간을 쓴 프로젝트: X / AI 도움을 가장 많이 받은 작업: Y"
- Obsidian에 주간 리뷰 노트 자동 생성

---

## Phase 7 - 캐릭터 에이전트 (바탕화면 동반자)

**목표**: worklog.db의 데이터를 기억으로 가진 AI 캐릭터가 바탕화면에서
사용자와 함께 작업하며 협업하는 인터페이스를 구현한다.

### 기술 스택 후보

| 옵션 | 장점 | 단점 |
|------|------|------|
| PyQt6 / PySide6 | Python 기반, 기존 코드 연결 쉬움 | UI 구현 복잡 |
| Tauri (Rust + WebView) | 가볍고 크로스플랫폼, 성능 우수 | Rust 학습 필요 |
| Electron | 구현 쉬움, 생태계 풍부 | 무거움 |

### 캐릭터 동작

- 바탕화면 위를 자유롭게 이동하는 애니메이션 캐릭터
- 작업 상태에 따라 표정/동작 변화
  - 집중 작업 중 → 활발한 애니메이션
  - 오래 자리 비움 → 졸거나 기지개 켜기
  - 새 AI 세션 시작 → 반짝이며 주목
  - 오류 감지 → 걱정하는 표정

### 말풍선 알림

- "방금 `config.py` 저장됐어요. 오늘 17번째 파일 수정이에요!"
- "Claude Code 세션이 끝났어요. Daily Note에 기록할까요?"
- "3시간 연속 작업 중이에요. 잠깐 쉬는 건 어때요?"
- "이 프로젝트, 지난번에 비슷한 문제 있었는데 기억나세요?"

### 클릭 시 미니 대시보드

- 오늘 작업 요약 팝업 (프로젝트별 시간, AI 세션 수)
- 빠른 수동 로그 입력창
- 진행 중인 프로젝트 상태 및 어제 마무리 못한 항목

---

## Phase 8 - 멀티 에이전트 협업

**목표**: 여러 에이전트가 worklog.db를 공유 기억으로 사용하며 서로 소통하고
사용자의 작업 전반을 자율적으로 보조한다.

### 구조

```
worklog.db (공유 기억)
    ↑↓              ↑↓              ↑↓
Morning Agent   Context Agent   캐릭터 Agent
    ↑↓              ↑↓              ↑↓
        ← 메시지 버스 (로컬 pub/sub) →
```

### 에이전트 간 소통 방식 후보

- **로컬 파일 기반**: 각 에이전트가 `data/agent-messages/` 폴더에 메시지 파일 생성
- **SQLite 메시지 테이블**: `agent_messages` 테이블로 비동기 소통
- **로컬 pub/sub**: ZeroMQ 또는 Python `multiprocessing.Queue`

### 확장 에이전트 아이디어

- **Code Review Agent**: 저장된 파일 변경 이력 분석 → 패턴/품질 피드백
- **Learning Agent**: 어떤 문제에서 AI를 많이 쓰는지 분석 → 학습 추천
- **Focus Agent**: 집중 시간 패턴 분석 → 최적 작업 시간대 추천

---

## 미결 사항 (구현 전 확인 필요)

- [ ] `~/.claude/projects/` 실제 파일 구조 및 포맷 확인
- [ ] Claude Code 대화에서 `cwd` 필드 존재 여부 확인
- [ ] Obsidian Local REST API 플러그인 설치 후 실제 API 동작 확인
- [ ] Windows Task Scheduler 자동 시작 테스트
