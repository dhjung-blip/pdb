"""13개 서브커맨드 dispatch — `tools/*`와 `server.py` 헬퍼를 얇게 감싼다.

원칙:
- `server.py`의 검증된 로직(특히 `_collect_target_search`, `handle_*`)을 import해
  재사용한다. server.py는 변경하지 않는다.
- 비즈니스 데이터(Pydantic 모델)를 그대로 반환하고, 출력 포맷(JSON/Markdown)은
  formatter.py가 책임진다.
- 사람 친화 에러 문자열은 `DispatchResult.error`로 노출하고, exit code 매핑은
  cli.py가 정한다.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

import server  # noqa: F401 — 모듈 부수효과 최소, 헬퍼 재사용을 위해 import
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
    VariantList,
)
from tools.alphafold import AlphaFoldUnavailableError, fetch_alphafold_model
from tools.binding_site import fetch_binding_sites
from tools.bioactivity import fetch_target_bioactivities
from tools.gpcrdb import check_gpcr, get_gpcrdb_single
from tools.ligand import fetch_ligand_detail
from tools.literature import LiteratureAPIError, fetch_paper_abstract, search_papers
from tools.opentargets import OpenTargetsAPIError, fetch_target_intelligence
from tools.pdb import (
    fetch_all_pdb_entries_with_failures,
    fetch_single_pdb_entry,
)
from tools.sequence import (
    SequenceError,
    fetch_natural_variants,
    fetch_sequence_region,
)
from tools.rcsb_search import RCSBSearchError, search_pdb_ids_by_uniprot
from tools.uniprot import UniProtError, search_uniprot


# --------------------------------------------------------------------------
# 공통 반환 타입
# --------------------------------------------------------------------------

@dataclass
class DispatchResult:
    """모든 서브커맨드의 공통 반환 형태."""

    tool: str
    payload: Any = None  # Pydantic 모델 / list / dict
    metadata: dict = field(default_factory=dict)
    error: str | None = None
    exit_code: int = 0


# --------------------------------------------------------------------------
# 인자 정규화 헬퍼
# --------------------------------------------------------------------------

def _normalize_search_args(args: argparse.Namespace) -> dict:
    """search/family 공통 — argparse Namespace → server.handler가 받는 dict."""
    return {
        "target": getattr(args, "target", None),
        "sort_by": getattr(args, "sort_by", None) or "date",
        "max_resolution": getattr(args, "max_resolution", None),
        "min_year": getattr(args, "min_year", None),
        "ligand_modality_filter": getattr(args, "ligand_modality", None),
        "state_filter": getattr(args, "state", None),
        "method_filter": getattr(args, "method", None),
    }


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


# --------------------------------------------------------------------------
# 1) search
# --------------------------------------------------------------------------

async def run_search(args: argparse.Namespace) -> DispatchResult:
    arguments = _normalize_search_args(args)
    result, metadata, error = await server._collect_target_search(arguments)
    if error or result is None:
        return DispatchResult(
            tool="search_target",
            error=error or "검색 결과를 만들지 못했습니다.",
            exit_code=1,
        )
    return DispatchResult(
        tool="search_target",
        payload=result,
        metadata=metadata,
        exit_code=0,
    )


# --------------------------------------------------------------------------
# 2) family
# --------------------------------------------------------------------------

async def run_family(args: argparse.Namespace) -> DispatchResult:
    targets = _split_csv(getattr(args, "targets", None))
    if not targets:
        return DispatchResult(
            tool="search_family",
            error='검색할 타겟 목록을 입력해주세요. 예: --targets HTR2A,HTR2B,HTR2C',
            exit_code=2,
        )
    family_name = (getattr(args, "family_name", None) or getattr(args, "label", None)
                   or "_".join(targets))

    shared = {
        "sort_by": getattr(args, "sort_by", None) or "date",
        "max_resolution": getattr(args, "max_resolution", None),
        "min_year": getattr(args, "min_year", None),
        "ligand_modality_filter": getattr(args, "ligand_modality", None),
        "state_filter": getattr(args, "state", None),
        "method_filter": getattr(args, "method", None),
    }

    per_target: list[dict] = []
    successes: list[SearchResult] = []
    errors: list[dict] = []

    for target in targets:
        result, metadata, error = await server._collect_target_search(
            {"target": target, **shared}
        )
        if error or result is None:
            errors.append({"target": target, "error": error or "검색 실패"})
            per_target.append({"target": target, "result": None, "error": error})
            continue
        successes.append(result)
        per_target.append({"target": target, "result": result, "metadata": metadata})

    exit_code = 0 if not errors else (3 if successes else 1)
    return DispatchResult(
        tool="search_family",
        payload={
            "family_name": family_name,
            "targets": targets,
            "per_target": per_target,
            "successes": successes,
        },
        metadata={"errors": errors},
        error=None if successes else "패밀리 검색에서 모든 타겟이 실패했습니다.",
        exit_code=exit_code,
    )


# --------------------------------------------------------------------------
# 3) detail
# --------------------------------------------------------------------------

async def run_detail(args: argparse.Namespace) -> DispatchResult:
    pdb_id = (getattr(args, "pdb_id", "") or "").strip().upper()
    if not pdb_id:
        return DispatchResult(
            tool="get_pdb_detail",
            error="PDB ID를 입력해주세요. 예: 7T9K",
            exit_code=2,
        )

    try:
        entry: PDBEntry = await fetch_single_pdb_entry(pdb_id)
    except ValueError as exc:
        return DispatchResult(tool="get_pdb_detail", error=str(exc), exit_code=2)
    except Exception:
        return DispatchResult(
            tool="get_pdb_detail",
            error="외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요.",
            exit_code=1,
        )

    # GPCRdb 보강 (graceful — GPCR이 아니면 None)
    try:
        gpcr_data = await get_gpcrdb_single(pdb_id)
    except Exception:
        gpcr_data = None
    if gpcr_data:
        entry.is_gpcr = True
        entry.pref_chain = gpcr_data["pref_chain"]
        entry.state = gpcr_data["state"]
        entry.ligand = gpcr_data["ligand"]
        entry.ligand_modality = gpcr_data["ligand_modality"]
        entry.signaling_protein = gpcr_data["signaling_protein"]
        server._annotate_fusion_antibody(entry)

    return DispatchResult(tool="get_pdb_detail", payload=entry)


# --------------------------------------------------------------------------
# 4) compare
# --------------------------------------------------------------------------

async def run_compare(args: argparse.Namespace) -> DispatchResult:
    targets = _split_csv(getattr(args, "targets", None))
    if not targets:
        return DispatchResult(
            tool="compare_targets",
            error='비교할 타겟 목록을 입력해주세요. 예: --targets EGFR,HER2,MET',
            exit_code=2,
        )

    rows: list[dict] = []
    notes: list[str] = []

    for target in targets:
        try:
            uniprot = await search_uniprot(target)
        except UniProtError as exc:
            rows.append({"target": target, "status": "uniprot_not_found", "error": str(exc)})
            continue
        except Exception as exc:
            rows.append({
                "target": target, "status": "uniprot_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        is_gpcr, _ = await check_gpcr(uniprot.entry_name)

        # RCSB Search union — UniProt 동기화 지연 구간의 신규 구조도 비교에 반영
        uniprot_indexed_count = len(uniprot.pdb_ids)
        uniprot_set = {pid.upper() for pid in uniprot.pdb_ids}
        try:
            rcsb_ids = await search_pdb_ids_by_uniprot(uniprot.accession)
        except RCSBSearchError:
            rcsb_ids = []
        rcsb_set = {pid.upper() for pid in rcsb_ids}
        unindexed = sorted(rcsb_set - uniprot_set)
        if unindexed:
            uniprot.pdb_ids = sorted(uniprot_set | rcsb_set)

        if not uniprot.pdb_ids:
            rows.append({
                "target": target,
                "accession": uniprot.accession,
                "gene": uniprot.gene_name,
                "is_gpcr": is_gpcr,
                "total": 0,
                "uniprot_indexed_count": uniprot_indexed_count,
                "unindexed_pdb_ids": unindexed,
                "best_resolution": None,
                "latest": None,
                "status": "no_structures",
            })
            continue

        try:
            structures, failed = await fetch_all_pdb_entries_with_failures(uniprot.pdb_ids)
        except Exception as exc:
            rows.append({
                "target": target,
                "accession": uniprot.accession,
                "gene": uniprot.gene_name,
                "is_gpcr": is_gpcr,
                "total": len(uniprot.pdb_ids),
                "uniprot_indexed_count": uniprot_indexed_count,
                "unindexed_pdb_ids": unindexed,
                "best_resolution": None,
                "latest": None,
                "status": "pdb_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        # 최고 해상도 / 최신 구조 — server.py 헬퍼 셀 형태 그대로
        best = server._best_resolution_cell(structures)
        latest = server._latest_structure_cell(structures)
        rows.append({
            "target": target,
            "accession": uniprot.accession,
            "gene": uniprot.gene_name,
            "is_gpcr": is_gpcr,
            "total": len(uniprot.pdb_ids),
            "uniprot_indexed_count": uniprot_indexed_count,
            "unindexed_pdb_ids": unindexed,
            "fetched": len(structures),
            "failed_pdb_ids": failed,
            "best_resolution": best,
            "latest": latest,
            "status": "ok",
        })

    if not any(r.get("status") == "ok" or r.get("total", 0) > 0 for r in rows):
        return DispatchResult(
            tool="compare_targets", error="비교할 수 있는 타겟이 없습니다.",
            payload={"rows": rows, "notes": notes}, exit_code=1,
        )

    return DispatchResult(
        tool="compare_targets",
        payload={"rows": rows, "notes": notes},
    )


# --------------------------------------------------------------------------
# 5) ligand
# --------------------------------------------------------------------------

async def run_ligand(args: argparse.Namespace) -> DispatchResult:
    query = (getattr(args, "query", "") or "").strip()
    if not query:
        return DispatchResult(
            tool="get_ligand_detail",
            error="리간드 이름/코드/ChEMBL ID/InChIKey 중 하나를 입력해주세요.",
            exit_code=2,
        )
    try:
        detail: LigandDetail = await fetch_ligand_detail(query)
    except Exception as exc:
        return DispatchResult(
            tool="get_ligand_detail",
            error=f"리간드 상세 조회에 실패했습니다: {exc}",
            exit_code=1,
        )
    return DispatchResult(tool="get_ligand_detail", payload=detail)


# --------------------------------------------------------------------------
# 6) bioactivity
# --------------------------------------------------------------------------

async def run_bioactivity(args: argparse.Namespace) -> DispatchResult:
    accession = (getattr(args, "accession", "") or "").strip()
    if not accession:
        return DispatchResult(
            tool="get_target_bioactivities",
            error="타깃 UniProt accession을 입력해주세요. 예: P28223",
            exit_code=2,
        )
    gene_symbol = (getattr(args, "gene", None) or "").strip() or None
    min_pchembl = getattr(args, "min_pchembl", None)
    max_results = getattr(args, "max", None) or 30
    include_iuphar = not bool(getattr(args, "no_iuphar", False))
    types = _split_csv(getattr(args, "types", None))
    standard_types = tuple(types) if types else ("Ki", "Kd", "IC50", "EC50")

    try:
        result: TargetBioactivities = await fetch_target_bioactivities(
            accession,
            gene_symbol=gene_symbol,
            standard_types=standard_types,
            min_pchembl=float(min_pchembl) if min_pchembl is not None else None,
            max_results=max_results,
            include_iuphar=include_iuphar,
        )
    except ValueError as exc:
        return DispatchResult(
            tool="get_target_bioactivities", error=str(exc), exit_code=2,
        )
    except Exception:
        return DispatchResult(
            tool="get_target_bioactivities",
            error="ChEMBL / IUPHAR 조회 중 오류가 발생했습니다.",
            exit_code=1,
        )

    return DispatchResult(
        tool="get_target_bioactivities", payload=result,
        metadata={"min_pchembl": min_pchembl},
    )


# --------------------------------------------------------------------------
# 7) paper
# --------------------------------------------------------------------------

async def run_paper(args: argparse.Namespace) -> DispatchResult:
    pmid = (getattr(args, "pmid", None) or "").strip() or None
    doi = (getattr(args, "doi", None) or "").strip() or None
    if not pmid and not doi:
        return DispatchResult(
            tool="get_paper_abstract",
            error="PMID 또는 DOI 중 하나를 입력해주세요. 예: --pmid 12345678",
            exit_code=2,
        )
    try:
        paper = await fetch_paper_abstract(pmid=pmid, doi=doi)
    except LiteratureAPIError as exc:
        return DispatchResult(
            tool="get_paper_abstract",
            error=f"Europe PMC / PubMed 일시 장애로 논문을 조회하지 못했습니다: {exc}",
            exit_code=1,
        )
    if paper is None:
        return DispatchResult(
            tool="get_paper_abstract",
            error="해당 논문을 찾지 못했습니다. PMID 또는 DOI 표기를 확인해주세요.",
            exit_code=0,
        )
    return DispatchResult(tool="get_paper_abstract", payload=paper)


# --------------------------------------------------------------------------
# 8) papers (search)
# --------------------------------------------------------------------------

async def run_papers(args: argparse.Namespace) -> DispatchResult:
    query = (getattr(args, "query", "") or "").strip()
    if not query:
        return DispatchResult(
            tool="search_papers", error="검색 쿼리를 입력해주세요.", exit_code=2,
        )
    max_results = getattr(args, "max", None) or 5
    try:
        papers: list[PaperAbstract] = await search_papers(query, max_results=max_results)
    except LiteratureAPIError as exc:
        return DispatchResult(
            tool="search_papers",
            error=f"Europe PMC 일시 장애로 검색을 수행하지 못했습니다: {exc}",
            exit_code=1,
        )
    return DispatchResult(
        tool="search_papers",
        payload={"query": query, "papers": papers},
    )


# --------------------------------------------------------------------------
# 9) sequence
# --------------------------------------------------------------------------

async def run_sequence(args: argparse.Namespace) -> DispatchResult:
    accession = (getattr(args, "accession", "") or "").strip().upper()
    if not accession:
        return DispatchResult(
            tool="get_sequence_region",
            error="UniProt accession을 입력해주세요. 예: P28223",
            exit_code=2,
        )
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    feature_types = _split_csv(getattr(args, "feature_types", None)) or None

    try:
        region: SequenceRegion = await fetch_sequence_region(
            accession, start=start, end=end, feature_types=feature_types,
        )
    except SequenceError as exc:
        return DispatchResult(
            tool="get_sequence_region", error=str(exc), exit_code=2,
        )
    except Exception:
        return DispatchResult(
            tool="get_sequence_region",
            error="UniProt 서열 조회 중 오류가 발생했습니다.",
            exit_code=1,
        )
    return DispatchResult(tool="get_sequence_region", payload=region)


# --------------------------------------------------------------------------
# 10) variants
# --------------------------------------------------------------------------

async def run_variants(args: argparse.Namespace) -> DispatchResult:
    accession = (getattr(args, "accession", "") or "").strip().upper()
    if not accession:
        return DispatchResult(
            tool="get_natural_variants",
            error="UniProt accession을 입력해주세요. 예: P00533",
            exit_code=2,
        )
    position = getattr(args, "position", None)
    disease_only = bool(getattr(args, "disease_only", False))
    max_results = getattr(args, "max", None) or 200

    try:
        variants: VariantList = await fetch_natural_variants(
            accession,
            position=position,
            disease_only=disease_only,
            max_results=max_results,
        )
    except SequenceError as exc:
        return DispatchResult(
            tool="get_natural_variants", error=str(exc), exit_code=2,
        )
    except Exception:
        return DispatchResult(
            tool="get_natural_variants",
            error="UniProt Variation API 조회 중 오류가 발생했습니다.",
            exit_code=1,
        )
    return DispatchResult(
        tool="get_natural_variants", payload=variants,
        metadata={"position": position, "disease_only": disease_only},
    )


# --------------------------------------------------------------------------
# 11) binding
# --------------------------------------------------------------------------

async def run_binding(args: argparse.Namespace) -> DispatchResult:
    pdb_id = (getattr(args, "pdb_id", "") or "").strip().upper()
    if not pdb_id:
        return DispatchResult(
            tool="get_binding_site",
            error="4자리 PDB ID를 입력해주세요. 예: 7WC7",
            exit_code=2,
        )
    ligand_filter = (getattr(args, "ligand_filter", None) or "").strip() or None
    # --include-solvents가 True면 skip_solvents=False
    skip_solvents = not bool(getattr(args, "include_solvents", False))

    try:
        result: BindingSiteResult = await fetch_binding_sites(
            pdb_id, ligand_filter=ligand_filter, skip_solvents=skip_solvents,
        )
    except Exception as exc:
        return DispatchResult(
            tool="get_binding_site",
            error=f"결합부위 조회 중 오류가 발생했습니다: {exc}",
            exit_code=1,
        )
    return DispatchResult(tool="get_binding_site", payload=result)


# --------------------------------------------------------------------------
# 12) alphafold
# --------------------------------------------------------------------------

async def run_alphafold(args: argparse.Namespace) -> DispatchResult:
    accession = (getattr(args, "accession", "") or "").strip().upper()
    if not accession:
        return DispatchResult(
            tool="get_alphafold_model",
            error="UniProt accession을 입력해주세요. 예: Q9Y2I7",
            exit_code=2,
        )
    try:
        model: AlphaFoldModel | None = await fetch_alphafold_model(accession)
    except AlphaFoldUnavailableError as exc:
        return DispatchResult(
            tool="get_alphafold_model",
            error=f"AlphaFold API 일시 장애: {exc}",
            exit_code=1,
        )
    if model is None:
        return DispatchResult(
            tool="get_alphafold_model",
            error=f"AlphaFold DB에 {accession}의 예측 구조가 등록되어 있지 않습니다.",
            exit_code=0,
        )
    return DispatchResult(tool="get_alphafold_model", payload=model)


# --------------------------------------------------------------------------
# 13) intel
# --------------------------------------------------------------------------

async def run_intel(args: argparse.Namespace) -> DispatchResult:
    query = (getattr(args, "target", "") or "").strip()
    if not query:
        return DispatchResult(
            tool="get_target_intelligence",
            error="gene symbol 또는 Ensembl gene ID를 입력해주세요.",
            exit_code=2,
        )
    max_diseases = getattr(args, "max_diseases", None) or 15
    max_drugs = getattr(args, "max_drugs", None) or 15

    try:
        intel: TargetIntelligence | None = await fetch_target_intelligence(
            query, max_diseases=max_diseases, max_drugs=max_drugs,
        )
    except OpenTargetsAPIError as exc:
        return DispatchResult(
            tool="get_target_intelligence",
            error=f"OpenTargets API 일시 장애: {exc}",
            exit_code=1,
        )
    if intel is None:
        return DispatchResult(
            tool="get_target_intelligence",
            error=f"OpenTargets에서 '{query}'에 대한 타깃 정보를 찾지 못했습니다.",
            exit_code=0,
        )
    return DispatchResult(tool="get_target_intelligence", payload=intel)


# --------------------------------------------------------------------------
# Top-level dispatch
# --------------------------------------------------------------------------

_DISPATCH = {
    "search": run_search,
    "family": run_family,
    "detail": run_detail,
    "compare": run_compare,
    "ligand": run_ligand,
    "bioactivity": run_bioactivity,
    "paper": run_paper,
    "papers": run_papers,
    "sequence": run_sequence,
    "variants": run_variants,
    "binding": run_binding,
    "alphafold": run_alphafold,
    "intel": run_intel,
}


async def dispatch(args: argparse.Namespace) -> DispatchResult:
    fn = _DISPATCH.get(args.cmd)
    if fn is None:
        return DispatchResult(
            tool=args.cmd or "?",
            error=f"알 수 없는 서브커맨드: {args.cmd}",
            exit_code=2,
        )
    return await fn(args)


__all__ = ["DispatchResult", "dispatch"]
