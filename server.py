"""PDB 연구 자동화 MCP 서버 진입점.

타겟 단백질 이름 → UniProt → RCSB PDB 워크플로우를 MCP Tool 3개로 노출한다.
GPCR 타깃이면 GPCRdb를 추가 연동하여 확장 테이블을 생성한다.
tool description에는 자동 판단 규칙(별칭 사전·필터·정렬)을 내장하여, 연구원이
프롬프트 형식을 외우지 않아도 Claude가 올바르게 도구를 호출하도록 돕는다.
로컬(Claude Desktop)은 stdio transport, 사내 서버 배포는 SSE(HTTP) transport로
연동된다 — --transport 옵션으로 선택한다.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from urllib.parse import quote

import mcp.types as types
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from models.schemas import (
    AlphaFoldModel,
    BindingSiteResult,
    LigandDetail,
    PDBEntry,
    PaperAbstract,
    SearchResult,
    SequenceRegion,
    TargetBioactivities,
    TargetIntelligence,
    UniProtResult,
    VariantList,
)
from tools.alphafold import AlphaFoldUnavailableError, fetch_alphafold_model
from tools.binding_site import fetch_binding_sites
from tools.bioactivity import fetch_target_bioactivities
from tools.export import export_family_to_excel, export_to_excel, format_acs_citation
from tools.gpcrdb import (
    check_gpcr,
    consume_ligand_resolution_failures,
    consume_pubchem_failures,
    get_gpcrdb_single,
    get_gpcrdb_structures,
)
from tools.ligand import fetch_ligand_detail
from tools.literature import LiteratureAPIError, fetch_paper_abstract, search_papers
from tools.opentargets import OpenTargetsAPIError, fetch_target_intelligence
from tools.parser import extract_antibody_from_title, extract_fusion_from_title
from tools.pdb import (
    fetch_all_pdb_entries,
    fetch_all_pdb_entries_with_failures,
    fetch_single_pdb_entry,
    format_method,
)
from tools.sequence import (
    SequenceError,
    fetch_natural_variants,
    fetch_sequence_region,
)
from tools.rcsb_search import RCSBSearchError, search_pdb_ids_by_uniprot
from tools.uniprot import UniProtError, search_uniprot

server = Server("pdb-research-server")

# State 정렬 우선순위 (state_then_date 정렬용 — Inactive 우선)
_STATE_RANK = {"Inactive": 0, "Active": 1, "Intermediate": 2}


# --------------------------------------------------------------------------
# 공통 유틸리티
# --------------------------------------------------------------------------

def _text(message: str) -> list[types.TextContent]:
    """단일 TextContent 응답을 생성한다."""
    return [types.TextContent(type="text", text=message)]


def _md_escape(value: str) -> str:
    """마크다운 테이블 셀에 안전하도록 파이프/개행 문자를 정리한다."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _display_path(path: str) -> str:
    """저장 경로를 사용자에게 보여줄 형태로 변환한다.

    Docker로 실행할 때 컨테이너 내부 경로(PDB_MCP_OUTPUT_DIR)가 그대로 노출되면
    혼란스러우므로, PDB_MCP_DISPLAY_DIR(호스트 마운트 경로)가 지정돼 있으면
    출력 디렉토리 접두사를 그 값으로 치환해 호스트 경로로 보여준다.
    PDB_MCP_PUBLIC_BASE_URL 이 지정돼 있으면 서버 다운로드 URL을 우선 표시한다.
    """
    public_base_url = os.environ.get("PDB_MCP_PUBLIC_BASE_URL")
    if public_base_url:
        return public_base_url.rstrip("/") + "/files/" + quote(Path(path).name)

    display_dir = os.environ.get("PDB_MCP_DISPLAY_DIR")
    output_dir = os.environ.get("PDB_MCP_OUTPUT_DIR")
    if display_dir and output_dir:
        output_dir = output_dir.rstrip("/")
        if path.startswith(output_dir):
            return display_dir.rstrip("/") + path[len(output_dir):]
    return path


def _short(value: str | None, limit: int) -> str:
    """긴 문자열을 limit 길이로 잘라 말줄임표를 붙인다."""
    if not value:
        return "-"
    value = str(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _resolution_str(resolution: float | None) -> str:
    """해상도를 표시 문자열로 변환한다 (NMR 등 없는 경우 N/A)."""
    return f"{resolution:.2f}" if resolution is not None else "N/A"


def _year_of(entry: PDBEntry) -> int | None:
    """citation.year 우선, 없으면 released_date 앞 4자리에서 연도를 추출."""
    if entry.citation and entry.citation.year:
        return entry.citation.year
    if entry.released_date and entry.released_date[:4].isdigit():
        return int(entry.released_date[:4])
    return None


def _coerce_int(value) -> int | None:
    """인자를 정수로 변환한다. 불가능하면 None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value) -> float | None:
    """인자를 실수로 변환한다. 불가능하면 None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_label(sort_by: str) -> str:
    """정렬 기준의 한국어 라벨."""
    return {
        "resolution": "해상도 좋은순",
        "state_then_date": "State별 → 최신순",
        "date": "최신순(공개일)",
    }.get(sort_by, "최신순(공개일)")


def _sort_structures(
    structures: list[PDBEntry],
    sort_by: str,
    gpcr_priority: bool = False,
) -> list[PDBEntry]:
    """정렬 기준에 따라 구조 목록을 정렬한다.

    - "resolution": 해상도 오름차순(좋은 순), 값이 없는 구조는 뒤로.
    - "state_then_date": State(Inactive→Active→Intermediate→없음) 우선, 같은
      State 안에서는 공개일 내림차순(최신순).
    - "date"(기본): 공개일 내림차순(최신순), 값이 없는 구조는 뒤로.
    - gpcr_priority=True: GPCRdb 메타데이터가 병합된 구조를 앞으로 모은다.

    파이썬 정렬은 안정 정렬이므로 2단계 정렬로 그룹 내 순서를 보존한다.
    """
    if sort_by == "resolution":
        result = sorted(
            structures, key=lambda e: (e.resolution is None, e.resolution or 0.0)
        )
    elif sort_by == "state_then_date":
        result = sorted(structures, key=lambda e: e.released_date or "", reverse=True)
        result = sorted(result, key=lambda e: _STATE_RANK.get(e.state or "", 3))
    else:
        result = sorted(structures, key=lambda e: e.released_date or "", reverse=True)

    if gpcr_priority:
        # pref_chain은 GPCRdb 수록 구조에만 채워지므로 수록 여부 신호로 쓴다.
        result = sorted(result, key=lambda e: e.pref_chain is None)
    return result


def _apply_filters(
    structures: list[PDBEntry],
    *,
    max_resolution: float | None = None,
    min_year: int | None = None,
    method_filter: str | None = None,
    ligand_modality_filter: str | None = None,
    state_filter: str | None = None,
) -> tuple[list[PDBEntry], list[str]]:
    """후처리 필터를 적용하고 (필터링된 목록, 적용된 필터 설명) 을 반환한다."""
    result = structures
    notes: list[str] = []

    if max_resolution is not None:
        result = [
            e for e in result
            if e.resolution is not None and e.resolution <= max_resolution
        ]
        notes.append(f"해상도 ≤ {max_resolution:g}Å")

    if min_year is not None:
        result = [
            e for e in result
            if e.released_date and e.released_date[:4].isdigit()
            and int(e.released_date[:4]) >= min_year
        ]
        notes.append(f"{min_year}년 이후 공개")

    if method_filter:
        target_method = format_method(method_filter)
        result = [e for e in result if format_method(e.method) == target_method]
        notes.append(f"Method = {target_method}")

    if ligand_modality_filter:
        wanted = ligand_modality_filter.strip().lower()
        result = [
            e for e in result
            if e.ligand_modality and e.ligand_modality.lower() == wanted
        ]
        notes.append(f"Modality = {ligand_modality_filter}")

    if state_filter:
        wanted = state_filter.strip().lower()
        result = [e for e in result if e.state and e.state.lower() == wanted]
        notes.append(f"State = {state_filter}")

    return result, notes


def _annotate_fusion_antibody(entry: PDBEntry) -> None:
    """RCSB polymer entity 설명 + PDB 제목에서 Fusion protein / Antibody를 추출한다.

    이미 GPCRdb 또는 다른 소스에서 채워진 값이 있으면 **덮어쓰지 않는다** — None 필드만
    polymer/제목 fallback으로 채운다. GPCRdb가 "BRIL"을 제공했는데 polymer 정규식이
    매칭 실패해서 None으로 회귀하는 사고를 막기 위함.
    """
    if entry.fusion_protein is not None and entry.antibody is not None:
        return  # 두 필드 모두 이미 채워져 있으면 작업 없음
    text = " / ".join([entry.title or ""] + entry.polymer_descriptions)
    if entry.fusion_protein is None:
        entry.fusion_protein = extract_fusion_from_title(text)
    if entry.antibody is None:
        entry.antibody = extract_antibody_from_title(text)


def _citation_cell(entry: PDBEntry) -> str:
    """테이블의 '논문' 셀 — ACS 스타일 전체 인용문. 없으면 '-'."""
    return _md_escape(format_acs_citation(entry.citation) or "-")


# --------------------------------------------------------------------------
# Tool 정의 — description에 자동 판단 규칙을 내장
# --------------------------------------------------------------------------

_SEARCH_TARGET_DESCRIPTION = """\
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

비GPCR 타겟:
  → PDB ID / Resolution / Released Date / 논문 기본 컬럼
  → sort_by="date" (최신순)

[Excel 출력]
이 도구는 데이터 테이블(마크다운)만 반환합니다. .xlsx 파일이 필요하면
Claude 의 xlsx 스킬로 만드세요 (Custom Instructions 참고). export_excel
기본값은 false 이며, 명시적으로 true 를 전달하면 서버측 저장을 시도하지만
Claude Desktop 의 macOS 샌드박스에서 실패할 수 있어 권장하지 않습니다.

[필터 파라미터 자동 설정]
- "고해상도" 언급 시 → max_resolution=2.5
- "최근 N년" 언급 시 → min_year=(현재연도-N)
- "Antagonist만" → ligand_modality_filter="Antagonist"
- "Cryo-EM만" → method_filter="EM"
- "Active만" → state_filter="Active"

[패밀리 검색 판단]
단일 서브타입이 명확하면 이 도구 1회 호출.
"세로토닌 수용체 전체" 처럼 패밀리 전체, 여러 타깃 통합 Excel, 5-HT2 패밀리
전체 정리가 요청되면 이 도구를 반복 호출하지 말고 search_family 도구를 호출합니다."""

_SEARCH_FAMILY_DESCRIPTION = """\
여러 타겟(수용체 패밀리)의 PDB 구조를 한 번에 검색해 타겟별 데이터를 반환합니다.

[자동 호출 조건]
- "세로토닌 2 패밀리", "5-HT2 전체", "HTR2A/2B/2C 전체 정리" 처럼
  여러 타겟의 구조 목록이 함께 필요한 경우 호출합니다.
- 연구원이 "엑셀", "파일", "시트", "패밀리 전체"를 언급해도 이 도구를 먼저 호출해
  데이터를 받고, 그 다음 Claude 의 xlsx 스킬로 .xlsx 를 만드세요.

[패밀리 자동 확장 규칙]
- "세로토닌 2" / "5-HT2" / "HTR2 패밀리" → ["HTR2A", "HTR2B", "HTR2C"]
- "세로토닌 전체" / "5-HT 전체" → ["HTR1A","HTR1B","HTR2A","HTR2B","HTR2C","HTR4","HTR6","HTR7"]
- "도파민 수용체" / "DRD 전체" → ["DRD1","DRD2","DRD3","DRD4","DRD5"]
- "아드레날린" / "ADRB" → ["ADRB1","ADRB2","ADRB3"]
- "무스카린" / "CHRM" → ["CHRM1","CHRM2","CHRM3","CHRM4","CHRM5"]
- "EGFR 패밀리" / "ErbB" → ["EGFR","ERBB2","ERBB3","ERBB4"]
- "CDK 패밀리" → ["CDK1","CDK2","CDK4","CDK6","CDK7","CDK9"]

[Excel 출력 — 중요]
이 도구는 데이터만 반환합니다. Excel 파일은 Claude 가 자신의 xlsx 스킬로
만듭니다 — Custom Instructions(SYSTEM_PROMPT) 의 "Excel 출력 표준" 섹션을
따르세요(시트 구성·컬럼·State 색상·ACS 인용·하이퍼링크). export_excel
기본값은 false 이며, 명시적으로 true 를 전달하면 서버측 저장을 시도하지만
Claude Desktop 의 macOS 샌드박스에서 실패할 수 있어 권장하지 않습니다."""

_COMPARE_TARGETS_DESCRIPTION = """\
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
- "CDK 패밀리" → ["CDK1","CDK2","CDK4","CDK6","CDK7","CDK9"]"""

_GET_PDB_DETAIL_DESCRIPTION = """\
특정 PDB ID 하나의 상세 정보를 조회합니다.

[자동 호출 조건]
- 연구원이 특정 PDB ID를 언급할 때 (예: "7WC7 자세히 알려줘")
- search_target 결과에서 특정 구조를 더 자세히 보고 싶을 때
- DOI나 논문 정보를 확인하고 싶을 때

[출력]
GPCR 구조: Resolution / Method / Released Date / State / Ligand /
            Ligand modality / Signaling protein / Fusion / Antibody / 논문 전체
비GPCR 구조: Resolution / Method / Released Date / 논문 전체"""


# --------------------------------------------------------------------------
# Phase 5 — 리서치 보조 도구 (할루시네이션 방지용)
# --------------------------------------------------------------------------

_GET_LIGAND_DETAIL_DESCRIPTION = """\
화합물(리간드)의 화학 구조·물성·신약 phase를 PubChem + ChEMBL + IUPHAR에서
통합해서 가져옵니다. Claude가 SMILES/MW/LogP/임상 단계를 기억으로 답하지 않도록
권위 있는 원본 값을 그대로 제공하는 것이 목적입니다.

[자동 호출 조건]
- 연구원이 특정 화합물 이름을 언급할 때 (예: "lisuride 구조 알려줘")
- search_target/get_pdb_detail 결과에서 본 리간드의 상세를 알고 싶을 때
- SMILES / InChI / 분자량 / LogP / synonym / 신약 phase를 물을 때
- "이 약 임상 몇 상까지 갔어?" 류 질문이 나올 때

[입력]
query: 화합물 이름 또는 PDB 화학성분 코드 또는 CHEMBLxxxx 또는 InChIKey
       예: "Risperidone", "LSD", "EZX", "CHEMBL85", "RZUSEABLOMUKTQ-UHFFFAOYSA-N"

[출력]
PubChem CID / ChEMBL ID / IUPHAR ID / SMILES / InChI / MW / LogP /
H-bond donors·acceptors / TPSA / 신약 phase / synonyms + 출처 URL"""

_GET_TARGET_BIOACTIVITIES_DESCRIPTION = """\
특정 타깃에 대한 화합물 활성 데이터(Ki / Kd / IC50 / EC50)를 ChEMBL과 IUPHAR에서
가져옵니다. Claude가 binding affinity 수치를 기억으로 답하지 않게 하는 도구입니다.

[자동 호출 조건]
- "HTR2A 에 대한 risperidone Ki 알려줘"
- "이 타깃에 강한 활성을 보인 화합물 뭐가 있어?"
- "고활성(Ki < 10 nM) 리간드만 추려줘"

[입력]
uniprot_accession: 타깃의 UniProt accession (예: "P28223")
gene_symbol: 선택 — IUPHAR 보강용 유전자 기호 (예: "HTR2A")
min_pchembl: 컷오프 (기본 6.0 ≈ Ki/IC50 1 µM). 0 또는 None이면 컷오프 없음
standard_types: 측정 타입 리스트 (기본 ["Ki","Kd","IC50","EC50"])
max_results: 결과 상한 (기본 30)

[출력]
pChEMBL 내림차순 정렬된 활성 데이터 — 리간드 이름 / 측정 타입 / 값 / 단위 /
assay 종류 / 출처 논문 PMID / 출처 URL"""

_GET_PAPER_ABSTRACT_DESCRIPTION = """\
PMID 또는 DOI 로 논문의 제목·저자·연도·저널·초록·MeSH terms를 Europe PMC
(1차) / PubMed (fallback) 에서 가져옵니다.

[자동 호출 조건]
- 연구원이 특정 논문(PMID 또는 DOI)을 언급할 때
- search_target 결과에서 특정 PDB의 연결 논문 내용이 궁금할 때
- "이 논문 결론이 뭐였어?" 라고 물을 때

Claude는 가져온 초록을 그대로 보여주고, 본문 추측은 하지 않습니다.

[입력 — 둘 중 하나 필수]
pmid: PubMed ID (예: "32555340")
doi:  DOI (예: "10.1038/s41586-020-1968-7")

[출력]
제목 / 저자 / 저널 / 연도 / 권·페이지 / 초록 (있으면 섹션별) / MeSH terms /
오픈액세스 여부 + 출처 URL"""

_SEARCH_PAPERS_DESCRIPTION = """\
자유 텍스트로 Europe PMC 논문을 검색합니다. PMID 모를 때 keyword 검색용.

[자동 호출 조건]
- "HTR2A psychedelic 관련 최근 논문 찾아줘"
- "GPCR allosteric modulator 리뷰 논문"
- 검색만 하고, 상세 초록이 필요하면 get_paper_abstract로 이어집니다.

[입력]
query: 검색 키워드 (Europe PMC 쿼리 문법 지원, 예: 'HTR2A AND review')
max_results: 결과 상한 (기본 5, 최대 25)

[출력]
상위 N개 논문 — 제목 / 저자 / 연도 / 저널 / PMID / 초록 미리보기 + 출처 URL"""

_GET_SEQUENCE_REGION_DESCRIPTION = """\
UniProt 단백질의 지정 구간 서열 + 그 구간과 겹치는 feature(active site /
binding site / domain / transmembrane / disulfide / modification 등)를 한 번에
가져옵니다.

[자동 호출 조건]
- "HTR2A의 222번 잔기가 뭐야?" → start=222, end=222
- "이 타깃의 ATP-binding site 위치 알려줘" → feature_types=["Binding site"]
- "transmembrane helix 위치" → feature_types=["Transmembrane"]
- 특정 mutation의 wild-type residue 확인 (Claude가 추측하지 않게)

[입력]
accession: UniProt accession (예: "P28223")
start, end: 1-based 잔기 범위 (둘 다 생략 가능 — 전체 서열 반환)
feature_types: 필터링할 feature 타입 (예: ["Binding site","Active site"])

[출력]
구간 서열 + 겹치는 feature 목록 (타입 / 설명 / start-end / 리간드 / 근거)"""

_GET_NATURAL_VARIANTS_DESCRIPTION = """\
UniProt natural variant — 알려진 missense 변이/SNP/질환 연관을 가져옵니다.
"L858R 변이 알려져 있어?" "이 위치 mutation hotspot 인가?" 류 답을 추측하지
않도록 합니다.

[자동 호출 조건]
- 연구원이 특정 변이(예: "T790M", "L858R")의 알려짐 여부를 물을 때
- "이 타깃의 disease-causing mutation 정리" 요청
- 특정 잔기 위치의 알려진 변이 확인

[입력]
accession: UniProt accession (예: "P00533")
position: 특정 잔기 번호 (선택 — 그 위치 변이만)
disease_only: 질환 연관 변이만 필터링 (기본 false)
max_results: 결과 상한 (기본 200)

[출력]
변이 목록 — 위치 / wild-type / variant / 설명 / 질환 / 임상적 의의 /
dbSNP / ClinVar ID + 출처 URL"""

_GET_BINDING_SITE_DESCRIPTION = """\
PDB 구조의 결합부위 잔기 목록을 PDBe (1차) / RCSB 에서 가져옵니다.
"이 구조의 binding pocket 핵심 잔기" 같은 질문에 추측 없이 답하기 위함.

[자동 호출 조건]
- "7WC7의 결합부위 잔기 알려줘"
- "이 구조에서 risperidone과 직접 접촉하는 잔기"
- get_pdb_detail 결과에서 더 깊이 들어갈 때

[입력]
pdb_id: 4자리 PDB ID (예: "7WC7")
ligand_filter: 특정 PDB chem code의 결합부위만 (예: "RSP")
skip_solvents: 용매/이온 결합부위 제외 (기본 true)

[출력]
결합부위별 — site ID / 리간드 코드·이름 / 체인 / 잔기 리스트
(chain·번호·이름) + 출처 URL"""

_GET_ALPHAFOLD_MODEL_DESCRIPTION = """\
AlphaFold DB 예측 구조 메타데이터(다운로드 URL + 신뢰도 pLDDT)를 가져옵니다.
실험 구조가 없는 단백질도 "모름"이 아니라 예측 구조와 신뢰도 정보를 줄 수
있게 하기 위한 도구.

[자동 호출 조건]
- "이 단백질 구조 없어?" → 실험 구조 없을 때 AlphaFold로 fallback
- "AlphaFold 모델 어떻게 받아?"
- 예측 구조의 신뢰도(pLDDT)를 알고 싶을 때

[입력]
uniprot_accession: UniProt accession (예: "Q9Y2I7")

[출력]
모델 PDB/CIF URL / 평균 pLDDT / 신뢰도 라벨 (Very high/Confident/Low/Very low) /
PAE 이미지 URL / 단백질 길이 + 출처 URL"""

_GET_TARGET_INTELLIGENCE_DESCRIPTION = """\
OpenTargets Platform에서 타깃의 질환 연관 + known drugs(임상~승인 약물)를
가져옵니다. "이 타깃 어느 질환에 쓰이고 있나?", "임상 들어간 약 있어?" 류
질문에 추측 없이 답하기 위함.

[자동 호출 조건]
- "EGFR의 알려진 질환·약물 정리해줘"
- "이 타깃이 어느 단계 임상에 있어?"
- 타깃 검증/생물학적 합리성 확인

[입력]
target_query: gene symbol 또는 Ensembl ID (예: "EGFR", "ENSG00000146648")
max_diseases: 표시할 질환 수 (기본 15)
max_drugs: 표시할 known drugs 수 (기본 15)

[출력]
연관 질환 목록 (OpenTargets 종합 점수 내림차순) +
known drugs (drug name / type / mechanism / phase / 적응증) + 출처 URL"""


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_target",
            description=_SEARCH_TARGET_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "유전자명 또는 UniProt 검색어. 별칭은 위 사전 참고.",
                    },
                    "max_structures": {
                        "type": "integer",
                        "description": "테이블에 표시할 최대 구조 수. 미입력 시 전체 표시.",
                    },
                    "export_excel": {
                        "type": "boolean",
                        "description": "서버측 Excel 저장 시도 여부(권장 false). 기본값 false — Claude 의 xlsx 스킬로 만드세요.",
                        "default": False,
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["date", "resolution", "state_then_date"],
                        "description": "정렬 기준. GPCR은 state_then_date, 비GPCR은 date 기본.",
                        "default": "date",
                    },
                    "max_resolution": {
                        "type": "number",
                        "description": "이 값 이하의 Resolution만 포함 (Å). 예: 2.5",
                    },
                    "min_year": {
                        "type": "integer",
                        "description": "이 연도 이후 공개된 구조만 포함. 예: 2020",
                    },
                    "ligand_modality_filter": {
                        "type": "string",
                        "description": "특정 modality만 포함. 예: Antagonist, Agonist, Inverse agonist",
                    },
                    "state_filter": {
                        "type": "string",
                        "description": "특정 State만 포함. 예: Active, Inactive, Intermediate",
                    },
                    "method_filter": {
                        "type": "string",
                        "description": "특정 실험방법만 포함. 예: X-ray, EM, NMR",
                    },
                },
                "required": ["target"],
            },
        ),
        types.Tool(
            name="search_family",
            description=_SEARCH_FAMILY_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "family_name": {
                        "type": "string",
                        "description": "패밀리 이름 또는 파일 라벨. 예: HTR2_family, 5-HT2",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "검색할 유전자명 목록. 예: [\"HTR2A\", \"HTR2B\", \"HTR2C\"]",
                    },
                    "export_excel": {
                        "type": "boolean",
                        "description": "서버측 패밀리 Excel 저장 시도 여부(권장 false). 기본값 false — Claude 의 xlsx 스킬로 만드세요.",
                        "default": False,
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["date", "resolution", "state_then_date"],
                        "description": "정렬 기준. GPCR은 state_then_date, 비GPCR은 date 기본.",
                        "default": "date",
                    },
                    "max_resolution": {
                        "type": "number",
                        "description": "이 값 이하의 Resolution만 포함 (Å). 예: 2.5",
                    },
                    "min_year": {
                        "type": "integer",
                        "description": "이 연도 이후 공개된 구조만 포함. 예: 2020",
                    },
                    "ligand_modality_filter": {
                        "type": "string",
                        "description": "특정 modality만 포함. 예: Antagonist, Agonist, Inverse agonist",
                    },
                    "state_filter": {
                        "type": "string",
                        "description": "특정 State만 포함. 예: Active, Inactive, Intermediate",
                    },
                    "method_filter": {
                        "type": "string",
                        "description": "특정 실험방법만 포함. 예: X-ray, EM, NMR",
                    },
                },
                "required": ["targets"],
            },
        ),
        types.Tool(
            name="get_pdb_detail",
            description=_GET_PDB_DETAIL_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "pdb_id": {
                        "type": "string",
                        "description": "PDB ID (4자리). 예: 7WC7, 8JT8, 1IVO",
                    }
                },
                "required": ["pdb_id"],
            },
        ),
        types.Tool(
            name="compare_targets",
            description=_COMPARE_TARGETS_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "비교할 유전자명 목록. 패밀리 키워드는 위 규칙으로 자동 구성.",
                    }
                },
                "required": ["targets"],
            },
        ),
        types.Tool(
            name="get_ligand_detail",
            description=_GET_LIGAND_DETAIL_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "화합물 이름 / PDB 화학성분 코드 / CHEMBL ID / InChIKey",
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_target_bioactivities",
            description=_GET_TARGET_BIOACTIVITIES_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "uniprot_accession": {
                        "type": "string",
                        "description": "타깃 UniProt accession. 예: P28223",
                    },
                    "gene_symbol": {
                        "type": "string",
                        "description": "IUPHAR 보강용 gene symbol. 예: HTR2A",
                    },
                    "min_pchembl": {
                        "type": "number",
                        "description": "pChEMBL 컷오프 (기본 6.0 ≈ Ki 1 µM). 0이면 컷오프 없음.",
                        "default": 6.0,
                    },
                    "standard_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "측정 타입 필터. 예: ['Ki','Kd','IC50','EC50']",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "결과 상한. 기본 30.",
                        "default": 30,
                    },
                    "include_iuphar": {
                        "type": "boolean",
                        "description": "IUPHAR/GtoPdb 활성을 함께 포함할지 여부. 기본 true.",
                        "default": True,
                    },
                },
                "required": ["uniprot_accession"],
            },
        ),
        types.Tool(
            name="get_paper_abstract",
            description=_GET_PAPER_ABSTRACT_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "pmid": {"type": "string", "description": "PubMed ID"},
                    "doi": {"type": "string", "description": "DOI"},
                },
            },
        ),
        types.Tool(
            name="search_papers",
            description=_SEARCH_PAPERS_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Europe PMC 검색 쿼리",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "최대 결과 수 (1~25, 기본 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_sequence_region",
            description=_GET_SEQUENCE_REGION_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "accession": {
                        "type": "string",
                        "description": "UniProt accession. 예: P28223",
                    },
                    "start": {"type": "integer", "description": "1-based 시작 잔기"},
                    "end": {"type": "integer", "description": "1-based 끝 잔기 (포함)"},
                    "feature_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "필터링할 feature 타입. 예: ['Binding site','Active site']",
                    },
                },
                "required": ["accession"],
            },
        ),
        types.Tool(
            name="get_natural_variants",
            description=_GET_NATURAL_VARIANTS_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "accession": {
                        "type": "string",
                        "description": "UniProt accession. 예: P00533",
                    },
                    "position": {
                        "type": "integer",
                        "description": "특정 잔기 위치만 (선택)",
                    },
                    "disease_only": {
                        "type": "boolean",
                        "description": "질환 연관 변이만 (기본 false)",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "결과 상한 (기본 200)",
                        "default": 200,
                    },
                },
                "required": ["accession"],
            },
        ),
        types.Tool(
            name="get_binding_site",
            description=_GET_BINDING_SITE_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "pdb_id": {
                        "type": "string",
                        "description": "4자리 PDB ID. 예: 7WC7",
                    },
                    "ligand_filter": {
                        "type": "string",
                        "description": "특정 PDB 화학성분 코드만 필터 (선택). 예: RSP",
                    },
                    "skip_solvents": {
                        "type": "boolean",
                        "description": "용매/이온 결합부위 제외 (기본 true)",
                        "default": True,
                    },
                },
                "required": ["pdb_id"],
            },
        ),
        types.Tool(
            name="get_alphafold_model",
            description=_GET_ALPHAFOLD_MODEL_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "uniprot_accession": {
                        "type": "string",
                        "description": "UniProt accession. 예: Q9Y2I7",
                    }
                },
                "required": ["uniprot_accession"],
            },
        ),
        types.Tool(
            name="get_target_intelligence",
            description=_GET_TARGET_INTELLIGENCE_DESCRIPTION,
            inputSchema={
                "type": "object",
                "properties": {
                    "target_query": {
                        "type": "string",
                        "description": "gene symbol 또는 Ensembl gene ID",
                    },
                    "max_diseases": {
                        "type": "integer",
                        "description": "표시할 질환 수 (기본 15)",
                        "default": 15,
                    },
                    "max_drugs": {
                        "type": "integer",
                        "description": "표시할 known drugs 수 (기본 15)",
                        "default": 15,
                    },
                },
                "required": ["target_query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "search_target":
        return await handle_search_target(arguments)
    if name == "search_family":
        return await handle_search_family(arguments)
    if name == "get_pdb_detail":
        return await handle_get_pdb_detail(arguments)
    if name == "compare_targets":
        return await handle_compare_targets(arguments)
    if name == "get_ligand_detail":
        return await handle_get_ligand_detail(arguments)
    if name == "get_target_bioactivities":
        return await handle_get_target_bioactivities(arguments)
    if name == "get_paper_abstract":
        return await handle_get_paper_abstract(arguments)
    if name == "search_papers":
        return await handle_search_papers(arguments)
    if name == "get_sequence_region":
        return await handle_get_sequence_region(arguments)
    if name == "get_natural_variants":
        return await handle_get_natural_variants(arguments)
    if name == "get_binding_site":
        return await handle_get_binding_site(arguments)
    if name == "get_alphafold_model":
        return await handle_get_alphafold_model(arguments)
    if name == "get_target_intelligence":
        return await handle_get_target_intelligence(arguments)
    raise ValueError(f"Unknown tool: {name}")


# --------------------------------------------------------------------------
# Tool 1: search_target
# --------------------------------------------------------------------------

async def _collect_target_search(
    arguments: dict,
) -> tuple[SearchResult | None, dict, str | None]:
    """타겟 하나의 검색 워크플로우를 실행한다.

    Excel 저장/렌더링은 호출자가 맡도록 분리하여 search_target과 search_family가
    동일한 데이터 수집 로직을 공유한다.
    """
    target = (arguments.get("target") or "").strip()
    if not target:
        return None, {}, "타겟 이름을 입력해주세요. 예: EGFR, TP53, HTR2A"

    sort_by = arguments.get("sort_by") or "date"
    max_resolution = _coerce_float(arguments.get("max_resolution"))
    min_year = _coerce_int(arguments.get("min_year"))
    ligand_modality_filter = (arguments.get("ligand_modality_filter") or "").strip() or None
    state_filter = (arguments.get("state_filter") or "").strip() or None
    method_filter = (arguments.get("method_filter") or "").strip() or None

    # STEP 1-2: UniProt 검색 + PDB ID 목록
    try:
        uniprot: UniProtResult = await search_uniprot(target)
    except UniProtError as exc:
        return None, {}, str(exc)
    except Exception:
        return None, {}, "외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요."

    # STEP 2b: RCSB Search API로 PDB ID 보강 (Union).
    # UniProt cross-reference는 RCSB 신규 등록 후 며칠~수주 지연이 있다.
    # RCSB Search를 union 처리하면 그 사이 등록된 신규 구조도 결과에 포함된다.
    uniprot_indexed_count = len(uniprot.pdb_ids)
    uniprot_set = {pid.upper() for pid in uniprot.pdb_ids}
    rcsb_search_warning: str | None = None
    try:
        rcsb_ids = await search_pdb_ids_by_uniprot(uniprot.accession)
    except RCSBSearchError as exc:
        rcsb_ids = []
        rcsb_search_warning = (
            f"RCSB Search API 일시 장애 — UniProt cross-reference만 사용했습니다. ({exc})"
        )
    rcsb_set = {pid.upper() for pid in rcsb_ids}
    unindexed_ids = sorted(rcsb_set - uniprot_set)
    if unindexed_ids:
        uniprot.pdb_ids = sorted(uniprot_set | rcsb_set)

    if not uniprot.pdb_ids:
        return None, {}, (
            f"'{uniprot.protein_name}' ({uniprot.accession})의 실험 구조가 "
            f"PDB에 등록되어 있지 않습니다."
        )

    # STEP 3: GPCR 여부 확인 (실패해도 비GPCR로 간주하고 진행)
    is_gpcr, gpcrdb_slug = await check_gpcr(uniprot.entry_name)
    uniprot.is_gpcr = is_gpcr
    uniprot.gpcrdb_slug = gpcrdb_slug

    # STEP 4: GPCRdb 메타데이터 (GPCR인 경우 — 절대 예외를 던지지 않음).
    #         UniProt PDB ID 목록을 전달하면 배치 실패 시 단일 구조 엔드포인트로 폴백한다.
    gpcrdb_map: dict[str, dict] = {}
    gpcrdb_warning: str | None = None
    pubchem_failures = 0
    ligand_resolution_failures = 0
    if is_gpcr and gpcrdb_slug:
        # 이 batch에서 발생한 장애를 집계하기 위해 사전에 누적치 초기화
        consume_pubchem_failures()
        consume_ligand_resolution_failures()
        gpcrdb_map = await get_gpcrdb_structures(gpcrdb_slug, pdb_ids=uniprot.pdb_ids)
        pubchem_failures = consume_pubchem_failures()
        ligand_resolution_failures = consume_ligand_resolution_failures()

        # 휴리스틱이 GPCR로 분류했지만 GPCRdb에 단 한 건도 매칭이 없으면 강등.
        # (a) 휴리스틱 false positive (비-GPCR을 GPCR로 잘못 분류) 또는
        # (b) GPCRdb가 일시 장애로 응답을 못 줬을 때 — 두 경우 모두 GPCR 확장
        # 테이블이 의미를 잃으므로 기본 테이블로 fallback. 사용자에게는 더 안전.
        if not gpcrdb_map:
            is_gpcr = False
            uniprot.is_gpcr = False
            uniprot.gpcrdb_slug = None

    # STEP 5: RCSB PDB 메타데이터 병렬 조회 — 실패 ID도 함께 수집
    try:
        fetched, failed_pdb_ids = await fetch_all_pdb_entries_with_failures(uniprot.pdb_ids)
    except Exception:
        return None, {}, "PDB 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."

    if not fetched:
        return None, {}, (
            f"'{uniprot.protein_name}' ({uniprot.accession})의 PDB 구조 메타데이터를 "
            f"조회하지 못했습니다. 잠시 후 다시 시도해주세요."
        )

    # STEP 6: GPCRdb 데이터 병합 + 제목 파싱 fallback
    if is_gpcr:
        for entry in fetched:
            entry.is_gpcr = True
            gpcr_data = gpcrdb_map.get(entry.pdb_id)
            if gpcr_data:
                entry.pref_chain = gpcr_data["pref_chain"]
                entry.state = gpcr_data["state"]
                entry.ligand = gpcr_data["ligand"]
                entry.ligand_modality = gpcr_data["ligand_modality"]
                entry.signaling_protein = gpcr_data["signaling_protein"]
                # m10: GPCRdb가 stabilizing_agents를 제공한 경우 1차 채움
                # (대부분의 구조에서는 None — polymer/title fallback이 _annotate_*에서 채운다)
                if gpcr_data.get("fusion_protein") is not None:
                    entry.fusion_protein = gpcr_data["fusion_protein"]
                if gpcr_data.get("antibody") is not None:
                    entry.antibody = gpcr_data["antibody"]
            # Fusion/Antibody는 GPCRdb가 제공하지 않는 경우가 더 흔하므로 RCSB polymer
            # 설명 + PDB 제목에서 추출한다 (None 필드만 보강 — _annotate_fusion_antibody가
            # 이미 채워진 값은 덮어쓰지 않는다).
            _annotate_fusion_antibody(entry)

    fetched_count = len(fetched)
    total_registered = len(uniprot.pdb_ids)

    # STEP 7: 후처리 필터 적용
    filtered, filter_notes = _apply_filters(
        fetched,
        max_resolution=max_resolution,
        min_year=min_year,
        method_filter=method_filter,
        ligand_modality_filter=ligand_modality_filter,
        state_filter=state_filter,
    )

    if not filtered:
        condition = ", ".join(filter_notes) if filter_notes else "지정한 조건"
        return None, {}, (
            f"'{uniprot.gene_name or target}' ({uniprot.accession})의 "
            f"{fetched_count}개 구조 중 필터 조건({condition})에 맞는 구조가 없습니다. "
            f"조건을 완화해 다시 시도해보세요."
        )

    # STEP 8: 정렬 (GPCR 기본 정렬은 state_then_date)
    effective_sort = sort_by
    if is_gpcr and sort_by == "date":
        effective_sort = "state_then_date"
    structures = _sort_structures(filtered, effective_sort, gpcr_priority=is_gpcr)

    gpcrdb_count = sum(1 for e in structures if e.pref_chain) if is_gpcr else None

    # GPCRdb 커버리지가 50% 미만이면 사용자/LLM에 명시적 경고를 띄운다.
    # — Option C: LLM이 자체 지식으로 비어 있는 state/ligand/modality 컬럼을 채워
    #   누락 구조를 만들지 않도록 명시적 가드를 제공한다.
    # RCSB Search로 추가된 신규 구조(unindexed_ids)는 GPCRdb 미수록이 자연스러우므로
    # 분모에서 제외해 false-positive 경고를 줄인다.
    warnings: list[str] = []
    if is_gpcr and fetched_count > 0:
        unindexed_in_fetched = sum(1 for e in structures if e.pdb_id in set(unindexed_ids))
        denom = max(fetched_count - unindexed_in_fetched, 1)
        coverage = (gpcrdb_count or 0) / denom
        if coverage < 0.5:
            shortage = denom - (gpcrdb_count or 0)
            warnings.append(
                f"GPCRdb 일시 장애 또는 데이터 미수록으로 {shortage}개 구조의 "
                f"state/ligand/modality 정보를 가져오지 못했습니다. "
                f"AI 모델은 이 컬럼들을 외부 지식으로 보완하지 말고 "
                f"표에 표시된 그대로 유지하십시오 — 누락 시 '-'로 두십시오."
            )

    # PubChem 일시 장애가 있었으면 리간드 이름이 PDB 코드(EZX 등)로 표시될 수 있음을 알림
    if pubchem_failures > 0:
        warnings.append(
            f"PubChem 일시 장애로 {pubchem_failures}건의 리간드가 일반명 대신 "
            f"PDB chem code(예: EZX, 3IQ)로 표시되었습니다. "
            f"AI 모델은 코드의 일반명을 임의로 추측하지 마십시오."
        )

    # 리간드 이름 해석 중 예상치 못한 예외가 있었으면 코드 결함 가능성 — 사용자에게도 알림
    if ligand_resolution_failures > 0:
        warnings.append(
            f"⚠️ 리간드 이름 해석 중 {ligand_resolution_failures}건의 예상치 못한 오류가 "
            f"발생했습니다. 해당 구조의 ligand 컬럼이 비어 있을 수 있습니다 — "
            f"AI 모델은 외부 지식으로 보완하지 마십시오."
        )

    if warnings:
        gpcrdb_warning = "\n> ".join(warnings)

    # 실제 fetched된 구조 중 RCSB Search로만 발견된 것만 추적 (메타데이터 조회 실패한 신규는 제외)
    fetched_pdb_ids = {e.pdb_id for e in structures}
    unindexed_in_result = sorted(pid for pid in unindexed_ids if pid in fetched_pdb_ids)

    result = SearchResult(
        query=target,
        uniprot=uniprot,
        structures=structures,
        total_count=total_registered,
        gpcrdb_count=gpcrdb_count,
        unindexed_pdb_ids=unindexed_in_result,
        uniprot_indexed_count=uniprot_indexed_count,
    )
    metadata = {
        "fetched_count": fetched_count,
        "total_registered": total_registered,
        "effective_sort": effective_sort,
        "filter_notes": filter_notes,
        "gpcrdb_warning": gpcrdb_warning,
        "failed_pdb_ids": failed_pdb_ids,
        "unindexed_pdb_ids": unindexed_in_result,
        "uniprot_indexed_count": uniprot_indexed_count,
        "rcsb_search_warning": rcsb_search_warning,
    }
    return result, metadata, None


async def handle_search_target(arguments: dict) -> list[types.TextContent]:
    max_structures = _coerce_int(arguments.get("max_structures"))
    export_excel = bool(arguments.get("export_excel", False))

    result, metadata, error = await _collect_target_search(arguments)
    if error or result is None:
        return _text(error or "검색 결과를 만들지 못했습니다.")

    # Excel 저장 (실패해도 전체 결과는 반환)
    export_error: str | None = None
    if export_excel:
        try:
            result.exported_file = export_to_excel(result)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            export_error = f"Excel 저장 중 오류가 발생했습니다: {exc}"

    # 표시할 구조 수 제한 (테이블 한정, Excel은 필터 결과 전체 수록)
    display = result.structures
    if max_structures and max_structures > 0:
        display = result.structures[:max_structures]

    if result.uniprot.is_gpcr:
        text = _render_gpcr_result(
            result, display,
            metadata["fetched_count"], metadata["total_registered"],
            metadata["effective_sort"], metadata["filter_notes"],
            export_error, metadata["gpcrdb_warning"],
            metadata.get("failed_pdb_ids") or [],
            metadata.get("unindexed_pdb_ids") or [],
            metadata.get("rcsb_search_warning"),
        )
    else:
        text = _render_basic_result(
            result, display,
            metadata["fetched_count"], metadata["total_registered"],
            metadata["effective_sort"], metadata["filter_notes"],
            export_error,
            metadata.get("failed_pdb_ids") or [],
            metadata.get("unindexed_pdb_ids") or [],
            metadata.get("rcsb_search_warning"),
        )
    return _text(text)


async def handle_search_family(arguments: dict) -> list[types.TextContent]:
    """여러 타겟을 검색하고 패밀리 workbook을 서버에서 직접 저장한다."""
    raw_targets = arguments.get("targets")
    if isinstance(raw_targets, str):
        targets = [item.strip() for item in raw_targets.split(",") if item.strip()]
    elif isinstance(raw_targets, list):
        targets = [str(item).strip() for item in raw_targets if str(item).strip()]
    else:
        targets = []

    if not targets:
        return _text('검색할 타겟 목록을 입력해주세요. 예: ["HTR2A", "HTR2B", "HTR2C"]')

    family_name = (arguments.get("family_name") or "_".join(targets)).strip()
    export_excel = bool(arguments.get("export_excel", False))

    shared_args = {
        "sort_by": arguments.get("sort_by") or "date",
        "max_resolution": arguments.get("max_resolution"),
        "min_year": arguments.get("min_year"),
        "ligand_modality_filter": arguments.get("ligand_modality_filter"),
        "state_filter": arguments.get("state_filter"),
        "method_filter": arguments.get("method_filter"),
    }

    results: list[SearchResult] = []
    rows: list[str] = []
    details: list[str] = []

    for target in targets:
        result, metadata, error = await _collect_target_search(
            {"target": target, **shared_args}
        )
        if error or result is None:
            rows.append(f"| {target} | - | 조회 실패 | - | - | - | - |")
            details.append(f"- **{target}**: {error or '검색 결과를 만들지 못했습니다.'}")
            continue

        results.append(result)
        type_label = "🧬 GPCR" if result.uniprot.is_gpcr else "일반"
        rows.append(
            f"| {result.uniprot.gene_name or target} "
            f"| {result.uniprot.accession} "
            f"| {type_label} "
            f"| {result.total_count} "
            f"| {len(result.structures)} "
            f"| {_best_resolution_cell(result.structures)} "
            f"| {_latest_structure_cell(result.structures)} |"
        )
        if metadata.get("gpcrdb_warning"):
            details.append(f"- **{target}**: {metadata['gpcrdb_warning']}")
        unindexed = metadata.get("unindexed_pdb_ids") or []
        if unindexed:
            sample = ", ".join(unindexed[:3])
            rest = len(unindexed) - 3
            extra = f" 외 {rest}개" if rest > 0 else ""
            details.append(
                f"- **{target}**: 신규 {len(unindexed)}개 구조가 UniProt 미반영 "
                f"(RCSB Search 직접 조회): {sample}{extra}"
            )
        if metadata.get("rcsb_search_warning"):
            details.append(f"- **{target}**: {metadata['rcsb_search_warning']}")

    if not results:
        lines = ["## 패밀리 구조 검색 실패", ""]
        lines.extend(details or ["검색 가능한 타겟이 없습니다."])
        return _text("\n".join(lines))

    exported_file: str | None = None
    export_error: str | None = None
    if export_excel:
        try:
            exported_file = export_family_to_excel(family_name, results)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            export_error = f"패밀리 Excel 저장 중 오류가 발생했습니다: {exc}"

    lines = [f"## {family_name} 패밀리 구조 검색 결과", ""]
    lines.append("| 타겟 | UniProt | 분류 | 총 구조 수 | Excel 수록 | 최고 해상도 | 최신 구조 |")
    lines.append("|------|---------|------|-----------|------------|------------|-----------|")
    lines.extend(rows)
    if details:
        lines.append("")
        lines.extend(details)

    if exported_file:
        lines.append("")
        lines.append(f"> 📁 Excel 파일 저장됨: {_display_path(exported_file)}")
        lines.append("> 이 Excel은 MCP 서버가 직접 생성했습니다. 별도 xlsx/명령 실행으로 다시 만들 필요가 없습니다.")
    if export_error:
        lines.append("")
        lines.append(f"> ⚠️ {export_error}")

    return _text("\n".join(lines))


def _render_header(u: UniProtResult, query: str, total_registered: int,
                   gpcr: bool) -> list[str]:
    """검색 결과 텍스트의 공통 헤더 블록을 만든다."""
    title = f"## {u.gene_name or query} ({u.accession}) — 실험 구조 검색 결과"
    if gpcr:
        title += "  🧬 GPCR"
    lines = [title, ""]
    lines.append(f"**단백질명**: {u.protein_name}  ")
    lines.append(f"**유전자명**: {u.gene_name or '-'}  ")
    lines.append(f"**UniProt**: {u.accession} ({u.entry_name})  ")
    if u.organism:
        lines.append(f"**Organism**: {u.organism}  ")
    lines.append(f"**총 PDB 구조 수**: {total_registered}개")
    return lines


def _render_meta(lines: list[str], sort_by: str, fetched_count: int,
                 filtered_count: int, displayed: int,
                 filter_notes: list[str], gpcrdb_count: int | None) -> None:
    """정렬·필터·건수 메타 정보 줄을 추가한다."""
    parts: list[str] = []
    if gpcrdb_count is not None:
        parts.append(f"GPCRdb 매칭 {gpcrdb_count}개")
    parts.append(f"정렬: {_sort_label(sort_by)}")
    parts.append(f"조회 {fetched_count}개")
    if filtered_count != fetched_count:
        parts.append(f"필터 적용 {filtered_count}개")
    parts.append(f"표시 {displayed}개")
    lines.append("")
    lines.append(" · ".join(parts))
    if filter_notes:
        lines.append(f"필터 조건: {', '.join(filter_notes)}")


def _append_unindexed_note(
    lines: list[str],
    unindexed_pdb_ids: list[str] | None,
    rcsb_search_warning: str | None,
) -> None:
    """RCSB Search로만 발견된 신규 구조(UniProt 미반영)와 Search API 장애 경고를 표 하단에 한 줄로 표시.

    PDB ID가 5개를 넘으면 앞 5개만 보여주고 "외 N개" 형태로 압축한다.
    """
    if unindexed_pdb_ids:
        sample = unindexed_pdb_ids[:5]
        rest = len(unindexed_pdb_ids) - len(sample)
        ids_text = ", ".join(sample)
        if rest > 0:
            ids_text += f" 외 {rest}개"
        lines.append("")
        lines.append(
            f"> ℹ️ 신규 {len(unindexed_pdb_ids)}개 구조가 UniProt cross-reference에 "
            f"아직 반영되지 않았습니다 (RCSB Search 직접 조회): {ids_text}"
        )
    if rcsb_search_warning:
        lines.append("")
        lines.append(f"> ⚠️ {rcsb_search_warning}")


def _render_basic_result(
    result: SearchResult,
    display: list[PDBEntry],
    fetched_count: int,
    total_registered: int,
    sort_by: str,
    filter_notes: list[str],
    export_error: str | None,
    failed_pdb_ids: list[str] | None = None,
    unindexed_pdb_ids: list[str] | None = None,
    rcsb_search_warning: str | None = None,
) -> str:
    """비GPCR 타깃 — 기본 테이블."""
    lines = _render_header(result.uniprot, result.query, total_registered, gpcr=False)

    if fetched_count < total_registered:
        lines.append("")
        lines.append(
            f"> ⚠️ {total_registered}개 중 {fetched_count}개의 메타데이터만 "
            f"조회되었습니다. (일부 항목 조회 실패)"
        )
        if failed_pdb_ids:
            lines.append(f"> 실패한 PDB ID: {', '.join(failed_pdb_ids)}")

    _append_unindexed_note(lines, unindexed_pdb_ids, rcsb_search_warning)

    _render_meta(lines, sort_by, fetched_count, len(result.structures),
                 len(display), filter_notes, None)

    lines.append("")
    lines.append("| PDB ID | Resolution (Å) | Method | Released Date | 논문 |")
    lines.append("|--------|---------------|--------|---------------|------|")
    for entry in display:
        lines.append(
            f"| {entry.pdb_id} "
            f"| {_resolution_str(entry.resolution)} "
            f"| {format_method(entry.method)} "
            f"| {entry.released_date or '-'} "
            f"| {_citation_cell(entry)} |"
        )

    _append_footer(lines, result.exported_file, export_error)
    return "\n".join(lines)


def _render_gpcr_result(
    result: SearchResult,
    display: list[PDBEntry],
    fetched_count: int,
    total_registered: int,
    sort_by: str,
    filter_notes: list[str],
    export_error: str | None,
    gpcrdb_warning: str | None,
    failed_pdb_ids: list[str] | None = None,
    unindexed_pdb_ids: list[str] | None = None,
    rcsb_search_warning: str | None = None,
) -> str:
    """GPCR 타깃 — State/Ligand/Modality 등을 포함한 확장 테이블."""
    lines = _render_header(result.uniprot, result.query, total_registered, gpcr=True)

    if fetched_count < total_registered:
        lines.append("")
        lines.append(
            f"> ⚠️ {total_registered}개 중 {fetched_count}개의 메타데이터만 조회되었습니다."
        )
        if failed_pdb_ids:
            lines.append(f"> 실패한 PDB ID: {', '.join(failed_pdb_ids)}")
    if gpcrdb_warning:
        lines.append("")
        lines.append(f"> ⚠️ {gpcrdb_warning}")

    _append_unindexed_note(lines, unindexed_pdb_ids, rcsb_search_warning)

    _render_meta(lines, sort_by, fetched_count, len(result.structures),
                 len(display), filter_notes, result.gpcrdb_count or 0)

    lines.append("")
    lines.append(
        "| Method | PDB ID | Res.(Å) | Chain | State | Ligand | "
        "Modality | Sign. | Fusion | Antibody | Year | 논문 |"
    )
    lines.append(
        "|--------|--------|---------|-------|-------|--------|"
        "----------|-------|--------|----------|------|------|"
    )
    for e in display:
        year = _year_of(e)
        lines.append(
            f"| {format_method(e.method)} "
            f"| {e.pdb_id} "
            f"| {_resolution_str(e.resolution)} "
            f"| {e.pref_chain or '-'} "
            f"| {e.state or '-'} "
            f"| {_md_escape(e.ligand or '-')} "
            f"| {e.ligand_modality or '-'} "
            f"| {e.signaling_protein or '-'} "
            f"| {_md_escape(e.fusion_protein or '-')} "
            f"| {_md_escape(e.antibody or '-')} "
            f"| {year or '-'} "
            f"| {_citation_cell(e)} |"
        )

    _append_footer(lines, result.exported_file, export_error)
    return "\n".join(lines)


def _append_footer(lines: list[str], exported_file: str | None,
                   export_error: str | None) -> None:
    """Excel 저장 결과/오류 안내 줄을 추가한다."""
    if exported_file:
        lines.append("")
        lines.append(f"> 📁 Excel 파일 저장됨: {_display_path(exported_file)}")
    if export_error:
        lines.append("")
        lines.append(f"> ⚠️ {export_error}")


# --------------------------------------------------------------------------
# Tool 2: get_pdb_detail
# --------------------------------------------------------------------------

async def handle_get_pdb_detail(arguments: dict) -> list[types.TextContent]:
    pdb_id = (arguments.get("pdb_id") or "").strip().upper()
    if not pdb_id:
        return _text("PDB ID를 입력해주세요. 예: 7T9K")

    try:
        entry = await fetch_single_pdb_entry(pdb_id)
    except ValueError as exc:
        return _text(str(exc))
    except Exception:
        return _text("외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요.")

    # GPCRdb 보강 (graceful — GPCR 구조가 아니면 None)
    gpcr_data = await get_gpcrdb_single(pdb_id)
    if gpcr_data:
        entry.is_gpcr = True
        entry.pref_chain = gpcr_data["pref_chain"]
        entry.state = gpcr_data["state"]
        entry.ligand = gpcr_data["ligand"]
        entry.ligand_modality = gpcr_data["ligand_modality"]
        entry.signaling_protein = gpcr_data["signaling_protein"]
        _annotate_fusion_antibody(entry)

    return _text(_render_pdb_detail(entry))


def _render_pdb_detail(entry: PDBEntry) -> str:
    lines: list[str] = []
    header = f"## PDB {entry.pdb_id} — 상세 정보"
    if entry.is_gpcr:
        header += "  🧬 GPCR"
    lines.append(header)
    lines.append("")
    if entry.title:
        lines.append(f"**제목**: {entry.title}  ")
    lines.append(f"**Resolution**: {_resolution_str(entry.resolution)} Å  ")
    method_raw = f" ({entry.method})" if entry.method else ""
    lines.append(f"**실험 방법**: {format_method(entry.method)}{method_raw}  ")
    lines.append(f"**공개일(Released Date)**: {entry.released_date or '-'}  ")
    lines.append(f"**RCSB 페이지**: https://www.rcsb.org/structure/{entry.pdb_id}")

    if entry.is_gpcr:
        lines.append("")
        lines.append("### GPCR 정보 (GPCRdb)")
        lines.append(f"- **State**: {entry.state or '-'}")
        lines.append(f"- **Preferred chain**: {entry.pref_chain or '-'}")
        lines.append(f"- **Ligand**: {entry.ligand or '-'}")
        lines.append(f"- **Ligand modality**: {entry.ligand_modality or '-'}")
        lines.append(f"- **Signaling protein**: {entry.signaling_protein or '-'}")
        lines.append(f"- **Fusion protein**: {entry.fusion_protein or '-'}")
        lines.append(f"- **Antibody**: {entry.antibody or '-'}")

    cit = entry.citation
    lines.append("")
    lines.append("### 연결 논문")
    if not cit:
        lines.append("연결된 논문 정보가 없습니다.")
    else:
        lines.append(f"- **논문 제목**: {cit.title or '-'}")
        lines.append(f"- **저자**: {cit.authors or '-'}")
        lines.append(f"- **저널**: {cit.journal or '-'}")
        lines.append(f"- **연도**: {cit.year or '-'}")
        if cit.doi:
            lines.append(f"- **DOI**: {cit.doi} (https://doi.org/{cit.doi})")
        else:
            lines.append("- **DOI**: -")
        if cit.pmid:
            lines.append(
                f"- **PMID**: {cit.pmid} "
                f"(https://pubmed.ncbi.nlm.nih.gov/{cit.pmid})"
            )
        else:
            lines.append("- **PMID**: -")
        lines.append(f"- **ACS 인용**: {format_acs_citation(cit)}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool 3: compare_targets
# --------------------------------------------------------------------------

async def handle_compare_targets(arguments: dict) -> list[types.TextContent]:
    targets = arguments.get("targets")
    if not targets or not isinstance(targets, list):
        return _text('비교할 타겟 목록을 입력해주세요. 예: ["EGFR", "HER2", "MET"]')

    rows: list[str] = []
    details: list[str] = []

    for raw in targets:
        target = str(raw).strip()
        if not target:
            continue

        try:
            uniprot = await search_uniprot(target)
        except UniProtError as exc:
            rows.append(f"| {target} | - | UniProt 조회 실패 | - | - | - |")
            details.append(f"- **{target}**: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            rows.append(f"| {target} | - | UniProt 일시 장애 | - | - | - |")
            details.append(
                f"- **{target}**: UniProt 일시 장애 — {type(exc).__name__}: {exc}. "
                f"잠시 후 다시 시도해주세요."
            )
            continue

        is_gpcr, _ = await check_gpcr(uniprot.entry_name)
        type_label = "🧬 GPCR" if is_gpcr else "일반"

        if not uniprot.pdb_ids:
            rows.append(
                f"| {target} | {uniprot.accession} | {type_label} | 0 | - | - |"
            )
            details.append(
                f"- **{target}** ({uniprot.accession}): 등록된 PDB 구조 없음"
            )
            continue

        # PDB 메타데이터 조회 — 실패한 ID와 API 오류를 분리해 details에 명시
        fetch_error: str | None = None
        failed_ids: list[str] = []
        try:
            structures, failed_ids = await fetch_all_pdb_entries_with_failures(uniprot.pdb_ids)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            structures = []
            fetch_error = f"{type(exc).__name__}: {exc}"

        total = len(uniprot.pdb_ids)
        if fetch_error:
            # 전체 조회 자체가 실패 — 구조 수만 표시하고 상세에 사유 기록
            rows.append(
                f"| {target} | {uniprot.accession} | {type_label} | {total} "
                f"| (PDB 일시 장애) | (PDB 일시 장애) |"
            )
            details.append(
                f"- **{target}** ({uniprot.accession}): PDB API 일시 장애로 메타데이터를 "
                f"조회하지 못했습니다. (사유: {fetch_error}) 등록된 구조 수만 표시합니다."
            )
        else:
            rows.append(
                f"| {target} | {uniprot.accession} | {type_label} | {total} "
                f"| {_best_resolution_cell(structures)} "
                f"| {_latest_structure_cell(structures)} |"
            )
            if failed_ids:
                # 일부 ID만 실패 — 사용자가 어떤 PDB가 빠졌는지 알 수 있게 명시
                details.append(
                    f"- **{target}** ({uniprot.accession}): {total}개 중 "
                    f"{len(failed_ids)}개 PDB 메타데이터 조회 실패 — "
                    f"{', '.join(failed_ids)}. 표시된 최고 해상도/최신 구조는 "
                    f"성공한 {len(structures)}개 기준입니다."
                )

    if not rows:
        return _text('비교할 타겟 목록을 입력해주세요. 예: ["EGFR", "HER2", "MET"]')

    lines: list[str] = []
    lines.append("## 타겟 구조 비교 결과")
    lines.append("")
    lines.append("| 타겟 | UniProt | 분류 | 총 구조 수 | 최고 해상도 | 최신 구조 |")
    lines.append("|------|---------|------|-----------|------------|-----------|")
    lines.extend(rows)
    if details:
        lines.append("")
        lines.extend(details)
    return _text("\n".join(lines))


def _best_resolution_cell(structures: list[PDBEntry]) -> str:
    """해상도가 가장 좋은(낮은) 구조를 'X.XX Å (PDBID)' 형태로 표시한다."""
    with_res = [e for e in structures if e.resolution is not None]
    if not with_res:
        return "-"
    best = min(with_res, key=lambda e: e.resolution)
    return f"{best.resolution:.2f} Å ({best.pdb_id})"


def _latest_structure_cell(structures: list[PDBEntry]) -> str:
    """가장 최근에 공개된 구조를 'PDBID (YYYY-MM-DD)' 형태로 표시한다."""
    with_date = [e for e in structures if e.released_date]
    if not with_date:
        return "-"
    latest = max(with_date, key=lambda e: e.released_date)
    return f"{latest.pdb_id} ({latest.released_date})"


# --------------------------------------------------------------------------
# Phase 5 — 리서치 보조 도구 핸들러 + 렌더링
# 모든 응답은 권위 있는 외부 소스의 원본 값을 보여주고, 출처 URL을 함께 표기한다.
# 알 수 없는 값은 "-" 로 표시하고, 임의로 채우지 않는다.
# --------------------------------------------------------------------------

def _format_num(value, fmt: str = "{:g}") -> str:
    """숫자를 표시 형식으로 변환. None이면 '-'."""
    if value is None:
        return "-"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return str(value)


def _trim(text: str | None, limit: int = 400) -> str:
    """긴 문자열을 limit 길이로 축약 (말줄임표 추가)."""
    if not text:
        return "-"
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Tool: get_ligand_detail
# --------------------------------------------------------------------------

async def handle_get_ligand_detail(arguments: dict) -> list[types.TextContent]:
    query = (arguments.get("query") or "").strip()
    if not query:
        return _text("리간드 이름/코드/ChEMBL ID/InChIKey 중 하나를 입력해주세요.")

    try:
        detail = await fetch_ligand_detail(query)
    except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
        return _text(f"리간드 상세 조회에 실패했습니다: {exc}")

    return _text(_render_ligand_detail(detail))


def _render_ligand_detail(d: LigandDetail) -> str:
    """LigandDetail → 마크다운 응답."""
    header = f"## 리간드 상세 — {d.common_name or d.query}"
    lines = [header, ""]
    if d.common_name and d.common_name != d.query:
        lines.append(f"입력: `{d.query}` → 정식 이름: **{d.common_name}**")
    lines.append("")
    lines.append("### 식별자")
    lines.append(f"- **PubChem CID**: {d.pubchem_cid or '-'}")
    lines.append(f"- **ChEMBL ID**: {d.chembl_id or '-'}")
    lines.append(f"- **IUPHAR Ligand ID**: {d.iuphar_ligand_id or '-'}")
    lines.append(f"- **InChIKey**: {d.inchi_key or '-'}")

    lines.append("")
    lines.append("### 화학 구조 / 물성")
    lines.append(f"- **SMILES**: `{d.smiles or '-'}`")
    if d.canonical_smiles and d.canonical_smiles != d.smiles:
        lines.append(f"- **Canonical SMILES**: `{d.canonical_smiles}`")
    lines.append(f"- **InChI**: `{_trim(d.inchi, 200)}`")
    lines.append(f"- **IUPAC Name**: {_trim(d.iupac_name, 250)}")
    lines.append(f"- **Molecular Formula**: {d.molecular_formula or '-'}")
    lines.append(f"- **Molecular Weight**: {_format_num(d.molecular_weight, '{:.2f}')} g/mol")
    lines.append(f"- **XLogP**: {_format_num(d.xlogp, '{:.2f}')}")
    lines.append(f"- **H-bond donors / acceptors**: {d.h_bond_donors if d.h_bond_donors is not None else '-'} / {d.h_bond_acceptors if d.h_bond_acceptors is not None else '-'}")
    lines.append(f"- **TPSA**: {_format_num(d.tpsa, '{:.1f}')} Å²")
    lines.append(f"- **Rotatable bonds**: {d.rotatable_bonds if d.rotatable_bonds is not None else '-'}")

    lines.append("")
    lines.append("### 신약 단계 (ChEMBL)")
    phase_label = {
        4: "4 — 승인 (Approved)",
        3: "3 — Phase 3 임상",
        2: "2 — Phase 2 임상",
        1: "1 — Phase 1 임상",
        0: "0 — 전임상",
        -1: "지정 안 됨",
    }.get(d.max_phase, str(d.max_phase) if d.max_phase is not None else "-")
    lines.append(f"- **Max phase**: {phase_label}")
    lines.append(f"- **Drug type**: {d.drug_type or '-'}")
    lines.append(f"- **Indication class**: {d.indication_class or '-'}")

    if d.synonyms:
        lines.append("")
        lines.append("### 동의어 (상위 10개)")
        lines.append(", ".join(d.synonyms[:10]))

    if d.sources:
        lines.append("")
        lines.append("### 출처")
        for label, url in d.sources.items():
            lines.append(f"- {label}: {url}")

    if d.notes:
        lines.append("")
        for note in d.notes:
            lines.append(f"> {note}")

    if not any([d.pubchem_cid, d.chembl_id, d.iuphar_ligand_id]):
        lines.append("")
        lines.append(
            "> ⚠️ PubChem / ChEMBL / IUPHAR 어디서도 일치하는 화합물을 찾지 "
            "못했습니다. 이름 표기를 확인하거나 InChIKey/CHEMBL ID로 다시 시도해보세요."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_target_bioactivities
# --------------------------------------------------------------------------

async def handle_get_target_bioactivities(
    arguments: dict,
) -> list[types.TextContent]:
    accession = (arguments.get("uniprot_accession") or "").strip()
    if not accession:
        return _text("타깃 UniProt accession을 입력해주세요. 예: P28223")

    gene_symbol = (arguments.get("gene_symbol") or "").strip() or None
    min_pchembl = _coerce_float(arguments.get("min_pchembl"))
    if min_pchembl == 0:
        min_pchembl = None
    max_results = _coerce_int(arguments.get("max_results")) or 30
    include_iuphar = bool(arguments.get("include_iuphar", True))
    raw_types = arguments.get("standard_types")
    if isinstance(raw_types, list) and raw_types:
        standard_types = tuple(str(t).strip() for t in raw_types if str(t).strip())
    else:
        standard_types = ("Ki", "Kd", "IC50", "EC50")

    try:
        result = await fetch_target_bioactivities(
            accession,
            gene_symbol=gene_symbol,
            standard_types=standard_types,
            min_pchembl=min_pchembl if min_pchembl is None else float(min_pchembl),
            max_results=max_results,
            include_iuphar=include_iuphar,
        )
    except ValueError as exc:
        return _text(str(exc))
    except Exception:  # noqa: BLE001
        return _text("ChEMBL / IUPHAR 조회 중 오류가 발생했습니다.")

    return _text(_render_bioactivities(result, min_pchembl))


def _render_bioactivities(
    r: TargetBioactivities, min_pchembl: float | None
) -> str:
    label = r.gene_name or r.uniprot_accession or r.target_query
    lines = [f"## 타깃 활성 데이터 — {label} ({r.uniprot_accession})", ""]
    lines.append(f"- **ChEMBL target**: {r.chembl_target_id or '-'}")
    lines.append(f"- **IUPHAR target**: {r.iuphar_target_id or '-'}")
    if min_pchembl is not None:
        lines.append(f"- **pChEMBL 컷오프**: ≥ {min_pchembl}")
    lines.append(f"- **ChEMBL 보고 총 활성**: {r.total_count}건 (필터 적용 전)")
    lines.append(f"- **반환된 활성**: {len(r.bioactivities)}건")

    # API 장애 등 부분 실패가 있었으면 명시적으로 노출
    if r.notes:
        lines.append("")
        for note in r.notes:
            lines.append(f"> {note}")

    if not r.bioactivities:
        lines.append("")
        lines.append(
            "> 조건을 만족하는 활성 데이터가 없습니다. min_pchembl을 낮추거나 "
            "standard_types를 확장해보세요."
        )
    else:
        lines.append("")
        lines.append(
            "| 순위 | 리간드 | Type | 값 | 단위 | pChEMBL | Assay | 출처 | PMID |"
        )
        lines.append(
            "|------|--------|------|----|------|---------|-------|------|------|"
        )
        for i, b in enumerate(r.bioactivities, 1):
            rel = b.standard_relation or ""
            val = (
                f"{rel}{b.standard_value:.4g}"
                if b.standard_value is not None
                else "-"
            )
            lines.append(
                f"| {i} "
                f"| {_md_escape(b.ligand_name or '-')} "
                f"| {b.standard_type or '-'} "
                f"| {val} "
                f"| {b.standard_units or '-'} "
                f"| {_format_num(b.pchembl_value, '{:.2f}')} "
                f"| {_md_escape(_trim(b.assay_description, 60))} "
                f"| {b.source} "
                f"| {b.pubmed_id or '-'} |"
            )

    if r.sources:
        lines.append("")
        lines.append("### 출처")
        for label, url in r.sources.items():
            lines.append(f"- {label}: {url}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_paper_abstract
# --------------------------------------------------------------------------

async def handle_get_paper_abstract(
    arguments: dict,
) -> list[types.TextContent]:
    pmid = (arguments.get("pmid") or "").strip() or None
    doi = (arguments.get("doi") or "").strip() or None
    if not pmid and not doi:
        return _text("PMID 또는 DOI 중 하나를 입력해주세요.")

    try:
        paper = await fetch_paper_abstract(pmid=pmid, doi=doi)
    except LiteratureAPIError as exc:
        return _text(
            f"> ⚠️ Europe PMC / PubMed 모두 일시 장애로 논문을 조회하지 못했습니다.\n"
            f"> 사유: {exc}\n"
            f"> 잠시 후 다시 시도해주세요. (이 메시지는 미수록과 다릅니다 — "
            f"AI 모델은 초록/저자/저널을 임의로 생성하지 마십시오.)"
        )

    if paper is None:
        return _text(
            "Europe PMC / PubMed 어디서도 해당 논문을 찾지 못했습니다. "
            "PMID 또는 DOI 표기를 확인해주세요."
        )
    return _text(_render_paper(paper))


def _render_paper(p: PaperAbstract) -> str:
    title = p.title or "(제목 없음)"
    lines = [f"## {title}", ""]
    if p.authors:
        first_authors = ", ".join(p.authors[:5])
        if len(p.authors) > 5:
            first_authors += " et al."
        lines.append(f"**저자**: {first_authors}")
    if p.journal or p.year:
        venue_parts = [v for v in [p.journal, str(p.year) if p.year else None] if v]
        lines.append(f"**저널/연도**: {' · '.join(venue_parts)}")
    if p.volume or p.issue or p.pages:
        bib = ", ".join([x for x in [p.volume, p.issue, p.pages] if x])
        lines.append(f"**Volume/Issue/Pages**: {bib}")
    ids = []
    if p.pmid:
        ids.append(f"PMID {p.pmid}")
    if p.pmcid:
        ids.append(f"PMCID {p.pmcid}")
    if p.doi:
        ids.append(f"DOI {p.doi}")
    if ids:
        lines.append(f"**식별자**: {' · '.join(ids)}")
    if p.is_open_access is True:
        lines.append("**Open access**: Yes")

    lines.append("")
    lines.append("### 초록")
    lines.append(p.abstract or "초록이 제공되지 않습니다.")

    if p.mesh_terms:
        lines.append("")
        lines.append(
            f"**MeSH terms** ({len(p.mesh_terms)}개 중 상위 10): "
            + ", ".join(p.mesh_terms[:10])
        )
    if p.keywords:
        lines.append(
            f"**Keywords**: " + ", ".join(p.keywords[:10])
        )

    if p.source_url:
        lines.append("")
        lines.append(f"**출처**: {p.source} — {p.source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: search_papers
# --------------------------------------------------------------------------

async def handle_search_papers(arguments: dict) -> list[types.TextContent]:
    query = (arguments.get("query") or "").strip()
    if not query:
        return _text("검색 쿼리를 입력해주세요.")

    max_results = _coerce_int(arguments.get("max_results")) or 5
    try:
        papers = await search_papers(query, max_results=max_results)
    except LiteratureAPIError as exc:
        return _text(
            f"> ⚠️ Europe PMC 일시 장애로 '{query}' 검색을 수행하지 못했습니다.\n"
            f"> 사유: {exc}\n"
            f"> 잠시 후 다시 시도해주세요. — AI 모델은 임의의 논문 결과를 생성하지 마십시오."
        )

    if not papers:
        return _text(
            f"'{query}'에 대한 Europe PMC 검색 결과가 없습니다. 쿼리를 단순화해보세요."
        )

    lines = [f"## 논문 검색 결과 — '{query}'", "", f"상위 {len(papers)}건"]
    for i, p in enumerate(papers, 1):
        lines.append("")
        lines.append(f"### {i}. {p.title or '(제목 없음)'}")
        if p.authors:
            authors = ", ".join(p.authors[:3])
            if len(p.authors) > 3:
                authors += " et al."
            lines.append(f"- **저자**: {authors}")
        venue = " · ".join(
            x for x in [p.journal, str(p.year) if p.year else None] if x
        )
        if venue:
            lines.append(f"- **저널/연도**: {venue}")
        ids = []
        if p.pmid:
            ids.append(f"PMID {p.pmid}")
        if p.doi:
            ids.append(f"DOI {p.doi}")
        if ids:
            lines.append(f"- **식별자**: {' · '.join(ids)}")
        if p.abstract:
            lines.append(f"- **초록 미리보기**: {_trim(p.abstract, 280)}")
        if p.source_url:
            lines.append(f"- **출처**: {p.source_url}")

    return _text("\n".join(lines))


# --------------------------------------------------------------------------
# Tool: get_sequence_region
# --------------------------------------------------------------------------

async def handle_get_sequence_region(
    arguments: dict,
) -> list[types.TextContent]:
    accession = (arguments.get("accession") or "").strip().upper()
    if not accession:
        return _text("UniProt accession을 입력해주세요. 예: P28223")

    start = _coerce_int(arguments.get("start"))
    end = _coerce_int(arguments.get("end"))
    feature_types = arguments.get("feature_types") or None
    if isinstance(feature_types, list):
        feature_types = [str(t).strip() for t in feature_types if str(t).strip()]
    else:
        feature_types = None

    try:
        region = await fetch_sequence_region(
            accession, start=start, end=end, feature_types=feature_types
        )
    except SequenceError as exc:
        return _text(str(exc))
    except Exception:  # noqa: BLE001
        return _text("UniProt 서열 조회 중 오류가 발생했습니다.")

    return _text(_render_sequence_region(region))


def _chunk_sequence(seq: str, start: int, width: int = 60) -> str:
    """FASTA-like 60글자/줄 + 줄 시작에 잔기 번호 표기."""
    lines: list[str] = []
    for i in range(0, len(seq), width):
        chunk = seq[i: i + width]
        lines.append(f"{start + i:>5d}  {chunk}")
    return "\n".join(lines)


def _render_sequence_region(r: SequenceRegion) -> str:
    name = r.protein_name or r.entry_name or r.accession
    header = (
        f"## 단백질 서열 — {name} ({r.accession}) "
        f"[{r.start}-{r.end}, 전체 {r.full_length} aa]"
    )
    lines = [header, ""]
    lines.append("### 서열 (FASTA-like)")
    lines.append("```")
    lines.append(_chunk_sequence(r.sequence, r.start))
    lines.append("```")

    if r.features:
        lines.append("")
        lines.append(f"### Feature ({len(r.features)}건)")
        lines.append("| Type | Range | 설명 | Ligand | 근거 |")
        lines.append("|------|-------|------|--------|------|")
        for f in r.features:
            rng = (
                f"{f.start}-{f.end}"
                if f.start != f.end
                else f"{f.start}"
            )
            lines.append(
                f"| {f.type} "
                f"| {rng} "
                f"| {_md_escape(_trim(f.description, 80))} "
                f"| {_md_escape(f.ligand or '-')} "
                f"| {f.evidence or '-'} |"
            )
    else:
        lines.append("")
        lines.append("이 구간과 겹치는 feature가 없습니다.")

    if r.source_url:
        lines.append("")
        lines.append(f"**출처**: {r.source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_natural_variants
# --------------------------------------------------------------------------

async def handle_get_natural_variants(
    arguments: dict,
) -> list[types.TextContent]:
    accession = (arguments.get("accession") or "").strip().upper()
    if not accession:
        return _text("UniProt accession을 입력해주세요. 예: P00533")

    position = _coerce_int(arguments.get("position"))
    disease_only = bool(arguments.get("disease_only", False))
    max_results = _coerce_int(arguments.get("max_results")) or 200

    try:
        variants = await fetch_natural_variants(
            accession,
            position=position,
            disease_only=disease_only,
            max_results=max_results,
        )
    except SequenceError as exc:
        return _text(str(exc))
    except Exception:  # noqa: BLE001
        return _text("UniProt Variation API 조회 중 오류가 발생했습니다.")

    return _text(_render_variants(variants, position, disease_only))


def _render_variants(
    v: VariantList, position: int | None, disease_only: bool
) -> str:
    header = f"## 자연 변이 — {v.entry_name or v.accession} ({v.accession})"
    lines = [header, ""]
    parts = []
    if position is not None:
        parts.append(f"잔기 {position}")
    if disease_only:
        parts.append("질환 연관만")
    if parts:
        lines.append(f"필터: {', '.join(parts)}")
    lines.append(f"반환된 변이: {len(v.variants)}건 (총 {v.total_count}건)")

    if not v.variants:
        lines.append("")
        lines.append("조건에 맞는 알려진 자연 변이가 없습니다.")
    else:
        lines.append("")
        lines.append(
            "| Pos | WT | Var | 설명 | 질환 | 임상의의 | dbSNP | ClinVar |"
        )
        lines.append(
            "|-----|----|-----|------|------|----------|-------|---------|"
        )
        for nv in v.variants:
            lines.append(
                f"| {nv.position} "
                f"| {nv.wild_type or '-'} "
                f"| {nv.variant or '-'} "
                f"| {_md_escape(_trim(nv.description, 80))} "
                f"| {_md_escape(nv.disease or '-')} "
                f"| {nv.clinical_significance or '-'} "
                f"| {nv.dbsnp_id or '-'} "
                f"| {nv.clinvar_id or '-'} |"
            )

    if v.source_url:
        lines.append("")
        lines.append(f"**출처**: {v.source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_binding_site
# --------------------------------------------------------------------------

async def handle_get_binding_site(arguments: dict) -> list[types.TextContent]:
    pdb_id = (arguments.get("pdb_id") or "").strip().upper()
    if not pdb_id:
        return _text("4자리 PDB ID를 입력해주세요. 예: 7WC7")
    ligand_filter = (arguments.get("ligand_filter") or "").strip() or None
    skip_solvents = bool(arguments.get("skip_solvents", True))

    result = await fetch_binding_sites(
        pdb_id, ligand_filter=ligand_filter, skip_solvents=skip_solvents
    )
    return _text(_render_binding_sites(result))


def _render_binding_sites(r: BindingSiteResult) -> str:
    lines = [f"## 결합부위 — PDB {r.pdb_id}", ""]
    if not r.sites:
        lines.append("결합부위 데이터가 없습니다.")
        for note in r.notes:
            lines.append(f"> {note}")
        return "\n".join(lines)

    lines.append(f"총 {len(r.sites)}개 결합부위 (용매 제외 시 기준)")
    for site in r.sites:
        lines.append("")
        title = f"### Site {site.site_id or '-'}"
        if site.ligand_code:
            ligand_label = (
                f"{site.ligand_code} — {site.ligand_name}"
                if site.ligand_name
                else site.ligand_code
            )
            title += f" · Ligand: {ligand_label}"
        if site.chain_id:
            title += f" · Chain {site.chain_id}"
        lines.append(title)

        if site.residues:
            res_strs = [
                f"{r.residue_name} {r.residue_number} ({r.chain_id})"
                for r in site.residues
            ]
            lines.append("")
            lines.append("**잔기 (" + str(len(res_strs)) + "개)**: " + ", ".join(res_strs))
        else:
            lines.append("잔기 정보가 없습니다.")

    if r.notes:
        lines.append("")
        for note in r.notes:
            lines.append(f"> {note}")
    if r.sites and r.sites[0].source_url:
        lines.append("")
        lines.append(f"**출처**: {r.sites[0].source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_alphafold_model
# --------------------------------------------------------------------------

async def handle_get_alphafold_model(
    arguments: dict,
) -> list[types.TextContent]:
    accession = (arguments.get("uniprot_accession") or "").strip().upper()
    if not accession:
        return _text("UniProt accession을 입력해주세요. 예: Q9Y2I7")

    try:
        model = await fetch_alphafold_model(accession)
    except AlphaFoldUnavailableError as exc:
        return _text(
            f"> ⚠️ AlphaFold API 일시 장애로 {accession}의 예측 구조를 조회하지 못했습니다.\n"
            f"> 사유: {exc}\n"
            f"> 잠시 후 다시 시도해주세요. (이 메시지는 데이터 미수록과 다릅니다 — "
            f"AI 모델은 임의의 pLDDT/URL을 생성하지 마십시오.)"
        )

    if model is None:
        return _text(
            f"AlphaFold DB에 {accession}의 예측 구조가 등록되어 있지 않습니다. "
            "(인간/주요 모델 종 외에는 누락될 수 있습니다)"
        )
    return _text(_render_alphafold(model))


def _render_alphafold(m: AlphaFoldModel) -> str:
    lines = [f"## AlphaFold 예측 구조 — {m.uniprot_accession}", ""]
    lines.append(f"- **Entry ID**: {m.entry_id or '-'}")
    lines.append(f"- **Organism**: {m.organism or '-'}")
    lines.append(
        f"- **단백질 길이**: {m.sequence_length} aa" if m.sequence_length else "- **단백질 길이**: -"
    )
    lines.append(f"- **모델 버전**: {m.model_version or '-'}")
    lines.append("")
    lines.append("### 신뢰도")
    lines.append(f"- **평균 pLDDT**: {_format_num(m.mean_plddt, '{:.2f}')}")
    lines.append(f"- **요약**: {m.confidence_summary or '-'}")
    lines.append("")
    lines.append("### 다운로드 / 시각화")
    lines.append(f"- **PDB**: {m.model_url_pdb or '-'}")
    lines.append(f"- **CIF**: {m.model_url_cif or '-'}")
    lines.append(f"- **PAE 이미지**: {m.pae_image_url or '-'}")
    lines.append(f"- **PAE 데이터**: {m.pae_doc_url or '-'}")
    if m.source_url:
        lines.append("")
        lines.append(f"**출처**: {m.source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool: get_target_intelligence
# --------------------------------------------------------------------------

async def handle_get_target_intelligence(
    arguments: dict,
) -> list[types.TextContent]:
    query = (arguments.get("target_query") or "").strip()
    if not query:
        return _text("gene symbol 또는 Ensembl gene ID를 입력해주세요.")

    max_diseases = _coerce_int(arguments.get("max_diseases")) or 15
    max_drugs = _coerce_int(arguments.get("max_drugs")) or 15

    try:
        intel = await fetch_target_intelligence(
            query, max_diseases=max_diseases, max_drugs=max_drugs
        )
    except OpenTargetsAPIError as exc:
        return _text(
            f"> ⚠️ OpenTargets API 일시 장애로 '{query}'의 타깃 정보를 조회하지 못했습니다.\n"
            f"> 사유: {exc}\n"
            f"> 잠시 후 다시 시도해주세요. (이 메시지는 미수록과 다릅니다 — "
            f"AI 모델은 임의의 질병/약물 정보를 생성하지 마십시오.)"
        )

    if intel is None:
        return _text(
            f"OpenTargets에서 '{query}'에 대한 타깃 정보를 찾지 못했습니다."
        )
    return _text(_render_target_intel(intel))


def _render_target_intel(t: TargetIntelligence) -> str:
    label = t.gene_name or t.target_query
    lines = [f"## 타깃 인텔리전스 — {label} (OpenTargets)", ""]
    lines.append(f"- **Gene symbol**: {t.gene_name or '-'}")
    lines.append(f"- **Ensembl ID**: {t.ensembl_id or '-'}")
    lines.append(f"- **UniProt**: {t.uniprot_accession or '-'}")
    lines.append(f"- **Biotype**: {t.biotype or '-'}")

    if t.diseases:
        lines.append("")
        lines.append(f"### 연관 질환 상위 {len(t.diseases)}개 (종합 점수 내림차순)")
        lines.append("| # | Disease | EFO ID | Score | Therapeutic areas |")
        lines.append("|---|---------|--------|-------|-------------------|")
        for i, d in enumerate(t.diseases, 1):
            ta = ", ".join(d.therapeutic_areas[:3]) if d.therapeutic_areas else "-"
            lines.append(
                f"| {i} "
                f"| {_md_escape(d.disease_name)} "
                f"| {d.disease_id} "
                f"| {_format_num(d.overall_score, '{:.3f}')} "
                f"| {_md_escape(ta)} |"
            )
    else:
        lines.append("")
        lines.append("연관 질환 데이터가 없습니다.")

    if t.known_drugs:
        lines.append("")
        lines.append(f"### Known drugs ({len(t.known_drugs)}건)")
        lines.append(
            "| Drug | Type | Mechanism | Max phase | Indication |"
        )
        lines.append("|------|------|-----------|-----------|------------|")
        for k in t.known_drugs:
            phase = k.max_phase_for_indication
            phase_str = str(phase) if phase is not None else "-"
            lines.append(
                f"| {_md_escape(k.drug_name)} "
                f"| {k.drug_type or '-'} "
                f"| {_md_escape(_trim(k.mechanism_of_action, 50))} "
                f"| {phase_str} "
                f"| {_md_escape(_trim(k.indication, 50))} |"
            )
    else:
        lines.append("")
        lines.append("OpenTargets에 보고된 known drugs가 없습니다.")

    if t.source_url:
        lines.append("")
        lines.append(f"**출처**: {t.source_url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 진입점 — stdio / HTTP transports
# --------------------------------------------------------------------------

logger = logging.getLogger(__name__)


async def health(request: Request) -> JSONResponse:
    """헬스 체크 엔드포인트 (Docker HEALTHCHECK / Apache 프록시 점검용)."""
    return JSONResponse({"status": "ok", "service": "pdb-mcp-server"})


async def download_file(request: Request) -> Response:
    """저장된 Excel 파일을 다운로드한다."""
    filename = request.path_params["filename"]
    requested = Path(filename)
    if requested.name != filename or not filename.endswith(".xlsx"):
        return Response("Invalid file name", status_code=400)

    output_dir = Path(os.environ.get("PDB_MCP_OUTPUT_DIR") or "output")
    filepath = output_dir / filename
    if not filepath.is_file():
        return Response("File not found", status_code=404)

    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


def _csv_env(name: str) -> list[str]:
    """쉼표로 구분한 환경변수를 리스트로 읽는다."""
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _transport_security_settings() -> TransportSecuritySettings | None:
    """Host/Origin allowlist가 주어졌을 때 MCP transport 보안을 켠다."""
    allowed_hosts = _csv_env("PDB_MCP_ALLOWED_HOSTS")
    allowed_origins = _csv_env("PDB_MCP_ALLOWED_ORIGINS")
    if not allowed_hosts and not allowed_origins:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _normalize_prefix(prefix: str) -> str:
    """프록시 공개 prefix를 '/mcp' 형태로 정규화한다."""
    prefix = (prefix or "").strip()
    if not prefix or prefix == "/":
        return ""
    return "/" + prefix.strip("/")


def _prefixed_path(prefix: str, path: str) -> str:
    """SSE가 클라이언트에게 안내할 공개 경로를 만든다."""
    suffix = "/" + path.strip("/")
    if path.endswith("/"):
        suffix += "/"
    return _normalize_prefix(prefix) + suffix


class StreamableHTTPASGIApp:
    """Streamable HTTP transport를 Starlette Route에 붙이기 위한 ASGI 어댑터."""

    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)


def build_streamable_http_app(
    mcp_server: Server,
    *,
    http_path: str = "/mcp",
    security_settings: TransportSecuritySettings | None = None,
) -> Starlette:
    """MCP 서버를 Streamable HTTP transport로 노출하는 Starlette 앱을 만든다."""
    http_path = "/" + http_path.strip("/")
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        security_settings=security_settings,
        session_idle_timeout=1800,
    )
    streamable_http_app = StreamableHTTPASGIApp(session_manager)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/files/{filename:path}", download_file),
            Route(http_path, endpoint=streamable_http_app),
        ],
        lifespan=lifespan,
    )


def build_sse_app(
    mcp_server: Server,
    *,
    public_prefix: str = "",
    security_settings: TransportSecuritySettings | None = None,
) -> Starlette:
    """MCP 서버를 SSE(HTTP) transport로 노출하는 Starlette 앱을 만든다."""
    message_endpoint = _prefixed_path(public_prefix, "/messages/")
    sse = SseServerTransport(message_endpoint, security_settings=security_settings)

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1],
                mcp_server.create_initialization_options(),
            )
        # connect_sse 가 request._send 로 HTTP 응답을 이미 모두 전송하므로,
        # Starlette Route 가 추가 응답을 보내지 않도록 빈 Response 를 반환한다.
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/files/{filename:path}", download_file),
            Route("/sse", handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )


def main() -> None:
    """CLI 진입점 — --transport 로 stdio / HTTP transport 를 선택한다."""
    parser = argparse.ArgumentParser(description="PDB Research MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default="stdio",
        help="전송 방식: stdio, sse(legacy), streamable-http/http",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "0.0.0.0"),
        help="HTTP transport 바인드 주소",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8000")),
        help="HTTP transport 포트",
    )
    parser.add_argument(
        "--http-path",
        default=os.getenv("PDB_MCP_HTTP_PATH", "/mcp"),
        help="Streamable HTTP MCP 경로",
    )
    parser.add_argument(
        "--public-prefix",
        default=os.getenv("PDB_MCP_PUBLIC_PREFIX", ""),
        help="Apache 등 reverse proxy에서 보이는 공개 prefix. 예: /mcp",
    )
    args = parser.parse_args()

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    security_settings = _transport_security_settings()

    if args.transport == "sse":
        logger.info("SSE 모드로 시작: http://%s:%d/sse", args.host, args.port)
        starlette_app = build_sse_app(
            server,
            public_prefix=args.public_prefix,
            security_settings=security_settings,
        )
        uvicorn.run(
            starlette_app,
            host=args.host,
            port=args.port,
            log_level=log_level.lower(),
        )
    elif args.transport in {"streamable-http", "http"}:
        logger.info(
            "Streamable HTTP 모드로 시작: http://%s:%d%s",
            args.host,
            args.port,
            args.http_path,
        )
        starlette_app = build_streamable_http_app(
            server,
            http_path=args.http_path,
            security_settings=security_settings,
        )
        uvicorn.run(
            starlette_app,
            host=args.host,
            port=args.port,
            log_level=log_level.lower(),
        )
    else:
        logger.info("stdio 모드로 시작")
        from mcp.server.stdio import stdio_server

        async def run_stdio():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream, write_stream,
                    server.create_initialization_options(),
                )

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
