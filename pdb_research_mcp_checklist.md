# PDB 리서치 MCP 반영 여부 검수 문서

## 1. 문서 목적

본 문서는 신약개발을 위한 PDB 리서치 과정에서 반드시 고려해야 하는 요소들이 현재 구성 중인 **PDB Research MCP**에 제대로 반영되어 있는지 점검하기 위한 기준 문서이다.

PDB 리서치는 단순히 타깃 단백질의 구조를 검색하는 것이 아니라, 신약개발 목적에 맞는 구조를 선별하기 위한 구조 생물학적·화학정보학적 검토 과정이다. 따라서 MCP는 단백질명, PDB ID, 해상도만 반환하는 수준을 넘어, 구조 상태, chain 구성, ligand, mutation, missing residue, biological assembly, docking/MD 적합성 등을 종합적으로 평가할 수 있어야 한다.

---

## 2. 핵심 검수 질문

PDB Research MCP는 다음 질문에 답할 수 있어야 한다.

> “이 PDB 구조가 내가 수행하려는 신약개발 목적에 적합한 구조인가?”

이를 위해 MCP는 다음 세 가지 기능을 제공해야 한다.

1. 관련 PDB 구조를 충분히 수집한다.
2. 각 PDB 구조의 생물학적·구조적 차이를 분석한다.
3. docking, MD, selectivity 분석, mutant 분석 등 목적별로 적합한 구조를 추천한다.

---

## 3. MCP 필수 반영 항목 체크리스트

| 구분 | 점검 항목 | MCP 반영 여부 | 중요도 | 비고 |
|---|---|---:|---:|---|
| Target 정보 | 타깃 단백질명, gene symbol, UniProt ID를 함께 조회하는가 | ☐ | High | 이름만 검색하면 isoform/동명이체 오류 가능 |
| Species | 인간/마우스/바이러스 등 species를 구분하는가 | ☐ | High | 신약개발 목적이면 human target 우선 여부 확인 필요 |
| Isoform | isoform 또는 construct 차이를 인식하는가 | ☐ | Medium | GPCR, kinase, viral protein에서 중요 |
| PDB 수집 | 관련 PDB ID를 누락 없이 수집하는가 | ☐ | High | RCSB, PDBe, UniProt cross-reference 활용 권장 |
| 구조 방법 | X-ray, cryo-EM, NMR, model 구조를 구분하는가 | ☐ | High | docking/MD 적합성 판단에 필요 |
| Resolution | resolution 값을 수집하고 기준에 따라 평가하는가 | ☐ | High | X-ray/cryo-EM 구조 품질 판단 |
| R-free/R-work | X-ray 구조의 refinement 품질을 확인하는가 | ☐ | Medium | 고급 품질 평가 항목 |
| Chain 구성 | PDB 내 chain별 단백질/파트너를 구분하는가 | ☐ | High | receptor chain 선택 오류 방지 |
| Biological assembly | asymmetric unit과 biological assembly를 구분하는가 | ☐ | High | dimer, oligomer, complex 분석에 중요 |
| Apo/Holo | ligand가 없는 apo와 ligand-bound holo 구조를 구분하는가 | ☐ | High | docking/약물 설계에서 매우 중요 |
| Ligand 종류 | inhibitor, substrate, cofactor, buffer, ion, solvent를 구분하는가 | ☐ | High | 실제 약물 결합 ligand인지 판별 필요 |
| Binding site | ligand가 어느 pocket에 결합했는지 인식하는가 | ☐ | High | orthosteric/allosteric/ATP site 구분 |
| Mutation | WT, disease mutant, engineered mutation을 구분하는가 | ☐ | High | mutant inhibitor 설계에서 필수 |
| Missing residue | missing loop/residue를 수집하고 pocket 근처 여부를 평가하는가 | ☐ | High | docking/MD reliability에 직접 영향 |
| Modified residue | phosphorylation, glycosylation, PTM 여부를 확인하는가 | ☐ | Medium | kinase, receptor, viral glycoprotein에서 중요 |
| Cofactor/metal | ATP, ADP, NAD, FAD, Mg, Zn 등 cofactor/metal을 식별하는가 | ☐ | Medium | 활성 상태/촉매 상태 판단에 필요 |
| Water | 구조적 water와 bulk water를 구분하거나 보존 후보를 제안하는가 | ☐ | Medium | docking 정확도에 영향 가능 |
| Construct artifact | tag, fusion protein, antibody, nanobody, crystallization partner를 구분하는가 | ☐ | Medium | 실제 target과 다른 구조 해석 방지 |
| Conformation | active/inactive, open/closed, DFG-in/out 등 상태를 분류하는가 | ☐ | High | kinase/GPCR에서 매우 중요 |
| Sequence coverage | PDB 구조가 full-length인지 domain-only인지 확인하는가 | ☐ | High | MD/기전 분석에서 필수 |
| Pocket completeness | binding pocket 주변 residue가 온전한지 평가하는가 | ☐ | High | docking 가능 여부 판단 |
| Ligand pose 신뢰도 | ligand 전자밀도/occupancy/B-factor 등 pose 신뢰도를 검토하는가 | ☐ | Medium | 단순 holo 구조라도 pose 품질이 낮을 수 있음 |
| Redundancy 제거 | 동일/유사 구조를 clustering 또는 기준 기반으로 정리하는가 | ☐ | Medium | 수십 개 PDB를 실무적으로 압축 필요 |
| 목적별 추천 | docking, MD, pharmacophore, selectivity, mutant 분석별 추천 구조를 제시하는가 | ☐ | High | MCP의 최종 실용성 결정 |
| 근거 제공 | 추천 이유와 제외 이유를 명확히 제공하는가 | ☐ | High | 사용자가 판단 가능해야 함 |

---

## 4. 목적별 MCP 판단 기준

### 4.1 Docking용 PDB 선정 기준

MCP는 docking 목적일 때 다음 구조를 우선 추천해야 한다.

- 고해상도 X-ray 또는 신뢰도 높은 cryo-EM 구조
- 실제 inhibitor 또는 유사 ligand가 결합된 holo 구조
- binding pocket 주변 residue가 누락되지 않은 구조
- ligand binding mode가 명확한 구조
- mutation, tag, fusion artifact가 적은 구조
- chain 선택이 명확한 구조

#### Docking용 제외 후보

- pocket 주변 missing residue가 많은 구조
- ligand가 buffer, detergent, crystallization additive인 구조
- apo 구조만 존재하지만 induced-fit pocket이 필요한 경우
- 해상도가 낮고 side-chain orientation 신뢰도가 낮은 구조

---

### 4.2 MD simulation용 PDB 선정 기준

MCP는 MD 목적일 때 다음 항목을 추가로 고려해야 한다.

- missing loop와 unresolved region이 적은 구조
- biological assembly가 명확한 구조
- monomer/dimer/complex 상태가 연구 목적과 일치하는 구조
- ligand, cofactor, ion, PTM 유지 여부 판단 가능
- protonation state 설정이 가능한 구조
- 구조 안정성에 영향을 줄 engineered mutation이 적은 구조

#### MD용 추가 검토 항목

- chain break 여부
- terminal truncation 여부
- disulfide bond 존재 여부
- glycan 또는 membrane component 필요 여부
- multimer interface 보존 여부

---

### 4.3 Pharmacophore / Ligand-based 분석용 PDB 선정 기준

MCP는 pharmacophore 목적일 때 다음 구조를 우선 수집해야 한다.

- 다양한 ligand-bound 구조
- 동일 pocket에 여러 chemotype이 결합된 구조
- agonist/antagonist/inhibitor 등 기능 상태가 구분된 구조
- key interaction residue가 반복적으로 관찰되는 구조

#### MCP가 제공해야 하는 결과

- ligand별 interaction summary
- 공통 hydrogen bond / hydrophobic / ionic interaction
- conserved interaction residue
- ligand scaffold별 binding mode 차이

---

### 4.4 Selectivity 분석용 PDB 선정 기준

Selectivity 분석에서는 단일 타깃만 보면 부족하다. MCP는 family homolog 구조를 함께 조사해야 한다.

예시:

- JAK1 / JAK2 / JAK3 / TYK2
- EGFR / HER2 / HER4
- 5-HT2A / 5-HT2B / 5-HT2C
- CDK family

#### MCP 필수 기능

- homolog protein의 PDB 동시 수집
- conserved residue와 divergent residue 비교
- pocket residue alignment
- selectivity pocket 또는 extended pocket 차이 요약
- ligand-bound homolog 구조 비교

---

### 4.5 Mutant inhibitor 설계용 PDB 선정 기준

Mutation이 중요한 타깃의 경우 MCP는 다음을 구분해야 한다.

- WT 구조
- disease-associated mutant 구조
- resistance mutant 구조
- engineered mutation 구조

#### MCP 필수 출력

- mutation residue 위치
- mutation이 binding pocket에 가까운지 여부
- mutation이 allosteric network 또는 dimer interface에 위치하는지 여부
- WT vs mutant 구조 차이
- mutant-bound ligand 존재 여부

---

## 5. MCP 출력 형식 제안

PDB Research MCP는 단순 리스트가 아니라 다음과 같은 구조화된 결과를 제공해야 한다.

### 5.1 PDB 후보 요약 테이블

| PDB ID | Method | Resolution | Species | Chain | Apo/Holo | Ligand | Mutation | Missing pocket residue | 추천 용도 | 평가 |
|---|---:|---:|---|---|---|---|---|---|---|---|
| 예: XXXX | X-ray | 2.1 Å | Human | A | Holo | inhibitor | WT | 없음 | Docking/MD | 우선 추천 |
| 예: YYYY | cryo-EM | 3.4 Å | Human | A/B | Holo | substrate | mutant | loop 일부 | Mechanism | 조건부 추천 |

---

### 5.2 구조별 상세 리포트

각 PDB에 대해 다음 내용을 포함한다.

```text
PDB ID:
Target:
Species:
UniProt ID:
Experimental method:
Resolution:
Chain 구성:
Biological assembly:
Ligand 목록:
실제 약물성 ligand 여부:
Binding site:
Mutation:
Missing residue:
Pocket completeness:
PTM/cofactor/metal:
구조 상태:
장점:
한계:
추천 용도:
제외 또는 주의 사유:
```

---

### 5.3 최종 추천 구조 선정 로직

MCP는 최종적으로 다음과 같이 추천해야 한다.

```text
Best PDB for docking:
- PDB ID:
- 이유:
- 주의점:

Best PDB for MD:
- PDB ID:
- 이유:
- 준비 시 필요한 보정:

Best PDB for pharmacophore:
- PDB ID list:
- 공통 interaction:

Best PDB for selectivity:
- 비교 대상 PDB:
- 주요 pocket 차이:

Excluded PDBs:
- PDB ID:
- 제외 이유:
```

---

## 6. MCP가 반드시 피해야 할 오류

다음 오류가 발생하면 PDB Research MCP는 신약개발 실무에서 신뢰하기 어렵다.

1. PDB ID와 resolution만 제공한다.
2. ligand가 실제 inhibitor인지 buffer molecule인지 구분하지 않는다.
3. chain A를 무조건 target으로 간주한다.
4. biological assembly를 확인하지 않는다.
5. missing residue가 binding pocket에 있는지 확인하지 않는다.
6. engineered mutation과 disease mutation을 구분하지 않는다.
7. apo와 holo를 구분하지 않는다.
8. kinase active/inactive conformation을 구분하지 않는다.
9. GPCR agonist/antagonist-bound 상태를 구분하지 않는다.
10. 목적별 추천 없이 단순히 “해상도 좋은 구조”만 추천한다.

---

## 7. MCP 기능 요구사항

### 7.1 최소 기능

- PDB ID 수집
- target/gene/UniProt 매핑
- method/resolution 수집
- chain 구성 확인
- ligand 목록 수집
- mutation 확인
- missing residue 확인
- apo/holo 구분
- 목적별 추천 구조 제시

### 7.2 권장 기능

- pocket residue completeness 평가
- ligand classification
- biological assembly 확인
- homolog 구조 비교
- pocket alignment
- ligand interaction fingerprint 추출
- 구조 redundancy clustering
- docking/MD readiness score 계산

### 7.3 고급 기능

- ligand electron density quality 평가
- AlphaFold/experimental structure 비교
- induced-fit 가능성 평가
- allosteric site 탐지
- conserved water 추천
- MD 준비 자동화 입력 생성
- docking grid center 자동 추천
- selectivity pocket residue 자동 비교

---

## 8. PDB Readiness Score 제안

MCP 내부 평가를 위해 다음과 같은 점수 체계를 사용할 수 있다.

| 항목 | 점수 | 설명 |
|---|---:|---|
| 구조 품질 | 0–20 | resolution, R-free, cryo-EM map quality |
| Target 적합성 | 0–20 | species, isoform, domain coverage |
| Pocket 완전성 | 0–20 | missing residue, side-chain completeness |
| Ligand 정보 | 0–15 | inhibitor-bound 여부, ligand 신뢰도 |
| 생물학적 상태 | 0–15 | active/inactive, oligomer, mutant 상태 적합성 |
| 실무 준비성 | 0–10 | docking/MD preparation 용이성 |

### 점수 해석

| 총점 | 해석 |
|---:|---|
| 85–100 | 우선 사용 가능 |
| 70–84 | 사용 가능하나 일부 보정 필요 |
| 50–69 | 조건부 사용 |
| <50 | 단독 사용 비권장 |

---

## 9. MCP 검수용 테스트 시나리오

### Scenario 1. Kinase target

입력 예시:

```text
Target: JAK2
Purpose: inhibitor docking and MD
```

MCP가 확인해야 할 내용:

- JH1/JH2 domain 구분
- ATP-binding site ligand 여부
- DFG-in/out 또는 active/inactive 상태
- V617F 등 mutation 여부
- WT vs mutant 구조 비교 가능성
- missing activation loop 여부
- docking용 PDB와 MD용 PDB를 따로 추천하는지

---

### Scenario 2. GPCR target

입력 예시:

```text
Target: 5-HT2A receptor
Purpose: subtype selectivity analysis
```

MCP가 확인해야 할 내용:

- 5-HT2A/2B/2C homolog 구조 동시 수집
- agonist/antagonist/inverse agonist 상태 구분
- orthosteric pocket과 extended pocket residue 비교
- chain, fusion protein, antibody/nanobody 여부 확인
- ligand-bound structure 기반 selectivity residue 제시

---

### Scenario 3. Viral protein target

입력 예시:

```text
Target: Bundibugyo ebolavirus glycoprotein
Purpose: viral entry mechanism and drug target analysis
```

MCP가 확인해야 할 내용:

- species/strain 구분
- glycoprotein trimer 상태 확인
- receptor-bound 또는 antibody-bound 구조 구분
- glycan 여부 확인
- fusion loop 또는 receptor-binding site 확인
- sequence variant와 structure mapping 가능 여부

---

## 10. 최종 검수 기준

현재 구성 중인 PDB Research MCP가 아래 조건을 만족하면 신약개발용 PDB 리서치 MCP로서 기본 요건을 충족한다고 볼 수 있다.

### 필수 통과 기준

- [ ] 단순 PDB 검색이 아니라 구조 적합성 평가를 수행한다.
- [ ] apo/holo, ligand, chain, mutation, missing residue를 구분한다.
- [ ] 연구 목적별로 추천 구조가 달라질 수 있음을 반영한다.
- [ ] docking용, MD용, selectivity용, mutant 분석용 기준을 분리한다.
- [ ] 추천 구조뿐 아니라 제외 사유도 제공한다.
- [ ] 신약개발 실무자가 바로 판단 가능한 표와 상세 리포트를 제공한다.

### 권장 통과 기준

- [ ] homolog 구조 비교가 가능하다.
- [ ] pocket residue alignment가 가능하다.
- [ ] ligand interaction fingerprint를 제공한다.
- [ ] docking grid 또는 MD preparation에 필요한 정보를 제공한다.
- [ ] PDB readiness score를 제공한다.

---

## 11. 결론

PDB Research MCP는 “PDB를 찾아주는 도구”가 아니라, 신약개발 목적에 맞는 구조를 선별하고 추천하는 구조 검토 에이전트로 설계되어야 한다.

따라서 MCP는 다음 관점을 반드시 반영해야 한다.

> 동일한 타깃이라도 PDB 구조는 ligand 상태, conformation, chain 구성, mutation, missing residue, biological assembly, 실험 방법에 따라 신약개발 활용성이 크게 달라진다.

최종적으로 MCP는 단순 구조 검색 결과가 아니라, 다음과 같은 판단을 제공해야 한다.

```text
이 구조는 왜 좋은가?
이 구조는 어떤 목적에 적합한가?
이 구조를 사용할 때 무엇을 조심해야 하는가?
어떤 구조는 왜 제외해야 하는가?
```

이 기준을 만족해야 PDB Research MCP가 신약개발 실무에 적합하다고 판단할 수 있다.
