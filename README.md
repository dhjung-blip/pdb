# PDB 연구 자동화 MCP 서버

신약 연구원이 **타겟 단백질 이름**을 입력하면, 해당 단백질의 모든 PDB 실험 구조를
자동으로 조회하여 **PDB ID / Resolution / Released Date / 연결 논문**을 정리한
테이블을 반환하는 MCP 서버입니다.

**GPCR 계열 타깃**이면 GPCRdb를 추가 연동하여 **State / Ligand / Ligand modality /
Signaling protein / Fusion protein / Antibody / Preferred chain** 컬럼까지 포함한
확장 테이블을 자동으로 생성합니다.

배포는 **`.mcpb` 한 번에 끝납니다** — 연구원은 Claude Desktop에서 `.mcpb` 파일 하나만
설치하면 Docker·Python·venv 없이 바로 사용할 수 있습니다.

---

## 빠른 설치 (.mcpb)

| 플랫폼 | 번들 파일 |
|---|---|
| **macOS (Apple Silicon)** | [dist/pdb-mcp-server-macos-arm64.mcpb](dist/pdb-mcp-server-macos-arm64.mcpb) (~55 MB) |
| **Windows (x64)** | [dist/pdb-mcp-server-win-x64.mcpb](dist/pdb-mcp-server-win-x64.mcpb) (~26 MB) |

### 공통 설치 절차

1. 자기 플랫폼에 해당하는 `.mcpb` 파일을 받습니다.
2. Claude Desktop 실행 → **Settings → Extensions → "Install Extension..."** 클릭
   → `.mcpb` 파일 선택 (또는 Claude Desktop 창으로 드래그 & 드롭).
3. 설치 화면에서 **Excel 저장 폴더**를 확인 (기본값 `~/Documents/PDBMCP`).
4. Claude Desktop을 완전히 종료(macOS: ⌘Q / Windows: 시스템 트레이에서 Quit)했다 다시 실행합니다.
5. 채팅창에서 `"EGFR 구조 찾아줘"` → 결과 표시 + Excel 파일 저장 확인.

> 기존에 `claude_desktop_config.json` 으로 `pdb-research` 를 수동 등록해뒀다면
> 그 항목을 제거하거나 비워둔 뒤 .mcpb 를 설치하세요(중복 등록 방지).
> Windows 의 설정 파일 경로는 `%APPDATA%\Claude\claude_desktop_config.json` 입니다.

### 다른 플랫폼

`mcpb/build_mcpb.sh <플랫폼>` 으로 빌드할 수 있습니다 (`macos-x64`,
`linux-x64`, `linux-arm64`). 자세한 내용은
[mcpb/](mcpb/) 와 [개발 / 빌드](#개발--빌드) 참조.

---

## 워크플로우

```
타겟 이름 (예: "EGFR")
   ↓  UniProt Search API      → UniProt Accession (P00533)
   ↓  UniProt Entry API       → PDB ID 목록 ([7T9K, 6JRH, ...])
   ↓  GPCRdb API              → GPCR 여부 확인 + (GPCR이면) State/Ligand 등 메타데이터
   ↓  RCSB PDB GraphQL API    → Resolution / Released Date / Method / Citation (병렬)
   ↓  GPCRdb 병합 + 정렬       → 결과 테이블 반환 (+ Excel 저장)
```

GPCR 타깃은 GPCRdb 수록 구조를 테이블 상단으로 모아 보여줍니다. GPCRdb 조회가
실패하더라도 기본 PDB 데이터는 항상 반환됩니다(선택적 강화).

---

## 제공 도구 (MCP Tools)

| 도구 | 설명 |
|------|------|
| `search_target` | 타겟 1개로 전체 워크플로우를 실행하고 PDB 구조 테이블 + Excel 반환 |
| `search_family` | 여러 타겟(수용체 패밀리)을 한 번에 검색하고 Summary + 타겟별 시트로 구성된 통합 Excel 저장 |
| `get_pdb_detail` | 특정 PDB ID 하나의 상세 정보(해상도·실험방법·논문 전체) 조회 |
| `compare_targets` | 여러 타겟을 검색하여 구조 수 / 최고 해상도 / 최신 구조 요약 비교 |

`search_target`/`search_family` 는 후처리 필터 파라미터를 지원합니다 —
`max_resolution`(해상도 상한), `min_year`(공개 연도 하한), `method_filter`(실험방법),
`state_filter`(GPCR State), `ligand_modality_filter`(리간드 modality).
정렬은 `sort_by`로 `date` / `resolution` / `state_then_date`(GPCR 기본) 중 선택합니다.

각 도구의 description에는 수용체 별칭 사전·패밀리 확장·필터 자동 설정 규칙이 내장되어
있어, Claude가 자연어 입력만으로 올바른 파라미터를 구성해 호출합니다.

---

## Excel 출력

`.mcpb` 배포에서 **Excel 파일은 Claude 가 자신의 xlsx 스킬로 직접 생성**해 응답에
첨부합니다 (MCP 서버는 데이터 테이블만 반환). Claude Desktop 의 macOS 샌드박스가
서버 프로세스의 `~/Documents/` 쓰기를 차단하기 때문에 서버측 저장 경로는 안정적이지
않습니다. 대신 [SYSTEM_PROMPT.md](SYSTEM_PROMPT.md) 의 "Excel 출력 표준" 섹션이
파일명·시트 구성·12/14 컬럼·조건부 색상·ACS 인용·하이퍼링크 규격을 명시하므로,
연구원 모두가 동일한 형식의 .xlsx 를 받습니다.

- **파일명**:
  - 단일 타깃: `{유전자명}_{Accession}_structures_{YYYYMMDD}.xlsx`
  - 패밀리: `{family_label}_family_structures_{YYYYMMDD}.xlsx`
- **시트 구성**: `Summary` (UniProt·GPCR 여부·구조 수·State 집계·조회 일시)
  + 패밀리는 타깃별 시트, 단일은 `Structures` 시트
- 비GPCR 타깃: 기본 12 컬럼 / GPCR 타깃: State·Ligand·Modality·Fusion·Antibody
  등 14 컬럼 확장
- GPCR 시트의 `State` 컬럼은 Active=초록 / Inactive=빨강 / Intermediate=노랑,
  `Ligand modality` 컬럼은 Agonist=초록 / Antagonist=빨강 / Inverse agonist=주황
  으로 셀 배경 색상이 적용됩니다.
- `Citation (ACS)` 컬럼: ACS 스타일 전체 인용문 (저자; 제목. 저널 연도, 권, 페이지. DOI)
- PDB ID / DOI / PMID 셀은 하이퍼링크로 연결됩니다.
- 정확한 컬럼 순서·서식·색상 코드는 [SYSTEM_PROMPT.md](SYSTEM_PROMPT.md) 참고.

> **서버측 저장(advanced)**: 도구 호출 시 `export_excel: true` 를 명시 전달하면
> 서버가 `~/Documents/PDBMCP/` (또는 user_config 폴더) 에 저장을 시도합니다.
> Claude Desktop 샌드박스 환경에서는 실패할 수 있어 권장하지 않습니다.

---

## 사용 예시

연동 후 채팅창에서 바로 사용:

```
"EGFR 구조 찾아줘"                  → 비GPCR 기본 테이블 + Excel
"HTR2A 구조 찾아줘"                 → 🧬 GPCR 확장 테이블 (State/Ligand/Modality 등)
"HTR2A Antagonist 고해상도만"        → modality=Antagonist + 해상도≤2.5Å 필터 자동 적용
"세로토닌 수용체 구조 정리해줘"        → HTR2A/HTR2B/HTR2C 패밀리 통합 Excel (search_family)
"HTR2A, HTR2B, HTR2C 구조 수 비교해줘"
"7WC7 상세 정보 알려줘"             → GPCR 구조 상세
```

---

## 프롬프트 자동화 (Custom Instructions)

연구원이 프롬프트 형식을 외우지 않아도 자연어 한 마디로 올바른 표가 나오도록,
[SYSTEM_PROMPT.md](SYSTEM_PROMPT.md)를 Claude Desktop의 Custom Instructions로 설정합니다.

1. Claude Desktop → **Settings → Profile → Custom Instructions** (또는 Personalization)
2. [SYSTEM_PROMPT.md](SYSTEM_PROMPT.md) 파일 내용을 전체 복사해 붙여넣기
3. 저장

설정하면 수용체 패밀리 자동 확장(세로토닌 → HTR2A/2B/2C 등), 필터 키워드 자동 인식
("고해상도", "Antagonist만" 등), 결과 요약·후속 질문 제안이 자동 적용됩니다.

> 별칭 사전·패밀리 확장·필터 규칙은 MCP 도구 description에도 내장되어 있어,
> Custom Instructions를 설정하지 않아도 기본적인 자동 판단은 동작합니다.

---

## 개발 / 빌드

소스에서 직접 실행하거나 `.mcpb` 를 재빌드할 때만 필요합니다.

### 환경 설정 (Python 3.11+)

```bash
cd /Users/jungdohoon/Desktop/PDBMCP
uv venv --python 3.11
uv pip install -r requirements.txt
```

### 직접 실행 (stdio)

```bash
.venv/bin/python server.py --transport stdio
```

### .mcpb 빌드

```bash
./mcpb/build_mcpb.sh                # 호스트 플랫폼 (현재 macOS-arm64 등)
./mcpb/build_mcpb.sh macos-arm64    # 명시 지정
./mcpb/smoke_test.sh dist/pdb-mcp-server-macos-arm64.mcpb
```

- 산출물: [dist/pdb-mcp-server-<plat>.mcpb](dist/)
- 번들 구성: `manifest.json` + `server/` (소스 + `lib/` 의존성) + `runtime/python/` (CPython 3.11.15 from python-build-standalone, stripped variant)
- 크기: ~55 MB (macOS-arm64) / ~26 MB (Windows x64)
- 제외 패턴: [.mcpbignore](.mcpbignore) (참조용 — 빌드 스크립트가 동일 패턴으로 stdlib idlelib/tkinter/turtledemo 등 + dist-info 메타 제거)

### 테스트

```bash
pytest -m "not network"   # 오프라인 단위 테스트만
pytest                    # 전체 (실제 UniProt / RCSB API 호출)
```

---

## 디렉토리 구조

```
PDBMCP/
├── README.md            이 파일
├── SYSTEM_PROMPT.md     Claude Desktop Custom Instructions 용 지침
├── CLAUDE.md            구현 지시서
├── pyproject.toml       프로젝트 메타데이터 / 의존성
├── requirements.txt     pip 설치용 의존성 목록
├── conftest.py          pytest sys.path 설정
├── server.py            MCP 서버 진입점 (stdio / sse / streamable-http)
├── tools/               UniProt / PDB / GPCRdb / Parser / Export 모듈
├── models/              Pydantic 데이터 모델
├── tests/               단위 테스트
├── mcpb/                .mcpb 패키징
│   ├── manifest.json    번들 메타 + tools + user_config
│   ├── build_mcpb.sh    빌드 스크립트 (PBS Python 다운로드 + 의존 설치 + zip)
│   └── smoke_test.sh    빌드된 .mcpb 의 MCP 핸드셰이크 검증
├── dist/                빌드 산출물 (*.mcpb)
└── deploy-server/       서버 배포 자산 (Docker / Apache, 선택/레거시 — README 참조)
```

---

## 서버 배포 모드 (선택)

`.mcpb` 가 기본 배포 경로지만, 중앙 서버 + 리버스 프록시 + Streamable HTTP 로 운영하고
싶다면 [deploy-server/](deploy-server/) 하위에 Docker / docker-compose / Apache 설정이
있습니다. 자세한 내용은 [deploy-server/README.md](deploy-server/README.md) 를 참조하세요.

---

## GPCRdb 연동 참고

- GPCR 여부는 UniProt entry_name(예: `5HT2A_HUMAN`)을 소문자로 변환해 GPCRdb
  `protein/{slug}/` 엔드포인트로 확인합니다(404면 비GPCR).
- 실제 GPCRdb structure API는 `Fusion protein` / `Antibody`를 별도 필드로 제공하지
  않으므로, 해당 두 컬럼은 RCSB polymer entity 설명 + PDB entry 제목 패턴 매칭
  ([tools/parser.py](tools/parser.py))으로 채웁니다. `Signaling protein`은 G단백질/arrestin
  복합체 구조에 한해 GPCRdb가 제공합니다.
- GPCRdb에 아직 수록되지 않은 최신 구조는 확장 컬럼이 `-`로 표시되며, 테이블에서는
  GPCRdb 수록 구조 다음 순서로 정렬됩니다.
