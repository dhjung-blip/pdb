# PDB Research MCP — 커버리지 & 활용 API

> **한 줄로**: 신약 리서치에서 자주 던지는 질문 13가지를 자동으로 받아, **무료 공개 DB 10여 곳**에서 원본 값을 가져와 표·요약으로 돌려줍니다. **모르는 값은 추측 없이 `-`로 표시합니다.**


---

## 1. 한 페이지 요약

```
질문 유형                  →   호출 도구                  →   외부 API
─────────────────────────────────────────────────────────────────────
"이 단백질 구조 다 보여줘"  →   search_target            →   UniProt + RCSB PDB (+GPCRdb)
"패밀리 통째로"             →   search_family            →   위와 동일 (병렬)
"이 PDB 자세히"             →   get_pdb_detail           →   RCSB PDB
"여러 타깃 비교"            →   compare_targets          →   UniProt + RCSB PDB
"이 약 SMILES/임상단계"     →   get_ligand_detail        →   PubChem + ChEMBL + IUPHAR
"이 타깃 Ki/IC50"           →   get_target_bioactivities →   ChEMBL + IUPHAR/GtoPdb
"PMID 초록"                 →   get_paper_abstract       →   Europe PMC + PubMed E-utils
"키워드로 논문"             →   search_papers            →   Europe PMC
"이 잔기/이 구간 서열"      →   get_sequence_region      →   UniProt
"알려진 변이/L858R"         →   get_natural_variants     →   UniProt Variation (Proteins API)
"이 PDB 결합부위 잔기"      →   get_binding_site         →   PDBe + RCSB PDB
"실험구조 없으면 AF 모델"   →   get_alphafold_model      →   AlphaFold DB
"이 타깃 질환·임상약물"     →   get_target_intelligence  →   OpenTargets Platform
```

**원칙**: ① 응답마다 `source_url` 동봉 · ② 모르는 값은 `-`/`null` 유지 · ③ 한 API가 죽어도 나머지 부분 결과는 반환(graceful degradation).

---

## 2. 커버리지 매트릭스 — "이런 질문은 어느 도구가 답하나"

연구원이 자연어로 던질 만한 질문을 카테고리별로 정리. 트리거 키워드만 보고도 사용 가능.

### 2-1. 구조(Structure) 관련

| 알고 싶은 것 | 자연어 트리거 예시 | 도구 |
|------------|----------------|------|
| 타깃의 PDB 구조 목록 전체 | "EGFR 구조 다 보여줘", "HTR2A 분석" | [search_target](#search_target) |
| 패밀리(여러 서브타입) 통합 | "5-HT2 패밀리", "DRD1~5 정리" | [search_family](#search_family) |
| 특정 PDB ID 한 개 상세 | "7WC7 자세히", "8JT8 정보" | [get_pdb_detail](#get_pdb_detail) |
| 여러 타깃 구조 수·해상도 비교 | "HER2랑 EGFR 차이", "어느 게 구조 많아?" | [compare_targets](#compare_targets) |
| 결합부위에 어떤 잔기가? | "7WC7 binding pocket", "이 구조 핵심 잔기" | [get_binding_site](#get_binding_site) |
| 실험 구조 없을 때 예측 모델 | "AlphaFold 받고싶어", "예측 구조 pLDDT" | [get_alphafold_model](#get_alphafold_model) |

### 2-2. 화합물(Ligand/Drug) 관련

| 알고 싶은 것 | 자연어 트리거 예시 | 도구 |
|------------|----------------|------|
| SMILES / MW / LogP / TPSA | "lisuride SMILES", "이 약 분자량" | [get_ligand_detail](#get_ligand_detail) |
| 임상 phase (Approved / 1–3) | "이 약 임상 몇 상?", "Risperidone phase" | [get_ligand_detail](#get_ligand_detail) |
| 타깃에 대한 Ki / Kd / IC50 / EC50 | "HTR2A에 강한 antagonist Ki", "pChEMBL 8 이상" | [get_target_bioactivities](#get_target_bioactivities) |
| 타깃-약물 페어 사실 확인 | "Risperidone HTR2A Ki?" | [get_target_bioactivities](#get_target_bioactivities) |

### 2-3. 서열·변이(Sequence/Variant) 관련

| 알고 싶은 것 | 자연어 트리거 예시 | 도구 |
|------------|----------------|------|
| 잔기 번호·서열 구간 | "HTR2A 200–250 서열", "TM6 영역" | [get_sequence_region](#get_sequence_region) |
| 활성부위·결합부위·도메인 feature | "EGFR ATP-binding site", "transmembrane helix" | [get_sequence_region](#get_sequence_region) |
| 알려진 자연변이 (L858R 등) | "L858R 알려져 있어?", "disease-causing mutation" | [get_natural_variants](#get_natural_variants) |
| 특정 위치 변이 모두 | "858번 잔기 알려진 변이?" | [get_natural_variants](#get_natural_variants) |

### 2-4. 문헌(Literature) 관련

| 알고 싶은 것 | 자연어 트리거 예시 | 도구 |
|------------|----------------|------|
| PMID/DOI → 초록 그대로 | "PMID 32555340 결론", "DOI 10.xxx 초록" | [get_paper_abstract](#get_paper_abstract) |
| 키워드 → 최근 논문 N개 | "HTR2A psychedelic 최근 논문", "GPCR allosteric review" | [search_papers](#search_papers) |

### 2-5. 타깃 인텔리전스(Disease/Drug) 관련

| 알고 싶은 것 | 자연어 트리거 예시 | 도구 |
|------------|----------------|------|
| 이 타깃 어느 질환에 쓰여? | "EGFR 적응증", "ADGRG6 어떤 타깃?" | [get_target_intelligence](#get_target_intelligence) |
| 임상 약물(승인/개발 중) 리스트 | "이 타깃 known drug 정리" | [get_target_intelligence](#get_target_intelligence) |

---

## 3. 활용 API — 각 외부 DB가 "주는 것 / 못 주는 것"

연구원이 결과를 해석할 때 "왜 이 컬럼은 비어있지?"를 빠르게 판단할 수 있도록 정리.

### 3-1. UniProt (REST)
- **Base**: `https://rest.uniprot.org/`
- **주는 것**: Accession, gene/protein 이름, organism, 전체 서열, sequence feature(active site, binding site, TM helix, domain), cross-reference (PDB ID 목록, Ensembl, RefSeq 등)
- **변이는 별도 엔드포인트**: `https://www.ebi.ac.uk/proteins/api/variation/{accession}` (Proteins API)
- **빈 결과 원인**: 인간(`organism_id:9606`) + Swiss-Prot reviewed 우선이라 비표준 이름·동의어로 검색 시 매칭 실패 가능 → 표준 HGNC 심볼로 재시도
- **라이선스**: CC BY 4.0
- **연결 도구**: `search_target`, `search_family`, `compare_targets`, `get_sequence_region`, `get_natural_variants`

### 3-2. RCSB PDB (GraphQL)
- **Base**: `https://data.rcsb.org/graphql`
- **주는 것**: PDB ID, 제목, resolution_combined(Å), experimental_method, initial_release_date, citation(저자·저널·연도·권·페이지·DOI·PMID), polymer entity 설명
- **못 주는 것**: GPCR-특이적 큐레이션(State/Ligand modality 등) — 그것은 GPCRdb 영역
- **NMR 구조**: resolution = null → 표에는 `N/A`
- **라이선스**: CC0 (public domain)
- **연결 도구**: `search_target`, `search_family`, `get_pdb_detail`, `get_binding_site`

### 3-3. GPCRdb
- **Base**: `https://gpcrdb.org/services/`
- **주는 것**: GPCR 여부(슬러그 매칭), preferred_chain, state(Active/Inactive/Intermediate), ligand(+ function_label로 modality), signalling_protein(Gq/Gi/Gs/...), 일부 stabilizing_agents
- **못 주는 것**: 비-GPCR · GPCRdb에 아직 미수록된 최신 구조(→ `tools/parser.py`의 제목 패턴 매칭 fallback)
- **장애 대응**: 실패해도 RCSB 기본 컬럼은 정상 반환(선택적 강화)
- **라이선스**: CC BY 4.0
- **연결 도구**: `search_target`, `search_family`, `get_pdb_detail`

### 3-4. PubChem (REST PUG)
- **Base**: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound`
- **주는 것**: CID, IUPAC name, SMILES(canonical/isomeric), InChI/InChIKey, 분자식, MW, XLogP, H-bond donor/acceptor, TPSA, rotatable bonds, synonyms(상위 10개)
- **못 주는 것**: 임상 phase(→ ChEMBL), 표적 정보(→ IUPHAR)
- **PDB 화학성분 코드(3-letter)** 미매칭 시 → InChIKey 또는 CHEMBL ID로 재시도 권장
- **라이선스**: Public domain
- **연결 도구**: `get_ligand_detail`, (간접) `tools/gpcrdb.py`의 리간드 이름 해소

### 3-5. ChEMBL (REST)
- **Base**: `https://www.ebi.ac.uk/chembl/api/data`
- **주는 것**: CHEMBL ID, max_phase(0–4), bioactivity(Ki/Kd/IC50/EC50/standard_value/standard_units/pChEMBL_value/assay_description), 표적 매핑(UniProt accession 기반)
- **권장 컷오프**: `min_pchembl=6.0` (≈ Ki 1 µM 이하). 0이면 컷오프 없음
- **라이선스**: CC BY-SA 3.0
- **연결 도구**: `get_ligand_detail`, `get_target_bioactivities`

### 3-6. IUPHAR / Guide to Pharmacology (GtoPdb)
- **Base**: `https://www.guidetopharmacology.org/services`
- **주는 것**: GPCR·이온채널 약리 표준 — 리간드 ID, 표준화된 modality(agonist/antagonist/inverse agonist/PAM/NAM), affinity(pKi/pKd/pIC50/pEC50), species
- **권장 보강**: `get_target_bioactivities` 호출 시 `gene_symbol` 추가하면 IUPHAR 매칭 활성화 → GPCR/이온채널 타깃에서 ChEMBL 누락분 보완
- **라이선스**: CC BY-SA 4.0
- **연결 도구**: `get_ligand_detail`, `get_target_bioactivities`

### 3-7. Europe PMC (REST)
- **Base**: `https://www.ebi.ac.uk/europepmc/webservices/rest/`
- **주는 것**: 키워드 검색, 제목·저자·저널·연도·권·페이지, **전체 초록**(섹션 라벨 보존), MeSH terms, Open Access 여부
- **PubMed E-utils fallback**: Europe PMC 매칭 실패 시 `eutils.ncbi.nlm.nih.gov` (esearch/efetch)
- **라이선스**: 개별 논문 라이선스 따름 (메타데이터는 free)
- **연결 도구**: `get_paper_abstract`, `search_papers`

### 3-8. PDBe (REST)
- **Base**: `https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/{pdb_id}`
- **주는 것**: 결합부위(site) 단위 정보 — site_id, 결합 리간드, 체인, **잔기 리스트**(`PHE 340 (A)` 형태)
- **옵션**: `skip_solvents=true`(기본) — HOH/EDO/GOL/이온 자동 제외, `ligand_filter`로 특정 chem code만
- **라이선스**: CC BY 4.0
- **연결 도구**: `get_binding_site`

### 3-9. AlphaFold DB
- **Base**: `https://alphafold.ebi.ac.uk/api/prediction/{accession}`
- **주는 것**: 모델 entry ID, organism, 서열 길이, **평균 pLDDT** + 신뢰도 라벨, PDB/CIF 다운로드 URL, PAE 이미지·데이터 URL
- **신뢰도 라벨**: `>90` Very high · `70–90` Confident · `50–70` Low · `<50` Very low
- **빈 결과 원인**: 인간 외 종 미수록 / 매우 짧은 펩타이드 / 특수 isoform
- **라이선스**: CC BY 4.0
- **연결 도구**: `get_alphafold_model`

### 3-10. OpenTargets Platform (GraphQL)
- **Base**: `https://api.platform.opentargets.org/api/v4/graphql`
- **주는 것**: 식별자(gene/Ensembl/UniProt/biotype), **연관 질환** (Disease/EFO ID/종합 점수/therapeutic area), **Known drugs** (이름/타입/mechanism/max phase/적응증)
- **입력**: gene symbol 또는 Ensembl ID(`ENSGxxxxx...`)
- **빈 결과 원인**: 비표준 심볼 또는 매칭 실패 → HGNC 표준 심볼로 재시도
- **라이선스**: CC0
- **연결 도구**: `get_target_intelligence`

---

## 4. 도구별 카드 (입력 / 출력 / 트리거 / 사용 API)

### <a id="search_target"></a>📦 `search_target`
- **무엇**: 타깃 1개의 PDB 구조 전체를 표로
- **트리거**: 단백질·유전자명 1개 ("EGFR 분석", "HTR2A 정리")
- **입력 핵심**: `target` (필수), `max_resolution`, `min_year`, `state_filter`, `ligand_modality_filter`, `method_filter`, `sort_by`
- **출력 핵심**: 비GPCR 5컬럼 / GPCR 11컬럼 확장 (State·Ligand·Modality·Fusion·Antibody 등) — Claude의 xlsx 스킬로 Excel 첨부
- **사용 API**: UniProt → (GPCRdb) → RCSB PDB (병렬, sem=10)

### <a id="search_family"></a>📦 `search_family`
- **무엇**: 여러 서브타입(패밀리)을 한 번에
- **트리거**: "패밀리", "전체", "5-HT2", "DRD"
- **입력 핵심**: `targets`(배열), `family_name`, 그 외 필터는 `search_target`과 동일
- **출력**: 타깃별 시트 + Summary 시트 통합 Excel
- **사용 API**: search_target 워크플로우를 N회 병렬 실행

### <a id="get_pdb_detail"></a>📦 `get_pdb_detail`
- **무엇**: PDB ID 1개 상세 (해상도/method/날짜/논문 ACS 인용 + GPCR이면 State/Ligand/Modality)
- **트리거**: 4자리 PDB ID + "자세히/정보"
- **입력**: `pdb_id` (4자리)
- **사용 API**: RCSB PDB (+ GPCRdb if applicable)

### <a id="compare_targets"></a>📦 `compare_targets`
- **무엇**: N개 타깃의 구조 수·최고해상도·최신 구조 한 표
- **트리거**: "비교", "차이", "어느 게 더"
- **입력**: `targets`(배열)
- **사용 API**: UniProt + RCSB PDB

### <a id="get_ligand_detail"></a>💊 `get_ligand_detail`
- **무엇**: 화합물 1개의 화학·약리 프로필
- **트리거**: 화합물명 + "SMILES/MW/임상단계/구조"
- **입력**: `query` — 이름 / PDB chem code / `CHEMBLxxxx` / InChIKey 모두 가능
- **출력**: PubChem CID·ChEMBL ID·IUPHAR ID, SMILES, InChI, MW, XLogP, donor/acceptor, TPSA, **max phase**, synonyms, 출처 URL 3종
- **사용 API**: PubChem + ChEMBL + IUPHAR (병렬)

### <a id="get_target_bioactivities"></a>💊 `get_target_bioactivities`
- **무엇**: 타깃 한 개에 대한 활성 데이터 표
- **트리거**: 타깃 + "Ki/Kd/IC50/affinity/활성"
- **입력**: `uniprot_accession` (필수), `gene_symbol` (IUPHAR 보강), `min_pchembl`(기본 6.0), `standard_types`, `max_results`, `include_iuphar`
- **출력**: 순위·리간드·Type·값·단위·**pChEMBL**·assay 설명·출처·PMID
- **사용 API**: ChEMBL + IUPHAR/GtoPdb

### <a id="get_paper_abstract"></a>📚 `get_paper_abstract`
- **무엇**: PMID 또는 DOI → 전체 초록
- **트리거**: "PMID xxxxx 초록/결론", "DOI 10.xxx"
- **입력**: `pmid` 또는 `doi` (둘 중 하나 필수)
- **출력**: 제목·저자(상위 5)·저널·연도·권·페이지·**섹션별 초록**·MeSH·OA 여부
- **사용 API**: Europe PMC (1순위) → PubMed E-utils (fallback)

### <a id="search_papers"></a>📚 `search_papers`
- **무엇**: 키워드로 논문 N개
- **트리거**: "최근 논문", "review" + 키워드
- **입력**: `query` (Europe PMC 쿼리 문법), `max_results` (1–25, 기본 5)
- **출력**: 제목·저자(상위 3)·저널·연도·PMID·DOI·초록 미리보기(280자)·출처 URL
- **사용 API**: Europe PMC

### <a id="get_sequence_region"></a>🧬 `get_sequence_region`
- **무엇**: UniProt 서열 구간 + 그 구간과 겹치는 feature
- **트리거**: "잔기 번호", "서열", "TM helix", "binding site"
- **입력**: `accession` (필수), `start`/`end` (1-based, 생략 시 전체), `feature_types`(필터)
- **출력**: FASTA-like 60글자/줄, feature 표(타입·범위·설명·리간드·ECO 근거)
- **사용 API**: UniProt

### <a id="get_natural_variants"></a>🧬 `get_natural_variants`
- **무엇**: 알려진 자연변이 목록
- **트리거**: "변이", "mutation", "L858R" 류, "disease-causing"
- **입력**: `accession` (필수), `position`(특정 잔기만), `disease_only`, `max_results`
- **출력**: 위치·wild-type·variant·설명·질환·임상의의·dbSNP·ClinVar
- **사용 API**: UniProt Variation (Proteins API)

### <a id="get_binding_site"></a>🧬 `get_binding_site`
- **무엇**: PDB 구조의 결합부위 잔기
- **트리거**: PDB ID + "결합부위/binding pocket"
- **입력**: `pdb_id` (필수), `ligand_filter`(특정 chem code), `skip_solvents`(기본 true)
- **출처**: 결합부위별 site_id·리간드·체인·잔기 리스트
- **사용 API**: PDBe + RCSB PDB

### <a id="get_alphafold_model"></a>🧬 `get_alphafold_model`
- **무엇**: 예측 구조와 신뢰도
- **트리거**: "AlphaFold", "예측 구조", "실험구조 없을 때"
- **입력**: `uniprot_accession` (필수)
- **출력**: entry ID·organism·길이·**평균 pLDDT**·라벨·PDB/CIF 다운로드 URL·PAE 자료
- **사용 API**: AlphaFold DB

### <a id="get_target_intelligence"></a>🩺 `get_target_intelligence`
- **무엇**: 타깃의 질환 연관 + 임상 약물
- **트리거**: 타깃 + "질환/임상약물/적응증"
- **입력**: `target_query` (gene symbol 또는 ENSGxxxxx), `max_diseases`(기본 15), `max_drugs`(기본 15)
- **출력**: 식별자, 연관 질환 표, known drugs 표
- **사용 API**: OpenTargets Platform (GraphQL)

---

## 5. 한 화면으로 보는 워크플로우 (조합 사용 시나리오)

> "이 PMID 본 다음, 거기 나오는 약 SMILES 보고, 그 약 다른 타깃 활성까지" — 도구를 어떻게 묶어서 쓰는지.

### 5-1. 새 타깃 입문 (3단)
```
"ADGRG6 어떤 타깃? 임상 약물 있어?"
  ① search_target("ADGRG6")           ← UniProt + PDB 구조 수 확인
  ② get_target_intelligence(...)      ← 질환·known drug
  ③ (구조 0개면) get_alphafold_model(<accession>)
```

### 5-2. 구조 → 결합부위 → 약 (3단)
```
"7WC7 결합부위 잔기랑 거기 결합하는 약 상세까지"
  ① get_pdb_detail("7WC7")           ← 리간드 코드 확보
  ② get_binding_site("7WC7")          ← 핵심 잔기 목록
  ③ get_ligand_detail("Lisuride")     ← SMILES/MW/phase
```

### 5-3. 약 ↔ 타깃 페어 검증 (1단)
```
"Risperidone HTR2A Ki 얼마?"
  ① get_target_bioactivities("P28223", gene_symbol="HTR2A", min_pchembl=8)
     → ChEMBL+IUPHAR 표에서 Risperidone 행
```

### 5-4. 변이 hotspot 확인 (N단 병렬)
```
"EGFR L858R, T790M, C797S 다 알려졌어?"
  ① get_natural_variants("P00533", position=858)
  ② get_natural_variants("P00533", position=790)
  ③ get_natural_variants("P00533", position=797)
```

### 5-5. 패밀리 → Excel
```
"5-HT2 패밀리 전체 정리해서 엑셀"
  ① search_family(targets=["HTR2A","HTR2B","HTR2C"], family_name="5-HT2_family")
  ② Claude의 xlsx 스킬이 응답을 받아 .xlsx 첨부
```

---

## 6. "이건 못 합니다" — 명시적 비커버리지

| 못하는 것 | 이유 | 우회 |
|---------|------|------|
| 단백질 도킹·시뮬레이션 실행 | MCP는 데이터 인용자, 계산 엔진 아님 | 결합부위·서열만 가져와 외부 도구에 입력 |
| 임상시험 결과 해석 / 결론 종합 | Claude는 초록 텍스트만 그대로 반환 | `get_paper_abstract` 결과를 사람이 직접 판단 |
| Affinity 값 추측 (값이 없을 때) | "모름"은 `-`로 유지 원칙 | 다른 동족 타깃·동족 리간드로 우회 검색 |
| 비인간 단백질 광범위 검색 | UniProt 쿼리에 `organism_id:9606` 기본 | 직접 accession 입력 (예: 마우스 P-번호) |
| 사내·비공개 데이터 | 모든 API가 공개 엔드포인트 | 사내 시스템 별도 연동 필요 |
| Excel 서버측 저장 안정성 | macOS 샌드박스 권한 이슈 | Claude의 xlsx 스킬이 응답에 첨부 (기본 권장) |
| 인증 필요 API (BioGPS, Reaxys 등) | 무인증 공개 API만 사용 | 별도 라이선스/연동 필요 |

---

## 7. 응답 신뢰성 빠른 체크

| 마크 | 의미 | 행동 |
|-----|------|-----|
| 값이 `-` 또는 `null` | 해당 외부 DB에 등록된 정보 없음 | **추측 금지**. `source_url`로 원본 확인 |
| "조회 실패" 경고 | 일시 네트워크/서버 오류 | 잠시 후 재시도. 다른 컬럼은 정상 |
| `source_url` 동봉 | 모든 응답의 표준 — 1초 안에 원본 검증 가능 | 사외 인용 시 출처 표기 |
| `pChEMBL` 값 | `-log10(M)`. 7=100nM, 8=10nM, 9=1nM, 10=0.1nM | [USAGE_GUIDE.md §6-3](USAGE_GUIDE.md) 환산표 |
| `pLDDT` 값 | AlphaFold 신뢰도. >90=실험급 / <50=구조 추론 금지 | [USAGE_GUIDE.md §6-4](USAGE_GUIDE.md) 참고 |

---

## 8. 외부 API 정책 한 줄 요약

- **인증**: 없음 (모두 무료 공개 엔드포인트)
- **Rate limit**: RCSB PDB 동시 10, GPCRdb 동시 5, UniProt 호출 간 0.1초 — 서버가 자동 제어
- **장애 시**: 다른 소스의 부분 결과 반환 (graceful degradation)
- **라이선스**: 응답 동봉된 `source_url`로 출처 표기 의무 (특히 CC BY-SA 계열은 사외 인용 시 주의)