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
from collections import OrderedDict

import httpx

from models.schemas import Bioactivity, TargetBioactivities

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
IUPHAR_BASE = "https://www.guidetopharmacology.org/services"

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)

# molecule_pref_name이 비어 있을 때 /molecule/{id}.json으로 보강한 결과를 캐싱.
# 같은 호출 안에서 중복 활성에 동일 chembl_id가 여러 번 등장하므로 dedup 효과 큼.
_MOLECULE_NAME_CACHE: "OrderedDict[str, str | None]" = OrderedDict()
_MOLECULE_NAME_CACHE_MAX = 1024
_MOLECULE_NAME_SENTINEL = object()


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
    params: dict[str, str | int] = {
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
    params: dict[str, str | int] = {
        "target_chembl_id": target_chembl_id,
        "limit": max(1, min(max_results, 1000)),
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


def _cache_get_molecule_name(chembl_id: str):
    """캐시 lookup. hit이면 cached value 반환, miss면 sentinel 반환."""
    if chembl_id in _MOLECULE_NAME_CACHE:
        _MOLECULE_NAME_CACHE.move_to_end(chembl_id)
        return _MOLECULE_NAME_CACHE[chembl_id]
    return _MOLECULE_NAME_SENTINEL


def _cache_put_molecule_name(chembl_id: str, name: str | None) -> None:
    """캐시에 결과 저장 (None도 음의 캐시로 저장)."""
    _MOLECULE_NAME_CACHE[chembl_id] = name
    _MOLECULE_NAME_CACHE.move_to_end(chembl_id)
    while len(_MOLECULE_NAME_CACHE) > _MOLECULE_NAME_CACHE_MAX:
        _MOLECULE_NAME_CACHE.popitem(last=False)


def clear_molecule_name_cache() -> None:
    """테스트용 — 모듈 레벨 캐시 비우기."""
    _MOLECULE_NAME_CACHE.clear()
    _DOC_PMID_CACHE.clear()


async def _chembl_molecule_name(
    client: httpx.AsyncClient, chembl_id: str
) -> str | None:
    """ChEMBL `/molecule/{id}.json`에서 사람이 읽을 수 있는 이름 fallback 조회.

    우선순위:
    1. `pref_name` (대표 이름)
    2. `molecule_synonyms[*].molecule_synonym` (별칭) 중 첫 번째

    미수록(404) 또는 두 필드 모두 비어 있으면 None 반환.
    일시 장애(타임아웃/5xx)는 None 반환하고 캐시하지 않음 — 다음 호출에서 재시도 가능.
    """
    if not chembl_id:
        return None

    cached = _cache_get_molecule_name(chembl_id)
    if cached is not _MOLECULE_NAME_SENTINEL:
        return cached  # type: ignore[return-value]

    url = f"{CHEMBL_BASE}/molecule/{chembl_id}.json"
    try:
        async with _semaphore:
            resp = await client.get(url, timeout=httpx.Timeout(10.0))
        if resp.status_code == 404:
            # 확정 미수록 — 음의 캐시
            _cache_put_molecule_name(chembl_id, None)
            return None
        if resp.status_code != 200:
            # 일시 장애 추정 — 캐시하지 않음
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        # 일시 장애 — 캐시하지 않음
        return None

    name = data.get("pref_name")
    if not name:
        syns = data.get("molecule_synonyms") or []
        for s in syns:
            if not isinstance(s, dict):
                continue
            cand = s.get("molecule_synonym") or s.get("synonym")
            if cand:
                name = cand
                break

    _cache_put_molecule_name(chembl_id, name)
    return name


# document_chembl_id → PMID 캐시 (ChEMBL activity 응답은 PMID를 직접 주지 않는다)
_DOC_PMID_CACHE: "OrderedDict[str, str | None]" = OrderedDict()


async def _chembl_document_pmid(client: httpx.AsyncClient, doc_id: str) -> str | None:
    """ChEMBL `/document/{id}.json`에서 PMID 조회(캐시). 미수록/장애 시 None."""
    if not doc_id:
        return None
    if doc_id in _DOC_PMID_CACHE:
        _DOC_PMID_CACHE.move_to_end(doc_id)
        return _DOC_PMID_CACHE[doc_id]
    url = f"{CHEMBL_BASE}/document/{doc_id}.json"
    try:
        async with _semaphore:
            resp = await client.get(url, timeout=httpx.Timeout(10.0))
        if resp.status_code == 404:
            _DOC_PMID_CACHE[doc_id] = None
            return None
        if resp.status_code != 200:
            return None  # 일시 장애 — 캐시 안 함
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    pmid = data.get("pubmed_id")
    pmid_str = str(pmid) if pmid else None
    _DOC_PMID_CACHE[doc_id] = pmid_str
    while len(_DOC_PMID_CACHE) > _MOLECULE_NAME_CACHE_MAX:
        _DOC_PMID_CACHE.popitem(last=False)
    return pmid_str


async def _enrich_chembl_pmids(
    client: httpx.AsyncClient, bioactivities: list[Bioactivity]
) -> None:
    """ChEMBL 행 중 PMID가 비고 document_chembl_id가 있는 항목을 보강(in-place)."""
    missing = sorted({
        ba.document_chembl_id
        for ba in bioactivities
        if ba.source == "ChEMBL" and not ba.pubmed_id and ba.document_chembl_id
    })
    if not missing:
        return
    fetched = await asyncio.gather(
        *[_chembl_document_pmid(client, d) for d in missing],
        return_exceptions=True,
    )
    pmids: dict[str, str | None] = {}
    for d, r in zip(missing, fetched, strict=False):
        pmids[d] = None if isinstance(r, Exception) else r  # type: ignore[assignment]
    for ba in bioactivities:
        if ba.source == "ChEMBL" and not ba.pubmed_id and ba.document_chembl_id:
            ba.pubmed_id = pmids.get(ba.document_chembl_id)


async def _enrich_chembl_ligand_names(
    client: httpx.AsyncClient, bioactivities: list[Bioactivity]
) -> None:
    """ChEMBL bioactivity 중 ligand_name이 비어 있는 항목을 보강 (in-place).

    동일 chembl_id가 여러 활성에 등장할 수 있으므로 set으로 dedup 후 병렬 조회.
    개별 lookup 실패는 silently None 유지.
    """
    missing_ids = sorted({
        ba.ligand_chembl_id
        for ba in bioactivities
        if ba.source == "ChEMBL" and ba.ligand_name is None and ba.ligand_chembl_id
    })
    if not missing_ids:
        return

    tasks = [_chembl_molecule_name(client, mid) for mid in missing_ids]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)
    names: dict[str, str | None] = {}
    for mid, result in zip(missing_ids, fetched, strict=False):
        if isinstance(result, Exception):
            names[mid] = None
        else:
            names[mid] = result  # type: ignore[assignment]

    for ba in bioactivities:
        if (
            ba.source == "ChEMBL"
            and ba.ligand_name is None
            and ba.ligand_chembl_id
        ):
            resolved = names.get(ba.ligand_chembl_id)
            # 최종 fallback: ChEMBL ID 그대로 — pref_name/synonym 둘 다 없는
            # 도구화합물(high-throughput library 등)에서 빈 셀이 나오지 않게.
            # 사용자는 ID로 ChEMBL 페이지를 클릭해 검증 가능.
            ba.ligand_name = resolved or ba.ligand_chembl_id


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
_AFFINITY_RANGE_RE = re.compile(
    r"^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$"
)
_SUP_TAG_RE = re.compile(r"<[^>]+>")


def _parse_affinity_value(aff_str: str) -> tuple[str | None, float | None]:
    """IUPHAR affinity 문자열 → (relation, pX 값).

    단일 값('7.1', '>8.0')과 범위('7.4 - 9.2') 모두 인식한다.
    범위는 중앙값을 채택해 min_pchembl 컷오프에서 누락되지 않게 한다.
    파싱 실패 시 (None, None).
    """
    s = (aff_str or "").strip()
    if not s:
        return None, None
    m = _AFFINITY_RE.match(s)
    if m:
        relation = m.group(1) or "="
        try:
            return relation, float(m.group(2))
        except ValueError:
            return None, None
    rng = _AFFINITY_RANGE_RE.match(s)
    if rng:
        try:
            low = float(rng.group(1))
            high = float(rng.group(2))
        except ValueError:
            return None, None
        return "=", (low + high) / 2.0
    return None, None


def _clean_ligand_name(name: str | None) -> str | None:
    """IUPHAR ligandName에서 HTML 태그(<sup> 등)를 제거한다."""
    if not name:
        return None
    cleaned = _SUP_TAG_RE.sub("", name).strip()
    return cleaned or None


def _iuphar_to_bioactivity(item: dict) -> Bioactivity | None:
    """IUPHAR interaction dict → Bioactivity (nM 단위 표준)."""
    aff_str = (item.get("affinity") or item.get("affinityHigh") or "").strip()
    affinity_type = item.get("affinityParameter") or item.get("affinityType")
    relation, pchembl = _parse_affinity_value(aff_str)

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
        ligand_name=_clean_ligand_name(item.get("ligandName") or item.get("ligand")),
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


def _dedupe_by_ligand_type(bioactivities: list[Bioactivity]) -> list[Bioactivity]:
    """같은 (ligand, source, standard_type)의 여러 측정치를 중앙값 대표 1건으로 축약.

    같은 화합물의 중복·극단치가 표를 지배해 SAR 비교를 망치는 것을 막는다.
    측정 타입(Ki/IC50/EC50 등)이 다르면 별도 행으로 유지(binding/functional 구분).
    """
    groups: "OrderedDict[tuple, list[Bioactivity]]" = OrderedDict()
    for b in bioactivities:
        ligand_key = b.ligand_chembl_id or (b.ligand_name or "").lower()
        key = (ligand_key, b.source, (b.standard_type or "").upper())
        groups.setdefault(key, []).append(b)
    out: list[Bioactivity] = []
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        with_p = sorted(
            (g for g in grp if g.pchembl_value is not None),
            key=lambda g: g.pchembl_value or 0.0,
        )
        rep = with_p[len(with_p) // 2] if with_p else grp[0]
        rep.assay_description = (
            f"[{len(grp)}건 측정의 중앙값] {rep.assay_description or ''}".strip()
        )
        out.append(rep)
    return out


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
    # dedup 재료 확보를 위해 max_results보다 넉넉히 가져온 뒤 축약한다.
    pool = min(max(max_results * 8, 60), 300)
    chembl_api_failed = False

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # ChEMBL — API 장애 시 notes에 명시적으로 기록하고 계속 진행
        try:
            target = await _chembl_target_by_uniprot(client, accession)
        except BioactivityAPIError as exc:
            target = None
            chembl_api_failed = True
            result.notes.append(
                f"⚠️ ChEMBL target 조회 일시 장애 — 활성 데이터가 누락될 수 있습니다. "
                f"(사유: {exc}) AI 모델은 임의의 Ki/IC50 수치를 생성하지 마십시오."
            )

        if target:
            # accession 교차검증 — ChEMBL이 요청과 다른 타깃을 매칭했는지 확인
            comp_accs = {
                (c.get("accession") or "").upper()
                for c in (target.get("target_components") or [])
                if isinstance(c, dict)
            }
            if comp_accs and accession not in comp_accs:
                result.notes.append(
                    f"⚠️ 요청 accession {accession}이(가) ChEMBL 타깃 "
                    f"{target.get('target_chembl_id')}의 구성요소 accession"
                    f"({', '.join(sorted(comp_accs))})와 일치하지 않습니다 — accession 표기를 확인하세요."
                )
            tid = target.get("target_chembl_id")
            result.chembl_target_id = tid
            if tid:
                try:
                    activities, total = await _chembl_activities(
                        client,
                        tid,
                        standard_types=standard_types,
                        min_pchembl=min_pchembl,
                        max_results=pool,
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
        elif not chembl_api_failed:
            result.notes.append(
                f"ChEMBL에 accession {accession}로 매칭되는 타깃이 없습니다 — "
                f"accession 표기를 확인하거나 gene symbol로 IUPHAR를 조회하세요."
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

                for raw in interactions:
                    bio = _iuphar_to_bioactivity(raw)
                    if bio is None:
                        continue
                    if min_pchembl is not None and (
                        bio.pchembl_value is None or bio.pchembl_value < min_pchembl
                    ):
                        continue
                    result.bioactivities.append(bio)
                result.sources["IUPHAR/GtoPdb"] = (
                    f"https://www.guidetopharmacology.org/GRAC/ObjectDisplayForward?objectId={target_id}"
                )

        # 같은 (ligand, type) 중복 측정 → 중앙값 대표 1건으로 축약 후
        # pChEMBL 내림차순 정렬, distinct 리간드 max_results개로 절단
        result.bioactivities = _dedupe_by_ligand_type(result.bioactivities)
        result.bioactivities.sort(
            key=lambda b: (b.pchembl_value is None, -(b.pchembl_value or 0.0))
        )
        if max_results and len(result.bioactivities) > max_results:
            result.bioactivities = result.bioactivities[:max_results]

        # 최종 표시 행에 대해서만 이름·PMID 보강(네트워크 절약)
        try:
            await _enrich_chembl_ligand_names(client, result.bioactivities)
            await _enrich_chembl_pmids(client, result.bioactivities)
        except Exception as exc:  # noqa: BLE001 - 보강 실패는 fatal이 아님
            result.notes.append(
                f"⚠️ ChEMBL 이름/PMID 보강 중 일부 실패 — 일부 셀이 비어 있을 수 있습니다. (사유: {exc})"
            )

    return result


__all__ = ["fetch_target_bioactivities", "DEFAULT_STANDARD_TYPES", "BioactivityAPIError"]
