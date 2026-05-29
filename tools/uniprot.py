"""UniProt API 클라이언트.

타겟 단백질 이름 → UniProt Accession → 등록된 PDB ID 목록을 조회한다.
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import UniProtResult

UNIPROT_SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
UNIPROT_ENTRY_URL = "https://rest.uniprot.org/uniprotkb/{accession}.json"

# UniProt 비상업적 무료 사용 기준 — 요청 간 대기 시간(초)
REQUEST_DELAY = 0.1

# 검색 시 반환받을 필드 목록
_SEARCH_FIELDS = "accession,id,protein_name,gene_names,organism_name"

# httpx 공통 타임아웃
_TIMEOUT = httpx.Timeout(30.0)


class UniProtError(ValueError):
    """UniProt 조회 관련 사용자 대상 에러.

    ValueError를 상속하므로 상위에서 ValueError로도 잡을 수 있다.
    """


def _extract_protein_name(entry: dict) -> str:
    """UniProt 검색 결과 항목에서 단백질명을 추출한다.

    recommendedName이 없는 항목(예: TrEMBL)을 위해 submissionNames,
    alternativeNames 순으로 폴백한다.
    """
    desc = entry.get("proteinDescription") or {}
    rec = desc.get("recommendedName") or {}
    full = (rec.get("fullName") or {}).get("value")
    if full:
        return full
    for key in ("submissionNames", "alternativeNames"):
        names = desc.get(key) or []
        if names:
            full = (names[0].get("fullName") or {}).get("value")
            if full:
                return full
    return entry.get("uniProtkbId", "Unknown")


def _extract_gene_name(entry: dict) -> str | None:
    """검색 결과 항목에서 대표 유전자명을 추출한다."""
    genes = entry.get("genes") or []
    if genes:
        return (genes[0].get("geneName") or {}).get("value")
    return None


async def _search_once(client: httpx.AsyncClient, query: str) -> list[dict]:
    """단일 검색 쿼리를 실행하고 results 리스트를 반환한다."""
    params = {
        "query": query,
        "fields": _SEARCH_FIELDS,
        "format": "json",
        "size": "5",
    }
    resp = await client.get(UNIPROT_SEARCH_URL, params=params)
    resp.raise_for_status()
    await asyncio.sleep(REQUEST_DELAY)
    return resp.json().get("results", [])


async def _get_pdb_ids(client: httpx.AsyncClient, accession: str) -> list[str]:
    """UniProt Entry API에서 PDB cross-reference ID 목록을 추출한다."""
    resp = await client.get(UNIPROT_ENTRY_URL.format(accession=accession))
    resp.raise_for_status()
    await asyncio.sleep(REQUEST_DELAY)
    data = resp.json()

    pdb_ids: list[str] = []
    for db_ref in data.get("uniProtKBCrossReferences", []) or []:
        if db_ref.get("database") == "PDB":
            pdb_id = db_ref.get("id")
            if pdb_id:
                pdb_ids.append(pdb_id.upper())  # PDB ID는 항상 대문자로 처리
    return pdb_ids


async def search_uniprot(target: str) -> UniProtResult:
    """타겟 이름으로 UniProt를 검색하고 PDB ID 목록까지 채워서 반환한다.

    인간(organism_id:9606) + 검증 항목(reviewed:true) 필터로 먼저 검색하고,
    결과가 없으면 필터를 순차적으로 완화하여 재시도한다.

    Raises:
        UniProtError: 타겟을 찾지 못했거나 외부 API 연결에 실패한 경우.
    """
    target = (target or "").strip()
    if not target:
        raise UniProtError("타겟 이름이 비어 있습니다. 단백질명이나 유전자명을 입력해주세요.")

    # 쿼리 후보 — 앞쪽 우선.
    # 1) 유전자명 정확 일치 → 2) 유전자명 부분 일치 → 3) 자유 텍스트 검색 순서로
    #    시도하여, "TP53" 입력 시 TP53BP1 같은 유사명이 잡히는 것을 방지한다.
    # 그 다음 인간/검증 필터를 순차적으로 완화한다.
    quoted = f'"{target}"'
    queries = [
        f"gene_exact:{quoted} AND organism_id:9606 AND reviewed:true",
        f"gene:{quoted} AND organism_id:9606 AND reviewed:true",
        f"{target} AND organism_id:9606 AND reviewed:true",
        f"{target} AND organism_id:9606",
        f"{target} AND reviewed:true",
        target,
    ]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            results: list[dict] = []
            for query in queries:
                results = await _search_once(client, query)
                if results:
                    break

            if not results:
                raise UniProtError(
                    f"'{target}'에 해당하는 인간 단백질을 UniProt에서 찾지 못했습니다. "
                    f"유전자명이나 단백질명으로 다시 시도해보세요. (예: EGFR, TP53)"
                )

            entry = results[0]
            accession = entry["primaryAccession"]
            result = UniProtResult(
                accession=accession,
                entry_name=entry.get("uniProtkbId", ""),
                protein_name=_extract_protein_name(entry),
                gene_name=_extract_gene_name(entry),
                organism=(entry.get("organism") or {}).get("scientificName"),
            )
            result.pdb_ids = await _get_pdb_ids(client, accession)
            return result
    except httpx.TimeoutException as exc:
        raise UniProtError(
            "UniProt 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."
        ) from exc
    except httpx.HTTPError as exc:
        raise UniProtError(
            "외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요."
        ) from exc


async def get_pdb_ids_from_uniprot(accession: str) -> list[str]:
    """UniProt Accession으로 등록된 PDB ID 목록만 조회한다.

    Raises:
        UniProtError: 외부 API 연결에 실패한 경우.
    """
    accession = (accession or "").strip()
    if not accession:
        raise UniProtError("UniProt Accession이 비어 있습니다.")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            return await _get_pdb_ids(client, accession)
    except httpx.TimeoutException as exc:
        raise UniProtError(
            "UniProt 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."
        ) from exc
    except httpx.HTTPError as exc:
        raise UniProtError(
            "외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요."
        ) from exc
