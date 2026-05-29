"""DispatchResult → stdout 문자열 변환 (JSON / Markdown)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

import server  # 마크다운 렌더러 재사용
from adapters.runner import DispatchResult
from models.schemas import (
    PDBEntry,
    SearchResult,
)


# --------------------------------------------------------------------------
# JSON 변환
# --------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=False)
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return str(obj)


def _summarize_search(result: SearchResult) -> dict:
    with_res = [e for e in result.structures if e.resolution is not None]
    best = min(with_res, key=lambda e: e.resolution) if with_res else None
    with_date = [e for e in result.structures if e.released_date]
    latest = max(with_date, key=lambda e: e.released_date) if with_date else None
    return {
        "fetched_count": len(result.structures),
        "registered_count": result.total_count,
        "is_gpcr": result.uniprot.is_gpcr,
        "gpcrdb_count": result.gpcrdb_count,
        # RCSB Search union — UniProt 미반영 신규 구조 정보
        "uniprot_indexed_count": result.uniprot_indexed_count,
        "unindexed_pdb_ids": list(result.unindexed_pdb_ids),
        "unindexed_count": len(result.unindexed_pdb_ids),
        "best_resolution": (
            {"pdb_id": best.pdb_id, "resolution": best.resolution} if best else None
        ),
        "latest_structure": (
            {"pdb_id": latest.pdb_id, "released_date": latest.released_date}
            if latest else None
        ),
    }


def _render_json(result: DispatchResult) -> str:
    success = result.error is None or result.payload is not None
    body: dict[str, Any] = {
        "tool": result.tool,
        "success": success,
        "exit_code": result.exit_code,
    }
    if result.error:
        body["error"] = result.error
    if result.metadata:
        body["metadata"] = _to_jsonable(result.metadata)

    payload = result.payload
    if payload is None:
        return json.dumps(body, ensure_ascii=False, indent=2)

    # 도구별 JSON 구조화
    tool = result.tool
    if tool == "search_target" and isinstance(payload, SearchResult):
        body["data"] = _to_jsonable(payload)
        body["summary"] = _summarize_search(payload)
    elif tool == "search_family" and isinstance(payload, dict):
        per_target_serialized = []
        for item in payload.get("per_target", []):
            entry = {"target": item["target"]}
            res: SearchResult | None = item.get("result")
            if res is not None:
                entry["result"] = _to_jsonable(res)
                entry["summary"] = _summarize_search(res)
            if item.get("error"):
                entry["error"] = item["error"]
            if item.get("metadata"):
                entry["metadata"] = _to_jsonable(item["metadata"])
            per_target_serialized.append(entry)
        body["data"] = {
            "family_name": payload["family_name"],
            "targets": payload["targets"],
            "per_target": per_target_serialized,
        }
        body["summary"] = {
            "total_targets": len(payload["targets"]),
            "success_count": len(payload.get("successes", [])),
        }
    elif tool == "get_pdb_detail" and isinstance(payload, PDBEntry):
        body["data"] = _to_jsonable(payload)
    elif tool == "compare_targets" and isinstance(payload, dict):
        body["data"] = _to_jsonable(payload)
        body["summary"] = {"total_rows": len(payload.get("rows", []))}
    elif tool == "search_papers" and isinstance(payload, dict):
        body["data"] = {
            "query": payload["query"],
            "papers": [_to_jsonable(p) for p in payload["papers"]],
        }
        body["summary"] = {"count": len(payload["papers"])}
    else:
        # 단일 Pydantic 모델 (ligand/bioactivity/paper/sequence/variants/binding/alphafold/intel)
        body["data"] = _to_jsonable(payload)

    return json.dumps(body, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Markdown 변환 — server.py의 _render_* 재사용
# --------------------------------------------------------------------------

def _render_markdown_search(result: DispatchResult) -> str:
    sr: SearchResult = result.payload
    md = result.metadata
    display = sr.structures  # cli는 --max-structures를 적용하지 않음 (LLM이 자름)
    failed_ids = md.get("failed_pdb_ids") or []
    unindexed_ids = md.get("unindexed_pdb_ids") or []
    rcsb_warning = md.get("rcsb_search_warning")
    if sr.uniprot.is_gpcr:
        return server._render_gpcr_result(
            sr, display,
            md["fetched_count"], md["total_registered"],
            md["effective_sort"], md["filter_notes"],
            None, md.get("gpcrdb_warning"), failed_ids,
            unindexed_ids, rcsb_warning,
        )
    return server._render_basic_result(
        sr, display,
        md["fetched_count"], md["total_registered"],
        md["effective_sort"], md["filter_notes"],
        None, failed_ids,
        unindexed_ids, rcsb_warning,
    )


def _render_markdown_family(result: DispatchResult) -> str:
    payload = result.payload
    family_name = payload["family_name"]
    lines = [f"## {family_name} 패밀리 구조 검색 결과", ""]
    lines.append("| 타겟 | UniProt | 분류 | 총 구조 수 | Excel 수록 | 최고 해상도 | 최신 구조 |")
    lines.append("|------|---------|------|-----------|------------|------------|-----------|")

    details: list[str] = []
    for item in payload["per_target"]:
        target = item["target"]
        res: SearchResult | None = item.get("result")
        if res is None:
            err = item.get("error") or "조회 실패"
            lines.append(f"| {target} | - | 조회 실패 | - | - | - | - |")
            details.append(f"- **{target}**: {err}")
            continue
        type_label = "🧬 GPCR" if res.uniprot.is_gpcr else "일반"
        lines.append(
            f"| {res.uniprot.gene_name or target} "
            f"| {res.uniprot.accession} "
            f"| {type_label} "
            f"| {res.total_count} "
            f"| {len(res.structures)} "
            f"| {server._best_resolution_cell(res.structures)} "
            f"| {server._latest_structure_cell(res.structures)} |"
        )
        meta = item.get("metadata") or {}
        if meta.get("gpcrdb_warning"):
            details.append(f"- **{target}**: {meta['gpcrdb_warning']}")
        unindexed = meta.get("unindexed_pdb_ids") or []
        if unindexed:
            sample = ", ".join(unindexed[:3])
            rest = len(unindexed) - 3
            extra = f" 외 {rest}개" if rest > 0 else ""
            details.append(
                f"- **{target}**: 신규 {len(unindexed)}개 구조가 UniProt 미반영 "
                f"(RCSB Search 직접 조회): {sample}{extra}"
            )
        if meta.get("rcsb_search_warning"):
            details.append(f"- **{target}**: {meta['rcsb_search_warning']}")
    if details:
        lines.append("")
        lines.extend(details)
    return "\n".join(lines)


def _render_markdown_compare(result: DispatchResult) -> str:
    payload = result.payload
    lines: list[str] = ["## 타겟 구조 비교 결과", ""]
    lines.append("| 타겟 | UniProt | 분류 | 총 구조 수 | 최고 해상도 | 최신 구조 |")
    lines.append("|------|---------|------|-----------|------------|-----------|")
    details: list[str] = []
    for row in payload["rows"]:
        target = row["target"]
        status = row.get("status")
        if status == "uniprot_not_found":
            lines.append(f"| {target} | - | UniProt 조회 실패 | - | - | - |")
            details.append(f"- **{target}**: {row.get('error', '조회 실패')}")
            continue
        if status == "uniprot_error":
            lines.append(f"| {target} | - | UniProt 일시 장애 | - | - | - |")
            details.append(f"- **{target}**: {row.get('error', '일시 장애')}")
            continue
        type_label = "🧬 GPCR" if row.get("is_gpcr") else "일반"
        if status == "no_structures":
            lines.append(
                f"| {target} | {row.get('accession') or '-'} | {type_label} | 0 | - | - |"
            )
            details.append(f"- **{target}** ({row.get('accession') or '-'}): 등록된 PDB 구조 없음")
            continue
        if status == "pdb_error":
            lines.append(
                f"| {target} | {row.get('accession') or '-'} | {type_label} "
                f"| {row.get('total', 0)} | (PDB 일시 장애) | (PDB 일시 장애) |"
            )
            details.append(
                f"- **{target}** ({row.get('accession')}): PDB 일시 장애 — "
                f"{row.get('error', '사유 미상')}"
            )
            continue
        lines.append(
            f"| {target} | {row['accession']} | {type_label} | {row['total']} "
            f"| {row['best_resolution']} | {row['latest']} |"
        )
        if row.get("failed_pdb_ids"):
            details.append(
                f"- **{target}** ({row['accession']}): {row['total']}개 중 "
                f"{len(row['failed_pdb_ids'])}개 메타데이터 조회 실패 — "
                f"{', '.join(row['failed_pdb_ids'])}"
            )
        unindexed = row.get("unindexed_pdb_ids") or []
        if unindexed:
            sample = ", ".join(unindexed[:3])
            rest = len(unindexed) - 3
            extra = f" 외 {rest}개" if rest > 0 else ""
            details.append(
                f"- **{target}** ({row.get('accession') or '-'}): 신규 "
                f"{len(unindexed)}개 구조가 UniProt 미반영 "
                f"(RCSB Search 직접 조회): {sample}{extra}"
            )
    if details:
        lines.append("")
        lines.extend(details)
    return "\n".join(lines)


def _render_markdown_papers(result: DispatchResult) -> str:
    payload = result.payload
    query = payload["query"]
    papers = payload["papers"]
    if not papers:
        return f"'{query}'에 대한 Europe PMC 검색 결과가 없습니다. 쿼리를 단순화해보세요."
    lines = [f"## 논문 검색 결과 — '{query}'", "", f"상위 {len(papers)}건"]
    for i, p in enumerate(papers, 1):
        lines.append("")
        lines.append(f"### {i}. {p.title or '(제목 없음)'}")
        if p.authors:
            authors = ", ".join(p.authors[:3])
            if len(p.authors) > 3:
                authors += " et al."
            lines.append(f"- **저자**: {authors}")
        venue = " · ".join(x for x in [p.journal, str(p.year) if p.year else None] if x)
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
            lines.append(f"- **초록 미리보기**: {server._trim(p.abstract, 280)}")
        if p.source_url:
            lines.append(f"- **출처**: {p.source_url}")
    return "\n".join(lines)


_MD_RENDERERS = {
    "search_target": _render_markdown_search,
    "search_family": _render_markdown_family,
    "compare_targets": _render_markdown_compare,
    "search_papers": _render_markdown_papers,
    "get_pdb_detail": lambda r: server._render_pdb_detail(r.payload),
    "get_ligand_detail": lambda r: server._render_ligand_detail(r.payload),
    "get_target_bioactivities": lambda r: server._render_bioactivities(
        r.payload, r.metadata.get("min_pchembl"),
    ),
    "get_paper_abstract": lambda r: server._render_paper(r.payload),
    "get_sequence_region": lambda r: server._render_sequence_region(r.payload),
    "get_natural_variants": lambda r: server._render_variants(
        r.payload, r.metadata.get("position"), r.metadata.get("disease_only", False),
    ),
    "get_binding_site": lambda r: server._render_binding_sites(r.payload),
    "get_alphafold_model": lambda r: server._render_alphafold(r.payload),
    "get_target_intelligence": lambda r: server._render_target_intel(r.payload),
}


def _render_markdown(result: DispatchResult) -> str:
    if result.error and result.payload is None:
        return f"> ⚠️ {result.error}"
    renderer = _MD_RENDERERS.get(result.tool)
    if renderer is None:
        return f"(마크다운 렌더러 미정의: {result.tool})"
    return renderer(result)


# --------------------------------------------------------------------------
# 공개 진입점
# --------------------------------------------------------------------------

def render(result: DispatchResult, *, mode: str = "json") -> str:
    if mode == "md":
        return _render_markdown(result)
    return _render_json(result)


__all__ = ["render"]
