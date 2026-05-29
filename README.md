# `/pdb` — PDB 단백질 구조 리서치 Claude Code Plugin

신약 연구원이 단백질 이름·유전자명·수용체명을 자연어로 던지면, **UniProt / RCSB PDB / GPCRdb / PubChem / ChEMBL / Europe PMC / AlphaFold / OpenTargets** 13개 외부 API에서 권위 있는 원본 데이터를 가져와 표로 정리해 주는 **Claude Code Skill**입니다.

GPCR 계열 타깃이면 **State / Ligand / Ligand modality / Signaling protein / Fusion protein / Antibody / Preferred chain** 컬럼이 추가된 약리학 확장 테이블이 자동으로 생성됩니다. 모르는 값은 절대 추측하지 않고 `-`로 둡니다.

> Claude Desktop / 사내 SSE·HTTP MCP 서버로 같은 기능을 쓰는 가이드는 [README_MCP.md](README_MCP.md)를 참조하세요. 이 repo는 Plugin 배포를 1차 목표로 하지만 같은 Python 모듈로 MCP 진입점도 함께 제공합니다.

---

## 빠른 설치

### 1) Marketplace 등록 (사용자별 1회)

```bash
# Claude Code 안에서
/plugin marketplace add https://github.com/dhjung-blip/pdb.git
/plugin install pdb@pdb
```

### 2) 가상환경 부트스트랩 (최초 1회)

```bash
PLUGIN_DIR=$(ls -d ~/.claude/plugins/cache/pdb/pdb/*/ | tail -1)
cd "$PLUGIN_DIR" && bash setup.sh
```

### 3) 사용

```
사용자: EGFR 구조 분석해줘
→ /pdb search EGFR 자동 호출

사용자: 5-HT2 패밀리 엑셀로
→ /pdb family 5-HT2 --targets HTR2A,HTR2B,HTR2C 자동 호출
→ 결과 JSON → xlsx 스킬이 14컬럼 GPCR 표준으로 .xlsx 생성
```

자세한 설치·검증·트러블슈팅은 [PLUGIN_INSTALL.md](PLUGIN_INSTALL.md) 참조.

---

## 자연어 트리거 예시

자연어 한 마디로 `/pdb` 서브커맨드가 자동 매핑됩니다. 패밀리 확장·필터 키워드는 [skills/pdb/references/family_map.md](skills/pdb/references/family_map.md) / [filter_keywords.md](skills/pdb/references/filter_keywords.md) 참조.

| 연구원 입력 | 자동 실행 |
|---|---|
| "EGFR 분석해줘" | `pdb search EGFR` (비GPCR 12컬럼 표) |
| "HTR2A 구조" | `pdb search HTR2A` (🧬 GPCR 14컬럼 확장 표) |
| "세로토닌 수용체 정리" | `pdb family 5-HT2 --targets HTR2A,HTR2B,HTR2C` |
| "HTR2A Antagonist 고해상도만" | `pdb search HTR2A --ligand-modality Antagonist --max-resolution 2.5` |
| "도파민 vs 세로토닌 구조 수 비교" | `pdb compare --targets DRD1,DRD2,DRD3,DRD4,DRD5,HTR2A,HTR2B,HTR2C` |
| "7WC7 자세히" | `pdb detail 7WC7` |
| "PMID 35084960 초록" | `pdb paper --pmid 35084960` |

---

## 서브커맨드 13개

| Subcommand | 외부 API | 대표 케이스 |
|---|---|---|
| `search <target>` | UniProt + RCSB + GPCRdb + PubChem | 단일 타깃 PDB 구조 + 약리학 메타 |
| `family --targets ...` | (search 동일) | 패밀리 일괄 검색 → Summary + 타깃별 시트 |
| `detail <pdb_id>` | RCSB + GPCRdb | PDB 단건 상세 |
| `compare --targets ...` | UniProt + RCSB | 다중 타깃 요약 비교 |
| `ligand <query>` | PubChem + ChEMBL + IUPHAR | 화합물 식별자·물성·Phase |
| `bioactivity <accession>` | ChEMBL + IUPHAR | Ki / Kd / IC50 활성 |
| `paper --pmid/--doi` | Europe PMC + PubMed | 단일 논문 메타 + 초록 |
| `papers <query>` | Europe PMC | 키워드 논문 검색 |
| `sequence <accession>` | UniProt | 서열 + feature (BINDING/DOMAIN 등) |
| `variants <accession>` | UniProt Variation | 자연 변이 (SNP / 질환) |
| `binding <pdb_id>` | PDBe + RCSB | 결합부위 잔기 좌표 |
| `alphafold <accession>` | AlphaFold DB | 예측 구조 + pLDDT |
| `intel <target>` | OpenTargets | 연관 질환 + known drugs |

각 서브커맨드는 `--json`(기본, LLM 파싱용) 또는 `--md`(사용자 직접 보기용)로 출력. 자세한 옵션은 `bash skills/pdb/scripts/pdb <cmd> --help`.

---

## 작동 원리

```
Claude Code 슬래시 명령 /pdb
         ↓
skills/pdb/scripts/pdb (셸 래퍼, .venv 자동 탐지)
         ↓
.venv/bin/python cli.py <subcommand> [options]
         ↓
adapters/runner.py — 13개 dispatch
         ↓
tools/*.py — UniProt/RCSB/GPCRdb/... 13개 API 클라이언트
         ↓
JSON 또는 Markdown stdout (← LLM이 컨텍스트에 받음)
```

- venv 위치는 plugin install 시 `${CLAUDE_PLUGIN_DATA}/.venv`, project mode면 저장소 안 `.venv`. setup.sh가 자동 감지.
- Excel(.xlsx) 출력은 cli가 만들지 않고 **별도 xlsx 스킬**이 `skills/pdb/references/excel_spec.md` 규격대로 생성. Plugin 안에 그 규격이 단일 원천(single source of truth)으로 들어 있음.
- GPCR 분기는 cli가 stdout JSON의 `is_gpcr` 필드로 알림 — LLM이 추측하지 않음.

---

## GPCRdb 약리학 메타 자동 분기

- GPCR 여부는 UniProt `entry_name`(예: `5HT2A_HUMAN`)을 GPCRdb `/protein/{slug}/` 엔드포인트로 확인 (404면 비GPCR).
- GPCRdb 응답에서 State / Ligand / Ligand modality / Signaling protein 채움.
- **Fusion protein** / **Antibody** 컬럼은 GPCRdb가 별도 필드로 제공하지 않아 RCSB polymer entity 설명 + PDB 제목 패턴 매칭으로 채움 ([tools/parser.py](tools/parser.py)).
- 리간드 이름은 PubChem으로 일반명 변환 ("EZX" → "IHCH-7179" 등).
- GPCRdb 일시 장애 시 graceful degradation — 기본 PDB 데이터는 항상 반환되고 누락 경고가 표시됨.

---

## 디렉토리 구조

```
pdb/
├── .claude-plugin/
│   ├── marketplace.json    사내 marketplace 등록 메타
│   └── plugin.json         pdb plugin 메타
├── skills/pdb/             /pdb Skill 본체
│   ├── SKILL.md            트리거 + 자동 판단 규칙
│   ├── scripts/pdb         셸 래퍼 (.venv 자동 탐지)
│   └── references/
│       ├── family_map.md       패밀리 확장 표
│       ├── filter_keywords.md  필터 키워드 매핑
│       └── excel_spec.md       Excel 출력 규격 (단일 원천)
├── cli.py                  argparse 진입점
├── adapters/
│   ├── runner.py           13개 dispatch
│   └── formatter.py        JSON / Markdown 변환
├── tools/                  13개 외부 API 클라이언트
├── models/schemas.py       Pydantic 데이터 모델
├── server.py               MCP 서버 (cli가 import해서 헬퍼 재사용)
├── setup.sh                .venv 부트스트랩
├── pyproject.toml / requirements.txt
├── PLUGIN_INSTALL.md       상세 설치 가이드
├── README.md               (이 파일)
└── README_MCP.md           Claude Desktop / SSE·HTTP MCP 사용자용
```

---

## 보안 / 데이터 정책

- 모든 외부 API는 **anonymous 호출**. API 키 불필요. 사용자 식별 정보를 전송하지 않음.
- `allowed-tools`로 `Bash`, `Read`만 허용 (셸 래퍼 호출 + reference 파일 읽기 목적).
- GitHub repo는 private. 사내 신약연구소 전용.

---

## 라이선스 / 저자

사내 전용. 외부 공개·재배포 시 별도 협의 필요. 문의: aidrugdev2.namuict@gmail.com
