# PDB Research MCP — 이용 가이드 및 설명서

> **버전**: 0.2.0 · **빌드일**: 2026-05-26 · **도구 개수**: 13개
> **대상**: 나무아이씨티 신약연구소 연구원
> **목적**: 신약 리서치 과정에서 Claude가 "그럴듯하게 추측"하는 대신, 권위 있는 공개 DB의 원본 값을 가져와 보여주는 어시스턴트

---

## 1. 이 MCP가 하는 일 (한 줄 요약)

연구원의 자연어 질문(예: "HTR2A 구조 정리해줘", "lisuride 임상 단계", "PMID 32555340 결론") 을 받아,
**PDB / UniProt / GPCRdb / PubChem / ChEMBL / IUPHAR / Europe PMC / PDBe / AlphaFold / OpenTargets** 등
10여 개의 공개 데이터베이스에서 사실(fact)을 직접 가져와 표/요약 형태로 반환한다.

Claude는 가져온 데이터를 그대로 보여주고, **모르는 값은 "-" 로 표시한다** — 절대 추측으로 채우지 않는다.

---

## 2. 설치 (5분)

### 2-1. 설치 파일 위치

| 플랫폼 | 파일 |
|--------|------|
| macOS (Apple Silicon) | `dist/pdb-mcp-server-macos-arm64.mcpb` |
| Windows x64 | `dist/pdb-mcp-server-win-x64.mcpb` |

`.dxt` 파일도 같은 폴더에 있음(동일 내용, 구버전 Claude Desktop 호환용).

### 2-2. 설치 절차 (Claude Desktop)

1. **Claude Desktop** 완전 종료 (⌘Q / 시스템 트레이 우클릭 → 종료)
2. 다시 실행 → **Settings → Extensions** 메뉴
3. 위 `.mcpb` 파일을 **드래그앤드롭** 또는 *Install Extension* 버튼으로 추가
4. (선택) 출력 폴더 설정 — 기본값 `~/Documents/PDBMCP` 그대로 두면 됨
5. 채팅창 좌측 하단 **망치(🔨) 아이콘** 클릭 → **13개 도구**가 보이면 정상 설치 완료

### 2-3. 업데이트
새 `.mcpb` 파일이 나오면, Settings → Extensions 에서 기존 항목을 **Update** 하거나
한 번 제거 후 새 파일 드래그하면 된다.

---

## 3. 13개 도구 한눈에 보기

### A. PDB 구조 검색 (Phase 1–4)

| 도구 | 한 줄 설명 | 입력 예시 |
|------|----------|----------|
| `search_target` | 단백질 1개의 PDB 구조 전체 표 | "EGFR 분석" / "HTR2A" |
| `search_family` | 패밀리(여러 수용체)의 통합 검색 | "5-HT2 패밀리 정리" |
| `get_pdb_detail` | 특정 PDB ID 1개의 상세 | "7WC7 자세히" |
| `compare_targets` | 여러 타깃 구조 수·해상도 비교 | "DRD1~5 비교" |

### B. 리서치 보조 (Phase 5 — 할루시네이션 방지)

| 도구 | 한 줄 설명 | 외부 API |
|------|----------|---------|
| `get_ligand_detail` | 화합물의 SMILES·MW·LogP·임상 phase | PubChem + ChEMBL + IUPHAR |
| `get_target_bioactivities` | 타깃에 대한 Ki/Kd/IC50/EC50 | ChEMBL + IUPHAR/GtoPdb |
| `get_paper_abstract` | PMID/DOI로 논문 초록 | Europe PMC + PubMed |
| `search_papers` | 키워드로 논문 검색 | Europe PMC |
| `get_sequence_region` | UniProt 서열 + feature | UniProt |
| `get_natural_variants` | 알려진 변이 + 질환 연관 | UniProt Variation |
| `get_binding_site` | PDB 결합부위 잔기 | PDBe + RCSB |
| `get_alphafold_model` | 예측 구조 + pLDDT | AlphaFold DB |
| `get_target_intelligence` | 질환 연관 + 알려진 약물 | OpenTargets Platform |

---

## 4. 도구별 상세 사용법

각 도구는 **자연어로 부탁하면 Claude가 자동 호출**한다.
도구를 직접 부르지 않아도 되지만, 트리거 키워드를 알면 더 정확히 호출된다.

### A. PDB 구조 검색

#### 4-A-1. `search_target` — 타깃 1개의 PDB 구조 전체

**자연어 트리거**: "EGFR 분석해줘", "HTR2A 구조 정리"

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `target` (필수) | string | 유전자명 / UniProt 검색어. 별칭 사전 자동 적용 |
| `max_structures` | int | 표시 상한 (미입력 시 전체) |
| `sort_by` | enum | `date` / `resolution` / `state_then_date` (GPCR 기본) |
| `max_resolution` | float | "고해상도" 언급 시 자동 2.5 |
| `min_year` | int | "최근 5년" 자동 변환 |
| `ligand_modality_filter` | string | "Antagonist만" 등 |
| `state_filter` | string | "Active만" 등 |
| `method_filter` | string | "Cryo-EM만" 등 |
| `export_excel` | bool | 기본 false. Claude의 xlsx 스킬 사용 권장 |

**별칭 사전 예시** (description에 내장 — 그대로 입력해도 자동 변환):
- "세로토닌 2A" / "5-HT2A" → `HTR2A`
- "베타2 아드레날린" → `ADRB2`
- "p53" → `TP53`

**예시 입력 → 동작**:
- "EGFR 분석" → 비GPCR 기본 테이블 (PDB ID / Resolution / Method / Date / 논문)
- "HTR2A 고해상도만" → GPCR 확장 테이블 + `max_resolution=2.5` 자동
- "DRD2 Antagonist Cryo-EM" → `ligand_modality_filter=Antagonist`, `method_filter=EM` 자동

#### 4-A-2. `search_family` — 패밀리 통합 검색

**자연어 트리거**: "세로토닌 2 패밀리", "5-HT2 전체", "HTR2A/B/C 정리"

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `targets` (필수) | string[] | 타깃 목록. 예: `["HTR2A","HTR2B","HTR2C"]` |
| `family_name` | string | 라벨. 예: "5-HT2_family" |
| (그 외 필터는 `search_target` 과 동일) |

**자동 확장 규칙**:
- "세로토닌 2" → `[HTR2A, HTR2B, HTR2C]`
- "도파민 수용체" → `[DRD1..DRD5]`
- "아드레날린" → `[ADRB1, ADRB2, ADRB3]`
- "EGFR 패밀리" → `[EGFR, ERBB2, ERBB3, ERBB4]`

#### 4-A-3. `get_pdb_detail` — PDB ID 1개 상세

**자연어 트리거**: "7WC7 자세히 알려줘", "8JT8 정보"

| 파라미터 | 타입 |
|---------|------|
| `pdb_id` (필수) | string (4자리) |

GPCR이면 State / Ligand / Modality / Signaling protein / Fusion / Antibody 포함,
비GPCR이면 Resolution / Method / Date / 논문 ACS 인용.

#### 4-A-4. `compare_targets` — 여러 타깃 비교

**자연어 트리거**: "DRD1~5 구조 수 비교", "HER2랑 EGFR 차이"

| 파라미터 | 타입 |
|---------|------|
| `targets` (필수) | string[] |

각 타깃별 구조 수 / 최고 해상도 / 최신 구조 표.

---

### B. 리서치 보조 — 할루시네이션 방지

#### 4-B-1. `get_ligand_detail` — 화합물 상세

**자연어 트리거**: "lisuride 구조 알려줘", "risperidone SMILES", "이 약 임상 몇 상?"

| 파라미터 | 타입 |
|---------|------|
| `query` (필수) | string — 이름 / PDB chem code / `CHEMBLxxxx` / InChIKey |

**예시 출력 항목**:
- 식별자: PubChem CID / ChEMBL ID / IUPHAR ID / InChIKey
- 화학: SMILES / InChI / 분자식 / **MW (g/mol)** / **XLogP** / H-bond donors·acceptors / TPSA / 회전결합 수
- 신약 단계: **Max phase** (Approved / Phase 1–3 / 전임상)
- Synonyms 상위 10개
- 출처 URL (PubChem · ChEMBL · IUPHAR 각각)

#### 4-B-2. `get_target_bioactivities` — 타깃에 대한 활성 데이터

**자연어 트리거**: "HTR2A에 강한 antagonist", "이 타깃 Ki nM 이하 화합물"

| 파라미터 | 타입 | 기본 |
|---------|------|------|
| `uniprot_accession` (필수) | string | — |
| `gene_symbol` | string | IUPHAR 보강용. 없으면 ChEMBL만 |
| `min_pchembl` | number | 6.0 (≈ Ki 1 µM). 0이면 컷오프 없음 |
| `standard_types` | string[] | `["Ki","Kd","IC50","EC50"]` |
| `max_results` | int | 30 |
| `include_iuphar` | bool | true |

**출력 표**: 순위 · 리간드 · Type · 값 · 단위 · **pChEMBL** · Assay 설명 · 출처(ChEMBL/IUPHAR) · 논문 PMID

> 💡 **pChEMBL** = `-log10(value in M)`. 7 = 100 nM, 8 = 10 nM, 9 = 1 nM, 10 = 0.1 nM.

#### 4-B-3. `get_paper_abstract` — 논문 초록

**자연어 트리거**: "PMID 32555340 결론", "DOI 10.xxx 논문 초록"

| 파라미터 | 타입 |
|---------|------|
| `pmid` | string |
| `doi` | string |

둘 중 **하나 이상** 필수. 우선순위: Europe PMC → PubMed E-utils fallback.

**출력**: 제목 / 저자(상위 5명) / 저널·연도·권·페이지 / **전체 초록** (섹션 라벨 보존) / MeSH terms / Open access 여부 / 출처 URL

#### 4-B-4. `search_papers` — 키워드 논문 검색

**자연어 트리거**: "HTR2A psychedelic 최근 논문", "GPCR allosteric review"

| 파라미터 | 타입 |
|---------|------|
| `query` (필수) | string (Europe PMC 쿼리 문법) |
| `max_results` | int (1~25, 기본 5) |

**출력**: 상위 N개 — 제목 / 저자(상위 3명) / 저널·연도 / PMID·DOI / 초록 미리보기(280자) / 출처 URL

#### 4-B-5. `get_sequence_region` — UniProt 서열 + feature

**자연어 트리거**: "HTR2A 222번 잔기", "EGFR ATP-binding site", "이 타깃 transmembrane helix"

| 파라미터 | 타입 |
|---------|------|
| `accession` (필수) | string — UniProt ID (예: P28223) |
| `start`, `end` | int — 1-based 잔기 범위 (생략 시 전체) |
| `feature_types` | string[] — 예: `["Binding site","Active site","Transmembrane"]` |

**출력**: 구간 서열 (FASTA-like, 60글자/줄, 잔기 번호 표기) + 해당 구간과 겹치는 feature 표 (타입 / 범위 / 설명 / 리간드 / 근거 ECO)

#### 4-B-6. `get_natural_variants` — 알려진 변이

**자연어 트리거**: "L858R 변이 알려져 있어?", "이 타깃 disease-causing mutation"

| 파라미터 | 타입 |
|---------|------|
| `accession` (필수) | string |
| `position` | int — 특정 잔기만 |
| `disease_only` | bool (기본 false) |
| `max_results` | int (기본 200) |

**출력**: 위치 / wild-type / variant / 설명 / 질환 / 임상의의 / dbSNP ID / ClinVar ID

#### 4-B-7. `get_binding_site` — PDB 결합부위 잔기

**자연어 트리거**: "7WC7 결합부위 잔기", "이 구조에서 risperidone과 접촉하는 잔기"

| 파라미터 | 타입 |
|---------|------|
| `pdb_id` (필수) | string (4자리) |
| `ligand_filter` | string — 특정 chem code만. 예: "RSP" |
| `skip_solvents` | bool (기본 true — HOH/EDO/GOL/이온 제외) |

**출력**: 결합부위별 — Site ID / 리간드 코드·이름 / 체인 / **잔기 리스트** (`PHE 340 (A)` 형태)

#### 4-B-8. `get_alphafold_model` — 예측 구조

**자연어 트리거**: "이 단백질 구조 없어?" → 실험구조 없을 때 fallback, "AlphaFold 모델 받고 싶어"

| 파라미터 | 타입 |
|---------|------|
| `uniprot_accession` (필수) | string |

**출력**: Entry ID / Organism / 길이 / **평균 pLDDT** + 신뢰도 라벨 (Very high > 90 / Confident 70–90 / Low 50–70 / Very low < 50) / PDB·CIF 다운로드 URL / PAE 이미지·데이터 URL

#### 4-B-9. `get_target_intelligence` — 질환 연관 + 임상 약물

**자연어 트리거**: "EGFR 어느 질환에 쓰여?", "이 타깃 임상 약물 정리"

| 파라미터 | 타입 |
|---------|------|
| `target_query` (필수) | string — gene symbol 또는 Ensembl ID |
| `max_diseases` | int (기본 15) |
| `max_drugs` | int (기본 15) |

**출력**:
- 식별자: gene / Ensembl ID / UniProt / biotype
- **연관 질환 표**: Disease / EFO ID / OpenTargets 종합 점수 / Therapeutic areas
- **Known drugs 표**: Drug / Type (Small molecule/Antibody 등) / Mechanism / Max phase (1~4) / 적응증

---

## 5. 자주 쓰는 워크플로우 (시나리오 예시)

### 시나리오 1 — 새 타깃 입문 (한 화면 요약)

연구원: "**ADGRG6 어떤 타깃이야? 임상 약물 있어?**"

Claude 자동 호출:
1. `search_target("ADGRG6")` → UniProt 매칭 + PDB 구조 수
2. `get_target_intelligence("ADGRG6")` → 질환 연관 + known drugs
3. 실험 구조 없으면 `get_alphafold_model(<accession>)` → AlphaFold pLDDT

### 시나리오 2 — 리간드-타깃 페어 사실 확인

연구원: "**HTR2A에 대한 risperidone Ki가 얼마였지?**"

Claude 자동 호출:
1. `get_target_bioactivities("P28223", gene_symbol="HTR2A", min_pchembl=8)`
   → ChEMBL + IUPHAR 표에서 risperidone 행 확인

### 시나리오 3 — 논문 결론 검증

연구원: "**PMID 32555340 결론이 뭐였어?**"

Claude 자동 호출:
1. `get_paper_abstract(pmid="32555340")` → 제목·초록 그대로

> **주의**: Claude는 초록 텍스트를 그대로 보여줍니다. 본문 추측·요약 해석은 하지 않습니다.

### 시나리오 4 — 변이 hotspot 확인

연구원: "**EGFR L858R, T790M, C797S 다 알려진 변이야?**"

Claude 자동 호출:
1. `get_natural_variants("P00533", position=858)` → L858R 확인
2. `get_natural_variants("P00533", position=790)` → T790M 확인
3. `get_natural_variants("P00533", position=797)` → C797S 확인

### 시나리오 5 — 구조 → 결합부위 → 화합물

연구원: "**7WC7 결합부위 잔기 알려주고, 거기 결합하는 화합물 상세도 같이.**"

Claude 자동 호출:
1. `get_pdb_detail("7WC7")` → 리간드 확인 (예: Lisuride)
2. `get_binding_site("7WC7")` → 핵심 잔기 목록
3. `get_ligand_detail("Lisuride")` → SMILES / MW / phase

### 시나리오 6 — 패밀리 비교 + Excel

연구원: "**5-HT2 패밀리 전체 정리해서 엑셀로**"

Claude 자동 호출:
1. `search_family(targets=["HTR2A","HTR2B","HTR2C"], family_name="5-HT2_family")`
2. 응답 데이터를 Claude의 xlsx 스킬이 받아서 .xlsx 파일 생성

---

## 6. 출력 해석 가이드

### 6-1. "값이 -" 일 때
해당 외부 DB에 등록된 정보가 없음을 의미. **절대 Claude가 모르는 값을 채우지 않는다**. 직접 확인하려면 응답 끝 **출처 URL**을 클릭.

### 6-2. "조회 실패" / "graceful degradation"
일부 외부 API가 일시 오류를 내도 다른 소스의 부분 결과는 반환된다. GPCRdb가 실패해도 PDB 기본 정보는 정상.

### 6-3. pChEMBL 값 빠른 변환표
| pChEMBL | Ki (M) | Ki (편의 표기) |
|---------|--------|---------------|
| 6.0 | 1×10⁻⁶ | 1 µM |
| 7.0 | 1×10⁻⁷ | 100 nM |
| 8.0 | 1×10⁻⁸ | 10 nM |
| 9.0 | 1×10⁻⁹ | 1 nM |
| 10.0 | 1×10⁻¹⁰ | 0.1 nM |

### 6-4. pLDDT 신뢰도
- **> 90 (Very high)**: 거의 실험 구조 수준. 잔기 측쇄 배치까지 신뢰
- **70–90 (Confident)**: 백본 매우 정확. 측쇄는 주의
- **50–70 (Low)**: 백본만 참고 가능
- **< 50 (Very low)**: 보통 IDR(intrinsically disordered region). 구조 추론 금지

---

## 7. 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| "도구를 찾을 수 없다" 류 에러 | Claude Desktop이 옛 .mcpb를 보고 있음 | Extensions에서 제거 후 최신 `.mcpb` 재설치 → Claude Desktop 재시작 |
| 망치 아이콘에 도구 13개가 아닌 4개만 보임 | 마찬가지로 옛 패키지 | 위와 동일 |
| Claude가 도구를 안 부르고 자기 지식으로 답함 | 자연어가 도구 트리거를 못 침 | 도구 이름을 직접 언급 — 예: "`get_paper_abstract` 도구로 PMID 32555340 가져와줘" |
| GPCRdb 조회 실패 경고 | GPCRdb 서버 일시 장애 | PDB 기본 정보는 정상 반환됨. 잠시 후 재시도 |
| PubChem이 화합물을 못 찾음 | PDB 화학성분 코드가 비표준이거나 신규 코드 | InChIKey 또는 CHEMBL ID로 다시 시도 |
| OpenTargets 결과 None | gene symbol 오타 / Ensembl ID 매칭 실패 | 표준 HGNC 심볼 또는 ENSGxxxxx 형식으로 입력 |
| AlphaFold 결과 None | 모델 종(主에 인간) 외 단백질 / 매우 짧은 펩타이드 | 실험 구조 검색으로 우회 |
| 응답이 한국어로 안 옴 | (드물게) 영문 응답 | 시스템 프롬프트(`SYSTEM_PROMPT.md`)를 Custom Instructions에 적용 |

---

## 8. 외부 API 정보 (출처와 라이선스)

모든 API는 **무료·무인증·상업적 사용 가능 범위 내** 사용. 출처를 항상 응답에 동봉한다.

| API | 용도 | 데이터 라이선스 |
|-----|------|----------------|
| UniProt REST | 단백질 식별·서열·feature·변이 | CC BY 4.0 |
| RCSB PDB GraphQL | PDB 메타데이터 | CC0 (Public domain) |
| GPCRdb | GPCR 큐레이션 | CC BY 4.0 |
| PubChem REST | 화학 구조·물성·synonyms | Public domain |
| ChEMBL REST | 신약 phase·bioactivity | CC BY-SA 3.0 |
| IUPHAR/GtoPdb | GPCR 약리 표준 | CC BY-SA 4.0 |
| Europe PMC REST | 논문 메타·초록 | 개별 논문 라이선스 따름 |
| PubMed E-utilities (NCBI) | 논문 메타 (fallback) | NCBI 이용약관 |
| PDBe REST | 결합부위 잔기 | CC BY 4.0 |
| AlphaFold DB | 예측 구조 | CC BY 4.0 |
| OpenTargets Platform | 질환·약물 인텔리전스 | CC0 |

> 💡 응답을 사외 자료로 인용할 때는 각 데이터의 출처를 표기.

---

## 9. 데이터 신뢰성 원칙 (이 MCP의 설계 철학)

### 9-1. 출처 동봉
모든 응답에 `source_url`을 포함 — 연구원이 1초 안에 원본을 검증할 수 있다.

### 9-2. "모름"은 명시
값을 모르는 경우 `null` / `-` 로 표시. **임의 값으로 채우지 않는다**.

### 9-3. Graceful degradation
한 API가 실패해도 다른 소스의 부분 결과를 반환한다 — 전체가 끊기지 않는다.

### 9-4. Claude의 역할 = 인용자, 아닌 것 = 종합 평가자
- ✅ 권위 있는 원본 데이터를 가져와 보여주기
- ✅ 표·요약·정렬 등 가공 (단, 원본 값은 보존)
- ❌ binding affinity 추측, 변이 효과 예측, 임상 결론 종합 판단
- ❌ 가져오지 못한 항목을 "약 X nM 정도일 것" 으로 채우는 행위

---

## 10. 자주 묻는 질문 (FAQ)

**Q. 비GPCR 타깃에도 쓸 수 있나?**
A. 네. `search_target` 은 모든 인간 단백질을 다룹니다. GPCR이면 자동으로 확장 컬럼 추가, 아니면 기본 5컬럼.

**Q. Excel 파일은 어떻게 받나?**
A. MCP 도구는 데이터(마크다운 표)를 반환하고, Claude 가 자체 xlsx 스킬로 파일을 만들어 응답에 첨부합니다. `export_excel=true` 는 macOS 샌드박스 권한 이슈로 권장하지 않음.

**Q. 인터넷 없이 쓸 수 있나?**
A. 불가. 모든 도구가 실시간 외부 API를 호출. 폐쇄망에서는 사내 proxy를 설정해야 함.

**Q. 검색 결과가 너무 많을 때 잘라낼 수 있나?**
A. 대부분 도구에 `max_*` 파라미터가 있음. `search_target` 은 `max_structures`, `get_target_bioactivities` 는 `max_results` 등.

**Q. 한 번에 여러 PMID 초록을 받을 수 있나?**
A. 현재 `get_paper_abstract` 는 1건씩. 여러 건은 `search_papers` 로 키워드 검색 → 상위 N건 초록 미리보기.

**Q. 사내 SSO / API key 입력은 없나?**
A. 없음. 모든 외부 API가 무인증 공개 엔드포인트.

**Q. 응답 결과를 데이터로 받아 후처리하고 싶다.**
A. 도구 응답은 마크다운 텍스트라 정규식/파싱이 필요. Pydantic 모델은 서버 내부용. 직접 API 호출이 필요하면 `tools/*.py` 모듈을 import 해서 Python에서 사용 가능.

---

## 11. 트리거 키워드 치트시트

연구원이 다음 표현을 쓰면 Claude가 해당 도구를 자동 호출한다.

| 표현 키워드 | 호출되는 도구 |
|------------|-------------|
| 단백질명 / 유전자명 | `search_target` |
| "패밀리", "전체", "5-HT2" | `search_family` |
| PDB ID 4자리 (예: "7WC7 자세히") | `get_pdb_detail` |
| "비교", "차이" + 여러 타깃 | `compare_targets` |
| 화합물명 + "구조"/"SMILES"/"MW"/"임상 단계" | `get_ligand_detail` |
| 타깃 + "Ki"/"affinity"/"활성" | `get_target_bioactivities` |
| "PMID" / "DOI" + "초록"/"결론" | `get_paper_abstract` |
| "최근 논문" + 키워드 | `search_papers` |
| "잔기 번호"/"서열"/"binding site"/"transmembrane" | `get_sequence_region` |
| "변이"/"mutation"/"L858R" 류 | `get_natural_variants` |
| PDB ID + "결합부위"/"binding pocket" | `get_binding_site` |
| "AlphaFold"/"예측 구조"/"실험 구조 없을 때" | `get_alphafold_model` |
| 타깃 + "질환"/"임상 약물"/"승인 약물" | `get_target_intelligence` |

---

## 12. 변경 이력 (Changelog)

### v0.2.0 (2026-05-26) — Phase 5: 리서치 보조 도구 8개 추가
- 신규: `get_ligand_detail`, `get_target_bioactivities`, `get_paper_abstract`, `search_papers`, `get_sequence_region`, `get_natural_variants`, `get_binding_site`, `get_alphafold_model`, `get_target_intelligence`
- 연결 API: PubChem · ChEMBL · IUPHAR · Europe PMC · PubMed · PDBe · AlphaFold DB · OpenTargets
- 모든 신규 응답에 `source_url` 동봉

### v0.1.0 — 초기 릴리스
- `search_target`, `search_family`, `get_pdb_detail`, `compare_targets`
- UniProt + RCSB PDB + GPCRdb 통합
- GPCR 확장 컬럼 + Excel 출력 + 자동 별칭 사전 + 패밀리 자동 확장

---

## 13. 지원

- **개발/이슈**: 나무아이씨티 신약연구소 — aidrugdev2.namuict@gmail.com
- **참고 문서**:
  - `CLAUDE.md` — 전체 기술 사양·구현 지시서
  - `SYSTEM_PROMPT.md` — Claude Desktop Custom Instructions 템플릿
  - `README.md` — 빌드/배포 가이드
