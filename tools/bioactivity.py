"""ChEMBL + IUPHAR target bioactivity 조회.

연구원이 "HTR2A에 대한 risperidone Ki 가 얼마지?" 를 물을 때, Claude가
숫자를 기억으로 답하지 않도록 ChEMBL/IUPHAR이 보고한 활성 데이터 원본을
가져와서 제공한다.

ChEMBL 흐름:
  UniProt accession → /target.json (search) → target_chembl_id
                   → /activity.json?target_chembl_id=...&pchembl_value__isnull=false

IUPHAR 흐름 (보조):
  Gene symbol → /targets/?geneSymbol=... → targetId
             → /targets/{id}/interactions
"""

from __future__ import annotations

import asyncio
import re

import httpx

from models.schemas import Bioactivity, TargetBioactivities

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
IUPHAR_BASE = "https://www.guidetopharmacology.org/services"

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)


class BioactivityAPIError(RuntimeError):
    """ChEMBL / IUPHAR API 일시 장애를 나타내는 예외.

    "활성 데이터 미수록(404, 빈 응답)"과 "API 장애(타임아웃/5xx/파싱 실패)"를 구분한다.
    """

# 측정 표준 — 결합/약효의 핵심 4종 (기본)
DEFAULT_STANDARD_TYPES = ("Ki", "Kd", "IC50", "EC50")


# --------------------------------------------------------------------------
# ChEMBL — target / activity
# --------------------------------------------------------------------------

async def _chembl_target_by_uniprot(
    client: httpx.AsyncClient, accession: str
) -> dict | None:
    """UniProt accession → ChEMBL target 첫 결과.

    404 또는 빈 결과 → None (정상 케이스: 타깃 미등록).
    그 외 HTTP/파싱 오류 → BioactivityAPIError 발생.
    """
    url = f"{CHEMBL_BASE}/target.json"
    params = {
        "target_components__accession": accession,
        "limit": 1,
    }
    try:
        async with _semaphore:
            resp = await client.get(url, params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise BioactivityAPIError(
                f"ChEMBL target.json이 HTTP {resp.status_code}를 반환했습니다."
            )
        targets = resp.json().get("targets") or []
        return targets[0] if targets else None
    except httpx.TimeoutException as exc:
        raise BioactivityAPIError("ChEMBL target.json 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BioactivityAPIError(f"ChEMBL target.json 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BioactivityAPIError(f"ChEMBL target.json 응답 파싱 실패: {exc}") from exc


async def _chembl_activities(
    client: httpx.AsyncClient,
    target_chembl_id: str,
    *,
    standard_types: tuple[str, ...] = DEFAULT_STANDARD_TYPES,
    min_pchembl: float | None = None,
    max_results: int = 50,
    only_with_pchembl: bool = True,
) -> tuple[list[dict], int]:
    """ChEMBL activity 목록을 가져온다. (활성 dict 리스트, 전체 카운트) 반환."""
    url = f"{CHEMBL_BASE}/activity.json"
    params: dict[str, object] = {
        "target_chembl_id": target_chembl_id,
        "limit": max(1, min(max_results, 100)),
    }
    if standard_types:
        params["standard_type__in"] = ",".join(standard_types)
    if only_with_pchembl:
        params["pchembl_value__isnull"] = "false"
    if min_pchembl is not None:
        params["pchembl_value__gte"] = str(min_pchembl)

    # pChEMBL이 높을수록 활성 강 → 내림차순 정렬
    params["order_by"] = "-pchembl_value"

    try:
        async with _semaphore:
            resp = await client.get(url, params=params)
        if resp.status_code == 404:
            return [], 0  # 미수록 (정상 케이스)
        if resp.status_code != 200:
            raise BioactivityAPIError(
                f"ChEMBL activity.json이 HTTP {resp.status_code}를 반환했습니다."
            )
        data = resp.json()
    except httpx.TimeoutException as exc:
        raise BioactivityAPIError("ChEMBL activity.json 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BioactivityAPIError(f"ChEMBL activity.json 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BioactivityAPIError(f"ChEMBL activity.json 응답 파싱 실패: {exc}") from exc

    activities = data.get("activities") or []
    total = (data.get("page_meta") or {}).get("total_count", len(activities))
    return activities, total


def _chembl_activity_to_model(activity: dict) -> Bioactivity:
    """ChEMBL activity dict → Bioactivity."""
    val = None
    try:
        v = activity.get("standard_value")
        val = float(v) if v is not None else None
    except (TypeError, ValueError):
        pass
    pchembl = None
    try:
        p = activity.get("pchembl_value")
        pchembl = float(p) if p is not None else None
    except (TypeError, ValueError):
        pass

    chembl_id = activity.get("molecule_chembl_id")
    return Bioactivity(
        ligand_name=activity.get("molecule_pref_name"),
        ligand_chembl_id=chembl_id,
        target_chembl_id=activity.get("target_chembl_id"),
        standard_type=activity.get("standard_type"),
        standard_relation=activity.get("standard_relation"),
        standard_value=val,
        standard_units=activity.get("standard_units"),
        pchembl_value=pchembl,
        assay_type=activity.get("assay_type"),
        assay_description=activity.get("assay_description"),
        document_chembl_id=activity.get("document_chembl_id"),
        pubmed_id=str(activity.get("document_pubmed_id"))
        if activity.get("document_pubmed_id")
        else None,
        source="ChEMBL",
        source_url=(
            f"https://www.ebi.ac.uk/chembl/explore/compound/{chembl_id}"
            if chembl_id
            else None
        ),
    )


# --------------------------------------------------------------------------
# IUPHAR — target / interactions
# --------------------------------------------------------------------------

async def _iuphar_target_by_gene(
    client: httpx.AsyncClient, gene_symbol: str
) -> dict | None:
    """Gene symbol → IUPHAR target 첫 결과 (human 우선).

    404/빈 결과 → None (미등록). 그 외 → BioactivityAPIError.
    """
    url = f"{IUPHAR_BASE}/targets"
    try:
        async with _semaphore:
            resp = await client.get(
                url, params={"geneSymbol": gene_symbol, "species": "Human"}
            )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise BioactivityAPIError(
                f"IUPHAR /targets가 HTTP {resp.status_code}를 반환했습니다."
            )
        items = resp.json()
        if not items:
            # Human 필터 없이 재시도
            async with _semaphore:
                resp = await client.get(url, params={"geneSymbol": gene_symbol})
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise BioactivityAPIError(
                    f"IUPHAR /targets (species 무제한)가 HTTP {resp.status_code}를 반환했습니다."
                )
            items = resp.json()
        return items[0] if items else None
    except httpx.TimeoutException as exc:
        raise BioactivityAPIError("IUPHAR /targets 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BioactivityAPIError(f"IUPHAR /targets 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BioactivityAPIError(f"IUPHAR /targets 응답 파싱 실패: {exc}") from exc


async def _iuphar_interactions(
    client: httpx.AsyncClient, target_id: int
) -> list[dict]:
    """IUPHAR target interactions.

    404 → 빈 리스트 (정상). 그 외 → BioactivityAPIError.
    """
    url = f"{IUPHAR_BASE}/targets/{target_id}/interactions"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise BioactivityAPIError(
                f"IUPHAR interactions가 HTTP {resp.status_code}를 반환했습니다."
            )
        data = resp.json()
        return data if isinstance(data, list) else []
    except httpx.TimeoutException as exc:
        raise BioactivityAPIError("IUPHAR interactions 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BioactivityAPIError(f"IUPHAR interactions 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BioactivityAPIError(f"IUPHAR interactions 응답 파싱 실패: {exc}") from exc


_AFFINITY_RE = re.compile(r"^(<|>|<=|>=|=)?\s*(-?\d+(?:\.\d+)?)$")


def _iuphar_to_bioactivity(item: dict) -> Bioactivity | None:
    """IUPHAR interaction dict → Bioactivity (nM 단위 표준)."""
    aff_str = (item.get("affinity") or item.get("affinityHigh") or "").strip()
    affinity_type = item.get("affinityType") or item.get("affinityParameter")
    relation = None
    pchembl = None
    if aff_str:
        m = _AFFINITY_RE.match(aff_str)
        if m:
            relation = m.group(1) or "="
            try:
                pchembl = float(m.group(2))  # IUPHAR는 pX 형태
            except ValueError:
                pchembl = None

    standard_value_nm = None
    if pchembl is not None:
        # pX = -log10(M) → nM = 10^(9 - pX)
        try:
            standard_value_nm = 10 ** (9 - pchembl)
        except (TypeError, OverflowError):
            standard_value_nm = None

    ligand_id = item.get("ligandId")
    ligand_url = (
        f"https://www.guidetopharmacology.org/GRAC/LigandDisplayForward?ligandId={ligand_id}"
        if ligand_id
        else None
    )

    return Bioactivity(
        ligand_name=item.get("ligand"),
        ligand_chembl_id=None,
        target_chembl_id=None,
        standard_type=affinity_type,
        standard_relation=relation,
        standard_value=standard_value_nm,
        standard_units="nM" if standard_value_nm is not None else None,
        pchembl_value=pchembl,
        assay_type=item.get("type"),
        assay_description=item.get("action"),
        document_chembl_id=None,
        pubmed_id=str(item["refs"][0]["pmid"])
        if item.get("refs")
        and isinstance(item["refs"], list)
        and item["refs"]
        and item["refs"][0].get("pmid")
        else None,
        source="IUPHAR",
        source_url=ligand_url,
    )


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------

async def fetch_target_bioactivities(
    uniprot_accession: str,
    *,
    gene_symbol: str | None = None,
    standard_types: tuple[str, ...] = DEFAULT_STANDARD_TYPES,
    min_pchembl: float | None = 6.0,
    max_results: int = 30,
    include_iuphar: bool = True,
) -> TargetBioactivities:
    """타깃 UniProt accession으로 ChEMBL/IUPHAR 활성 데이터를 묶어서 반환.

    - `min_pchembl=6.0` (≈ 1 µM Ki/IC50)이 기본; None이면 컷오프 없음.
    - 결과는 pChEMBL 내림차순으로 정렬.
    """
    accession = (uniprot_accession or "").strip().upper()
    if not accession:
        raise ValueError("UniProt Accession이 비어 있습니다.")

    result = TargetBioactivities(
        target_query=uniprot_accession,
        uniprot_accession=accession,
        gene_name=gene_symbol,
    )
    result.sources = {}

    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        # ChEMBL — API 장애 시 notes에 명시적으로 기록하고 계속 진행
        try:
            target = await _chembl_target_by_uniprot(client, accession)
        except BioactivityAPIError as exc:
            target = None
            result.notes.append(
                f"⚠️ ChEMBL target 조회 일시 장애 — 활성 데이터가 누락될 수 있습니다. "
                f"(사유: {exc}) AI 모델은 임의의 Ki/IC50 수치를 생성하지 마십시오."
            )

        if target:
            tid = target.get("target_chembl_id")
            result.chembl_target_id = tid
            if tid:
                try:
                    activities, total = await _chembl_activities(
                        client,
                        tid,
                        standard_types=standard_types,
                        min_pchembl=min_pchembl,
                        max_results=max_results,
                    )
                except BioactivityAPIError as exc:
                    activities, total = [], 0
                    result.notes.append(
                        f"⚠️ ChEMBL activity 조회 일시 장애 — 활성 데이터가 누락되었습니다. "
                        f"(사유: {exc}) AI 모델은 임의의 Ki/IC50 수치를 생성하지 마십시오."
                    )
                result.bioactivities.extend(
                    _chembl_activity_to_model(a) for a in activities
                )
                result.total_count = total
                result.sources["ChEMBL"] = (
                    f"https://www.ebi.ac.uk/chembl/explore/target/{tid}"
                )

        if include_iuphar and gene_symbol:
            try:
                target_iu = await _iuphar_target_by_gene(client, gene_symbol)
            except BioactivityAPIError as exc:
                target_iu = None
                result.notes.append(
                    f"⚠️ IUPHAR target 조회 일시 장애 — IUPHAR 활성 데이터가 누락됩니다. (사유: {exc})"
                )

            if target_iu and target_iu.get("targetId"):
                target_id = int(target_iu["targetId"])
                result.iuphar_target_id = target_id
                try:
                    interactions = await _iuphar_interactions(client, target_id)
                except BioactivityAPIError as exc:
                    interactions = []
                    result.notes.append(
                        f"⚠️ IUPHAR interactions 조회 일시 장애 — IUPHAR 활성 데이터가 누락됩니다. "
                        f"(사유: {exc})"
                    )

                added = 0
                for raw in interactions:
                    bio = _iuphar_to_bioactivity(raw)
                    if bio is None:
                        continue
                    if min_pchembl is not None and (
                        bio.pchembl_value is None or bio.pchembl_value < min_pchembl
                    ):
                        continue
                    result.bioactivities.append(bio)
                    added += 1
                    if added >= max_results:
                        break
                result.sources["IUPHAR/GtoPdb"] = (
                    f"https://www.guidetopharmacology.org/GRAC/ObjectDisplayForward?objectId={target_id}"
                )

    # 통합 정렬 — pChEMBL 내림차순, None은 뒤로
    result.bioactivities.sort(
        key=lambda b: (b.pchembl_value is None, -(b.pchembl_value or 0.0))
    )
    if max_results and len(result.bioactivities) > max_results:
        result.bioactivities = result.bioactivities[:max_results]

    return result


__all__ = ["fetch_target_bioactivities", "DEFAULT_STANDARD_TYPES", "BioactivityAPIError"]
