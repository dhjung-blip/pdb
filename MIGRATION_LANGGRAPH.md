# PDB Research MCP → LangGraph 에이전트 마이그레이션 설계 문서

작성일: 2026-05-26
대상 시스템: `pdb-mcp-server` (Python 3.11+, MCP stdio)
마이그레이션 대상: LangGraph 0.2+ 기반 stateful 에이전트

---

## 1. 마이그레이션 동기

| 현재(MCP) 한계 | LangGraph로 얻는 것 |
|---|---|
| 도구 실행 순서를 Claude Desktop이 자율 결정 → 같은 질의도 호출 패턴이 매번 다름 | 도메인-특화 워크플로(예: "타깃 → 구조 → 리간드 → 활성")를 그래프로 명시 |
| stateless 도구. 동일 타깃을 연속 질의해도 매번 외부 API 재호출 | 그래프 state에 결과 캐싱 + checkpointer로 세션 지속 |
| Claude Desktop 외 인터페이스 부재 (CLI/웹/슬랙 등 불가) | LangServe/Streamlit/FastAPI 등 임의 채널로 서빙 |
| LLM이 Anthropic Claude로 고정 | LangChain `BaseChatModel` 추상화 — Claude/OpenAI/Gemini/로컬 모델 전환 가능 |
| Silent-failure 워닝이 응답 텍스트에만 존재 → 후속 도구 호출이 이를 무시할 수 있음 | state에 `warnings: list[str]` 필드 → 모든 노드가 인지 + 라우팅 결정에 반영 |
| 평가/디버깅 어려움 (호출 시퀀스 재현 불가) | LangSmith 트레이스로 노드별 입출력 기록·재실행 |

**비-동기**: MCP 자체는 정상 동작 중. 누락 보고 사례(NAMPT, 5-HT2 family)는 silent-failure 수정으로 해결. 이 마이그레이션은 **확장성·관측성·다중 채널 서빙**이 필요해질 때만 정당화된다.

---

## 1.5 MCP의 강점 (마이그레이션의 기회비용)

1장의 동기 표는 "LangGraph가 잘하는 것"만 모은 편향된 시각이다. 반대편 — **MCP에서 LangGraph로 가면 실제로 잃는 것** — 을 같은 무게로 기록한다.

### 1.5.1 운영 단순성

| 항목 | MCP (현재) | LangGraph |
|---|---|---|
| 배포 | stdio 서브프로세스 1개 (Python 파일 + Claude Desktop config 한 줄) | FastAPI 서버 + 포트 + 체크포인트 DB + 컨테이너 + (다중 사용자 시) 인증 |
| 업데이트 | `server.py` 편집 후 Claude Desktop 재시작 | 컨테이너 재빌드/배포, 진행 중 세션 처리 정책 결정 |
| 장애 복구 | 사용자가 Claude Desktop 재시작 | 헬스체크, 자동 재시작, 세션 복구 정책 필요 |
| 운영 인력 | 0 (개발자 개인 컴퓨터) | 최소 0.2 FTE (운영 대시보드·알림·온콜) |

연구소가 운영팀 없이 1인 개발자가 유지보수하는 상황이라면 이 차이가 가장 결정적이다.

### 1.5.2 LLM 비용의 위치

- **MCP**: 모든 LLM 호출이 사용자의 Claude Desktop 구독 안에서 소비됨. 시스템 운영자는 LLM 비용 0.
- **LangGraph**: 분류기 노드 + 응답 생성 + (선택적) ReAct 도구 호출 모두 API 비용으로 운영자가 부담.

연구소가 수십 명의 연구원에게 동시 제공한다면 월 수백~수천 달러 차이. **개인 사용 도구 → 사내 서비스 전환에 따르는 비용 모델 변경**임을 명시해야 한다.

### 1.5.3 LLM 지능 활용도 다운그레이드 위험

현재 SYSTEM_PROMPT의 규칙 1~7은 **Claude Sonnet 4.6의 자연어 이해에 위임**되어 있다.

예: 연구원이 "HTR2A에 antagonist만, 그리고 가능하면 cryo-EM 위주로 봐줘 — 너무 옛날 건 빼고"라고 입력하면 Claude는:
- `target=HTR2A` 추출
- `ligand_modality_filter=Antagonist` 추출
- `method_filter=EM`을 "위주로"라는 표현에서 soft preference로 해석 (필터 대신 정렬)
- `min_year`을 "너무 옛날" → 임의 기준(예: 2015) 추론

이걸 LangGraph `classify_intent` 노드로 옮긴다는 건:
- few-shot 예제로 패턴을 직접 enumerate해야 함
- "위주로"의 soft vs hard 구분 같은 미묘한 의도를 코드로 정의해야 함
- 분류 정확도가 Sonnet 4.6 수준에 도달하려면 분류기 자체도 Sonnet → 비용 증가
- Haiku로 다운그레이드하면 미묘한 의도 손실

**즉 SYSTEM_PROMPT.md의 자연어 규칙을 코드화한다는 건 유연성 일부를 영구적으로 포기하는 것**.

### 1.5.4 데이터 프라이버시 / 컴플라이언스

- **MCP**: 모든 외부 API 호출이 연구원 로컬 머신에서 발생. 회사 네트워크 정책·VPN·프록시 그대로 적용. 연구 중인 타깃·리간드 이름이 회사 외부 서버를 거치지 않음(LLM은 Anthropic이 처리하지만, 별도 도구 호출은 로컬).
- **LangGraph**: 사내 서버에 배포 시 OK. 외부 SaaS 호스팅 시 연구 데이터가 추가 인프라를 경유.

신약개발은 IP 민감도가 높은 영역. **로컬 실행 모델 → 서버 경유 모델로의 전환은 보안팀 검토 사항**.

### 1.5.5 사용자 친숙도 / 학습 비용

- 연구원은 이미 Claude Desktop을 일상적으로 사용 중. 자연어 질의 → 결과 흐름이 익숙.
- LangGraph + Streamlit/웹 UI로 옮기면 별도 도구를 띄워야 함. 채팅 + 코드 검토 + 논문 검색을 Claude 하나로 처리하던 워크플로가 분리됨.
- "이 결과를 가지고 다른 단백질도 비교해줘" 같은 follow-up이 Claude Desktop 채팅에선 자연스럽지만, 별도 웹앱이면 새 세션을 시작해야 함.

### 1.5.6 도구 합성성 (Composability)

MCP의 핵심 강점: **Claude Desktop이 여러 MCP 서버를 동시에 활용**한다.

예: 연구원이 "HTR2A 최신 구조 찾고, 결과를 Linear 티켓에 정리해줘"라고 하면:
- PDB-MCP 서버 → `search_target`
- Linear-MCP 서버 → 티켓 생성

Claude가 두 서버의 도구를 조합. LangGraph로 옮기면 **닫힌 시스템** — Linear 통합도 직접 그래프에 추가해야 한다. 즉 도메인 외 통합이 모두 직접 구현 대상이 된다.

Anthropic이 MCP 생태계에 투자 중이고 외부 MCP 서버가 늘어나는 추세 — 이 흐름과 단절된다.

### 1.5.7 사용자 가시성

- **MCP**: Claude Desktop 채팅에 "search_target 도구를 호출했습니다 (target=HTR2A)" 식으로 도구 사용이 노출. 연구원이 LLM의 행동을 즉시 검증 가능.
- **LangGraph**: 그래프 트레이스는 LangSmith에서 보지만 이건 **개발자용**. 사용자에겐 결과만 보임. 어떤 노드가 fallback 됐는지 등의 디버깅 정보를 사용자에게 노출하려면 별도 UI 작업 필요.

### 1.5.8 업데이트와 변경 단순성

- **MCP**: `tools/uniprot.py` 한 줄 수정 → Claude Desktop 재시작 → 즉시 반영.
- **LangGraph**: 동일 변경 시 컨테이너 재빌드 → 배포 → (체크포인트 호환성 검증) → 진행 중 세션 처리.

이번 silent-failure 수정 12건을 LangGraph 환경에서 했다면 배포 사이클이 12번 발생. MCP 환경에선 Claude Desktop 재시작 12번.

### 1.5.9 정리: 어떤 경우에 MCP를 유지해야 하나?

다음 조건에 다수 해당하면 **마이그레이션은 비추**:

- 사용자 규모 < 10명, 운영팀 없음
- 연구 데이터 외부 인프라 경유에 보안팀 제약 있음
- Slack 봇·이메일 알림·CI 통합 같은 다중 채널 요구 없음
- 세션 메모리("아까 본 그거")가 critical 기능 아님 (현재도 Claude Desktop 컨텍스트로 어느 정도 가능)
- 비-Claude LLM(GPT/Gemini/로컬)으로 전환 계획 없음
- 자율 다중 단계 리서치(open_research ReAct) 빈도 낮음

반대로 다음 조건이 강하면 마이그레이션이 정당화:

- 사용자 수십 명에게 사내 서비스로 제공
- 비-개발자 사용자가 채팅 UI 외 채널(Slack/이메일/CLI)로 접근해야 함
- LLM 공급사 락인 회피 필요
- 평가/회귀/A/B 테스트 인프라 구축 필요
- LangSmith 등 관측 도구를 통한 품질 추적이 요구사항

### 1.5.10 절충안 — 양립 운영

마이그레이션 전면 결정 대신 **MCP를 default 채널로 유지하면서 LangGraph를 점진 추가**하는 옵션:

```
사용자 1~10명 (연구원 일상)        →  Claude Desktop + MCP (현재)
사용자 10~50명 (사내 서비스)       →  LangServe API + Streamlit (Phase 1~3 결과물)
배치/스케줄링/Slack 봇             →  LangGraph 노드 일부만 재사용
```

이 모델에서는:
- `tools/*.py`는 두 채널 공유 (이미 Phase 1~3 silent-failure 수정 완료 상태로 견고)
- `server.py` (MCP)는 무변경 유지
- `agent/` (LangGraph)는 신규 디렉터리에 격리 — MCP 사용자 영향 0

5장의 "MCP 호환 유지" 옵션이 이에 해당. **풀 마이그레이션이 아닌 듀얼 채널 운영**이 1인 개발 환경에서 가장 현실적.

---

## 2. 현재 시스템 구조 (Baseline)

### 2.1 모듈 인벤토리

| 계층 | 파일 | LOC | 역할 |
|---|---|---|---|
| 진입점 | `server.py` | ~2900 | MCP `Server` 정의, 13개 `@server.call_tool` 핸들러, 렌더링 |
| 데이터 모델 | `models/schemas.py` | 291 | Pydantic 모델 (PDBEntry, UniProtResult, SearchResult, ...) |
| 외부 API | `tools/uniprot.py` | 173 | UniProt 검색 + cross-ref |
| | `tools/pdb.py` | 260 | RCSB GraphQL |
| | `tools/gpcrdb.py` | 724 | GPCRdb + PubChem |
| | `tools/alphafold.py` | 133 | AlphaFold DB |
| | `tools/binding_site.py` | 278 | PDBe + RCSB binding sites |
| | `tools/bioactivity.py` | 408 | ChEMBL + IUPHAR |
| | `tools/literature.py` | 429 | Europe PMC + PubMed |
| | `tools/opentargets.py` | 298 | OpenTargets GraphQL |
| | `tools/ligand.py` | 418 | PubChem + ChEMBL + IUPHAR 통합 |
| | `tools/sequence.py` | 316 | UniProt feature/variant |
| 출력 | `tools/export.py` | 452 | Excel 워크북 생성 |
| 보조 | `tools/parser.py` | 64 | PDB 제목 파싱 (fusion/antibody) |

### 2.2 13개 MCP 도구

```
search_target            search_family           get_pdb_detail
compare_targets          get_ligand_detail       get_target_bioactivities
get_paper_abstract       search_papers           get_sequence_region
get_natural_variants     get_binding_site        get_alphafold_model
get_target_intelligence
```

### 2.3 데이터 흐름 (현재)

```
Claude Desktop chat
       ↓ (자연어)
Claude Sonnet 4.6 (Desktop 내장 LLM)
       ↓ (도구 선택 결정)
MCP stdio → server.py (Python)
       ↓
tools/*.py → 외부 REST/GraphQL API
       ↓
TextContent (markdown) → Claude
       ↓
사용자에게 표시
```

핵심: **LLM 자체가 도구 오케스트레이터**. 사용자→LLM→도구→LLM→사용자 패턴.

---

## 3. 목표 구조 (LangGraph)

### 3.1 기술 스택

| 컴포넌트 | 선택 | 비고 |
|---|---|---|
| 그래프 프레임워크 | `langgraph` 0.2+ | StateGraph + checkpointer |
| 도구 데코레이터 | `@langchain_core.tools.tool` | MCP `@call_tool`을 1:1 변환 |
| LLM | `ChatAnthropic` (기본) / `ChatOpenAI` (옵션) | `claude-sonnet-4-6` 우선, 환경변수로 전환 |
| 상태 직렬화 | Pydantic v2 (이미 사용 중) | `models/schemas.py` 그대로 재사용 |
| 영속화 | `SqliteSaver` (개발) / `PostgresSaver` (운영) | 세션·체크포인트 |
| 트레이싱 | LangSmith (선택) | 노드별 트레이스 |
| 서빙 | LangServe (FastAPI) | `/agent/invoke` 엔드포인트 |
| UI | Streamlit (Phase 0~1) → 별도 React (Phase 2+) | |

### 3.2 그래프 토폴로지 — 하이브리드 ReAct + 도메인 워크플로

```
                  ┌────────────────┐
                  │   START        │
                  └───────┬────────┘
                          ↓
                  ┌────────────────┐
                  │ classify_intent │  ── LLM이 사용자 의도를 5가지로 분류
                  └───────┬────────┘    (target_search / pdb_detail / compare /
                          │              ligand_detail / open_research)
            ┌─────────────┼─────────────┬─────────────┐
            ↓             ↓             ↓             ↓
   ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
   │target_search │ │pdb_detail│ │ compare  │ │open_research │
   │  (DAG)       │ │  (linear)│ │  (fanout)│ │  (ReAct)     │
   └──────┬───────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘
          │              │            │              │
          └──────────────┴────┬───────┴──────────────┘
                              ↓
                  ┌────────────────────┐
                  │ format_response    │  ── 결과 + warnings → 마크다운
                  └────────┬───────────┘
                           ↓
                       ┌───────┐
                       │  END  │
                       └───────┘
```

핵심 설계 결정:
- **도메인 워크플로는 DAG로 고정**: target_search는 `uniprot → gpcr_check → [gpcrdb || pdb_meta] → merge → filter → sort` 순서 보장 (현재 SYSTEM_PROMPT 규칙이 코드로 강제됨)
- **개방형 질의만 ReAct**: "이 단백질 어떤 약물이 있나?" 같은 다중 단계 추론은 LLM이 도구 호출 시퀀스를 결정
- **분류기로 두 모드 분기**: 의도 분류가 틀려도 ReAct 모드로 fallback

### 3.3 State 설계

```python
from typing import TypedDict, Optional, Annotated
from operator import add
from models.schemas import UniProtResult, PDBEntry, ...

class AgentState(TypedDict):
    # 입력
    user_query: str
    intent: Optional[Literal["target_search", "pdb_detail", "compare",
                              "ligand_detail", "open_research"]]

    # 정규화된 파라미터 (분류기가 채움)
    target: Optional[str]
    pdb_id: Optional[str]
    targets: Optional[list[str]]
    ligand_query: Optional[str]
    filters: dict  # {max_resolution, min_year, modality, state, method}

    # 누적 결과 (각 노드가 채움)
    uniprot: Optional[UniProtResult]
    structures: list[PDBEntry]  # target_search 결과
    pdb_entry: Optional[PDBEntry]  # pdb_detail 결과
    bioactivities: Optional[TargetBioactivities]
    papers: list[PaperAbstract]
    ligand_detail: Optional[LigandDetail]

    # 메타 (모든 노드가 append)
    warnings: Annotated[list[str], add]
    failed_external_calls: Annotated[list[str], add]

    # ReAct 모드용
    messages: Annotated[list[BaseMessage], add]
    tool_calls_remaining: int  # 토큰/비용 보호용 카운터

    # 출력
    response_markdown: Optional[str]
    excel_path: Optional[str]
```

`Annotated[..., add]`로 병렬 노드의 warnings를 자동 병합. 현재 시스템의 `gpcrdb_warning` 단일 string보다 표현력이 높다.

### 3.4 노드 구현 패턴

기존 `tools/*.py`는 **그대로 재사용**. 노드는 그 위에 얇은 어댑터:

```python
async def gpcrdb_enrichment_node(state: AgentState) -> dict:
    """state.uniprot.is_gpcr가 True이면 GPCRdb로 보강."""
    from tools.gpcrdb import (
        get_gpcrdb_structures,
        consume_pubchem_failures,
        consume_ligand_resolution_failures,
    )
    if not state["uniprot"] or not state["uniprot"].is_gpcr:
        return {}

    consume_pubchem_failures()
    consume_ligand_resolution_failures()
    gpcrdb_map = await get_gpcrdb_structures(
        state["uniprot"].gpcrdb_slug,
        pdb_ids=state["uniprot"].pdb_ids,
    )

    new_warnings = []
    if consume_pubchem_failures() > 0:
        new_warnings.append("PubChem 일시 장애 — 리간드 일반명 일부 누락")
    if consume_ligand_resolution_failures() > 0:
        new_warnings.append("리간드 이름 해석 중 예외 발생")

    # 기존 server.py의 GPCRdb 병합 로직을 함수화해 호출
    updated_structures = merge_gpcrdb_data(state["structures"], gpcrdb_map)
    return {"structures": updated_structures, "warnings": new_warnings}
```

핵심: `tools/*.py`의 silent-failure 수정 (Phase 1~3) 패턴이 LangGraph state의 `warnings` 채널과 자연스럽게 정합한다.

### 3.5 라우팅 (조건부 엣지)

```python
def route_after_uniprot(state: AgentState) -> str:
    """UniProt 결과에 따라 GPCRdb 보강 여부 결정."""
    if not state["uniprot"]:
        return "format_response"  # 검색 실패 → 바로 응답
    if state["uniprot"].is_gpcr:
        return "gpcrdb_enrichment"
    return "pdb_metadata_fetch"

graph.add_conditional_edges("uniprot_search", route_after_uniprot, {
    "gpcrdb_enrichment": "gpcrdb_enrichment",
    "pdb_metadata_fetch": "pdb_metadata_fetch",
    "format_response": "format_response",
})
```

GPCRdb 보강과 PDB 메타데이터 조회는 **병렬 노드**로 실행 가능 (`fan_out`):

```python
graph.add_edge("gpcr_check", "gpcrdb_enrichment")
graph.add_edge("gpcr_check", "pdb_metadata_fetch")
graph.add_edge("gpcrdb_enrichment", "merge_and_filter")
graph.add_edge("pdb_metadata_fetch", "merge_and_filter")
```

LangGraph가 자동으로 두 분기 완료를 기다린 후 `merge_and_filter` 실행.

---

## 4. MCP → LangGraph 매핑

### 4.1 도구 단위 매핑

| MCP 도구 | LangGraph 처리 방식 |
|---|---|
| `search_target` | `target_search` 서브그래프 (5~7 노드) |
| `search_family` | `compare` 의도와 통합 — `targets` 배열 처리 |
| `get_pdb_detail` | `pdb_detail` 선형 그래프 (3 노드) |
| `compare_targets` | `compare` fan-out 그래프 — `Send` API로 타깃별 병렬 |
| `get_ligand_detail` | `ligand_detail` 선형 그래프 |
| `get_target_bioactivities` | `open_research` ReAct 도구 |
| `get_paper_abstract` | `open_research` ReAct 도구 |
| `search_papers` | `open_research` ReAct 도구 |
| `get_sequence_region` | `open_research` ReAct 도구 |
| `get_natural_variants` | `open_research` ReAct 도구 |
| `get_binding_site` | `open_research` ReAct 도구 |
| `get_alphafold_model` | `open_research` ReAct 도구 |
| `get_target_intelligence` | `open_research` ReAct 도구 |

설계 의도: **자주 쓰이는 정형 워크플로(4개)는 DAG로 고정**, **나머지 9개는 LLM이 ReAct로 자유롭게 호출**. 현재 SYSTEM_PROMPT의 규칙 1·2·3 (자동 호출/패밀리 확장/출력 형식 선택)이 분류기 + DAG로 코드화된다.

### 4.2 SYSTEM_PROMPT.md 매핑

| 규칙 | LangGraph 구현 |
|---|---|
| 규칙 1: 타겟 감지 즉시 호출 | `classify_intent` 노드 |
| 규칙 2: 수용체 패밀리 자동 확장 | `classify_intent`의 출력 후처리 (별칭 사전) |
| 규칙 3: 출력 형식 자동 선택 | `format_response` 노드가 `state.uniprot.is_gpcr` 분기 |
| 규칙 4: Excel 자동 저장 | `target_search` 서브그래프 마지막에 `export_excel` 노드 (조건부) |
| 규칙 5: 필터 자동 적용 | `classify_intent`가 `state.filters`를 채움 |
| 규칙 6: 결과 요약 한 줄 | `format_response`의 헤더 템플릿 |
| 규칙 7: 후속 질문 제안 | `format_response`의 마지막 섹션 |

### 4.3 silent-failure 패턴 매핑

현재 (Phase 1~3에서 정착):
```python
try:
    result = await fetch_xxx()
except XxxUnavailableError as exc:
    return _text(f"> ⚠️ {exc}...")
```

LangGraph (state-driven):
```python
async def fetch_xxx_node(state: AgentState) -> dict:
    try:
        result = await fetch_xxx(state["target"])
        return {"xxx_result": result}
    except XxxUnavailableError as exc:
        return {
            "xxx_result": None,
            "warnings": [f"⚠️ {exc}"],
            "failed_external_calls": [f"xxx:{exc}"],
        }
```

워닝이 state에 누적되어 **모든 후속 노드가 인지**. 예: GPCRdb가 죽었으면 비교 노드가 state 텍스트만 보고도 "이 비교는 부분 데이터"를 알 수 있다.

---

## 5. 서빙 아키텍처

### 5.1 API 엔드포인트 (LangServe)

```python
from fastapi import FastAPI
from langserve import add_routes

app = FastAPI(title="PDB Research Agent")
add_routes(
    app,
    graph.with_config({"configurable": {"thread_id": "..."}}),
    path="/agent",
    input_type=AgentInput,
    output_type=AgentOutput,
)
```

- `/agent/invoke`: 단발 질의
- `/agent/stream`: SSE 스트리밍 (노드 진행 상황 실시간 표시)
- `/agent/batch`: 다중 타깃 일괄 (compare 의도 자동 라우팅)

### 5.2 UI 옵션

| 옵션 | 장점 | 단점 |
|---|---|---|
| Streamlit | 30분 만에 PoC, 데이터 시각화 강함 | 다중 사용자 동시성 약함 |
| Gradio | 챗 UI 기본 제공 | 커스터마이징 한계 |
| React + LangServe | 완전한 컨트롤 | 개발 비용 |
| Claude Desktop 유지 | 기존 사용자 영향 0 | LangGraph 장점 일부만 활용 |

**권장**: Phase 1에서 Streamlit으로 PoC, Phase 3에서 React 전환.

### 5.3 MCP 호환 유지 (선택)

기존 Claude Desktop 사용자가 있다면 `langgraph-mcp-adapter` 패턴으로 양립 가능:

```
Claude Desktop ──→ MCP server (server.py 유지)
                      ↓
                  LangGraph (내부 구현)
                      ↑
Streamlit/Web ────────┘
```

`server.py`의 각 `@server.call_tool`이 LangGraph subgraph를 invoke. 도구 정의 자체는 그대로.

---

## 6. 마이그레이션 단계

### Phase 0: 기반 작업 (1주)
- LangGraph 종속성 추가 (`langgraph`, `langchain-anthropic`, `langserve`)
- `state.py`에 `AgentState` 정의
- 기존 `tools/*.py`는 무변경 (이미 silent-failure 수정 완료, 그대로 재사용)
- LangSmith 프로젝트 셋업 (선택)

**결과물**: 빈 그래프가 `pip install`로 설치되고 `pytest`가 통과

### Phase 1: target_search 서브그래프 (1.5주)
- `target_search` DAG 노드 6개 구현 (uniprot → gpcr_check → fanout(gpcrdb, pdb_meta) → merge → filter → sort)
- `format_response` 노드 (현재 `_render_basic_result` / `_render_gpcr_result` 이식)
- Streamlit UI 한 페이지
- 회귀 테스트: 기존 MCP와 같은 입력 → 같은 결과 확인

**결과물**: "HTR2A 구조 찾아줘" 가 LangGraph로 동작

### Phase 2: 분류기 + compare/pdb_detail/ligand_detail (2주)
- `classify_intent` LLM 노드 (few-shot 프롬프트로 5가지 분류)
- `compare` 서브그래프 (Send API로 타깃별 병렬)
- `pdb_detail`, `ligand_detail` 선형 서브그래프
- 별칭 사전 (세로토닌→HTR2A,B,C 등) `classify_intent` 출력 후처리에 통합

**결과물**: 자연어 5종이 모두 적절한 서브그래프로 라우팅

### Phase 3: open_research ReAct + 9개 보조 도구 (2주)
- `@tool` 데코레이터로 9개 도구 노출 (paper, bioactivity, alphafold, binding_site, sequence, variants, intel)
- `create_react_agent` 또는 커스텀 ReAct 그래프
- `tool_calls_remaining` 카운터로 무한 루프 방지

**결과물**: "이 단백질 어떤 약물 임상 중이야?" 같은 복합 질의

### Phase 4: 운영화 (1주)
- `SqliteSaver` 체크포인터 → 세션 지속
- LangServe로 FastAPI 노출
- Docker 컨테이너화
- 로깅/모니터링 (LangSmith 트레이스 또는 OpenTelemetry)

**결과물**: 단일 컨테이너로 배포 가능

### Phase 5: 컷오버 (선택, 1주)
- MCP 호환 어댑터 제거 또는 유지 결정
- Claude Desktop 안내문 갱신
- 사용자 교육

**총 추정**: 약 7~8주. 1인 개발 기준. Phase 1까지 도달하면 MCP와 병행 운영 가능.

---

## 7. 새로 가능해지는 기능

마이그레이션 후 자연스럽게 추가 가능한 기능 (현 MCP로는 어려운):

| 기능 | 이유 |
|---|---|
| **세션 메모리** ("아까 본 HTR2A 구조 중 8JT8 자세히") | checkpointer가 state 지속 |
| **다중 단계 자율 리서치** ("KRAS G12C 억제제 phase 2 이상 정리") | open_research ReAct |
| **Slack/이메일 봇** | LangServe 위에 추가 핸들러 |
| **배치 처리** (50개 타깃 한 번에) | Send API + 비동기 fan-out |
| **결과 확신도 표시** | state.warnings + LLM이 self-rating |
| **휴먼 인 더 루프** (특정 노드에서 사용자 승인) | LangGraph `interrupt_before` |
| **A/B 테스트 다른 LLM** | ChatModel 추상화 |

---

## 8. 트레이드오프

### 8.1 비용
- 분류기 노드가 매 질의마다 LLM 호출 추가 → 토큰 비용 5~10% 증가 (Haiku 사용 시 무시 가능)
- LangSmith 트레이스는 별도 SaaS 비용 (월 ~$50, 자체 호스팅 가능)

### 8.2 복잡도
- 그래프 정의 + state 스키마 + 라우팅 — 코드 라인 수 증가 (~600줄 추가 예상)
- 디버깅 시 그래프 흐름 이해 필요 (LangSmith로 완화)

### 8.3 의존성 락인
- LangChain/LangGraph는 빠르게 변하는 생태계 → 0.x 버전 호환성 이슈 가능
- 완화: 도구 함수(`tools/*.py`)는 LangChain 의존 없이 유지 — 어댑터 노드만 LangChain

### 8.4 Claude Desktop 사용자 영향
- Phase 5까지 MCP 어댑터 유지하면 영향 0
- 컷오버 시 SYSTEM_PROMPT.md 의 자연어 질의가 LangGraph 분류기로 대체됨 → 분류 정확도 검증 필요

---

## 9. 위험 요소

| 위험 | 가능성 | 완화 |
|---|---|---|
| `classify_intent` 오분류로 잘못된 서브그래프 라우팅 | 中 | (1) few-shot 예제 풍부히, (2) 분류 신뢰도 임계치 미만 시 ReAct로 fallback, (3) 회귀 테스트로 분류 정확도 측정 |
| LangGraph 0.x API 변경 | 中 | 핵심 도구 함수는 LangChain 비의존. 노드 어댑터만 영향 받음 |
| 외부 API rate limit (RCSB/GPCRdb/PubChem 등) | 高 (현재도 발생) | 현 `asyncio.Semaphore` 패턴 그대로 유지 + state에 cache 키 추가 가능 |
| state 직렬화 실패 (Pydantic 모델 호환성) | 低 | 기존 schemas.py가 이미 Pydantic v2 |
| LLM 환각으로 도구 호출 누락 | 中 | DAG로 고정된 정형 워크플로(4개)는 영향 없음. ReAct만 위험 |
| Excel 출력 등 부수 효과(파일 시스템 쓰기) 노드의 멱등성 | 低 | 출력 파일명에 timestamp 포함, 노드 진입 전 state 확인 |

---

## 10. 의사결정 포인트 (시작 전 합의 필요)

1. **분류기 LLM**: Haiku 사용 vs Sonnet 사용? (비용 vs 정확도)
2. **MCP 양립 기간**: 영구 유지 vs Phase 5에서 폐기?
3. **세션 영속화 DB**: SQLite (단일 서버) vs Postgres (다중 인스턴스)?
4. **트레이싱**: LangSmith SaaS vs 자체 OpenTelemetry?
5. **UI 우선순위**: Streamlit PoC만 vs 처음부터 React?
6. **연구원 사용 채널 추가**: Slack 봇? 이메일 알림? CLI?

---

## 11. 다음 액션

이 문서가 승인되면:
1. Phase 0의 종속성 추가 PR (실제 코드 영향 최소)
2. `agent/state.py`, `agent/graph.py` 빈 골격 추가
3. Phase 1의 `target_search` 서브그래프 PoC — 1주 후 데모

승인 전 명확화가 필요한 부분이 있으면 8장(트레이드오프), 9장(위험), 10장(의사결정 포인트)를 우선 토론 대상으로 다룬다.

---

## 부록 A: 디렉터리 구조 비교

### 현재
```
pdb-mcp-server/
├── server.py                # MCP 진입점 + 핸들러
├── models/schemas.py
├── tools/
│   ├── uniprot.py
│   ├── pdb.py
│   ├── gpcrdb.py
│   └── ...
└── tests/
```

### 마이그레이션 후
```
pdb-research-agent/
├── server.py                # MCP 어댑터 (선택 유지)
├── agent/                   # ★ 신규
│   ├── __init__.py
│   ├── state.py             # AgentState TypedDict
│   ├── graph.py             # 그래프 빌더 (build_graph())
│   ├── nodes/
│   │   ├── classify.py
│   │   ├── target_search.py
│   │   ├── compare.py
│   │   ├── pdb_detail.py
│   │   ├── ligand_detail.py
│   │   ├── open_research.py
│   │   └── format.py
│   └── prompts/
│       ├── classify.md
│       └── format.md
├── api/                     # ★ 신규
│   ├── server.py            # LangServe FastAPI
│   └── streamlit_app.py
├── models/schemas.py        # 그대로 유지
├── tools/                   # 그대로 유지 (silent-failure 수정 완료 상태)
│   └── ...
└── tests/
    ├── ... (기존)
    └── agent/               # ★ 신규
        ├── test_classify.py
        ├── test_target_search.py
        └── test_routing.py
```

`tools/*.py` 와 `models/schemas.py`는 무변경. 신규 `agent/` 와 `api/` 디렉터리만 추가.

---

## 부록 B: 의존성 추가

```toml
[project.optional-dependencies]
agent = [
    "langgraph>=0.2.0",
    "langchain-anthropic>=0.2.0",
    "langchain-core>=0.3.0",
    "langserve>=0.3.0",
    "fastapi>=0.115.0",
    "streamlit>=1.39.0",        # PoC UI
    "langgraph-checkpoint-sqlite>=2.0.0",
]
```

기존 `mcp`, `httpx`, `openpyxl`, `pydantic` 모두 유지.
