# 수용체 패밀리 자동 확장 표

연구원이 패밀리 키워드를 언급하면, 이 표대로 `family --targets` 또는 `compare --targets`의 유전자명 목록을 자동 구성한다.

## GPCR

| 입력 키워드 (한/영) | 확장 유전자명 |
|---|---|
| 세로토닌 수용체 / 5-HT 전체 / serotonin | HTR1A, HTR1B, HTR2A, HTR2B, HTR2C, HTR4, HTR6, HTR7 |
| 세로토닌 2 / 5-HT2 / HTR2 | HTR2A, HTR2B, HTR2C |
| 도파민 수용체 / dopamine / DRD | DRD1, DRD2, DRD3, DRD4, DRD5 |
| 아드레날린 수용체 / adrenergic / β-adrenergic / ADRB | ADRB1, ADRB2, ADRB3 |
| α-adrenergic / ADRA | ADRA1A, ADRA1B, ADRA1D, ADRA2A, ADRA2B, ADRA2C |
| 무스카린 수용체 / muscarinic / CHRM | CHRM1, CHRM2, CHRM3, CHRM4, CHRM5 |
| 히스타민 수용체 / histamine / HRH | HRH1, HRH2, HRH3, HRH4 |
| 오피오이드 수용체 / opioid | OPRM1, OPRD1, OPRK1, OPRL1 |
| 카나비노이드 수용체 / cannabinoid / CB | CNR1, CNR2 |
| 글루카곤 / GLP / incretin | GCGR, GLP1R, GLP2R, GIPR |
| 안지오텐신 / angiotensin / AT receptor | AGTR1, AGTR2 |

## 키나제 / 효소

| 입력 키워드 | 확장 유전자명 |
|---|---|
| CDK 패밀리 / cyclin-dependent kinase | CDK1, CDK2, CDK4, CDK6, CDK7, CDK9 |
| EGFR 패밀리 / ErbB / HER | EGFR, ERBB2, ERBB3, ERBB4 |
| MAPK / ERK | MAPK1, MAPK3, MAPK8, MAPK14 |
| JAK | JAK1, JAK2, JAK3, TYK2 |
| Src 패밀리 | SRC, FYN, YES1, LCK |
| BCL-2 family | BCL2, BCL2L1, MCL1, BAX, BAK1 |

## 운반체 / 채널

| 입력 키워드 | 확장 유전자명 |
|---|---|
| nAChR / nicotinic | CHRNA1, CHRNA4, CHRNA7, CHRNB2 |
| GABA-A subunits | GABRA1, GABRA2, GABRB2, GABRG2 |

## 사용 원칙

- 단일 타깃이 명확하게 지목되면 패밀리 확장하지 않는다. 예: "HTR2A 분석" → `search HTR2A` (HTR2B/C 추가 호출 금지).
- 패밀리 확장 시 `family` 한 번에 모두 넘긴다. 절대 `search`를 N회 반복 호출하지 않는다.
- 비교 의도("어느 게 많아", "비교해줘")가 있으면 `compare`, 데이터 자체를 원하면 `family`를 사용한다.
- 표에 없는 패밀리 요청(예: "Wnt 수용체") → 사용자에게 정확한 유전자명을 물어 확인한다.
