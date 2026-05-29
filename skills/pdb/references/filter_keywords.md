# 필터 키워드 자동 매핑 표

연구원 입력에서 필터 의도를 감지하면 아래대로 cli 옵션을 추가한다.

## search / family 공용 필터

| 입력 키워드 (한/영) | cli 옵션 | 값 |
|---|---|---|
| "고해상도" / "좋은 해상도" / "선명한 구조" / "high resolution" | `--max-resolution` | 2.5 |
| "초고해상도" / "1Å대" / "ultra-high" | `--max-resolution` | 1.5 |
| "최근" / "최신" / "요즘" / "recent" | `--min-year` | 현재연도 − 5 |
| "최근 N년" / "last N years" | `--min-year` | 현재연도 − N |
| "Agonist만" / "작용제만" | `--ligand-modality` | `Agonist` |
| "Partial agonist만" | `--ligand-modality` | `Partial agonist` |
| "Antagonist만" / "길항제만" | `--ligand-modality` | `Antagonist` |
| "Inverse agonist만" / "역작용제만" | `--ligand-modality` | `Inverse agonist` |
| "Active 구조만" | `--state` | `Active` |
| "Inactive 구조만" / "비활성 구조" | `--state` | `Inactive` |
| "Intermediate 구조만" | `--state` | `Intermediate` |
| "X-ray만" / "X선" | `--method` | `X-ray` |
| "Cryo-EM만" / "cryo만" / "전자현미경" | `--method` | `EM` |
| "NMR만" | `--method` | `NMR` |

## 정렬 키워드

| 입력 | `--sort-by` |
|---|---|
| "최신순" / "공개일순" / "최근순" | `date` |
| "해상도 좋은 순" / "선명한 순" / "low Å 순" | `resolution` |
| (GPCR 기본 — State별로 묶기) | `state_then_date` (cli가 GPCR이면 자동 적용) |

## 조합 예시

| 연구원 입력 | 변환된 cli |
|---|---|
| "HTR2A Antagonist 고해상도만" | `search HTR2A --ligand-modality Antagonist --max-resolution 2.5` |
| "최근 5년 GPCR Cryo-EM Active만" | `search <target> --min-year 2021 --method EM --state Active` |
| "EGFR 1.5Å 이하" | `search EGFR --max-resolution 1.5` |
| "5-HT2 패밀리 최신 Agonist" | `family 5-HT2 --targets HTR2A,HTR2B,HTR2C --min-year 2020 --ligand-modality Agonist` |

## bioactivity 전용

| 입력 | 옵션 |
|---|---|
| "강력한 binder" / "pChEMBL 7 이상" / "nM 수준" | `--min-pchembl 7` |
| "Ki만" / "Kd만" / "IC50만" | `--types Ki,Kd` / `--types Kd` / `--types IC50` |
| "ChEMBL만" / "IUPHAR 제외" | `--no-iuphar` |

## variants / sequence 전용

| 입력 | 옵션 |
|---|---|
| "질환 관련 변이만" / "disease only" | `--disease-only` |
| "잔기 NNN 주변" | `--position NNN` |
| "도메인 X 구간" | `--start <S> --end <E>` |

## 사용 원칙

- 입력에 명시적 수치가 있으면 그 값을 우선. 예: "2.0Å 이하" → `--max-resolution 2.0` (사전 매핑 2.5보다 우선).
- 모호한 필터(예: "좋은 구조")는 묻지 말고 기본값(고해상도 = ≤2.5Å)으로 진행하고, 표 위 요약에 적용된 필터를 한 줄로 표시.
- 필터가 너무 엄격해서 결과가 0이면 cli가 exit 1과 안내 메시지를 반환한다. 이때는 LLM이 한 단계 완화한 옵션으로 자동 재시도하지 말고, 사용자에게 원인을 보고하고 완화 옵션을 제안한다.
