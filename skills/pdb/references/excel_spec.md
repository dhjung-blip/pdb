# Excel 출력 표준 (xlsx 스킬로 생성할 때 반드시 따를 규격)

`/pdb` Skill은 cli가 절대 `.xlsx`를 만들지 않는다. JSON을 받아 이 규격대로 별도 **xlsx 스킬**로 생성한다. **이 문서가 Excel 출력 규격의 단일 원천(single source of truth)**이며, 모든 셀·시트·서식·정렬은 아래 명세를 그대로 따른다.

---

## 파일명 규칙

- 단일 타깃: `{유전자명}_{Accession}_structures_{YYYYMMDD}.xlsx`
  - 예: `EGFR_P00533_structures_20260526.xlsx`
- 패밀리: `{family_label}_family_structures_{YYYYMMDD}.xlsx`
  - 예: `5HT2_family_structures_20260526.xlsx`
- `family_label`은 입력 패밀리 키워드를 영문 압축형으로(`5HT2`, `DRD`, `ADRB` 등)

---

## 시트 구성

- 첫 번째 시트: 항상 `Summary`
  - UniProt(accession, 단백질명, 유전자명), GPCR 여부, 총 구조 수, 조회 일시
  - 패밀리 검색일 때 추가로: 타깃별 총수 / GPCRdb 매칭 / Active / Inactive / Intermediate / 최고 해상도 / 최신 구조 (State별 집계는 `COUNTIF` 수식 사용)
- 두 번째 시트:
  - 단일 검색 → `Structures`
  - 패밀리 검색 → 타깃별 시트 (`HTR2A`, `HTR2B`, ...)

---

## 컬럼 — 비GPCR 타깃 (12컬럼, 순서 고정)

JSON `data.uniprot.is_gpcr == false` 일 때 사용.

| # | 컬럼 | 데이터 (JSON 경로) | 비고 |
|---|---|---|---|
| 1 | PDB ID | `structures[i].pdb_id` | 하이퍼링크: `https://www.rcsb.org/structure/{pdb_id}` |
| 2 | Resolution (Å) | `structures[i].resolution` | number_format `0.00`, NMR(None)은 `"N/A"` |
| 3 | Method | `structures[i].method` | X-ray / Cryo-EM / NMR / Other |
| 4 | Released Date | `structures[i].released_date` | YYYY-MM-DD |
| 5 | Entry Title | `structures[i].title` | |
| 6 | Paper Title | `structures[i].citation.title` | |
| 7 | Authors | `structures[i].citation.authors` | "F.M. Last 등" 형태 그대로 |
| 8 | Journal | `structures[i].citation.journal` | |
| 9 | Year | `structures[i].citation.year` | |
| 10 | Citation (ACS) | (ACS 포맷 — 아래 절 참조) | |
| 11 | DOI | `structures[i].citation.doi` | 하이퍼링크: `https://doi.org/{doi}` |
| 12 | PMID | `structures[i].citation.pmid` | 하이퍼링크: `https://pubmed.ncbi.nlm.nih.gov/{pmid}` |

---

## 컬럼 — GPCR 타깃 (14컬럼, 순서 고정)

JSON `data.uniprot.is_gpcr == true` 일 때 사용.

| # | 컬럼 | 데이터 (JSON 경로) | 비고 |
|---|---|---|---|
| 1 | Method | `structures[i].method` | |
| 2 | PDB ID | `structures[i].pdb_id` | 하이퍼링크 |
| 3 | Res. (Å) | `structures[i].resolution` | number_format `0.00` |
| 4 | Pref. chain | `structures[i].pref_chain` | |
| 5 | State | `structures[i].state` | **조건부 색상 적용** |
| 6 | Ligand | `structures[i].ligand` | |
| 7 | Ligand modality | `structures[i].ligand_modality` | **조건부 색상 적용** |
| 8 | Sign. prot. | `structures[i].signaling_protein` | 없으면 `-` |
| 9 | Fusion protein | `structures[i].fusion_protein` | BRIL/T4L/mT4L 등, 없으면 `-` |
| 10 | Antibody | `structures[i].antibody` | Fab/Nb 등, 없으면 `-` |
| 11 | Year | `structures[i].citation.year` | |
| 12 | Citation (ACS) | ACS 포맷 | |
| 13 | DOI | `structures[i].citation.doi` | 하이퍼링크 |
| 14 | PMID | `structures[i].citation.pmid` | 하이퍼링크 |

---

## 조건부 색상 (셀 배경)

### State 컬럼
| 값 | 배경 |
|---|---|
| `Active` | `#DCFCE7` (연한 초록) |
| `Inactive` | `#FEF2F2` (연한 빨강) |
| `Intermediate` | `#FEF9C3` (연한 노랑) |

### Ligand modality 컬럼
| 값 | 배경 |
|---|---|
| `Agonist`, `Partial agonist` | `#DCFCE7` |
| `Antagonist` | `#FEF2F2` |
| `Inverse agonist` | `#FFF7ED` (연한 주황) |

---

## 공통 서식

- 헤더 행: 배경 `#1E293B` (진한 남색), 글자색 흰색, 굵게.
- 짝수 행 배경: `#F8FAFC` (연한 회색).
- 첫 행 고정: `freeze_panes = A2`.
- 컬럼 너비 자동 조정. `Citation (ACS)`는 폭 60–80 + `wrap_text=True`.
- 데이터 없는 값: 텍스트 `"-"` 그대로 표시. `"Unknown"`, `"N/A"` 등 임의 값 채우기 금지.
  - 예외: Resolution이 None(NMR 등)일 때만 `"N/A"`.

---

## ACS Citation 형식

```
Last, F. M.; Last2, F. M. Article Title. J. Abbrev. Year, Vol, PageFirst–PageLast. DOI: 10.xxxx/xxxxx.
```

- 저자 3명 초과 시 "et al." 사용
- 누락 필드는 해당 부분 생략하고 나머지로 조합
- 저널·연도·권·페이지·DOI 순서 유지

**중요**: `cli detail` 결과의 `**ACS 인용**` 줄 또는 `formatter`가 마크다운 모드에서 생성한 ACS 문자열을 그대로 셀에 넣는다. 직접 재포맷하지 않는다.

---

## 데이터 무결성 규칙 (매우 중요)

다음을 **반드시** 지킨다.

- **Ligand 컬럼**: JSON `ligand` 필드 값을 그대로 넣는다. IUPAC 이름이 길어도 자르거나 의역하지 않는다. **Ligand modality 값(`Agonist`/`Antagonist` 등)을 절대로 Ligand 컬럼에 넣지 않는다** — 별도 컬럼이다.
- **Ligand modality 컬럼**: JSON `ligand_modality` 값 그대로. 없으면 `"-"`.
- **모르는 값은 추측하지 않는다**: JSON에서 `null` 또는 `"-"`로 온 필드는 셀에도 `"-"`로 남긴다. 특히 `signaling_protein`, `fusion_protein`, `antibody`, `state`가 그렇다.
- **Authors / Citation**: 받아온 그대로 사용. 이니셜 포함/제외, 구두점, "et al." 위치를 임의로 바꾸지 않는다.
- **DOI / PMID**: 값 자체는 받은 그대로, 셀에는 하이퍼링크만 추가.
- **Resolution**: 받아온 숫자 그대로 받고 셀 number_format만 `0.00` 적용 (NMR 등 None은 `"N/A"`).

---

## 정렬

- GPCR: State(Inactive→Active→Intermediate→없음) 우선 → 같은 State 안에서 공개일 내림차순. cli `--sort-by state_then_date` 결과 순서가 이미 이렇다.
- 비GPCR: 공개일 내림차순. cli `--sort-by date` (기본) 결과 순서가 이미 이렇다.

cli가 반환한 `structures` 배열 순서를 **그대로** Excel에 옮긴다. 재정렬하지 않는다.

---

## 패밀리 Summary 시트 추가 규칙

패밀리 검색일 때 Summary 시트에 포함:

| 컬럼 | 값 |
|---|---|
| 타깃 | gene |
| UniProt Accession | accession |
| 총 구조 수 | summary.registered_count |
| GPCRdb 매칭 수 | summary.gpcrdb_count |
| Active | `=COUNTIF({sheet}!E:E,"Active")` (State 컬럼 = E) |
| Inactive | `=COUNTIF({sheet}!E:E,"Inactive")` |
| Intermediate | `=COUNTIF({sheet}!E:E,"Intermediate")` |
| 최고 해상도 | summary.best_resolution.pdb_id + resolution |
| 최신 구조 | summary.latest_structure.pdb_id + released_date |
