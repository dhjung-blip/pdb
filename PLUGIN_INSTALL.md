# PDB Plugin 설치 가이드

`/pdb` Skill을 Claude Code에서 사용하기 위한 설치·검증 절차. 사내 marketplace 또는 직접 clone 모두 지원합니다.

---

## 사전 조건

| 항목 | 권장 |
|---|---|
| OS | macOS / Linux (Windows는 WSL 권장) |
| Python | 3.11 이상 |
| Claude Code | 최신 (`/plugin` 명령 지원) |
| 네트워크 | UniProt/RCSB/GPCRdb/PubChem/ChEMBL/Europe PMC/AlphaFold/OpenTargets 접근 |
| 디스크 | venv 포함 약 200MB |

`uv`가 설치되어 있으면 자동으로 사용하지만 필수는 아닙니다.

---

## 방법 1 — 사내 Marketplace 등록 (권장)

팀 단위 배포 시 가장 깔끔한 방식. 한 번 등록하면 팀원 누구나 `/plugin install pdb`로 받습니다.

### 1-1. Marketplace 등록 (사용자별 1회)

```bash
# Claude Code 안에서
/plugin marketplace add https://github.com/dhjung-blip/pdb.git
```

### 1-2. Plugin 설치

```bash
/plugin install pdb@pdb
```

설치 후 plugin 파일은 `~/.claude/plugins/cache/pdb/pdb/<version>/`에 캐시됩니다.

### 1-3. 가상환경 부트스트랩 (최초 1회)

Claude Code는 plugin install 시 자동으로 Python venv를 만들지 않습니다. plugin 디렉토리에서 `setup.sh`를 한 번 실행하세요.

```bash
PLUGIN_DIR=$(ls -d ~/.claude/plugins/cache/pdb/pdb/*/ | tail -1)
cd "$PLUGIN_DIR"
bash setup.sh
```

`setup.sh`는 `${CLAUDE_PLUGIN_DATA}/.venv`에 가상환경을 만들고 의존성을 설치합니다 (plugin update 후에도 유지됨).

### 1-4. 동작 확인

```bash
# Claude Code 안에서
/pdb search EGFR
```

또는 채팅에 "EGFR 구조 분석해줘" 같은 자연어 입력 — Skill의 자동 트리거가 작동합니다.

### 1-5. 업데이트

```bash
/plugin marketplace update
/plugin install pdb@pdb  # 새 버전으로 갱신
# .venv는 ${CLAUDE_PLUGIN_DATA}에 있으므로 그대로 유지됨
```

만약 의존성이 변경된 새 버전이면 한 번만 다시:

```bash
cd "$PLUGIN_DIR" && bash setup.sh
```

---

## 방법 2 — Git Clone 직접 사용 (개발자 모드)

Skill을 수정하면서 쓰거나, marketplace 없이 단일 사용자가 시도할 때.

```bash
# 1) 클론
git clone https://github.com/dhjung-blip/pdb.git ~/work/PDBMCP
cd ~/work/PDBMCP

# 2) 가상환경 + 의존성
bash setup.sh          # .venv 와 .claude/skills/pdb 심볼릭 링크 생성

# 3) Claude Code를 이 디렉토리로 열어 /pdb 사용
cd ~/work/PDBMCP
claude
```

이 경우 `.venv`는 저장소 안에 만들어지고, `.claude/skills/pdb`가 `skills/pdb`로 가는 심볼릭 링크가 자동 생성되어 project-level Skill로도 인식됩니다.

---

## 방법 3 — 압축본 배포 (오프라인 환경)

내부망에 git이 없거나 zip으로 배포할 때.

```bash
# 배포자: zip 생성 (.venv 제외)
cd ~/work && zip -r PDBMCP.zip PDBMCP \
    -x 'PDBMCP/.venv/*' 'PDBMCP/__pycache__/*' \
       'PDBMCP/output/*' 'PDBMCP/.pytest_cache/*'

# 받는 쪽
cd ~/work && unzip PDBMCP.zip
cd PDBMCP && bash setup.sh
```

이후는 방법 2와 동일.

---

## 검증 체크리스트

설치 후 다음 5개 명령이 모두 정상 동작하면 완료:

```bash
# Plugin 모드라면 PLUGIN_DIR= 경로로, project 모드라면 저장소 루트에서
cd "$PLUGIN_DIR_OR_REPO"

# 1) 의존성 import
.venv/bin/python -c "import httpx, openpyxl, pydantic, mcp; print('deps OK')"
# 또는 plugin 모드: "${CLAUDE_PLUGIN_DATA}/.venv/bin/python" -c "..."

# 2) cli 기본
.venv/bin/python cli.py --help | head

# 3) 단건 조회 (네트워크 필요)
bash skills/pdb/scripts/pdb detail 7WC7 --json | head -5
# 기대: {"tool":"get_pdb_detail","success":true,...}

# 4) GPCR full path (1~2분)
bash skills/pdb/scripts/pdb search HTR2A --json | jq '.summary'
# 기대: is_gpcr=true, fetched_count≈32, gpcrdb_count>10

# 5) (선택) cli 진입점 직접 확인
.venv/bin/python cli.py compare --targets EGFR,HER2 --md | head -10
# 기대: 2행짜리 마크다운 비교 표
```

> 단위 테스트(`pytest tests/`)는 사내 원본 PDBMCP 저장소에서만 가능합니다 — Plugin 배포본에는 `tests/`가 포함되어 있지 않습니다.

Claude Code 안에서:

```
사용자: EGFR 구조 분석해줘
→ /pdb search EGFR 자동 호출 → 12컬럼 비GPCR 표

사용자: HTR2A Antagonist 고해상도만
→ /pdb search HTR2A --ligand-modality Antagonist --max-resolution 2.5 자동 호출
```

---

## 트러블슈팅

### `[pdb] venv가 아직 만들어지지 않았습니다`
→ plugin 디렉토리(또는 저장소 루트)에서 `bash setup.sh` 1회 실행.

### `Python 3.11 이상이 필요합니다`
→ macOS: `brew install python@3.11`
→ Ubuntu: `sudo apt install python3.11 python3.11-venv`

### Plugin update 후 cli가 안 보인다
→ `${CLAUDE_PLUGIN_ROOT}`는 버전마다 새 경로로 바뀝니다. `${CLAUDE_PLUGIN_DATA}/.venv`는 유지되지만, cli.py / tools/ 등 코드는 새 버전 디렉토리에 있습니다. 셸 래퍼는 자동으로 새 경로를 잡으므로 추가 작업 불필요. 단 의존성이 변경된 버전이면 `bash setup.sh` 재실행.

### Skill이 `/pdb` 명령으로 인식되지 않는다
→ Plugin install이 정상인지 확인: `/plugin list`
→ Project mode라면 `.claude/skills/pdb` 심볼릭 링크가 존재하는지 확인: `ls -l .claude/skills/`
→ Claude Code 재시작.

### API 일부 호출 실패
→ `summary.gpcrdb_count`가 0이거나 부분 실패 시 cli가 stderr에 사유를 남깁니다. 잠시 후 재시도. GPCRdb/PubChem 일시 장애는 일반적입니다.

---

## MCP 서버와의 관계

이 Skill과 MCP 서버는 같은 `tools/` Python 모듈을 공유하지만 진입점이 분리되어 있습니다.

- Claude Desktop 사용자 → MCP 서버 (`server.py`, `.mcpb` 패키지, 사내 SSE/HTTP 배포)
- Claude Code 사용자 → 이 Plugin (`/pdb` 슬래시 명령)

Plugin 배포본의 `server.py`는 `cli.py`가 헬퍼 함수를 재사용하기 위해 import용으로만 포함됩니다 — Plugin 사용자가 직접 호출하지는 않습니다. MCP 서버 빌드(`.mcpb`)·배포 자산(`Docker`)은 이 GitHub repo에 포함되어 있지 않으며 사내 원본 PDBMCP 저장소에 있습니다. 자세한 내용은 [README_MCP.md](README_MCP.md) 참조.

---

## 보안 / 데이터 정책

- 모든 외부 API는 **anonymous 호출**(API 키 불필요). 사용자의 어떤 식별 정보도 전송하지 않습니다.
- `output/` 디렉토리에 Excel 파일이 생기지 않습니다 (xlsx 스킬이 별도 위치에 생성).
- Plugin 코드는 사용자 머신의 임의 명령을 실행할 수 있는 `Bash` 도구를 사용합니다 (셸 래퍼 호출 목적). `allowed-tools`로 `Bash`, `Read`만 허용되어 있습니다.
