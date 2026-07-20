"""RCSB PDB GraphQL API 클라이언트.

PDB ID → Resolution / Released Date / Method / Citation 메타데이터를 조회한다.
구조가 수십~수백 개일 수 있으므로 비동기 병렬 처리를 사용한다.
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import Citation, PDBEntry

RCSB_GRAPHQL_URL = "https://data.rcsb.org/graphql"

# 병렬 동시 요청 최대 개수
MAX_CONCURRENCY = 10
# 실패 시 재시도 전 대기 시간(초)
RETRY_DELAY = 1.0

_TIMEOUT = httpx.Timeout(30.0)

# RCSB PDB GraphQL 쿼리.
# 참고: citation의 DOI/PubMed 필드명은 GraphQL 스키마상 대소문자가
# 'pdbx_database_id_DOI', 'pdbx_database_id_PubMed' 로 고정되어 있다.
_QUERY = """
query GetPDBEntry($id: String!) {
  entry(entry_id: $id) {
    rcsb_id
    struct {
      title
    }
    rcsb_entry_info {
      resolution_combined
      experimental_method
      assembly_count
      deposited_polymer_entity_instance_count
    }
    refine {
      ls_R_factor_R_work
      ls_R_factor_R_free
    }
    rcsb_accession_info {
      initial_release_date
    }
    polymer_entities {
      rcsb_polymer_entity {
        pdbx_description
      }
    }
    citation {
      id
      title
      rcsb_authors
      journal_abbrev
      year
      journal_volume
      page_first
      page_last
      pdbx_database_id_DOI
      pdbx_database_id_PubMed
    }
  }
}
"""

# 결정화 첨가물/버퍼/이온/지질 — ref 화합물(저해제)에서 제외
_BUFFER_LIGANDS = {
    "HOH", "DOD", "SO4", "PO4", "GOL", "EDO", "PEG", "PGE", "PG4", "1PE", "2PE",
    "P6G", "P33", "CLR", "OLC", "OLA", "PLM", "MG", "NA", "CL", "K", "ZN", "CA",
    "MN", "FE", "CD", "NI", "CU", "ACT", "DMS", "TRS", "EPE", "MES", "FMT", "IOD",
    "BR", "CO3", "NO3", "NH4", "CAC", "BME", "DTT", "IMD", "FLC", "TLA", "MPD",
    "BOG", "LDA", "LMT", "Y01", "PLC", "POV", "PEW", "SIN", "SCN", "AZI", "PE4",
}

# get_pdb_detail 전용 — 결합 리간드 + RCSB 보고 binding affinity (검색 경로엔 미사용)
_LIGANDS_QUERY = """
query GetPDBLigands($id: String!) {
  entry(entry_id: $id) {
    rcsb_binding_affinity { comp_id type value unit provenance_code }
    nonpolymer_entities {
      nonpolymer_comp { chem_comp { id name } }
    }
  }
}
"""


def format_method(method: str | None) -> str:
    """RCSB의 실험방법 문자열을 짧은 표기로 변환한다."""
    if not method:
        return "기타"
    m = method.upper()
    # rcsb_entry_info.experimental_method 는 짧은 코드("X-ray", "EM", "NMR" 등)를,
    # 다른 필드는 전체 명칭("ELECTRON MICROSCOPY" 등)을 반환할 수 있어 둘 다 처리한다.
    if "X-RAY" in m:
        return "X-ray"
    if m == "EM" or "ELECTRON MICROSCOPY" in m or "CRYO" in m:
        return "Cryo-EM"
    if "NMR" in m:
        return "NMR"
    if "NEUTRON" in m:
        return "Neutron"
    if "ELECTRON CRYSTALLOGRAPHY" in m:
        return "Electron Crystallography"
    return method.title()


def _safe_float(value) -> float | None:
    """안전 float 변환 — None/비수치는 None."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_citation(citations: list[dict]) -> Citation | None:
    """citation 리스트에서 대표 논문(primary)을 골라 Citation 모델로 변환한다."""
    if not citations:
        return None

    primary = next(
        (c for c in citations if c.get("id") == "primary"),
        citations[0],
    )

    # 저자는 세미콜론으로 구분한다 — 각 저자가 "Last, F. M." 형태로 쉼표를
    # 포함하므로, 저자 간 구분자로 쉼표를 쓰면 모호해진다 (ACS 인용 표준).
    authors_list = primary.get("rcsb_authors") or []
    authors: str | None = None
    if authors_list:
        authors = "; ".join(authors_list[:3])
        if len(authors_list) > 3:
            authors += " et al."

    year = primary.get("year")
    pmid = primary.get("pdbx_database_id_PubMed")

    citation = Citation(
        title=primary.get("title"),
        authors=authors,
        journal=primary.get("journal_abbrev"),
        year=int(year) if year else None,
        volume=primary.get("journal_volume"),
        page_first=primary.get("page_first"),
        page_last=primary.get("page_last"),
        doi=primary.get("pdbx_database_id_DOI"),
        pmid=str(pmid) if pmid else None,
    )

    # 모든 필드가 비어 있으면 citation 자체를 None 처리한다.
    if not any(
        [
            citation.title,
            citation.authors,
            citation.journal,
            citation.year,
            citation.doi,
            citation.pmid,
        ]
    ):
        return None
    return citation


def _parse_entry(pdb_id: str, entry: dict) -> PDBEntry:
    """GraphQL 응답의 entry 객체를 PDBEntry 모델로 변환한다."""
    info = entry.get("rcsb_entry_info") or {}

    # resolution_combined 가 정수(JSON int)로 내려오는 경우가 있어 항상 float으로 변환.
    resolution_list = info.get("resolution_combined")
    resolution = None
    if resolution_list and resolution_list[0] is not None:
        resolution = float(resolution_list[0])

    method = info.get("experimental_method")

    released_date = (entry.get("rcsb_accession_info") or {}).get("initial_release_date")
    if released_date:
        released_date = released_date[:10]  # "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"

    title = (entry.get("struct") or {}).get("title")

    # polymer entity 설명 — Fusion protein / Antibody 추출의 1차 소스
    polymer_descriptions: list[str] = []
    for pe in entry.get("polymer_entities") or []:
        desc = (pe.get("rcsb_polymer_entity") or {}).get("pdbx_description")
        if desc:
            polymer_descriptions.append(desc)

    citation = _parse_citation(entry.get("citation") or [])

    # 구조 품질(QC) — X-ray refine R-factor (refine는 리스트; EM/NMR은 null)
    refine_list = entry.get("refine") or []
    rf = refine_list[0] if refine_list else {}
    r_work = _safe_float((rf or {}).get("ls_R_factor_R_work"))
    r_free = _safe_float((rf or {}).get("ls_R_factor_R_free"))

    return PDBEntry(
        pdb_id=pdb_id,
        resolution=resolution,
        method=method,
        released_date=released_date,
        title=title,
        polymer_descriptions=polymer_descriptions,
        citation=citation,
        r_work=r_work,
        r_free=r_free,
        assembly_count=info.get("assembly_count"),
        deposited_chain_count=info.get("deposited_polymer_entity_instance_count"),
    )


async def _fetch_entry(client: httpx.AsyncClient, pdb_id: str) -> PDBEntry:
    """단일 PDB entry를 조회한다. 네트워크 오류 시 1회 재시도한다."""
    pdb_id = pdb_id.upper()
    payload = {"query": _QUERY, "variables": {"id": pdb_id}}

    resp: httpx.Response | None = None
    for attempt in range(2):  # 최초 1회 + 재시도 1회
        try:
            resp = await client.post(RCSB_GRAPHQL_URL, json=payload)
            resp.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
                continue
            raise

    assert resp is not None
    body = resp.json()
    entry = (body.get("data") or {}).get("entry")
    if not entry:
        raise ValueError(f"'{pdb_id}' PDB 항목을 찾을 수 없습니다.")
    return _parse_entry(pdb_id, entry)


async def _fetch_with_semaphore(
    client: httpx.AsyncClient,
    pdb_id: str,
    semaphore: asyncio.Semaphore,
) -> PDBEntry:
    """세마포어로 동시 요청 수를 제한하며 단일 entry를 조회한다."""
    async with semaphore:
        return await _fetch_entry(client, pdb_id)


async def fetch_all_pdb_entries(pdb_ids: list[str]) -> list[PDBEntry]:
    """여러 PDB ID의 메타데이터를 병렬로 조회한다.

    개별 항목의 실패는 전체를 막지 않으며, 성공한 항목만 반환한다.
    실패한 ID 목록은 `fetch_all_pdb_entries_with_failures`로 얻을 수 있다.
    """
    entries, _ = await fetch_all_pdb_entries_with_failures(pdb_ids)
    return entries


async def fetch_all_pdb_entries_with_failures(
    pdb_ids: list[str],
) -> tuple[list[PDBEntry], list[str]]:
    """여러 PDB ID의 메타데이터를 병렬로 조회하고 (성공 목록, 실패 ID 목록)을 반환한다.

    호출자가 부분 실패를 사용자에게 명시적으로 알릴 수 있도록 한다.
    """
    if not pdb_ids:
        return [], []

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [_fetch_with_semaphore(client, pid, semaphore) for pid in pdb_ids]
        # return_exceptions=True: 개별 실패가 전체 gather를 막지 않도록 한다.
        results = await asyncio.gather(*tasks, return_exceptions=True)

    entries: list[PDBEntry] = []
    failed: list[str] = []
    for pid, r in zip(pdb_ids, results, strict=False):
        if isinstance(r, PDBEntry):
            entries.append(r)
        else:
            failed.append(pid.upper())
    return entries, failed


async def _fetch_entry_ligands(
    client: httpx.AsyncClient, pdb_id: str
) -> tuple[list[dict], list[dict]]:
    """결합 리간드(버퍼/이온 제외) + RCSB 보고 binding affinity. 실패 시 ([], [])."""
    try:
        resp = await client.post(
            RCSB_GRAPHQL_URL,
            json={"query": _LIGANDS_QUERY, "variables": {"id": pdb_id}},
        )
        resp.raise_for_status()
        entry = (resp.json().get("data") or {}).get("entry") or {}
    except (httpx.HTTPError, ValueError):
        return [], []
    ligands: list[dict] = []
    for npe in entry.get("nonpolymer_entities") or []:
        cc = (npe.get("nonpolymer_comp") or {}).get("chem_comp") or {}
        cid = cc.get("id")
        if cid and cid.upper() not in _BUFFER_LIGANDS:
            ligands.append({"id": cid, "name": cc.get("name")})
    affinities: list[dict] = []
    for ba in entry.get("rcsb_binding_affinity") or []:
        affinities.append(
            {
                "comp_id": ba.get("comp_id"),
                "type": ba.get("type"),
                "value": ba.get("value"),
                "unit": ba.get("unit"),
                "provenance": ba.get("provenance_code"),
            }
        )
    return ligands, affinities


async def fetch_single_pdb_entry(pdb_id: str) -> PDBEntry:
    """단일 PDB ID의 상세 메타데이터를 조회한다.

    Raises:
        ValueError: PDB 항목을 찾지 못했거나 외부 API 연결에 실패한 경우.
    """
    pdb_id = (pdb_id or "").strip().upper()
    if not pdb_id:
        raise ValueError("PDB ID가 비어 있습니다.")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            entry = await _fetch_entry(client, pdb_id)
            entry.ligands, entry.binding_affinities = await _fetch_entry_ligands(
                client, pdb_id
            )
            return entry
    except httpx.TimeoutException as exc:
        raise ValueError(
            "PDB 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."
        ) from exc
    except httpx.HTTPError as exc:
        raise ValueError(
            "외부 API 연결에 실패했습니다. 네트워크 연결을 확인해주세요."
        ) from exc
