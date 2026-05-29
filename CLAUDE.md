# PDB 연구 자동화 MCP 서버 — Claude Code 구현 지시서

## 프로젝트 개요

신약 연구원이 **타겟 단백질 이름**을 입력하면, 해당 단백질의 모든 PDB 실험 구조를 자동으로 조회하여
**PDB ID / Resolution / Released Date / 연결 논문** 을 정리한 테이블을 반환하는 MCP 서버를 구축한다.

GPCR 계열 타깃의 경우 **GPCRdb API**를 추가 연동하여
**State / Ligand / Ligand modality / Signaling protein / Fusion protein / Antibody / Preferred chain**
컬럼까지 포함한 확장 테이블을 자동으로 생성한다.

Claude Desktop에 MCP 서버로 연결되어, 연구원은 Claude 채팅창에서 자연어로 바로 사용한다.

---

## 핵심 워크플로우

```
[연구원 입력] 타겟 이름 (예: "EGFR", "TP53", "CDK2")
      ↓
[STEP 1] UniProt Search API → UniProt Accession 번호 확인
         예) EGFR → P00533
      ↓
[STEP 2] UniProt Entry API → 해당 단백질의 PDB 구조 목록 조회
         예) P00533 → [7T9K, 6JRH, 5XGM, ...]
      ↓
[STEP 3] RCSB PDB API → 각 PDB ID별 메타데이터 병렬 조회
         - Resolution (해상도, Å)
         - Released Date (최초 공개일)
         - Experimental Method (X-ray / Cryo-EM / NMR 등)
         - Citation (논문 제목, 저자, 저널, 연도, DOI, PMID)
      ↓
[STEP 4] 결과를 정렬하여 반환
         - 기본 정렬: Released Date 내림차순 (최신순)
         - Excel 파일(.xlsx) 저장 옵션 제공
```

---

## 기술 스택

- **언어**: Python 3.11+
- **MCP 프레임워크**: `mcp` (anthropic 공식 SDK, `pip install mcp`)
- **HTTP 클라이언트**: `httpx` (비동기, `pip install httpx`)
- **Excel 출력**: `openpyxl` (`pip install openpyxl`)
- **서버 실행**: stdio transport (Claude Desktop 연동 표준)
- **GPCR 확장**: GPCRdb 공개 REST API (추가 설치 없음, httpx로 호출)

---

## 디렉토리 구조

```
pdb-mcp-server/
├── CLAUDE.md              ← 이 파일
├── README.md              ← 설치 및 사용 방법
├── pyproject.toml         ← 의존성 관리
├── server.py              ← MCP 서버 진입점 (메인 실행 파일)
├── tools/
│   ├── __init__.py
│   ├── uniprot.py         ← UniProt API 클라이언트
│   ├── pdb.py             ← RCSB PDB API 클라이언트
│   ├── gpcrdb.py          ← GPCRdb API 클라이언트 (GPCR 확장)
│   ├── parser.py          ← PDB 제목 파싱 유틸 (Fusion protein, Antibody 추출)
│   └── export.py          ← Excel 출력 유틸리티
├── models/
│   ├── __init__.py
│   └── schemas.py         ← Pydantic 데이터 모델
└── tests/
    ├── test_uniprot.py
    ├── test_pdb.py
    └── test_gpcrdb.py
```

---

## 데이터 모델 (`models/schemas.py`)

```python
from pydantic import BaseModel
from typing import Optional, List, Literal

class Citation(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None   # "Last FM, Last FM, ..." 형태
    journal: Optional[str] = None
    year: Optional[int] = None
    volume: Optional[str] = None    # 저널 권호. 예: "598"
    page_first: Optional[str] = None  # 시작 페이지. 예: "397"
    page_last: Optional[str] = None   # 끝 페이지. 예: "401"
    doi: Optional[str] = None
    pmid: Optional[str] = None

class PDBEntry(BaseModel):
    pdb_id: str                          # 예: "7T9K"
    resolution: Optional[float] = None  # 단위: Å, NMR은 None
    method: Optional[str] = None        # "X-RAY DIFFRACTION", "ELECTRON MICROSCOPY", "SOLUTION NMR"
    released_date: Optional[str] = None # "YYYY-MM-DD" 형태
    title: Optional[str] = None         # PDB entry 제목
    citation: Optional[Citation] = None
    # ── GPCR 확장 필드 (GPCR 타깃일 때만 채워짐, 아니면 None) ──
    pref_chain: Optional[str] = None              # 예: "A"
    state: Optional[str] = None                   # "Active" | "Inactive" | "Intermediate"
    ligand: Optional[str] = None                  # 예: "LSD", "Risperidone"
    ligand_modality: Optional[str] = None         # "Agonist" | "Antagonist" | "Inverse agonist" | "Partial agonist"
    signaling_protein: Optional[str] = None       # "Gq" | "Gi" | "Gs" | "G12/13" 등
    fusion_protein: Optional[str] = None          # "BRIL" | "T4L" | "mT4L" 등
    antibody: Optional[str] = None                # "P2C2-Fab" | "Nb" 등
    is_gpcr: bool = False                         # GPCRdb에서 데이터를 가져왔는지 여부

class UniProtResult(BaseModel):
    accession: str          # 예: "P00533"
    entry_name: str         # 예: "EGFR_HUMAN"
    protein_name: str       # 예: "Epidermal growth factor receptor"
    gene_name: Optional[str] = None
    organism: Optional[str] = None
    pdb_ids: List[str] = []       # UniProt에 등록된 PDB ID 목록
    is_gpcr: bool = False         # GPCRdb에서 GPCR로 인식되는지 여부
    gpcrdb_slug: Optional[str] = None  # GPCRdb 내부 식별자. 예: "5ht2a_human"

class SearchResult(BaseModel):
    query: str
    uniprot: UniProtResult
    structures: List[PDBEntry] = []
    total_count: int = 0
    exported_file: Optional[str] = None  # Excel 저장 시 파일 경로
```

---

## API 레퍼런스

### 1. UniProt Search API

**목적**: 타겟 이름 → UniProt Accession 변환

```
GET https://rest.uniprot.org/uniprotkb/search
    ?query={target}+AND+organism_id:9606+AND+reviewed:true
    &fields=accession,id,protein_name,gene_names,organism_name
    &format=json
    &size=5
```

- `organism_id:9606` : 인간(Homo sapiens) 한정
- `reviewed:true` : Swiss-Prot (검증된 항목) 우선
- 결과 첫 번째 항목을 사용
- `results[0].primaryAccession` → Accession 번호

**파싱 예시**:
```python
data = response.json()
entry = data["results"][0]
accession = entry["primaryAccession"]          # "P00533"
entry_name = entry["uniProtkbId"]              # "EGFR_HUMAN"
protein_name = entry["proteinDescription"]["recommendedName"]["fullName"]["value"]
gene_name = entry["genes"][0]["geneName"]["value"] if entry.get("genes") else None
```

---

### 2. UniProt Entry API — PDB 구조 목록 조회

**목적**: UniProt Accession → PDB ID 목록

```
GET https://rest.uniprot.org/uniprotkb/{accession}.json
```

**PDB ID 파싱**:
```python
data = response.json()
pdb_ids = []
for db_ref in data.get("uniProtKBCrossReferences", []):
    if db_ref["database"] == "PDB":
        pdb_ids.append(db_ref["id"])  # 예: "7T9K"
```

---

### 3. RCSB PDB GraphQL API — 메타데이터 조회

**목적**: PDB ID → Resolution, Released Date, Method, Citation

**엔드포인트**: `POST https://data.rcsb.org/graphql`

**쿼리**:
```graphql
query GetPDBEntry($id: String!) {
  entry(entry_id: $id) {
    rcsb_id
    struct {
      title
    }
    rcsb_entry_info {
      resolution_combined
      experimental_method
    }
    rcsb_accession_info {
      initial_release_date
    }
    citation {
      title
      rcsb_authors
      journal_abbrev
      year
      journal_volume
      page_first
      page_last
      pdbx_database_id_doi
      pdbx_database_id_pub_med
    }
  }
}
```

**파싱 예시**:
```python
entry = response["data"]["entry"]

resolution_list = entry["rcsb_entry_info"].get("resolution_combined")
resolution = resolution_list[0] if resolution_list else None

method = entry["rcsb_entry_info"].get("experimental_method")

released_date = entry["rcsb_accession_info"].get("initial_release_date")
if released_date:
    released_date = released_date[:10]  # "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"

citations = entry.get("citation", [])
primary_citation = next(
    (c for c in citations if c.get("id") == "primary"),
    citations[0] if citations else None
)
if primary_citation:
    authors = ", ".join(primary_citation.get("rcsb_authors", [])[:3])
    if len(primary_citation.get("rcsb_authors", [])) > 3:
        authors += " et al."
    volume    = primary_citation.get("journal_volume")
    page_first = primary_citation.get("page_first")
    page_last  = primary_citation.get("page_last")
```

---

### 4. GPCRdb API — GPCR 확장 데이터 조회 (`tools/gpcrdb.py`)

**목적**: PDB 구조의 State / Ligand / Ligand modality / Signaling protein / Preferred chain 조회

GPCRdb는 GPCR 전용 큐레이션 데이터베이스로, GPCR 수용체 구조에 대해 약리학적으로 검증된
State(Active/Inactive/Intermediate)와 리간드 정보를 제공한다.

---

#### 4-1. GPCR 여부 확인

타깃이 GPCR인지 먼저 확인한다. UniProt entry_name(예: `5HT2A_HUMAN`)을 소문자로 변환하여
GPCRdb에 조회한다.

```
GET https://gpcrdb.org/services/protein/{entry_name_lower}/
    예: https://gpcrdb.org/services/protein/5ht2a_human/
```

**파싱**:
```python
async def check_gpcr(entry_name: str, client: httpx.AsyncClient) -> tuple[bool, str | None]:
    """UniProt entry_name으로 GPCRdb 단백질 슬러그 확인"""
    slug = entry_name.lower()  # "5HT2A_HUMAN" → "5ht2a_human"
    url = f"https://gpcrdb.org/services/protein/{slug}/"
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            return True, data.get("entry_name", slug)  # GPCRdb 슬러그 반환
        return False, None
    except Exception:
        return False, None
```

GPCR이 아닌 경우(404 등) → `is_gpcr = False`로 설정하고 GPCRdb 호출 전체를 건너뛴다.

---

#### 4-2. 특정 단백질의 전체 PDB 구조 목록 조회

```
GET https://gpcrdb.org/services/structure/protein/{gpcrdb_slug}/
    예: https://gpcrdb.org/services/structure/protein/5ht2a_human/
```

**응답 필드 → PDBEntry 매핑**:
```python
async def get_gpcrdb_structures(slug: str, client: httpx.AsyncClient) -> dict[str, dict]:
    """GPCRdb 슬러그로 구조 목록 조회 → {pdb_id: 메타데이터} dict 반환"""
    url = f"https://gpcrdb.org/services/structure/protein/{slug}/"
    resp = await client.get(url, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()  # list of structure dicts

    result = {}
    for s in data:
        pdb_id = s.get("pdb_code", "").upper()
        if not pdb_id:
            continue

        # Ligand 처리: 여러 리간드 중 primary (결합 위치 기준) 선택
        ligands = s.get("ligands", [])
        primary_ligand = next(
            (l for l in ligands if l.get("function") == "binding"),
            ligands[0] if ligands else None
        )

        # Stabilizing agents: Fusion protein vs Antibody 분류
        fusion, antibody = parse_stabilizing_agents(s.get("stabilizing_agents", []))

        result[pdb_id] = {
            "pref_chain":       s.get("preferred_chain"),
            "state":            s.get("state"),              # "Active" | "Inactive" | "Intermediate"
            "ligand":           primary_ligand["name"] if primary_ligand else None,
            "ligand_modality":  primary_ligand.get("function_label") if primary_ligand else None,
            "signaling_protein": parse_signaling_protein(s.get("signalling_protein")),
            "fusion_protein":   fusion,
            "antibody":         antibody,
        }
    return result
```

---

#### 4-3. 리간드 modality 정규화

GPCRdb의 `function_label` 값이 불규칙할 수 있으므로 정규화한다.

```python
MODALITY_MAP = {
    "agonist":          "Agonist",
    "partial agonist":  "Partial agonist",
    "antagonist":       "Antagonist",
    "inverse agonist":  "Inverse agonist",
    "ago-antagonist":   "Ago-antagonist",
    "positive allosteric modulator": "PAM",
    "negative allosteric modulator": "NAM",
}

def normalize_modality(raw: str | None) -> str | None:
    if not raw:
        return None
    return MODALITY_MAP.get(raw.lower().strip(), raw.strip().capitalize())
```

---

#### 4-4. Signaling protein 정규화

```python
def parse_signaling_protein(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    # GPCRdb 반환값 예: "Gαq/11", "Gαi/o", "Gαs", "G12/13", "arrestin-2"
    MAP = {
        "gaq": "Gq", "gaq/11": "Gq", "galphaq": "Gq",
        "gai": "Gi", "gai/o": "Gi", "galphai": "Gi",
        "gas": "Gs", "galphas": "Gs",
        "ga12": "G12/13", "ga12/13": "G12/13",
        "arrestin": "Arrestin", "arrestin-2": "β-Arrestin2", "arrestin-3": "β-Arrestin2",
    }
    return MAP.get(raw.lower().replace(" ", ""), raw)
```

---

#### 4-5. Stabilizing agents 분류 (`tools/parser.py` 에도 fallback 구현)

GPCRdb의 `stabilizing_agents` 배열에서 Fusion protein과 Antibody를 분류한다.

```python
FUSION_KEYWORDS = ["bril", "t4l", "t4 lysozyme", "mt4l", "flavodoxin", "rubredoxin", "gsα"]
ANTIBODY_KEYWORDS = ["fab", "nanobody", "nb", "vhh", "scfv", "antibody"]

def parse_stabilizing_agents(agents: list[dict]) -> tuple[str | None, str | None]:
    """stabilizing_agents 리스트 → (fusion_protein, antibody) 튜플 반환"""
    fusions, antibodies = [], []
    for agent in agents:
        name = agent.get("name", "").lower()
        display = agent.get("name", "")
        if any(k in name for k in FUSION_KEYWORDS):
            fusions.append(display)
        elif any(k in name for k in ANTIBODY_KEYWORDS):
            antibodies.append(display)
    return (
        " / ".join(fusions) if fusions else None,
        " / ".join(antibodies) if antibodies else None,
    )
```

**PDB 제목 텍스트 fallback** (GPCRdb에 없는 구조 대비, `tools/parser.py`):

GPCRdb가 특정 구조를 포함하지 않는 경우(최신 구조 등), PDB entry 제목에서 패턴 매칭으로
Fusion protein과 Antibody를 추출한다.

```python
import re

FUSION_PATTERNS = [
    (r'\bBRIL\b',           'BRIL'),
    (r'\bmT4L\b',           'mT4L'),
    (r'\bT4L\b',            'T4L'),
    (r'\bT4 lysozyme\b',    'T4L'),
    (r'\bflavodoxin\b',     'Flavodoxin'),
]

ANTIBODY_PATTERNS = [
    (r'\b[\w\-]+-Fab\b',    None),   # "P2C2-Fab", "Fabs" 등 → 매칭된 문자열 그대로 사용
    (r'\bFab\b',            'Fab'),
    (r'\bNanobody\b',       'Nanobody'),
    (r'\bNb\d+\b',          None),   # "Nb35" 등
    (r'\bVHH\b',            'VHH'),
]

def extract_fusion_from_title(title: str) -> str | None:
    for pattern, label in FUSION_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return label
    return None

def extract_antibody_from_title(title: str) -> str | None:
    for pattern, label in ANTIBODY_PATTERNS:
        m = re.search(pattern, title, re.IGNORECASE)
        if m:
            return label if label else m.group(0)
    return None
```

---

#### 4-6. 전체 워크플로우에서 GPCRdb 통합

`search_target` 핸들러에서 GPCR 여부에 따라 분기한다.

```python
async def handle_search_target(arguments: dict):
    target = arguments["target"]

    async with httpx.AsyncClient() as client:
        # STEP 1~2: UniProt 조회 (기존 로직)
        uniprot = await search_uniprot(target, client)

        # STEP 3: GPCR 여부 확인
        is_gpcr, gpcrdb_slug = await check_gpcr(uniprot.entry_name, client)
        uniprot.is_gpcr = is_gpcr
        uniprot.gpcrdb_slug = gpcrdb_slug

        # STEP 4: GPCRdb 메타데이터 (GPCR인 경우)
        gpcrdb_map = {}
        if is_gpcr and gpcrdb_slug:
            gpcrdb_map = await get_gpcrdb_structures(gpcrdb_slug, client)

        # STEP 5: RCSB PDB 메타데이터 (기존 로직 — 병렬)
        pdb_entries = await fetch_all_pdb_entries(uniprot.pdb_ids, client)

        # STEP 6: GPCRdb 데이터 병합
        for entry in pdb_entries:
            gpcr_data = gpcrdb_map.get(entry.pdb_id)
            if gpcr_data:
                entry.is_gpcr        = True
                entry.pref_chain     = gpcr_data["pref_chain"]
                entry.state          = gpcr_data["state"]
                entry.ligand         = gpcr_data["ligand"]
                entry.ligand_modality = normalize_modality(gpcr_data["ligand_modality"])
                entry.signaling_protein = gpcr_data["signaling_protein"]
                entry.fusion_protein = gpcr_data["fusion_protein"]
                entry.antibody       = gpcr_data["antibody"]
            elif is_gpcr:
                # GPCRdb에 없는 최신 구조 → 제목 파싱으로 fallback
                entry.is_gpcr        = True
                entry.fusion_protein = extract_fusion_from_title(entry.title or "")
                entry.antibody       = extract_antibody_from_title(entry.title or "")

    return format_result(uniprot, pdb_entries, arguments)
```

---

### 5. 병렬 조회 처리

PDB 구조가 수십~수백 개일 수 있으므로 **비동기 병렬 처리** 필수.

```python
import asyncio
import httpx

async def fetch_all_pdb_entries(pdb_ids: list[str]) -> list[PDBEntry]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [fetch_single_entry(client, pdb_id) for pdb_id in pdb_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    # return_exceptions=True: 개별 실패가 전체를 막지 않음
    return [r for r in results if isinstance(r, PDBEntry)]
```

---

## MCP 도구 정의 (`server.py`)

총 **3개의 MCP Tool** 을 구현한다.

---

### Tool 1: `search_target`

**설명**: 타겟 이름을 받아 전체 워크플로우를 실행하고 PDB 구조 테이블을 반환

```python
@server.call_tool()
async def search_target(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Arguments:
        target (str, 필수): 단백질 타겟 이름. 예: "EGFR", "TP53", "CDK2", "KRAS"
        max_structures (int, 선택, 기본값 없음): 반환할 최대 구조 수. 미입력 시 전체 반환
        export_excel (bool, 선택, 기본값 False): True이면 Excel 파일도 저장
        sort_by (str, 선택, 기본값 "date"): 정렬 기준. "date"(최신순) | "resolution"(해상도순)
    
    Returns:
        - UniProt 정보 (Accession, 단백질명, 유전자명)
        - PDB 구조 목록 테이블 (PDB ID, Resolution, Released Date, Method, 논문)
        - 총 구조 수
        - (export_excel=True인 경우) 저장된 Excel 파일 경로
    """
```

**반환 텍스트 형식** — GPCR 타깃일 때 (확장 테이블):
```
## 5-HT₂A (P28223) — 실험 구조 검색 결과  🧬 GPCR

**단백질명**: 5-hydroxytryptamine receptor 2A  
**유전자명**: HTR2A  
**UniProt**: P28223 (5HT2A_HUMAN)  
**총 PDB 구조 수**: 32개

| Method | PDB ID | Res.(Å) | Chain | State    | Ligand      | Modality        | Sign. | Fusion | Antibody | Year |
|--------|--------|---------|-------|----------|-------------|-----------------|-------|--------|----------|------|
| X-ray  | 8JT8   | 2.7     | A     | Inactive | IHCH-7179   | Antagonist      | -     | BRIL   | -        | 2024 |
| X-ray  | 7WC7   | 2.6     | A     | Inactive | Lisuride    | Agonist         | -     | BRIL   | -        | 2022 |
| X-ray  | 7WC5   | 3.2     | A     | Inactive | Psilocin    | Agonist         | -     | BRIL   | -        | 2022 |
...

> Excel 파일 저장됨: ./output/HTR2A_P28223_structures.xlsx
```

**반환 텍스트 형식** — 비GPCR 타깃일 때 (기본 테이블):
```
## EGFR (P00533) — 실험 구조 검색 결과

**단백질명**: Epidermal growth factor receptor  
**유전자명**: EGFR  
**UniProt**: P00533 (EGFR_HUMAN)  
**총 PDB 구조 수**: 351개

| PDB ID | Resolution (Å) | Method | Released Date | 논문 |
|--------|---------------|--------|---------------|------|
| 7T9K | 1.65 | X-ray | 2022-01-12 | Yun et al. (2021) Nature · DOI: 10.1038/... |
| 6JRH | 2.40 | X-ray | 2019-08-07 | Liu et al. (2019) JACS · DOI: 10.1021/... |
...

> Excel 파일 저장됨: ./output/EGFR_P00533_structures.xlsx
```

---

### Tool 2: `get_pdb_detail`

**설명**: 특정 PDB ID 하나의 상세 정보 조회

```python
@server.call_tool()
async def get_pdb_detail(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Arguments:
        pdb_id (str, 필수): PDB ID. 예: "7T9K"
    
    Returns:
        PDBEntry의 전체 정보 (resolution, method, released_date, title, citation 전체)
    """
```

---

### Tool 3: `compare_targets`

**설명**: 여러 타겟을 한 번에 검색하여 구조 수 비교 요약 반환

```python
@server.call_tool()
async def compare_targets(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Arguments:
        targets (list[str], 필수): 비교할 타겟 목록. 예: ["EGFR", "HER2", "MET"]
    
    Returns:
        각 타겟별 UniProt Accession, 총 구조 수, 최고 해상도 구조, 최신 구조 요약 테이블
    """
```

---

## Excel 출력 스펙 (`tools/export.py`)

파일명 규칙: `{GENE_NAME}_{ACCESSION}_structures_{YYYYMMDD}.xlsx`
저장 경로: 실행 디렉토리 기준 `./output/` 폴더 (없으면 자동 생성)

**시트 구성**: Excel 파일은 타깃 종류에 따라 시트를 다르게 구성한다.

- GPCR 타깃: `Structures` 시트(확장 컬럼) + `Summary` 시트
- 비GPCR 타깃: `Structures` 시트(기본 컬럼) + `Summary` 시트

---

**[A] GPCR 확장 컬럼 정의** (순서 고정, `is_gpcr=True`일 때):

| 컬럼명 | 데이터 | 비고 |
|--------|--------|------|
| Method | method | X-ray / Cryo-EM / NMR |
| PDB ID | pdb_id | 하이퍼링크: https://www.rcsb.org/structure/{pdb_id} |
| Res. (Å) | resolution | 소수점 2자리, NMR은 "N/A" |
| Pref. chain | pref_chain | A / B / C 등 |
| State | state | Active / Inactive / Intermediate / Unknown |
| Ligand | ligand | 리간드 이름 |
| Ligand modality | ligand_modality | Agonist / Antagonist / Inverse agonist / Partial agonist 등 |
| Sign. prot. | signaling_protein | Gq / Gi / Gs / G12/13 / β-Arrestin2 등, 없으면 "-" |
| Fusion protein | fusion_protein | BRIL / T4L / mT4L 등, 없으면 "-" |
| Antibody | antibody | Fab 이름 등, 없으면 "-" |
| Year | citation.year 또는 released_date[:4] | |
| Citation (ACS) | format_acs_citation(entry.citation) | ACS 스타일 전체 인용문. 아래 형식 참고 |
| DOI | citation.doi | 하이퍼링크 |
| PMID | citation.pmid | 하이퍼링크: https://pubmed.ncbi.nlm.nih.gov/{pmid} |

**State 컬럼 조건부 색상**:
- `Active` → 셀 배경 `#DCFCE7` (연한 초록)
- `Inactive` → 셀 배경 `#FEF2F2` (연한 빨강)
- `Intermediate` → 셀 배경 `#FEF9C3` (연한 노랑)
- `None` / 데이터 없음 → 텍스트 `"-"` 표시

**Ligand modality 컬럼 조건부 색상**:
- `Agonist` / `Partial agonist` → `#DCFCE7`
- `Antagonist` → `#FEF2F2`
- `Inverse agonist` → `#FFF7ED`

---

**[B] 기본 컬럼 정의** (순서 고정, `is_gpcr=False`일 때):

| 컬럼명 | 데이터 | 비고 |
|--------|--------|------|
| PDB ID | pdb_id | 하이퍼링크: https://www.rcsb.org/structure/{pdb_id} |
| Resolution (Å) | resolution | 숫자 형식, NMR은 "N/A" |
| Method | method | X-ray / Cryo-EM / NMR / Other |
| Released Date | released_date | YYYY-MM-DD |
| Entry Title | title | PDB entry 제목 |
| Paper Title | citation.title | |
| Authors | citation.authors | "Last FM et al." 형태 |
| Journal | citation.journal | |
| Year | citation.year | |
| Citation (ACS) | format_acs_citation(entry.citation) | ACS 스타일 전체 인용문 |
| DOI | citation.doi | 하이퍼링크 |
| PMID | citation.pmid | 하이퍼링크: https://pubmed.ncbi.nlm.nih.gov/{pmid} |

---

---

**ACS Citation 포맷 함수** (`tools/export.py` 또는 `tools/pdb.py`에 구현):

ACS 스타일 형식:
```
Last, F. M.; Last2, F. M.; Last3, F. M. Article Title. J. Abbrev. Year, Vol, PageFirst–PageLast. DOI: 10.xxxx/xxxxx.
```

```python
def format_acs_citation(citation) -> str:
    """
    Citation 객체 → ACS 스타일 인용문 문자열 반환.

    ACS 형식:
      Author1, F. M.; Author2, F. M. Title. J. Abbrev. Year, Vol, PageFirst–PageLast. DOI: xx.xxxx/xxxxx.

    필드가 없는 경우 해당 부분은 생략하고 나머지로 조합.
    """
    if citation is None:
        return ""

    parts = []

    # 저자 (세미콜론 구분, 마지막에 마침표)
    if citation.authors:
        parts.append(citation.authors.rstrip(".") + ".")

    # 논문 제목 (이탤릭 없이 그대로, 마침표로 끝)
    if citation.title:
        title = citation.title.rstrip(".")
        parts.append(title + ".")

    # 저널명 Year, Vol, Pages.
    journal_part = ""
    if citation.journal:
        journal_part += citation.journal
    if citation.year:
        journal_part += f" {citation.year}"
    if citation.volume:
        journal_part += f", {citation.volume}"
    if citation.page_first:
        if citation.page_last and citation.page_last != citation.page_first:
            journal_part += f", {citation.page_first}–{citation.page_last}"
        else:
            journal_part += f", {citation.page_first}"
    if journal_part:
        parts.append(journal_part.strip() + ".")

    # DOI
    if citation.doi:
        parts.append(f"DOI: {citation.doi}.")

    return " ".join(parts)
```

**예시 출력**:
```
Huang, J.; Chen, S.; Zhang, J. J. et al. Structure of the neurotensin receptor 1 in complex with β-arrestin 1. Nature 2020, 579, 303–308. DOI: 10.1038/s41586-020-1968-7.
```

**Excel 셀 서식 규칙 (Citation 컬럼)**:
- 열 너비: 60~80 (wrap_text=True 설정 필수)
- 글꼴: 일반체 (이탤릭 없음)
- 정렬: 왼쪽, 상단
- 배경: 행 교대 색상 그대로 유지

---

**공통 서식 규칙**:
- 헤더 행: 배경색 `#1E293B` (진한 남색), 글자색 흰색, 볼드
- 짝수 행 배경: `#F8FAFC` (연한 회색)
- Resolution 컬럼: 소수점 2자리 고정
- 첫 행 고정 (freeze_panes)
- 컬럼 너비 자동 조정 (auto-fit)
- Summary 시트: UniProt 정보, GPCR 여부, 총 구조 수, GPCRdb 기준 구조 수, 조회 일시

---

## GPCRdb Rate Limiting 및 에러 처리

- GPCRdb API: 동시 요청 최대 **5개** (`asyncio.Semaphore(5)`)
- GPCRdb 응답 실패 시: 경고 메시지 출력 후 GPCRdb 없이 기본 PDB 데이터만 반환
  - 에러가 전체 검색을 막아서는 안 된다
- GPCRdb에 없는 최신 PDB 구조: `parser.py`의 제목 파싱 fallback 적용

```python
# GPCRdb 실패 시 graceful degradation
try:
    gpcrdb_map = await get_gpcrdb_structures(gpcrdb_slug, client)
except Exception as e:
    gpcrdb_map = {}
    warning = f"⚠️ GPCRdb 조회 실패 ({e}). 기본 PDB 데이터로만 결과를 반환합니다."
```

---

## 에러 처리 규칙

모든 에러는 사용자가 이해할 수 있는 한국어 메시지로 반환한다.

| 상황 | 에러 메시지 예시 |
|------|----------------|
| 타겟을 UniProt에서 찾지 못함 | "'{target}'에 해당하는 인간 단백질을 UniProt에서 찾지 못했습니다. 유전자명이나 단백질명으로 다시 시도해보세요. (예: EGFR, TP53)" |
| UniProt에 PDB 구조 없음 | "'{protein_name}' (P00533)의 실험 구조가 PDB에 등록되어 있지 않습니다." |
| PDB API 타임아웃 | "PDB 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요." |
| 개별 PDB 조회 실패 | 해당 항목만 건너뛰고 결과에 "(조회 실패)"로 표시, 전체 진행 유지 |
| 네트워크 오류 | "외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요." |

---

## Rate Limiting 처리

- UniProt API: 요청 간 `0.1초` 대기 (비상업적 무료 사용 기준)
- RCSB PDB API: 병렬 동시 요청 최대 **10개**로 제한 (`asyncio.Semaphore(10)`)
- GPCRdb API: 병렬 동시 요청 최대 **5개**로 제한 (`asyncio.Semaphore(5)`)
- 재시도 로직: 실패 시 1회 재시도, 대기 1초

```python
semaphore = asyncio.Semaphore(10)

async def fetch_with_semaphore(client, pdb_id):
    async with semaphore:
        return await fetch_single_entry(client, pdb_id)
```

---

## `server.py` 전체 구조 템플릿

```python
import asyncio
from mcp import Server, types
from mcp.server.stdio import stdio_server

from tools.uniprot import search_uniprot, get_pdb_ids_from_uniprot
from tools.pdb import fetch_all_pdb_entries, fetch_single_pdb_entry
from tools.export import export_to_excel

server = Server("pdb-research-server")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_target",
            description=(
                "단백질 타겟 이름을 입력하면 UniProt에서 해당 단백질을 찾고, "
                "PDB에 등록된 모든 실험 구조(PDB ID, Resolution, Released Date, 연결 논문)를 조회합니다. "
                "신약개발 연구에서 타겟 단백질의 구조 데이터를 수집할 때 사용합니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "단백질 타겟 이름 또는 유전자명. 예: EGFR, TP53, CDK2, KRAS, BRAF"
                    },
                    "max_structures": {
                        "type": "integer",
                        "description": "반환할 최대 구조 수. 미입력 시 전체 반환"
                    },
                    "export_excel": {
                        "type": "boolean",
                        "description": "True이면 Excel 파일(.xlsx)로도 저장. 기본값 False",
                        "default": False
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["date", "resolution"],
                        "description": "정렬 기준: date(최신순, 기본값) 또는 resolution(해상도 좋은순)",
                        "default": "date"
                    }
                },
                "required": ["target"]
            }
        ),
        types.Tool(
            name="get_pdb_detail",
            description="특정 PDB ID의 상세 정보(해상도, 실험방법, 공개일, 논문 전체 정보)를 조회합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdb_id": {
                        "type": "string",
                        "description": "PDB ID. 예: 7T9K, 6JRH, 1IVO"
                    }
                },
                "required": ["pdb_id"]
            }
        ),
        types.Tool(
            name="compare_targets",
            description="여러 타겟을 동시에 검색하여 구조 수, 최고 해상도, 최신 구조 등을 비교합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "비교할 타겟 목록. 예: [\"EGFR\", \"HER2\", \"MET\"]"
                    }
                },
                "required": ["targets"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search_target":
        return await handle_search_target(arguments)
    elif name == "get_pdb_detail":
        return await handle_get_pdb_detail(arguments)
    elif name == "compare_targets":
        return await handle_compare_targets(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

---

## `pyproject.toml`

```toml
[project]
name = "pdb-mcp-server"
version = "0.1.0"
description = "PDB 연구 자동화 MCP 서버"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "httpx>=0.27.0",
    "openpyxl>=3.1.0",
    "pydantic>=2.0.0",
]

[project.scripts]
pdb-mcp-server = "server:main"
```

---

## Claude Desktop 연동 설정

구현 완료 후 `~/Library/Application Support/Claude/claude_desktop_config.json` 에 추가:

```json
{
  "mcpServers": {
    "pdb-research": {
      "command": "python",
      "args": ["/절대경로/pdb-mcp-server/server.py"],
      "env": {}
    }
  }
}
```

---

## 테스트 케이스

구현 후 아래 케이스를 순서대로 검증한다.

### 기능 테스트
```python
# tests/test_uniprot.py
def test_search_egfr():
    result = asyncio.run(search_uniprot("EGFR"))
    assert result.accession == "P00533"
    assert result.gene_name == "EGFR"
    assert len(result.pdb_ids) > 100  # EGFR은 구조가 매우 많음

def test_search_tp53():
    result = asyncio.run(search_uniprot("TP53"))
    assert result.accession == "P04637"

def test_unknown_target():
    # 존재하지 않는 타겟 → 에러 메시지 반환 확인
    with pytest.raises(ValueError, match="찾지 못했습니다"):
        asyncio.run(search_uniprot("XYZXYZ_NOTEXIST_12345"))

# tests/test_pdb.py
def test_fetch_single_entry():
    entry = asyncio.run(fetch_single_pdb_entry("7T9K"))
    assert entry.pdb_id == "7T9K"
    assert entry.resolution is not None
    assert entry.released_date is not None
    assert entry.citation is not None
```

### GPCRdb 확장 기능 테스트
```python
# tests/test_gpcrdb.py

def test_check_gpcr_htr2a():
    """5-HT2A 수용체 → GPCR로 인식되어야 함"""
    is_gpcr, slug = asyncio.run(check_gpcr("5HT2A_HUMAN", client))
    assert is_gpcr is True
    assert slug == "5ht2a_human"

def test_check_gpcr_egfr():
    """EGFR → GPCR이 아니어야 함"""
    is_gpcr, slug = asyncio.run(check_gpcr("EGFR_HUMAN", client))
    assert is_gpcr is False

def test_gpcrdb_structures_htr2a():
    """5-HT2A 구조 목록에서 알려진 구조 8JT8 확인"""
    result = asyncio.run(get_gpcrdb_structures("5ht2a_human", client))
    assert "8JT8" in result
    assert result["8JT8"]["state"] == "Inactive"
    assert result["8JT8"]["ligand"] == "IHCH-7179"
    assert result["8JT8"]["ligand_modality"] == "Antagonist"
    assert result["8JT8"]["fusion_protein"] == "BRIL"

def test_gpcrdb_structures_htr2b():
    """5-HT2B 구조 목록에서 알려진 구조 4IB4 확인"""
    result = asyncio.run(get_gpcrdb_structures("5ht2b_human", client))
    assert "4IB4" in result
    assert result["4IB4"]["ligand"] == "Ergotamine"

def test_modality_normalization():
    assert normalize_modality("agonist") == "Agonist"
    assert normalize_modality("inverse agonist") == "Inverse agonist"
    assert normalize_modality(None) is None

def test_fusion_title_parser():
    assert extract_fusion_from_title("Crystal structure with BRIL fusion") == "BRIL"
    assert extract_fusion_from_title("Structure of receptor with T4L") == "T4L"
    assert extract_fusion_from_title("Cryo-EM structure of receptor") is None

def test_antibody_title_parser():
    assert extract_antibody_from_title("Complex with P2C2-Fab") == "P2C2-Fab"
    assert extract_antibody_from_title("Nanobody bound structure") == "Nanobody"
    assert extract_antibody_from_title("X-ray structure of receptor") is None
```

### 통합 테스트 (Claude 채팅에서 직접)
```
1. "EGFR 구조 찾아줘"
   → UniProt P00533 확인, 비GPCR 기본 테이블 반환 확인

2. "HTR2A 구조 찾아줘"
   → 🧬 GPCR 표시 확인, State/Ligand/Modality 컬럼 포함 확장 테이블 반환

3. "5-HT2A 구조 Excel로 저장해줘"
   → GPCR 확장 컬럼 포함된 xlsx 생성, State 셀 조건부 색상 확인

4. "DRD2 구조 찾아줘"
   → 도파민 D2 수용체, GPCR 확장 테이블 확인

5. "HTR2A, HTR2B, HTR2C 구조 수 비교해줘"
   → compare_targets로 3개 서브타입 비교 확인

6. "7WC7 상세 정보 알려줘"
   → GPCR 구조 상세 (State: Inactive, Ligand: Lisuride, Modality: Agonist 확인)
```

---

## 구현 순서 (권장)

### Phase 1 — 기본 기능 (PDB + UniProt)
1. `models/schemas.py` — 전체 데이터 모델 정의 (GPCR 확장 필드 포함)
2. `tools/uniprot.py` — UniProt API 클라이언트 구현 및 테스트
3. `tools/pdb.py` — RCSB PDB GraphQL 클라이언트 구현 및 테스트
4. `tools/export.py` — 기본 Excel 출력 구현
5. `server.py` — MCP 서버 기본 조립 (search_target / get_pdb_detail / compare_targets)
6. Claude Desktop 연동 후 EGFR / TP53 / CDK2로 기본 테스트

### Phase 2 — GPCR 확장 (GPCRdb 연동)
7. `tools/gpcrdb.py` — GPCRdb API 클라이언트 구현
   - `check_gpcr()` 먼저 구현 및 단독 테스트
   - `get_gpcrdb_structures()` 구현 및 단독 테스트
8. `tools/parser.py` — 제목 파싱 (Fusion protein / Antibody fallback)
9. `server.py` 수정 — `handle_search_target`에 GPCRdb 분기 로직 추가
10. `tools/export.py` 수정 — GPCR 확장 컬럼 + 조건부 색상 추가
11. `tests/test_gpcrdb.py` — GPCRdb 단위 테스트 전체 실행
12. HTR2A / HTR2B / DRD2로 통합 테스트

---

---

## Phase 3 — 프롬프트 자동화 (연구원 자연어 → 자동 실행)

> **목표**: 연구원이 프롬프트 형식을 외우지 않아도 된다.
> "세로토닌 수용체 구조 보여줘" 한 마디로 올바른 표가 자동 생성된다.
>
> 구현할 파일 두 가지:
> 1. `SYSTEM_PROMPT.md` — Claude Desktop Custom Instructions용 연구소 전용 지침
> 2. `server.py` tool description 강화 — MCP 도구 설명에 자동 판단 규칙 내장

---

### Phase 3-1: `SYSTEM_PROMPT.md` 생성

**위치**: 프로젝트 루트 `pdb-mcp-server/SYSTEM_PROMPT.md`

**사용 방법**: 이 파일의 내용을 Claude Desktop → Settings → Custom Instructions에 붙여넣는다.
IT 담당자가 한 번 설정하면 해당 Claude 인스턴스를 사용하는 모든 연구원에게 적용된다.

**파일 내용 (아래를 그대로 `SYSTEM_PROMPT.md`에 작성할 것)**:

```
## 나무아이씨티 신약연구소 — PDB 구조 리서치 어시스턴트

당신은 신약연구소 연구원의 PDB 단백질 구조 분석을 돕는 전문 어시스턴트입니다.
연구원이 단백질 이름을 언급하면, 아래 규칙에 따라 자동으로 판단하고 실행합니다.

---

### 자동 실행 규칙

**규칙 1 — 타겟 감지 즉시 MCP 호출**
연구원이 단백질 이름, 수용체 이름, 유전자명을 언급하면
별도 확인 없이 즉시 `search_target` MCP 도구를 호출합니다.

**규칙 2 — 수용체 패밀리 자동 확장**
아래 키워드가 등장하면 해당 서브타입 전체를 자동으로 검색합니다.

| 연구원 입력 | 자동 확장 타겟 |
|------------|--------------|
| 세로토닌 수용체 / 5-HT / serotonin | HTR1A, HTR1B, HTR2A, HTR2B, HTR2C |
| 세로토닌 2 / 5-HT2 / HTR2 | HTR2A, HTR2B, HTR2C |
| 도파민 수용체 / dopamine / DRD | DRD1, DRD2, DRD3, DRD4, DRD5 |
| 아드레날린 수용체 / adrenergic / ADRB | ADRB1, ADRB2, ADRB3 |
| 무스카린 수용체 / muscarinic / CHRM | CHRM1, CHRM2, CHRM3, CHRM4, CHRM5 |
| 히스타민 수용체 / histamine / HRH | HRH1, HRH2, HRH3, HRH4 |
| 오피오이드 수용체 / opioid | OPRM1, OPRD1, OPRK1 |
| 키나제 / kinase / CDK | CDK1, CDK2, CDK4, CDK6 |

단일 타겟이 명확한 경우 패밀리 확장 없이 해당 타겟만 검색합니다.

**규칙 3 — 출력 형식 자동 선택**

GPCR 타겟인 경우 → 확장 테이블 자동 선택:
  컬럼: Method / PDB ID / Res.(Å) / Pref. chain / State / Ligand /
         Ligand modality / Sign. prot. / Fusion protein / Antibody / Year
  정렬: State(Inactive 우선) → Year(최신 우선)
  섹션: 서브타입별 분리 (패밀리 검색일 때)

비GPCR 타겟인 경우 → 기본 테이블 자동 선택:
  컬럼: PDB ID / Resolution(Å) / Method / Released Date / 논문(저자·저널·DOI)
  정렬: 최신순

**규칙 4 — Excel 자동 저장**
구조 검색 결과는 항상 Excel 파일로 저장합니다.
연구원이 "Excel 필요 없어"라고 명시한 경우에만 저장하지 않습니다.

**규칙 5 — 필터 자동 적용**
연구원 입력에 아래 키워드가 있으면 자동으로 필터를 적용합니다.

| 입력 키워드 | 자동 필터 |
|------------|----------|
| "고해상도" / "좋은 해상도" / "선명한" | Resolution ≤ 2.5Å |
| "최근" / "최신" / "요즘" | Released Date ≥ 최근 5년 |
| "Antagonist만" / "길항제만" | Ligand modality = Antagonist |
| "Agonist만" / "작용제만" | Ligand modality = Agonist |
| "Cryo-EM만" / "cryo만" | Method = Electron Microscopy |
| "X-ray만" | Method = X-ray |
| "Active 구조만" | State = Active |
| "Inactive 구조만" | State = Inactive |
| "G단백질 복합체" | Sign. prot. 있는 것만 |

**규칙 6 — 결과 앞에 요약 제공**
표를 보여주기 전에 반드시 아래 형식으로 한 줄 요약을 먼저 표시합니다.

예시:
  EGFR (P00533) — 총 351개 구조 | 최고 해상도 1.07Å (8A27) | 최신 구조 2025-05-21
  HTR2A (P28223) — 총 32개 구조 | Inactive 15개 / Active 3개 / Intermediate 4개

**규칙 7 — 후속 질문 자동 제안**
결과 출력 후 아래 중 맥락에 맞는 후속 질문을 1~2개 자동 제안합니다.
- "이 중 Antagonist 구조만 필터링해드릴까요?"
- "해상도 2.5Å 이하 구조만 추려드릴까요?"
- "Excel 파일에 저자·DOI 컬럼도 추가할까요?"
- "비슷한 타겟([패밀리 수용체])과 구조 수를 비교해드릴까요?"

---

### 연구원 입력 예시와 자동 처리

입력: "EGFR 분석해줘"
→ search_target("EGFR"), 기본 테이블, Excel 저장

입력: "세로토닌 수용체 구조 정리해줘"
→ HTR2A/HTR2B/HTR2C 자동 확장
→ 각각 search_target 호출, GPCR 확장 테이블
→ 서브타입 섹션 통합, Excel 저장

입력: "HTR2A Antagonist 고해상도만 보여줘"
→ search_target("HTR2A")
→ Ligand modality=Antagonist, Resolution≤2.5Å 필터 적용

입력: "도파민 수용체랑 세로토닌 수용체 구조 수 비교해줘"
→ compare_targets(["DRD1","DRD2","DRD3","DRD4","DRD5","HTR2A","HTR2B","HTR2C"])

---

### 응답 언어
연구원이 한국어로 물으면 한국어로 답합니다.
단, 단백질명·PDB ID·컬럼명은 영문 원문을 그대로 사용합니다.
```

---

### Phase 3-2: `server.py` tool description 강화

**목적**: Claude가 MCP 도구를 자동으로 올바르게 호출하도록
tool description에 판단 규칙과 수용체 별칭 사전을 내장한다.

**`server.py`의 `list_tools()` 함수에서 각 Tool의 description을 아래로 교체할 것.**

---

#### `search_target` tool description 교체

```python
types.Tool(
    name="search_target",
    description="""
단백질 타겟 이름으로 PDB 구조 전체를 검색하고 표로 반환합니다.

[자동 호출 조건]
연구원이 단백질·수용체·유전자 이름을 언급하면 즉시 호출합니다.
확인 질문 없이 바로 실행합니다.

[수용체 별칭 → 유전자명 변환 사전]
다음 별칭이 입력되면 해당 유전자명으로 변환하여 호출합니다:
- "세로토닌 2A", "5-HT2A", "serotonin 2A receptor" → "HTR2A"
- "세로토닌 2B", "5-HT2B" → "HTR2B"
- "세로토닌 2C", "5-HT2C" → "HTR2C"
- "도파민 D2", "DRD2", "dopamine D2" → "DRD2"
- "베타2 아드레날린", "beta2 adrenergic", "ADRB2" → "ADRB2"
- "EGFR", "표피성장인자수용체", "ErbB1" → "EGFR"
- "HER2", "ErbB2", "ERBB2" → "HER2"
- "p53", "종양억제인자" → "TP53"
- "CDK2", "사이클린의존인산화효소2" → "CDK2"
- "KRAS", "K-Ras" → "KRAS"

[출력 형식 자동 선택]
GPCR 타겟(HTR*, DRD*, ADRB*, CHRM*, HRH*, OPR* 등):
  → State / Ligand / Ligand modality / Fusion protein 등 확장 컬럼 포함
  → sort_by="state_then_date" (State 우선, 같은 State 내 최신순)
  → export_excel=True 자동 설정

비GPCR 타겟:
  → PDB ID / Resolution / Released Date / 논문 기본 컬럼
  → sort_by="date" (최신순)
  → export_excel=True 자동 설정

[필터 파라미터 자동 설정]
- "고해상도" 언급 시 → max_resolution=2.5
- "최근 N년" 언급 시 → min_year=(현재연도-N)
- "Antagonist만" → ligand_modality_filter="Antagonist"
- "Cryo-EM만" → method_filter="EM"
- "Active만" → state_filter="Active"

[패밀리 검색 판단]
단일 서브타입이 명확하면 이 도구 1회 호출.
"세로토닌 수용체 전체" 처럼 패밀리 전체가 요청되면
compare_targets 또는 이 도구를 서브타입별로 반복 호출 후 통합.
""",
    inputSchema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "유전자명 또는 UniProt 검색어. 별칭은 위 사전 참고."
            },
            "max_structures": {
                "type": "integer",
                "description": "반환할 최대 구조 수. 미입력 시 전체 반환."
            },
            "export_excel": {
                "type": "boolean",
                "description": "Excel 저장 여부. 기본값 True.",
                "default": True
            },
            "sort_by": {
                "type": "string",
                "enum": ["date", "resolution", "state_then_date"],
                "description": "정렬 기준. GPCR은 state_then_date, 비GPCR은 date 기본.",
                "default": "date"
            },
            "max_resolution": {
                "type": "number",
                "description": "이 값 이하의 Resolution만 포함 (Å). 예: 2.5"
            },
            "min_year": {
                "type": "integer",
                "description": "이 연도 이후 공개된 구조만 포함. 예: 2020"
            },
            "ligand_modality_filter": {
                "type": "string",
                "description": "특정 modality만 포함. 예: Antagonist, Agonist, Inverse agonist"
            },
            "state_filter": {
                "type": "string",
                "description": "특정 State만 포함. 예: Active, Inactive, Intermediate"
            },
            "method_filter": {
                "type": "string",
                "description": "특정 실험방법만 포함. 예: X-ray, EM, NMR"
            }
        },
        "required": ["target"]
    }
)
```

---

#### `compare_targets` tool description 교체

```python
types.Tool(
    name="compare_targets",
    description="""
여러 타겟을 동시에 검색하여 구조 수·해상도·최신 구조를 비교합니다.

[자동 호출 조건]
"비교", "차이", "어느 게 더", "몇 개씩" 등 비교 의도가 있을 때 호출합니다.
패밀리 전체가 요청될 때도 이 도구를 먼저 호출하여 개요를 제공합니다.

[패밀리 자동 확장 규칙]
연구원이 패밀리 키워드를 입력하면 targets 배열을 자동 구성합니다:
- "세로토닌 2" / "HTR2 패밀리" → ["HTR2A", "HTR2B", "HTR2C"]
- "세로토닌 전체" / "5-HT 전체" → ["HTR1A","HTR1B","HTR2A","HTR2B","HTR2C","HTR4","HTR6","HTR7"]
- "도파민 수용체" / "DRD 전체" → ["DRD1","DRD2","DRD3","DRD4","DRD5"]
- "아드레날린" / "ADRB" → ["ADRB1","ADRB2","ADRB3"]
- "무스카린" / "CHRM" → ["CHRM1","CHRM2","CHRM3","CHRM4","CHRM5"]
- "EGFR 패밀리" / "ErbB" → ["EGFR","ERBB2","ERBB3","ERBB4"]
- "CDK 패밀리" → ["CDK1","CDK2","CDK4","CDK6","CDK7","CDK9"]
""",
    inputSchema={
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "비교할 유전자명 목록. 패밀리 키워드는 위 규칙으로 자동 구성."
            }
        },
        "required": ["targets"]
    }
)
```

---

#### `get_pdb_detail` tool description 교체

```python
types.Tool(
    name="get_pdb_detail",
    description="""
특정 PDB ID 하나의 상세 정보를 조회합니다.

[자동 호출 조건]
- 연구원이 특정 PDB ID를 언급할 때 (예: "7WC7 자세히 알려줘")
- search_target 결과에서 특정 구조를 더 자세히 보고 싶을 때
- DOI나 논문 정보를 확인하고 싶을 때

[출력]
GPCR 구조: Resolution / Method / Released Date / State / Ligand /
            Ligand modality / Signaling protein / Fusion / Antibody / 논문 전체
비GPCR 구조: Resolution / Method / Released Date / 논문 전체
""",
    inputSchema={
        "type": "object",
        "properties": {
            "pdb_id": {
                "type": "string",
                "description": "PDB ID (4자리). 예: 7WC7, 8JT8, 1IVO"
            }
        },
        "required": ["pdb_id"]
    }
)
```

---

### Phase 3 구현 순서

1. `SYSTEM_PROMPT.md` 파일 생성 (위 내용 그대로 작성)
2. `server.py`의 `list_tools()` 함수에서 세 Tool의 description을 위 내용으로 교체
3. `server.py`의 `handle_search_target()` 함수에 필터 파라미터 처리 로직 추가:
   - `max_resolution`: PDB 메타데이터 조회 후 후처리 필터
   - `min_year`: released_date[:4] 기준 필터
   - `ligand_modality_filter`: GPCR 데이터 병합 후 필터
   - `state_filter`: GPCR 데이터 병합 후 필터
   - `method_filter`: PDB method 필드 기준 필터
4. Claude Desktop 재시작 → MCP 서버 재연결
5. 아래 테스트 문장으로 자동 판단 검증

### Phase 3 검증 테스트 (Claude 채팅에서 직접)

```
# 자동 판단 검증 — 이 문장들이 올바르게 실행되면 완료

T1: "세로토닌 수용체 구조 정리해줘"
    기대: HTR2A/HTR2B/HTR2C 자동 확장, 서브타입 섹션 분리, GPCR 확장 테이블

T2: "EGFR 분석해줘"
    기대: 단일 타겟, 기본 테이블, Excel 저장

T3: "HTR2A Antagonist 고해상도만"
    기대: Ligand modality=Antagonist 필터, Resolution≤2.5Å 필터 자동 적용

T4: "도파민이랑 세로토닌 2 수용체 구조 수 비교"
    기대: DRD1-5 + HTR2A/B/C → compare_targets 자동 호출

T5: "7WC7 자세히 알려줘"
    기대: get_pdb_detail 호출, GPCR 확장 정보 포함

T6: "최근 5년 GPCR Cryo-EM 구조 중 Active state만 보여줘"
    기대: min_year=2021, method_filter=EM, state_filter=Active 자동 적용
```

---

## 주의 사항

- 모든 외부 API 호출은 `httpx.AsyncClient` 를 사용하고, `with` 블록 안에서만 호출한다.
- PDB ID는 항상 **대문자**로 처리한다 (`pdb_id.upper()`).
- UniProt 검색 시 인간(`organism_id:9606`)과 검증된 항목(`reviewed:true`)으로 필터링하되,
  결과가 없으면 필터를 순차적으로 완화하여 재시도한다.
- Resolution이 없는 구조(NMR 등)는 None으로 처리하고, 테이블에는 `"N/A"`로 표시한다.
- 논문 저자가 없는 경우 citation 전체를 None 처리하지 말고, 있는 필드만 채운다.
- GPCRdb에 없는 구조(최신 구조 등)는 GPCR 플래그(`is_gpcr=True`)를 유지하되
  GPCRdb 데이터 필드는 None으로 두고 parser.py fallback을 적용한다.
- GPCRdb `state` 필드가 없거나 None인 경우 Excel/텍스트 모두 `"-"`로 표시한다.
  절대 "Unknown"이나 임의 값을 채우지 않는다.
- GPCRdb API 전체 실패 시에도 기본 PDB 데이터(PDB ID / Resolution / Date / 논문)는
  반드시 반환한다. GPCRdb는 선택적 강화(optional enrichment)이지 필수 의존이 아니다.

---

## Phase 4 — 버그 수정 (GPCRdb 데이터 품질)

> **배경**: HTR2A/2B/2C 구조 데이터를 수동 리서치 결과와 비교한 결과,
> `tools/gpcrdb.py`의 GPCRdb 데이터 파싱 로직에서 4개 버그를 발견했다.
> 아래 수정 사항을 순서대로 `tools/gpcrdb.py`와 `tools/parser.py`에 반영할 것.

---

### Bug 1 (Critical) — 잘못된 Primary Ligand 선택 로직

**증상**: 8JT8 구조에서 Ligand가 "IHCH-7179 / Antagonist" 대신 "EZX / Agonist"로 표시됨.

**원인**: `get_gpcrdb_structures()`에서 `"function": "binding"`으로 primary ligand를 선택하지만,
GPCRdb API 실제 응답에서 결합 리간드의 function 값이 `"binding"`이 아닌 다른 값
(`"antagonist"`, `"agonist"` 등 pharmacology 관련 값)으로 내려오는 경우가 있음.
`"binding"` 키를 찾지 못하면 `ligands[0]`(첫 번째 리간드, 즉 용매/buffer)이 선택되는 버그.

**수정**: `tools/gpcrdb.py`의 primary ligand 선택 로직을 아래로 교체할 것.

```python
def select_primary_ligand(ligands: list[dict]) -> dict | None:
    """
    GPCRdb ligands 배열에서 연구 대상 리간드(약물)를 선택한다.
    우선순위:
    1. 'role' 또는 'type' 필드가 있는 것 중 'Ligand' / 'inhibitor' / 'agonist' / 'antagonist' 류
    2. function 필드가 'antagonist', 'agonist', 'inverse_agonist', 'partial_agonist', 'binding' 인 것
    3. 위 모두 없으면 PDB 용매/버퍼를 제외한 첫 번째 항목
    4. 그래도 없으면 None
    """
    if not ligands:
        return None

    # GPCRdb가 'function' 필드에 실제로 사용하는 값 목록
    PHARMACOLOGICAL_FUNCTIONS = {
        "agonist", "antagonist", "inverse_agonist", "inverse agonist",
        "partial_agonist", "partial agonist", "ago-antagonist",
        "allosteric_modulator", "pam", "nam", "binding"
    }

    # 일반적인 용매/버퍼 PDB 코드 (이것들은 primary가 아님)
    SOLVENT_CODES = {
        "EDO", "GOL", "PEG", "PG4", "MPD", "FMT", "ACE", "ACT",
        "DMS", "SO4", "CL", "NA", "MG", "ZN", "CA", "K", "FE",
        "HOH", "WAT", "H2O"
    }

    # 1순위: pharmacological function이 명시된 것
    for lig in ligands:
        fn = (lig.get("function") or lig.get("function_label") or "").lower().replace("-", "_")
        if fn in PHARMACOLOGICAL_FUNCTIONS and fn != "binding":
            name = lig.get("name") or lig.get("pdb_code") or ""
            if name.upper() not in SOLVENT_CODES:
                return lig

    # 2순위: function == "binding"인 것 (단, 용매 제외)
    for lig in ligands:
        fn = (lig.get("function") or lig.get("function_label") or "").lower()
        name = lig.get("name") or lig.get("pdb_code") or ""
        if fn == "binding" and name.upper() not in SOLVENT_CODES:
            return lig

    # 3순위: 용매가 아닌 첫 번째 항목
    for lig in ligands:
        name = lig.get("name") or lig.get("pdb_code") or ""
        if name.upper() not in SOLVENT_CODES:
            return lig

    return None
```

`get_gpcrdb_structures()` 내부에서 기존 primary_ligand 선택 코드를 아래로 교체:

```python
# 기존 (삭제)
# primary_ligand = next(
#     (l for l in ligands if l.get("function") == "binding"),
#     ligands[0] if ligands else None
# )

# 교체
primary_ligand = select_primary_ligand(ligands)
```

---

### Bug 2 (Critical) — Ligand 이름이 PDB 코드로 표시됨 (PubChem 미연동)

**증상**: 리간드 이름이 `3IQ`, `EZX`, `CHEMBL428892`, `YEQ` 등 PDB 코드로 표시됨.
연구원이 읽을 수 있는 일반명(IHCH-7179, R-69, Methiothepin)으로 보여줘야 함.

**원인**: GPCRdb API가 리간드 이름으로 PDB 코드를 반환하는 경우가 있음. PubChem API 연동 없음.

**수정**: `tools/gpcrdb.py`에 PubChem 이름 조회 함수를 추가하고, ligand 이름 확정 시 호출할 것.

```python
# tools/gpcrdb.py 상단에 추가
LIGAND_NAME_CACHE: dict[str, str] = {}  # 모듈 레벨 캐시 (PDB code → 일반명)

# 알려진 PDB 코드 → 일반명 사전 (API 실패 시 fallback)
KNOWN_LIGAND_NAMES: dict[str, str] = {
    "3IQ":  "R-69",
    "EZX":  "IHCH-7179",
    "YEQ":  "Methiothepin",
    "LSD":  "LSD",
    "PSI":  "Psilocin",
    "LIS":  "Lisuride",
    "ZOL":  "Zolpidem",
    "CLZ":  "Clozapine",
    "RSP":  "Risperidone",
    "LUM":  "Lumateperone",
    "QTP":  "Quetiapine",
    "OLZ":  "Olanzapine",
    "ARI":  "Aripiprazole",
    "5HT":  "Serotonin",
    "DOM":  "Dopamine",
    "NE":   "Norepinephrine",
    "EPI":  "Epinephrine",
    "ALR":  "Alprenolol",
    "CAR":  "Carazolol",
    "TIM":  "Timolol",
    "SLB":  "Salbutamol",
    "FMT":  "Formoterol",
    "LAB":  "Labetalol",
    "ERG":  "Ergotamine",
    "MTH":  "Methiothepin",
}


async def resolve_ligand_name(raw_name: str | None, client: httpx.AsyncClient) -> str | None:
    """
    GPCRdb에서 받은 리간드 이름/코드를 사람이 읽을 수 있는 일반명으로 변환.
    
    우선순위:
    1. 이미 영문 일반명처럼 보이면 (공백 포함, 숫자만은 아닌 경우) 그대로 반환
    2. KNOWN_LIGAND_NAMES 사전 조회
    3. PubChem REST API 조회
    4. 모두 실패 시 원래 이름 반환
    """
    if not raw_name:
        return None

    raw = raw_name.strip()

    # 이미 일반명처럼 보이는 경우: 공백이 있거나, 하이픈+숫자 패턴이면 그대로 반환
    # 예: "IHCH-7179", "R-69", "Methiothepin" → 그대로
    # PDB 코드 패턴: 3자리 영문/숫자 (예: EZX, 3IQ, YEQ)
    import re
    is_pdb_code = bool(re.fullmatch(r"[A-Z0-9]{3,4}", raw.upper()))

    if not is_pdb_code:
        return raw  # 이미 일반명

    upper = raw.upper()

    # 모듈 캐시 확인
    if upper in LIGAND_NAME_CACHE:
        return LIGAND_NAME_CACHE[upper]

    # 알려진 이름 사전 확인
    if upper in KNOWN_LIGAND_NAMES:
        resolved = KNOWN_LIGAND_NAMES[upper]
        LIGAND_NAME_CACHE[upper] = resolved
        return resolved

    # PubChem REST API 조회
    try:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{raw}/property/IUPACName,Title/JSON"
        resp = await client.get(url, timeout=8.0)
        if resp.status_code == 200:
            props = resp.json().get("PropertyTable", {}).get("Properties", [])
            if props:
                # Title이 있으면 우선 (더 짧고 사람이 읽기 좋음)
                resolved = props[0].get("Title") or props[0].get("IUPACName") or raw
                LIGAND_NAME_CACHE[upper] = resolved
                return resolved
    except Exception:
        pass  # PubChem 실패 시 원래 이름 반환

    return raw
```

`get_gpcrdb_structures()` 함수 시그니처에 `client` 인자가 이미 있으므로,
ligand name 확정 직후 `resolve_ligand_name` 호출 코드를 추가:

```python
# get_gpcrdb_structures() 내부, primary_ligand 선택 직후에 추가
raw_ligand_name = primary_ligand.get("name") or primary_ligand.get("pdb_code") if primary_ligand else None
resolved_ligand_name = await resolve_ligand_name(raw_ligand_name, client)

result[pdb_id] = {
    "pref_chain":        s.get("preferred_chain"),
    "state":             s.get("state"),
    "ligand":            resolved_ligand_name,           # ← raw 대신 resolved 사용
    "ligand_modality":   primary_ligand.get("function") or primary_ligand.get("function_label") if primary_ligand else None,
    "signaling_protein": parse_signaling_protein(s.get("signalling_protein")),
    "fusion_protein":    fusion,
    "antibody":          antibody,
}
```

> **주의**: `get_gpcrdb_structures()`가 비동기 함수이므로 `await resolve_ligand_name(...)` 사용 가능.
> PubChem 조회가 느릴 수 있으므로 `asyncio.gather`로 병렬 처리하는 것을 권장:
>
> ```python
> # 병렬 처리 버전 (권장)
> name_tasks = [
>     resolve_ligand_name(s_data["raw_ligand"], client)
>     for s_data in temp_results
> ]
> resolved_names = await asyncio.gather(*name_tasks, return_exceptions=True)
> ```

---

### Bug 3 (Major) — Fusion protein(BRIL) 전혀 표시 안 됨

**증상**: X-ray 5-HT₂A 구조 전체에서 Fusion protein이 "-"로 표시됨.
7WC7, 7WC5, 8JT8 등 BRIL 융합 단백질이 확인된 구조들도 모두 누락.

**원인 후보 (두 가지 중 하나 또는 둘 다)**:

1. GPCRdb `stabilizing_agents` 필드의 실제 API 응답 구조가 예상과 다름.
   예: 배열이 아닌 딕셔너리, 또는 `"name"` 키가 아닌 다른 키 사용.

2. `parse_stabilizing_agents()` 함수가 `gpcrdb_map`에 데이터가 있는 구조에서만 호출되고,
   `parser.py` fallback은 `gpcrdb_map.get(entry.pdb_id)` 결과가 없을 때만 실행되는데,
   GPCRdb에 구조는 있지만 `stabilizing_agents`가 빈 배열 `[]`로 오는 경우
   fusion_protein이 None이 되고 fallback도 실행되지 않는 문제.

**수정 1**: `parse_stabilizing_agents()` 함수에 디버그 로깅 추가하고 응답 구조 확인:

```python
def parse_stabilizing_agents(agents) -> tuple[str | None, str | None]:
    """
    stabilizing_agents 파싱. agents가 list[dict] 또는 다른 형태일 수 있으므로
    방어적으로 처리.
    """
    # 실제 API 응답 형태 확인용 로깅 (디버그 시 활성화)
    import logging
    logger = logging.getLogger(__name__)

    if not agents:
        return None, None

    # agents가 list[str]인 경우 처리 (일부 GPCRdb 버전에서 문자열 배열로 옴)
    if isinstance(agents, list) and agents and isinstance(agents[0], str):
        fusions, antibodies = [], []
        for name in agents:
            name_lower = name.lower()
            if any(k in name_lower for k in FUSION_KEYWORDS):
                fusions.append(name)
            elif any(k in name_lower for k in ANTIBODY_KEYWORDS):
                antibodies.append(name)
        return (
            " / ".join(fusions) if fusions else None,
            " / ".join(antibodies) if antibodies else None,
        )

    # agents가 list[dict]인 경우 처리 (기존 로직)
    fusions, antibodies = [], []
    for agent in agents:
        if isinstance(agent, dict):
            # "name" 키가 없는 경우 다른 키 시도
            name = (
                agent.get("name") or
                agent.get("display_name") or
                agent.get("protein_name") or
                str(agent)
            ).lower()
            display = agent.get("name") or agent.get("display_name") or str(agent)
        elif isinstance(agent, str):
            name = agent.lower()
            display = agent
        else:
            continue

        if any(k in name for k in FUSION_KEYWORDS):
            fusions.append(display)
        elif any(k in name for k in ANTIBODY_KEYWORDS):
            antibodies.append(display)

    return (
        " / ".join(fusions) if fusions else None,
        " / ".join(antibodies) if antibodies else None,
    )
```

**수정 2**: `handle_search_target()`의 GPCRdb 병합 로직을 수정하여,
GPCRdb 데이터가 있는 구조에서도 fusion_protein이 None이면 `parser.py` fallback을 적용:

```python
# server.py handle_search_target() 내부 GPCRdb 병합 섹션 교체

for entry in pdb_entries:
    gpcr_data = gpcrdb_map.get(entry.pdb_id)
    if gpcr_data:
        entry.is_gpcr           = True
        entry.pref_chain        = gpcr_data["pref_chain"]
        entry.state             = gpcr_data["state"]
        entry.ligand            = gpcr_data["ligand"]
        entry.ligand_modality   = normalize_modality(gpcr_data["ligand_modality"])
        entry.signaling_protein = gpcr_data["signaling_protein"]
        entry.fusion_protein    = gpcr_data["fusion_protein"]
        entry.antibody          = gpcr_data["antibody"]

        # ★ 핵심 수정: GPCRdb에 구조는 있지만 stabilizing_agents가 비어있는 경우
        # parser.py fallback으로 제목에서 추출 시도
        if entry.fusion_protein is None and entry.title:
            entry.fusion_protein = extract_fusion_from_title(entry.title)
        if entry.antibody is None and entry.title:
            entry.antibody = extract_antibody_from_title(entry.title)

    elif is_gpcr:
        # GPCRdb에 없는 최신 구조 → 제목 파싱 fallback
        entry.is_gpcr        = True
        entry.fusion_protein = extract_fusion_from_title(entry.title or "")
        entry.antibody       = extract_antibody_from_title(entry.title or "")
```

**수정 3**: `tools/parser.py`의 FUSION_KEYWORDS와 ANTIBODY_PATTERNS에 누락 패턴 추가:

```python
# tools/parser.py 업데이트

FUSION_KEYWORDS = [
    "bril", "t4l", "t4 lysozyme", "mt4l", "flavodoxin",
    "rubredoxin", "gsα", "thermostabilized", "apocytochrome"
]

FUSION_PATTERNS = [
    (r'\bBRIL\b',                    'BRIL'),
    (r'\bmT4L\b',                    'mT4L'),
    (r'\bT4[- ]?[Ll]ysozyme\b',     'T4L'),
    (r'\bT4L\b',                     'T4L'),
    (r'\bflavodoxin\b',              'Flavodoxin'),
    (r'\brubredoxin\b',              'Rubredoxin'),
    (r'\bapocytochrome\s*b562\b',    'BRIL'),   # BRIL의 다른 이름
]

ANTIBODY_PATTERNS = [
    (r'\b[\w\-]+-Fab\b',    None),   # "P2C2-Fab" → 그대로
    (r'\bFab\d*\b',         'Fab'),
    (r'\bNanobody\b',        'Nanobody'),
    (r'\bNb\s*\d+\b',       None),   # "Nb35" → 그대로
    (r'\bVHH\b',             'VHH'),
    (r'\bscFv\b',            'scFv'),
    (r'\bantibody\b',        'Antibody'),
]
```

---

### Bug 4 (Major) — Antibody(P2C2-Fab) 누락

**증상**: 5TUD 구조에서 Antibody가 "-"로 표시됨. 실제로는 P2C2-Fab 결합 구조.

**원인**: Bug 3과 동일 원인. GPCRdb `stabilizing_agents`가 빈 배열이거나,
`"Fab"` 키워드 매칭에서 `"P2C2-Fab"` 패턴을 놓침.

**수정**: Bug 3의 수정(ANTIBODY_PATTERNS 업데이트 + fallback 강화)으로 함께 해결됨.
추가로 `extract_antibody_from_title()` 검증 테스트 케이스 추가:

```python
# tests/test_gpcrdb.py에 추가
def test_antibody_p2c2_fab():
    title = "Crystal Structure of 5-HT2A Receptor Bound to P2C2-Fab"
    assert extract_antibody_from_title(title) == "P2C2-Fab"

def test_antibody_fab_generic():
    title = "5-HT2B with Fab fragment"
    result = extract_antibody_from_title(title)
    assert result is not None and "Fab" in result
```

---

### Bug 5 (Minor) — Ligand 이름 대소문자 불일치

**증상**: "lisuride"와 "Lisuride", "LUMATEPERONE"과 "Lumateperone"이 혼재.

**수정**: `tools/gpcrdb.py`에 `normalize_ligand_name()` 함수 추가.
`resolve_ligand_name()` 내부 마지막 단계에서 호출:

```python
# 약물 이름 별칭 사전 (표준화)
LIGAND_ALIASES: dict[str, str] = {
    "5-hydroxytryptamine":  "Serotonin",
    "5ht":                  "Serotonin",
    "lysergide":            "LSD",
    "lysergic acid diethylamide": "LSD",
    "lumateperone":         "Lumateperone",
    "lisuride":             "Lisuride",
    "psilocin":             "Psilocin",
    "risperidone":          "Risperidone",
    "ketanserin":           "Ketanserin",
    "clozapine":            "Clozapine",
    "olanzapine":           "Olanzapine",
    "aripiprazole":         "Aripiprazole",
    "quetiapine":           "Quetiapine",
    "ergotamine":           "Ergotamine",
    "methiothepin":         "Methiothepin",
    "zolpidem":             "Zolpidem",
}


def normalize_ligand_name(name: str | None) -> str | None:
    """
    리간드 이름을 표준 형태로 정규화.
    1. 별칭 사전 우선 적용
    2. Title case로 변환 (전체 대문자인 경우)
    3. 짧은 PDB 코드(3자 대문자)는 그대로 유지
    """
    if not name:
        return None

    # 별칭 사전 (소문자로 비교)
    lower = name.lower().strip()
    if lower in LIGAND_ALIASES:
        return LIGAND_ALIASES[lower]

    # 전체 대문자이고 4자 이상이면 Title case로 변환
    # (예: LUMATEPERONE → Lumateperone, LSD → LSD 유지)
    import re
    if name.isupper() and len(name) > 4 and not re.fullmatch(r"[A-Z0-9]{3,4}", name):
        return name.title()

    return name
```

`resolve_ligand_name()` 반환 직전에 normalize 호출:

```python
# resolve_ligand_name() 함수 내 최종 반환 전에 추가
resolved = normalize_ligand_name(resolved)
return resolved
```

---

### Phase 4 수정 후 검증 테스트

Phase 4 수정이 완료되면 아래 단위 테스트를 `tests/test_gpcrdb.py`에 추가하고 전체 실행:

```python
import pytest
import asyncio
import httpx

# Bug 1 검증: primary ligand 선택
def test_select_primary_ligand_pharmacological():
    """pharmacological function이 있는 리간드 우선 선택"""
    from tools.gpcrdb import select_primary_ligand
    ligands = [
        {"name": "GOL", "function": "buffer"},        # 용매
        {"name": "IHCH-7179", "function": "antagonist"},  # 약물
    ]
    result = select_primary_ligand(ligands)
    assert result["name"] == "IHCH-7179"

def test_select_primary_ligand_skip_solvent():
    """용매만 있으면 None 반환"""
    from tools.gpcrdb import select_primary_ligand
    ligands = [
        {"name": "GOL", "function": "binding"},
        {"name": "SO4", "function": "binding"},
    ]
    result = select_primary_ligand(ligands)
    assert result is None

# Bug 2 검증: PubChem 이름 조회
@pytest.mark.asyncio
async def test_resolve_known_ligand_code():
    """알려진 PDB 코드 → 일반명 변환"""
    from tools.gpcrdb import resolve_ligand_name
    async with httpx.AsyncClient() as client:
        result = await resolve_ligand_name("3IQ", client)
        assert result == "R-69"

@pytest.mark.asyncio
async def test_resolve_already_common_name():
    """이미 일반명이면 그대로 반환"""
    from tools.gpcrdb import resolve_ligand_name
    async with httpx.AsyncClient() as client:
        result = await resolve_ligand_name("Lisuride", client)
        assert result == "Lisuride"

# Bug 3 검증: Fusion protein 파싱
def test_parse_stabilizing_agents_string_list():
    """stabilizing_agents가 문자열 배열인 경우"""
    from tools.gpcrdb import parse_stabilizing_agents
    agents = ["BRIL", "T4L"]
    fusion, antibody = parse_stabilizing_agents(agents)
    assert "BRIL" in (fusion or "")

def test_parse_stabilizing_agents_dict_list():
    """stabilizing_agents가 딕셔너리 배열인 경우"""
    from tools.gpcrdb import parse_stabilizing_agents
    agents = [{"name": "BRIL"}, {"name": "P2C2-Fab"}]
    fusion, antibody = parse_stabilizing_agents(agents)
    assert fusion == "BRIL"
    assert "Fab" in (antibody or "")

def test_fusion_title_fallback_bril():
    """PDB 제목에서 BRIL 추출"""
    from tools.parser import extract_fusion_from_title
    title = "Crystal structure of HTR2A-BRIL in complex with antagonist"
    assert extract_fusion_from_title(title) == "BRIL"

# Bug 5 검증: 이름 정규화
def test_normalize_lumateperone():
    from tools.gpcrdb import normalize_ligand_name
    assert normalize_ligand_name("LUMATEPERONE") == "Lumateperone"

def test_normalize_lisuride():
    from tools.gpcrdb import normalize_ligand_name
    assert normalize_ligand_name("lisuride") == "Lisuride"

def test_normalize_lsd_alias():
    from tools.gpcrdb import normalize_ligand_name
    assert normalize_ligand_name("Lysergide") == "LSD"

# 통합 검증: HTR2A 8JT8 구조 전체
@pytest.mark.asyncio
async def test_htr2a_8jt8_full():
    """8JT8이 올바른 Ligand/Modality/Fusion protein을 가지는지 검증"""
    from tools.gpcrdb import get_gpcrdb_structures
    async with httpx.AsyncClient() as client:
        result = await get_gpcrdb_structures("5ht2a_human", client)
    assert "8JT8" in result
    entry = result["8JT8"]
    # Ligand는 IHCH-7179 또는 그에 해당하는 이름
    assert entry["ligand"] is not None
    assert entry["ligand"] != "EZX"  # PDB 코드가 그대로 남으면 안 됨
    # Modality는 Antagonist
    assert entry["ligand_modality"] is not None
    assert "antagonist" in entry["ligand_modality"].lower()
    # Fusion protein은 BRIL
    assert entry["fusion_protein"] is not None
    assert "bril" in entry["fusion_protein"].lower()
```

---

---

### Bug 6 (Major) — Resolution이 소수점 없이 정수로 표시됨

**증상**: 6A93, 8V6U 등 일부 구조의 Resolution이 `3.00` 대신 `3`으로 표시됨.
연구원 수동 리서치 표는 모두 소수점 2자리(`3.00`, `2.45`, `2.60`)로 통일되어 있음.

**원인**: 두 가지 문제가 겹쳐 있음.

1. RCSB PDB API가 `resolution_combined: [3.0]`을 반환할 때 JSON 파싱 후 Python `float`가 되지만,
   일부 구조에서 `3` (integer JSON)으로 내려와 Python `int(3)`으로 저장됨.
   openpyxl이 `int`를 쓸 때 Excel이 `General` 포맷으로 `3`을 표시.
2. `tools/export.py`의 Resolution 컬럼 `number_format`이 `'General'`로 설정되어 있어
   소수점 자릿수를 통일하지 않음.

**수정 1**: `tools/pdb.py`에서 resolution 파싱 시 `float()` 강제 형변환:

```python
# tools/pdb.py — fetch_single_entry() 내부 resolution 파싱 부분

resolution_list = entry["rcsb_entry_info"].get("resolution_combined")

# 수정 전 (버그)
# resolution = resolution_list[0] if resolution_list else None

# 수정 후: 항상 float으로 변환
resolution = float(resolution_list[0]) if resolution_list else None
```

**수정 2**: `tools/export.py`에서 Resolution 컬럼 셀 서식을 `'0.00'`으로 고정:

```python
# tools/export.py — Resolution 셀을 쓰는 부분에 number_format 지정

# GPCR 확장 테이블과 기본 테이블 모두 동일하게 적용
# Resolution 컬럼 헤더가 "Res.(Å)" 또는 "Resolution (Å)"인 컬럼 전체에 적용

for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    for cell in row:
        if cell.column == resolution_col_index:   # Resolution 컬럼 번호
            cell.number_format = '0.00'
            # 값도 float으로 보장
            if cell.value is not None:
                cell.value = float(cell.value)
```

또는 컬럼 전체에 포맷을 적용하는 더 간단한 방법:

```python
# export.py에서 Resolution 컬럼 인덱스를 찾아 전체 적용
from openpyxl.utils import get_column_letter

# 헤더에서 Resolution 컬럼 찾기
res_col_idx = None
for col_idx, cell in enumerate(ws[1], start=1):
    if cell.value and "res" in str(cell.value).lower():
        res_col_idx = col_idx
        break

if res_col_idx:
    col_letter = get_column_letter(res_col_idx)
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row,
                             min_col=res_col_idx, max_col=res_col_idx):
        for cell in row:
            if cell.value is not None:
                cell.value = float(cell.value)   # int → float 보장
                cell.number_format = '0.00'      # 소수점 2자리 고정
```

**검증**: 수정 후 HTR2A Excel 재생성 시 아래 확인:
- 6A93: `3` → `3.00`
- 8V6U: `3` → `3.00`
- 7WC8: `2.45` → `2.45` (변화 없어야 함)
- 7WC6: `2.6` → `2.60` (자릿수 통일)

---

### Phase 4 구현 순서

1. `tools/pdb.py` 수정:
   - `fetch_single_entry()` 내 resolution 파싱 시 `float()` 강제 형변환 (Bug 6)

2. `tools/export.py` 수정:
   - Resolution 컬럼 셀 `number_format = '0.00'` 고정 (Bug 6)
   - Resolution 셀 값 `float()` 변환 보장 (Bug 6)

3. `tools/gpcrdb.py` 수정:
   - `select_primary_ligand()` 함수 추가 (Bug 1)
   - `LIGAND_NAME_CACHE`, `KNOWN_LIGAND_NAMES`, `LIGAND_ALIASES` 상수 추가 (Bug 2, 5)
   - `resolve_ligand_name()` 함수 추가 — 비동기 (Bug 2)
   - `normalize_ligand_name()` 함수 추가 (Bug 5)
   - `parse_stabilizing_agents()` 함수 방어 로직 강화 (Bug 3, 4)
   - `get_gpcrdb_structures()` 내부 호출 순서 업데이트 (Bug 1, 2)

4. `tools/parser.py` 수정:
   - `FUSION_KEYWORDS` 확장 (Bug 3)
   - `FUSION_PATTERNS` + `ANTIBODY_PATTERNS` 업데이트 (Bug 3, 4)

5. `server.py` 수정:
   - `handle_search_target()` GPCRdb 병합 섹션에 "GPCRdb 있어도 None이면 fallback" 로직 추가 (Bug 3, 4)

6. `tests/test_gpcrdb.py` 수정:
   - 위 검증 테스트 케이스 전체 추가

7. 실제 HTR2A 데이터로 통합 테스트:
   - Claude Desktop에서 "HTR2A 구조 분석해줘 Excel 저장해줘" 실행
   - 6A93: `3` → `3.00` 표시 확인 (Bug 6)
   - 7WC6: `2.6` → `2.60` 표시 확인 (Bug 6)
   - 8JT8: Ligand=IHCH-7179, Modality=Antagonist, Fusion=BRIL 확인 (Bug 1, 2, 3)
   - 5TUD: Antibody=P2C2-Fab 확인 (Bug 4)
   - 7WC7: Fusion=BRIL 확인 (Bug 3)
   - 모든 Ligand 이름이 PDB 코드가 아닌 일반명인지 확인 (Bug 2)
