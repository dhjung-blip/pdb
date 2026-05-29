---
name: pdb
description: PDB 단백질 구조 검색·분석. 연구원이 단백질명/유전자명/수용체명(EGFR, HTR2A, 세로토닌 수용체, 5-HT2, GPCR, kinase, opioid 등)을 언급하거나 PDB ID·UniProt accession·논문(PMID/DOI)·결합부위·리간드(SMILES/PubChem/ChEMBL)·바이오액티비티(Ki/Kd/IC50)·자연변이·AlphaFold 모델·약물/질환 정보를 요청하면 호출. 서브커맨드 13개 — search, family, detail, compare, ligand, bioactivity, paper, papers, sequence, variants, binding, alphafold, intel.
allowed-tools: Bash, Read
---

# `/pdb` — PDB 구조 리서치 Skill

신약연구원이 단백질 타깃·구조·논문·리간드 정보를 요청할 때 호출되는 통합 Skill.
이 Skill은 절대로 `.xlsx` 파일을 직접 만들지 않는다. JSON 결과를 받은 뒤 별도의
**xlsx 스킬**로 [references/excel_spec.md](references/excel_spec.md) 규격에 맞게 생성한다.

---

## 1. 자동 판단 규칙

### 1.1 즉시 호출
연구원이 단백질명/유전자명/수용체명을 언급하면 **확인 질문 없이** 즉시 적절한 서브커맨드를 호출한다.

### 1.2 패밀리 자동 확장
"세로토닌 수용체", "5-HT2", "도파민 수용체", "GPCR opioid" 같은 패밀리 키워드는 서브타입 유전자명 목록으로 펼친다. 매핑 표는 [references/family_map.md](references/family_map.md) 참조.

- 단일 타깃이 명확하면 → `search` 1회
- 패밀리 전체 → `family --targets A,B,C` 1회 (LLM이 여러 번 `search` 호출 금지)
- "비교", "차이", "어느 게 더 많아?" → `compare --targets A,B,C`

### 1.3 필터 키워드 자동 매핑
"고해상도", "Antagonist만", "Cryo-EM만", "최근 5년", "Active 구조만" 같은 키워드는 cli 옵션으로 변환. 매핑 표는 [references/filter_keywords.md](references/filter_keywords.md) 참조.

### 1.4 GPCR vs 비GPCR 분기
**LLM이 판단하지 않는다.** cli가 stdout JSON의 `summary.is_gpcr` 또는 `data.uniprot.is_gpcr`로 알려준다. 이 값이 `true`면 14컬럼(GPCR 확장 — State/Ligand/Modality/Fusion/Antibody 등), `false`면 12컬럼(기본 — PDB ID/Resolution/Method/Released Date/논문)으로 표를 만든다.

### 1.5 도구 선택 가이드 (의도 충돌 해결)
| 의도 | 도구 |
|---|---|
| "EGFR 구조 보여줘" — 단순 구조 검색 | `search` |
| "세로토닌 수용체 정리" — 패밀리 | `family` |
| "EGFR이랑 HER2 비교" — 비교만 | `compare` |
| "7WC7 자세히" — 단일 PDB | `detail` |
| "EGFR 결합부위" — 잔기 좌표 | `binding` (PDB ID 필요) |
| "EGFR Ki/IC50 데이터" — 활성 측정값 | `bioactivity` (UniProt accession 필요) |
| "EGFR 약물/질환 정보" — 임상/표적 | `intel` |
| "리간드 X의 SMILES/물성" | `ligand` |
| "이 논문 초록" — PMID/DOI 알 때 | `paper` |
| "X에 관한 논문 검색" — 키워드 | `papers` |
| "EGFR T790 주변 서열" | `sequence --start --end` |
| "EGFR 자연 변이" | `variants` |
| "EGFR AlphaFold 모델" | `alphafold` |

### 1.6 응답 언어
연구원이 한국어로 묻으면 한국어로 답한다. 단백질명·PDB ID·컬럼명·약물명은 영문 원문 유지.

---

## 2. 서브커맨드 카탈로그

| Subcommand | 매핑 MCP tool | 필수 인자 | 대표 케이스 |
|---|---|---|---|
| `search <target>` | search_target | target | 단일 타깃 PDB 구조 |
| `family --targets ...` | search_family | targets | 패밀리 일괄 검색 |
| `detail <pdb_id>` | get_pdb_detail | pdb_id | PDB ID 단건 |
| `compare --targets ...` | compare_targets | targets | 다중 타깃 요약 비교 |
| `ligand <query>` | get_ligand_detail | query | 화합물 식별자/물성 |
| `bioactivity <accession>` | get_target_bioactivities | accession | Ki/Kd/IC50 |
| `paper --pmid \| --doi` | get_paper_abstract | pmid 또는 doi | 단일 논문 초록 |
| `papers <query>` | search_papers | query | 논문 검색 |
| `sequence <accession>` | get_sequence_region | accession | 서열 + feature |
| `variants <accession>` | get_natural_variants | accession | 자연 변이 |
| `binding <pdb_id>` | get_binding_site | pdb_id | 결합부위 잔기 |
| `alphafold <accession>` | get_alphafold_model | accession | AF 모델 메타 |
| `intel <target>` | get_target_intelligence | target | OT 질환/약물 |

---

## 3. 호출 예시 (Bash 1줄)

모두 절대 경로의 셸 래퍼로 호출. `--json`이 기본 (LLM 파싱용), `--md`는 보조.

```bash
# 단일 타깃
bash .claude/skills/pdb/scripts/pdb search EGFR --json

# 패밀리 (5-HT2 → HTR2A/2B/2C)
bash .claude/skills/pdb/scripts/pdb family 5-HT2 --targets HTR2A,HTR2B,HTR2C --json

# 필터 조합 — "HTR2A Antagonist 고해상도만"
bash .claude/skills/pdb/scripts/pdb search HTR2A --ligand-modality Antagonist --max-resolution 2.5 --json

# 비교
bash .claude/skills/pdb/scripts/pdb compare --targets DRD1,DRD2,DRD3,DRD4,DRD5,HTR2A,HTR2B,HTR2C --md

# 단일 PDB 상세
bash .claude/skills/pdb/scripts/pdb detail 7WC7 --json

# 결합부위
bash .claude/skills/pdb/scripts/pdb binding 7WC7 --json

# 활성 데이터
bash .claude/skills/pdb/scripts/pdb bioactivity P28223 --min-pchembl 6 --max 20 --json

# 논문
bash .claude/skills/pdb/scripts/pdb paper --pmid 35084960 --md
bash .claude/skills/pdb/scripts/pdb papers "5-HT2A psychedelic" --max 5 --json

# 서열·변이·AF·intel
bash .claude/skills/pdb/scripts/pdb sequence P00533 --start 700 --end 850 --json
bash .claude/skills/pdb/scripts/pdb variants P00533 --disease-only --max 50 --json
bash .claude/skills/pdb/scripts/pdb alphafold P28223 --json
bash .claude/skills/pdb/scripts/pdb intel EGFR --max-diseases 10 --json
```

---

## 4. 출력 형식 정책

| 모드 | 언제 사용 | 형식 |
|---|---|---|
| `--json` (기본) | LLM이 파싱해 표·요약 만들 때 | 단일 JSON 객체 (`{tool, success, data, summary, metadata, error?}`) |
| `--md` | 사용자에게 그대로 보여줄 때 | 마크다운 (server.py 렌더러와 동일 포맷) |

- 두 옵션은 상호 배타. `--json --md` 동시 지정 시 argparse가 거부.
- stdout = 데이터만. stderr = 진행 로그(`[pdb] dispatch: ...`)·에러 사유. **stderr 데이터를 컨텍스트에 넣지 말 것.**

---

## 5. Excel 저장 정책

**이 Skill은 절대로 `.xlsx`를 생성하지 않는다.**

순서:
1. `--json`으로 데이터를 받는다.
2. [references/excel_spec.md](references/excel_spec.md) 규격(파일명·시트·컬럼·서식)을 정확히 지켜 **xlsx 스킬**로 `.xlsx`를 만든다.
3. 연구원이 "Excel 필요 없어"라고 명시하면 단계 2를 건너뛴다.

cli에는 `--export-excel`, `--output-dir` 옵션이 의도적으로 없다.

---

## 6. 결과 요약 가이드 (응답 작성)

표를 보여주기 전에 **한 줄 요약**을 먼저 표시한다 (JSON `summary` 필드 활용):

- 비GPCR 예: `EGFR (P00533) — 총 351개 구조 | 최고 해상도 1.07Å (8A27) | 최신 구조 9BY4 (2025-05-28)`
- GPCR 예: `HTR2A (P28223) — 총 32개 구조 | Inactive 15 / Active 3 / Intermediate 4`

표 출력 후, **맥락에 맞는 후속 질문 1~2개**를 자동 제안:
- "Antagonist 구조만 필터링해드릴까요?"
- "해상도 2.5Å 이하만 추려드릴까요?"
- "비슷한 타겟(HTR2B/2C)과 구조 수를 비교해드릴까요?"

---

## 7. 에러 처리

### 종료 코드
| Code | 의미 | 응답 방식 |
|---|---|---|
| 0 | 성공 (빈 결과도 포함) | 정상 표시 |
| 1 | 외부 API 실패 / 네트워크 | 사용자에게 사유 그대로 전달 + 재시도 안내 |
| 2 | 입력 검증 실패 | 어떤 값이 빠졌는지 명시 |
| 3 | 부분 실패 (패밀리 일부 타깃 실패) | 성공 분은 정상 표시 + JSON `metadata.errors` 별도 강조 |

### 부분 실패
`family`에서 일부 타깃만 실패하면 cli는 exit code 3을 반환한다. JSON `metadata.errors` 배열에 실패한 타깃별 사유가 들어 있다. 성공한 타깃은 정상 표시하고, 실패한 것만 별도 줄로 알린다.

### 데이터 무결성
- cli/MCP가 `-`(빈 값)로 표시한 필드는 LLM이 **외부 지식으로 채우지 말 것**. 특히 `state`, `ligand`, `ligand_modality`, `signaling_protein`, `fusion_protein`, `antibody`가 그렇다.
- 리간드 이름이 PDB 코드(3-4자 대문자)면 PubChem 일시 장애일 수 있다 (cli 로그에 안내). 임의 일반명으로 추측 금지.
- `summary.gpcrdb_count`가 `fetched_count`의 50% 미만이면 GPCRdb 일시 장애 가능성이 있다는 경고를 함께 표시.

---

## 8. 환경 가드

- `scripts/pdb` 셸 래퍼가 절대경로 `.venv/bin/python`을 강제한다. 시스템 Python 3.9(mcp 미설치)는 사용되지 않는다.
- `PYTHONPATH` 조정 불필요. 셸 래퍼가 `REPO_ROOT`를 자동 계산.
- 외부 API 타임아웃 30s. `--timeout` 옵션은 향후 추가 예정 (현재 미구현).
- 이 Skill은 MCP 서버(server.py)를 **건드리지 않는다**. Claude Desktop은 그대로 동작한다.
