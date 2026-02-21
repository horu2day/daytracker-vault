# DayTracker - CLAUDE.md

## 프로젝트 개요

하루 동안 컴퓨터로 수행한 모든 작업을 **시간별, 프로젝트별**로 자동 수집하고,
**외부 Obsidian 볼트**에 일별/프로젝트별로 정리하는 개인 작업 추적 시스템.

---

## 에이전트 시작 시 필수 확인

**새 대화를 시작하거나 작업을 이어받을 때 반드시 아래 두 파일을 먼저 읽어야 한다.**

```
PLAN.md     - 전체 구현 계획, 설계 결정, 각 Phase 상세 내용
PROGRESS.md - 현재까지 완료된 작업, 진행 중인 작업, 다음 할 일
```

이 두 파일을 읽지 않고 작업을 시작하면 중복 작업이나 설계 불일치가 발생할 수 있다.

### PROGRESS.md 작성 규칙

작업을 완료하거나 중단할 때마다 PROGRESS.md를 업데이트한다.

```markdown
# Progress

## 마지막 업데이트
- 날짜: YYYY-MM-DD HH:MM
- 작업한 에이전트/세션: (설명)

## 완료된 작업
- [x] 항목 (YYYY-MM-DD)

## 진행 중
- [ ] 현재 작업 중인 항목 (시작: YYYY-MM-DD)
  - 현재 상태: ...
  - 막힌 부분: ... (있을 경우)

## 다음 할 일
- [ ] 우선순위 1
- [ ] 우선순위 2

## 알게 된 사실 / 결정 사항
- 발견한 중요 정보나 설계 결정을 여기에 기록
```

### PLAN.md 작성 규칙

설계 변경이나 새로운 결정이 생기면 PLAN.md를 업데이트한다.
PLAN.md는 "무엇을 왜 만드는가"의 기준 문서다.

---

## 두 저장소의 역할 구분

| | 이 프로젝트 (daytracker) | Obsidian 볼트 (외부) |
|---|---|---|
| **성격** | 소스코드, 스크립트, 템플릿, 설정 | 실제 개인 작업 데이터 |
| **Git** | 버전 관리 대상 (공개 가능) | 제외 또는 별도 private 관리 |
| **내용** | `scripts/`, `vault-templates/`, `CLAUDE.md` | `Daily/`, `Projects/`, `AI-Sessions/` |
| **위치** | 이 저장소 | 사용자가 초기 설정 시 지정 |

---

## 이 프로젝트의 디렉토리 구조

```
daytracker/
├── scripts/
│   ├── collectors/           # 데이터 수집 모듈
│   │   ├── claude_code.py    # Claude Code 대화 기록 파서
│   │   ├── file_watcher.py   # 파일시스템 감시 데몬
│   │   ├── window_poller.py  # 활성 창/앱 감지
│   │   ├── browser_history.py
│   │   └── clipboard_monitor.py
│   ├── processors/
│   │   └── project_mapper.py # 경로 → 프로젝트명 매핑
│   ├── obsidian/             # Obsidian 노트 생성/업데이트
│   │   ├── daily_note.py
│   │   ├── ai_session.py
│   │   └── project_note.py
│   ├── init_db.py            # SQLite 스키마 초기화
│   ├── setup_vault.py        # Obsidian 볼트 초기 셋업
│   ├── daily_summary.py      # 일일 요약 생성 (메인 엔트리)
│   └── watcher_daemon.py     # 상시 실행 데몬 (메인 엔트리)
├── vault-templates/          # 볼트에 복사될 템플릿
│   ├── Templates/
│   │   ├── daily.md
│   │   ├── ai-session.md
│   │   └── project.md
│   └── .obsidian/
│       └── plugins/          # 권장 플러그인 설정 스냅샷
├── data/                     # 로컬 수집 데이터 (gitignore)
│   └── worklog.db
├── config.example.yaml       # 설정 예시 (실제 값 없음)
├── .env.example
├── requirements.txt
├── PLAN.md                   # 전체 구현 계획
├── PROGRESS.md               # 진행 상황 추적
└── CLAUDE.md
```

### Obsidian 볼트 구조 (외부, setup_vault.py가 생성)

```
{vault_path}/                 # 사용자가 지정한 경로
├── Daily/                    # 일별 작업 일지 (YYYY-MM-DD.md)
├── Projects/                 # 프로젝트별 MOC 페이지
├── AI-Sessions/              # AI 프롬프트 세션 기록
└── Templates/                # Obsidian 노트 템플릿
```

---

## 초기 셋업 흐름

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 볼트 경로 설정 후 초기화
python scripts/setup_vault.py --vault-path "C:/Users/yourname/Obsidian/DayTracker"

# 3. DB 초기화
python scripts/init_db.py

# 4. 데몬 시작
python scripts/watcher_daemon.py
```

`setup_vault.py`가 하는 일:

- 지정 경로에 볼트 폴더 및 하위 폴더 생성
- `vault-templates/` 내용을 볼트에 복사
- `config.yaml`에 `vault_path` 저장
- Obsidian에서 해당 폴더를 볼트로 열도록 안내 메시지 출력

---

## 설정 파일 구조 (config.yaml)

```yaml
# Obsidian 볼트 경로 (setup_vault.py 실행 후 자동 설정)
vault_path: ""

# 감시할 프로젝트 루트 폴더들 (하위 폴더 = 각 프로젝트)
watch_roots:
  - "C:/MYCLAUDE_PROJECT"

# 파일 감시 제외 패턴
exclude_patterns:
  - ".git"
  - "node_modules"
  - "__pycache__"
  - "*.log"
  - "*.tmp"
  - ".venv"

# Claude Code 기록 경로 (비워두면 ~/.claude/projects/ 자동 사용)
claude_history_path: ""

# Obsidian Local REST API (선택)
obsidian_api:
  enabled: false
  port: 27124
  api_key: ""

# 일일 요약 자동 생성 시간
daily_summary_time: "23:55"

# 민감 정보 마스킹 패턴 (정규식)
sensitive_patterns:
  - "sk-[a-zA-Z0-9]+"
  - "AIza[a-zA-Z0-9]+"
  - "password\\s*=\\s*\\S+"
```

---

## 노트 형식 규칙

### Daily Note (`{vault}/Daily/YYYY-MM-DD.md`)

```markdown
---
date: YYYY-MM-DD
work_start: "HH:MM"
work_end: "HH:MM"
tags: [daily]
projects: [프로젝트명1, 프로젝트명2]
total_ai_sessions: N
---

# YYYY-MM-DD 작업 일지

## 요약

- **N개** 프로젝트에서 작업
- AI 상호작용: **N건** (claude-code: N, chatgpt: N, gemini: N)
- 생성/수정 파일: **N개**

## 타임라인

| 시간  | 프로젝트                    | 작업 내용 |
|-------|----------------------------|---------|
| 09:00 | [[Projects/foo\|foo]]      | ...     |

## 프로젝트별 작업

### [[Projects/프로젝트명|프로젝트명]]

**작업 시간**: HH:MM - HH:MM

#### 변경 파일

- `path/to/file.py` (수정)

#### AI 세션

- [[AI-Sessions/YYYY-MM-DD-NNN|claude-code: 프롬프트 요약...]]
```

### AI Session Note (`{vault}/AI-Sessions/YYYY-MM-DD-NNN.md`)

```markdown
---
date: YYYY-MM-DD
time: "HH:MM"
tool: claude-code | chatgpt | gemini | claude
project: 프로젝트명
tags: [ai-session, claude-code]
input_tokens: N
output_tokens: N
---

# AI 세션 YYYY-MM-DD-NNN

## 프롬프트

{입력한 프롬프트 전문}

## 결과

{AI 응답 요약 또는 전문}

## 생성된 파일

- `path/to/generated_file.py`
```

### Project Note (`{vault}/Projects/프로젝트명.md`)

```markdown
---
type: project
name: 프로젝트명
status: active | paused | completed
started: YYYY-MM-DD
path: C:/절대경로/프로젝트폴더
tags: [project]
---

# 프로젝트명

## 최근 활동

` ``dataview
TABLE work_start, total_ai_sessions, file.link AS "일지"
FROM "Daily"
WHERE contains(projects, "프로젝트명")
SORT date DESC
LIMIT 14
` ``

## AI 세션 목록

` ``dataview
LIST file.link + " (" + tool + ")"
FROM "AI-Sessions"
WHERE project = "프로젝트명"
SORT date DESC
` ``
```

---

## 데이터 수집 대상

### 1. VSCode 활동

- **현재 열린 Root 폴더** = 현재 작업 프로젝트 (자동 인식)
- 수집 항목: 활성 파일, 저장된 파일, 터미널 명령어, git 커밋
- 수집 방법: Wakapi(WakaTime 호환) VSCode 확장 + 파일시스템 감시 병행

### 2. Claude Code 대화

- 저장 위치 (Windows): `C:/Users/{username}/.claude/projects/`
- 저장 위치 (Mac): `~/.claude/projects/`
- 수집 항목: 프롬프트, 응답, 토큰 수, 타임스탬프, 작업 디렉토리(`cwd`)
- 프로젝트 자동 식별: 대화의 `cwd` 필드 기준

### 3. 웹 AI 도구 (ChatGPT, Gemini, Claude.ai)

- 수집 방법 우선순위:
  1. 브라우저 확장 (Manifest V3 Content Script + 로컬 수신 서버)
  2. ChatGPT 공식 내보내기 파일 정기 파싱
  3. 수동 복사 보조
- 민감 정보 필터링 필수

### 4. 파일시스템 변경

- 감시 대상: `config.yaml`의 `watch_roots` 하위 폴더들
- 수집 항목: 파일 생성/수정/삭제, 타임스탬프, 파일 크기
- 제외: `exclude_patterns` 참조

---

## 기술 스택

### 언어 및 런타임

- **Python 3.11+**: 메인 자동화 스크립트
- **SQLite**: 중간 데이터 저장소 (`data/worklog.db`)

### 핵심 Python 라이브러리

```
watchdog          # 파일시스템 모니터링
pywinctl          # 활성 창/앱 감지 (Windows/Mac 공통)
pyperclip         # 클립보드 모니터링
browser-history   # 브라우저 히스토리 읽기
sqlite-utils      # SQLite 조작 유틸리티
schedule          # 스케줄러
requests          # Obsidian Local REST API 호출
python-dotenv     # 환경변수 관리
pyyaml            # config.yaml 파싱
```

### Obsidian 플러그인 (필수)

- **Dataview**: 노트를 DB처럼 쿼리, 프로젝트/일별 집계
- **Templater**: 동적 템플릿, 외부 스크립트 실행
- **Periodic Notes**: 일별/주별 노트 자동 생성
- **Local REST API**: Python 스크립트에서 노트 CRUD
- **QuickAdd**: 빠른 수동 캡처 매크로

### 스케줄링

- **Windows**: Task Scheduler 또는 데몬 내 `schedule` 라이브러리
- **Mac**: `launchd` plist 또는 데몬 내 `schedule`

---

## SQLite 스키마 (`data/worklog.db`)

```sql
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT,
    status      TEXT DEFAULT 'active',
    created_at  TEXT
);

CREATE TABLE activity_log (
    id           INTEGER PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    duration_s   INTEGER,
    event_type   TEXT NOT NULL,
    project_id   INTEGER REFERENCES projects(id),
    app_name     TEXT,
    summary      TEXT,
    data         TEXT
);

CREATE TABLE ai_prompts (
    id            INTEGER PRIMARY KEY,
    activity_id   INTEGER REFERENCES activity_log(id),
    timestamp     TEXT NOT NULL,
    tool          TEXT,
    project_id    INTEGER REFERENCES projects(id),
    prompt_text   TEXT,
    response_text TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    session_id    TEXT
);

CREATE TABLE file_events (
    id           INTEGER PRIMARY KEY,
    activity_id  INTEGER REFERENCES activity_log(id),
    timestamp    TEXT NOT NULL,
    file_path    TEXT,
    event_type   TEXT,
    project_id   INTEGER REFERENCES projects(id),
    file_size    INTEGER
);
```

---

## Obsidian 연동 방식

### 기본: 직접 파일 쓰기

Python이 볼트 폴더에 직접 `.md` 파일을 생성/수정한다.
Obsidian이 닫혀있어도 동작하며 다음 실행 시 자동 인덱싱된다.

### 보완: Local REST API (Obsidian 실행 중일 때)

```
GET   /vault/{path}   # 노트 읽기
POST  /vault/{path}   # 노트 생성
PATCH /vault/{path}   # 노트 일부 업데이트
```

포트 `27124` (기본값), `config.yaml`에 API Key 설정.

### 우선순위 로직

```python
if obsidian_api_is_running():
    use_local_rest_api()
else:
    write_file_directly()
```

---

## 자동화 워크플로우

### 상시 실행 (`watcher_daemon.py`)

```
watcher_daemon.py
  ├── FileWatcher (watchdog)       → file_events 테이블 기록
  ├── ClipboardMonitor             → 클립보드 변경 감지
  ├── WindowPoller (30초 간격)     → 활성 앱/창 기록
  └── Scheduler
        └── 매일 23:55            → daily_summary.py 호출
```

### 이벤트 트리거

```
Claude Code 세션 종료 감지
  └── claude_code.py    → ~/.claude/projects/ 파싱
                        → ai_prompts 테이블 upsert
                        → AI Session Note 생성
                        → Daily Note에 링크 추가
```

### 일일 요약 생성

```
daily_summary.py [--date YYYY-MM-DD] [--dry-run]
  ├── worklog.db 쿼리 (해당 날짜)
  ├── {vault}/Daily/YYYY-MM-DD.md  생성 또는 업데이트
  ├── {vault}/Projects/*.md        최근 활동 섹션 업데이트
  └── {vault}/AI-Sessions/         노트 생성
```

---

## 구현 단계 (Phase)

상세 내용은 **PLAN.md** 참조. 현재 진행 상황은 **PROGRESS.md** 참조.

### Phase 1 - 기반 구축

- [ ] `config.yaml` 로더 (`scripts/config.py`)
- [ ] SQLite 스키마 초기화 (`scripts/init_db.py`)
- [ ] 볼트 초기 셋업 (`scripts/setup_vault.py`)
- [ ] 프로젝트 자동 감지 (`scripts/processors/project_mapper.py`)
- [ ] Claude Code 대화 기록 파서 (`scripts/collectors/claude_code.py`)
- [ ] Daily Note 생성기 (`scripts/obsidian/daily_note.py`)
- [ ] AI Session Note 생성기 (`scripts/obsidian/ai_session.py`)

### Phase 2 - 자동 수집 데몬

- [ ] 파일시스템 감시 (`scripts/collectors/file_watcher.py`)
- [ ] 활성 창 폴러 (`scripts/collectors/window_poller.py`)
- [ ] 브라우저 히스토리 수집 (`scripts/collectors/browser_history.py`)
- [ ] 데몬 통합 및 스케줄러 (`scripts/watcher_daemon.py`)
- [ ] OS별 자동 시작 등록 스크립트

### Phase 3 - VSCode 연동

- [ ] Wakapi 연동 또는 VSCode Extension 개발
- [ ] Git post-commit hook 연동

### Phase 4 - 웹 AI 도구 연동

- [ ] 로컬 수신 서버 (`scripts/server.py`)
- [ ] 브라우저 확장 프로그램 (Manifest V3)
- [ ] ChatGPT 공식 내보내기 파서

### Phase 5 - 고도화

- [ ] 주간/월간 요약 노트 자동 생성
- [ ] 민감 정보 필터링 강화
- [ ] 통계 대시보드 (Datasette)

---

## 중요 경로

### Windows

```
이 프로젝트:       C:/MYCLAUDE_PROJECT/daytracker-vault/
SQLite DB:        {이 프로젝트}/data/worklog.db
볼트 (예시):       C:/Users/{username}/Obsidian/DayTracker/
Claude Code:      C:/Users/{username}/.claude/projects/
Chrome 히스토리:   C:/Users/{username}/AppData/Local/Google/Chrome/User Data/Default/History
VSCode 로그:       C:/Users/{username}/AppData/Roaming/Code/logs/
```

### Mac

```
볼트 (예시):       ~/Obsidian/DayTracker/
Claude Code:      ~/.claude/projects/
Chrome 히스토리:   ~/Library/Application Support/Google/Chrome/Default/History
VSCode 로그:       ~/Library/Application Support/Code/logs/
```

---

## 개발 규칙

### 코드 작성 원칙

- 모든 스크립트는 `scripts/` 하위 모듈별로 분리
- 각 수집기는 독립 실행 가능: `python -m scripts.collectors.claude_code`
- 설정값은 `config.yaml` 또는 `.env`로 분리, 하드코딩 금지
- 볼트 경로는 반드시 `config.yaml`의 `vault_path`에서 읽어야 함
- 수집 실패 시 예외를 던지지 말고 로그 기록 후 계속 진행
- 중복 이벤트 방지: `session_id` 또는 `timestamp + file_path` 기준 upsert

### 노트 생성 원칙

- 기존 Daily Note가 있으면 덮어쓰지 않고 섹션별로 병합 업데이트
- 모든 노트는 YAML frontmatter 포함
- 프로젝트명은 `{vault}/Projects/` 파일명과 반드시 일치
- 타임스탬프는 로컬 시간 기준

### 보안 원칙

- 클립보드, 프롬프트 내용에서 API키/비밀번호 패턴 자동 마스킹
- `.env`, `config.yaml`은 `.gitignore`에 포함 (`config.example.yaml`만 커밋)
- `data/` 폴더(SQLite DB)는 `.gitignore`에 포함

### 테스트 원칙

- 각 수집기는 `--dry-run` 옵션 지원 (DB/파일 쓰기 없이 파싱만 확인)
- 노트 생성기는 `--date YYYY-MM-DD` 옵션으로 특정 날짜 재생성 가능
- `setup_vault.py`는 `--vault-path` 없이 실행 시 인터랙티브 입력 안내

---

## 참고 도구 및 리소스

| 도구 | 용도 | 비고 |
|------|------|-----|
| ActivityWatch | 앱/창 시간 추적 | 선택적 연동 |
| Screenpipe | 화면 OCR 전체 캡처 | 선택적 연동 |
| Wakapi | 코딩 시간 추적 (VSCode 플러그인) | Phase 3 |
| Datasette | SQLite 웹 UI | Phase 5 |
| sqlite-utils | SQLite CLI/Python 유틸 | Phase 1~ |
| Obsidian Local REST API 플러그인 | 노트 CRUD API | Phase 1~ |
