# MCP 서버 진입점 — Claude Desktop / 사내 SSE·HTTP

이 문서는 **Claude Desktop** 또는 **사내 SSE/Streamable HTTP 서버**로 PDB 리서치 도구를 사용하는 분들을 위한 가이드입니다. Claude Code Plugin 사용자는 [README.md](README.md)를 참조하세요.

> **중요 — 빌드 자산은 이 GitHub repo에 포함되어 있지 않습니다.** Plugin 배포본을 가볍게 유지하기 위해 `mcpb/` (.mcpb 빌드), `deploy-server/` (Docker), `dist/` (빌드 산출물), `SYSTEM_PROMPT.md` (Claude Desktop Custom Instructions), `CLAUDE.md` (사양) 등은 git 추적에서 제외했습니다. 이 자산들은 **사내 원본 PDBMCP 저장소**에 있고 사용자 머신의 `~/Desktop/PDBMCP/`(또는 사내 파일 서버)에서 받습니다.

---

## 1. 진입점 종류

| 진입점 | 대상 사용자 | 전송 방식 | 시작 명령 |
|---|---|---|---|
| **stdio** | 개별 연구원 (Claude Desktop) | stdin/stdout | `python server.py --transport stdio` |
| **SSE** | 사내 서버 + 다수 사용자 (레거시) | HTTP SSE | `python server.py --transport sse --host 0.0.0.0 --port 8000` |
| **Streamable HTTP** | 사내 서버 + 다수 사용자 (권장) | HTTP streamable | `python server.py --transport streamable-http --host 0.0.0.0 --port 8000` |

세 모드 모두 같은 13개 도구를 노출합니다.

---

## 2. Claude Desktop 사용자 — `.mcpb` 한 번에 설치

### 2-1. `.mcpb` 파일 받기

| 플랫폼 | 사내 파일 서버 / 원본 PDBMCP 경로 |
|---|---|
| macOS (Apple Silicon) | `~/Desktop/PDBMCP/dist/pdb-mcp-server-macos-arm64.mcpb` (~55 MB) |
| Windows (x64) | `~/Desktop/PDBMCP/dist/pdb-mcp-server-win-x64.mcpb` (~26 MB) |

GitHub repo에는 없으므로 **사내 원본 PDBMCP 저장소** 또는 **사내 파일 서버**에서 받으세요.

### 2-2. Claude Desktop에 설치

1. Claude Desktop 실행 → **Settings → Extensions → "Install Extension..."** 클릭 → `.mcpb` 파일 선택 (또는 Claude Desktop 창에 드래그&드롭).
2. 설치 화면에서 **Excel 저장 폴더** 확인 (기본 `~/Documents/PDBMCP`).
3. Claude Desktop 완전 종료(macOS ⌘Q / Windows 트레이 Quit) → 재실행.
4. 채팅창 — "EGFR 구조 찾아줘" → 결과 + Excel 저장 확인.

> 이전에 `claude_desktop_config.json`으로 `pdb-research`를 수동 등록해뒀다면 그 항목을 제거하거나 비워둔 뒤 `.mcpb`를 설치하세요 (중복 등록 방지). Windows 설정 파일 경로는 `%APPDATA%\Claude\claude_desktop_config.json`.

### 2-3. Custom Instructions (선택, 권장)

자연어 한 마디로 자동 판단이 되려면 `SYSTEM_PROMPT.md`를 Claude Desktop **Settings → Profile → Custom Instructions**에 붙여넣으세요. `SYSTEM_PROMPT.md`는 사내 원본 PDBMCP 저장소에 있습니다.

설정 후 효과:
- 수용체 패밀리 자동 확장 ("세로토닌" → HTR2A/2B/2C)
- 필터 키워드 자동 인식 ("고해상도", "Antagonist만")
- 결과 요약 + 후속 질문 제안

---

## 3. 사내 서버 배포 — Docker SSE / Streamable HTTP

### 3-1. 필요 자산

사내 원본 PDBMCP 저장소의 `deploy-server/` 디렉토리에 다음이 있습니다.

```
deploy-server/
├── Dockerfile               Python 3.11 + uv + 의존성
├── docker-compose.yml       서비스 정의 + healthcheck
├── apache/pdb-mcp.conf      리버스 프록시 (선택)
├── deploy.sh                빌드·기동 스크립트
└── README.md                상세 운영 가이드
```

GitHub에는 포함되어 있지 않으므로 사내 원본에서 받아 운영 서버에 배치합니다.

### 3-2. 빠른 기동

```bash
cd ~/Desktop/PDBMCP/deploy-server   # 사내 원본 PDBMCP
docker compose up -d
curl http://localhost:8000/health   # {"status":"ok",...}
```

Claude Desktop에서 원격 서버 연결:

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "pdb-research": {
      "url": "http://<사내-서버>:8000/mcp",
      "transport": "streamable-http"
    }
  }
}
```

자세한 보안 설정(Allowed hosts/origins, Apache 리버스 프록시)은 `deploy-server/README.md` 참조.

---

## 4. 제공 도구 (MCP Tools)

Plugin이 사용하는 cli와 같은 13개 도구지만 MCP 프로토콜로 노출됩니다.

| 도구 | 설명 |
|---|---|
| `search_target` | 타깃 1개 → PDB 구조 테이블 + Excel |
| `search_family` | 여러 타깃(수용체 패밀리) → Summary + 타깃별 시트 통합 Excel |
| `get_pdb_detail` | PDB ID 1개 상세 (해상도·실험방법·논문 전체) |
| `compare_targets` | 여러 타깃 구조 수·해상도·최신 구조 비교 |
| `get_ligand_detail` | 화합물 식별자·물성·Phase (PubChem/ChEMBL/IUPHAR) |
| `get_target_bioactivities` | Ki/Kd/IC50 활성 데이터 (ChEMBL/IUPHAR) |
| `get_paper_abstract` | PMID/DOI 단일 논문 메타 + 초록 |
| `search_papers` | Europe PMC 논문 검색 |
| `get_sequence_region` | UniProt 서열 + feature |
| `get_natural_variants` | UniProt 자연 변이 (SNP/질환) |
| `get_binding_site` | PDB 결합부위 잔기 |
| `get_alphafold_model` | AlphaFold DB 예측 구조 |
| `get_target_intelligence` | OpenTargets 질환/약물 |

각 도구 description에는 수용체 별칭 사전·패밀리 확장·필터 자동 설정 규칙이 내장되어 있어, Claude가 자연어 입력만으로 올바른 파라미터를 구성해 호출합니다.

---

## 5. 워크플로우

```
타겟 이름 (예: "EGFR")
   ↓  UniProt Search API      → UniProt Accession (P00533)
   ↓  UniProt Entry API       → PDB ID 목록 ([7T9K, 6JRH, ...])
   ↓  GPCRdb API              → GPCR 여부 + (GPCR이면) State/Ligand 등
   ↓  RCSB PDB GraphQL API    → Resolution / Released Date / Method / Citation (병렬)
   ↓  GPCRdb 병합 + 정렬      → 결과 테이블 + Excel
```

---

## 6. Excel 출력 규격

`.mcpb` / SSE / Streamable HTTP 모두 Claude가 자신의 xlsx 스킬로 직접 `.xlsx`를 생성합니다 (서버 자체 저장은 macOS 샌드박스에서 실패 가능). 컬럼·서식·색상 규격은 Plugin의 [skills/pdb/references/excel_spec.md](skills/pdb/references/excel_spec.md)가 단일 원천입니다 — MCP / Plugin 모두 같은 규격을 따릅니다.

핵심 요약:
- **파일명**: `{유전자명}_{Accession}_structures_{YYYYMMDD}.xlsx` 또는 `{family}_family_structures_{YYYYMMDD}.xlsx`
- **시트**: `Summary` + (단일=`Structures` / 패밀리=타깃별 시트)
- 비GPCR 12컬럼 / GPCR 14컬럼 (State·Ligand·Modality·Fusion·Antibody 등 추가)
- State 셀 배경: Active=초록 / Inactive=빨강 / Intermediate=노랑
- Ligand modality 셀 배경: Agonist=초록 / Antagonist=빨강 / Inverse agonist=주황
- ACS 스타일 Citation 자동 생성, PDB ID/DOI/PMID 하이퍼링크

---

## 7. 개발자용 — 소스에서 직접 실행 / `.mcpb` 재빌드

이 GitHub repo 자체에는 빌드 자산이 없습니다. 사내 원본 PDBMCP 저장소에서 진행합니다.

```bash
# 환경 설정
cd ~/Desktop/PDBMCP
uv venv --python 3.11
uv pip install -r requirements.txt

# 직접 실행 (stdio)
.venv/bin/python server.py --transport stdio

# .mcpb 빌드
./mcpb/build_mcpb.sh                # 호스트 플랫폼 자동
./mcpb/build_mcpb.sh macos-arm64    # 명시 지정
./mcpb/smoke_test.sh dist/pdb-mcp-server-macos-arm64.mcpb

# 테스트
.venv/bin/python -m pytest -m "not network"   # 오프라인 단위 테스트만
.venv/bin/python -m pytest                    # 전체 (실제 API 호출, ~46s)
```

빌드 산출물: `dist/pdb-mcp-server-<plat>.mcpb` (~55 MB macOS / ~26 MB Windows).

---

## 8. 두 진입점의 관계 — 한 번 더

| 항목 | Claude Code Plugin | MCP 서버 |
|---|---|---|
| 진입점 | `/pdb` 슬래시 명령 + Bash 셸 래퍼 | stdio / SSE / Streamable HTTP |
| 어디서 받나 | GitHub marketplace (이 repo) | 사내 원본 PDBMCP / 파일 서버 |
| 공유 코드 | `tools/`, `models/`, `server.py` | 동일 |
| Excel | LLM이 xlsx 스킬로 생성 | LLM이 xlsx 스킬로 생성 |
| 자동 판단 | `skills/pdb/SKILL.md` + references/ | `SYSTEM_PROMPT.md` (Custom Instructions) |

두 진입점은 같은 비즈니스 로직(`tools/*`)을 공유하므로 어느 쪽으로 호출하든 동일한 결과가 나옵니다.

---

## 9. 문의

사내 신약연구소 AI팀: aidrugdev2.namuict@gmail.com
