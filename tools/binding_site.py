"""PDB 결합부위 잔기 추출 — PDBe 우선, RCSB GraphQL fallback.

연구원이 "이 구조의 결합 포켓 핵심 잔기가 뭐야?" 라고 물을 때 Claude가 추측하지
않도록 권위 있는 PDBe / RCSB 의 BINDING_SITE feature를 그대로 가져온다.

PDBe API 흐름 (1차):
  GET https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/{pdb_id}
  → site_id, ligand_residues, site_residues 배열 반환

RCSB GraphQL (fallback / 보강):
  polymer_entity_instance.rcsb_polymer_instance_feature(type="BINDING_SITE")
  → 한 chain의 binding site 위치
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import BindingSite, BindingSiteResidue, BindingSiteResult

PDBE_BINDING_URL = "https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/{pdb_id}"
RCSB_GRAPHQL_URL = "https://data.rcsb.org/graphql"

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)


class BindingSiteAPIError(RuntimeError):
    """결합부위 보조 API(PDBe/RCSB GraphQL)의 일시 장애를 나타내는 예외.

    "결합부위 미등록(빈 응답)"과 "API 장애(타임아웃/5xx)"를 구분하기 위함이다.
    """

_RCSB_QUERY = """
query GetBindingSites($id: String!) {
  entry(entry_id: $id) {
    nonpolymer_entities {
      pdbx_entity_nonpoly {
        comp_id
        name
      }
      nonpolymer_entity_instances {
        rcsb_nonpolymer_entity_instance_container_identifiers {
          auth_asym_id
          auth_seq_id
        }
        rcsb_nonpolymer_instance_feature {
          type
          name
          additional_properties { name values }
        }
        rcsb_nonpolymer_instance_feature_summary {
          type
          count
        }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------
# PDBe
# --------------------------------------------------------------------------

def _residue_from_pdbe(item: dict) -> BindingSiteResidue | None:
    """PDBe binding_sites 응답의 residue 항목 → BindingSiteResidue."""
    chain = item.get("chain_id") or item.get("struct_asym_id") or ""
    res_num = item.get("author_residue_number")
    if res_num is None:
        res_num = item.get("residue_number")
    res_name = item.get("chem_comp_id") or item.get("residue_name") or ""
    if not chain or res_num is None or not res_name:
        return None
    try:
        res_num_int = int(res_num)
    except (TypeError, ValueError):
        return None
    label = item.get("residue_number")
    try:
        label_int = int(label) if label is not None else None
    except (TypeError, ValueError):
        label_int = None
    return BindingSiteResidue(
        chain_id=str(chain),
        residue_number=res_num_int,
        residue_name=str(res_name).upper(),
        label_seq_id=label_int,
    )


async def _fetch_pdbe_binding_sites(
    client: httpx.AsyncClient, pdb_id: str
) -> list[BindingSite]:
    """PDBe API에서 binding_sites를 가져와 BindingSite 리스트로 변환.

    - 404 또는 200 빈 응답 → 정상 케이스로 빈 리스트 반환 (결합부위 미등록).
    - 그 외 HTTP 오류/타임아웃/파싱 실패 → BindingSiteAPIError 발생.
    """
    url = PDBE_BINDING_URL.format(pdb_id=pdb_id.lower())
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code == 404:
            return []  # 결합부위 미등록 (정상 케이스)
        if resp.status_code != 200:
            raise BindingSiteAPIError(
                f"PDBe binding_sites API가 HTTP {resp.status_code}를 반환했습니다."
            )
        data = resp.json()
    except httpx.TimeoutException as exc:
        raise BindingSiteAPIError("PDBe binding_sites API 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BindingSiteAPIError(f"PDBe binding_sites API 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BindingSiteAPIError(f"PDBe binding_sites API 응답 파싱 실패: {exc}") from exc

    sites_data = (data.get(pdb_id.lower()) or [])
    sites: list[BindingSite] = []
    for entry in sites_data:
        site_id = entry.get("site_id")
        ligand_residues = entry.get("ligand_residues") or []
        site_residues = entry.get("site_residues") or []

        # 대표 리간드 정보 (보통 ligand_residues에 1개)
        ligand_code = None
        ligand_chain = None
        if ligand_residues:
            first = ligand_residues[0]
            ligand_code = first.get("chem_comp_id")
            ligand_chain = first.get("chain_id") or first.get("struct_asym_id")

        residues: list[BindingSiteResidue] = []
        for raw in site_residues:
            r = _residue_from_pdbe(raw)
            if r is not None:
                residues.append(r)

        # 정렬: chain → residue number
        residues.sort(key=lambda r: (r.chain_id, r.residue_number))

        sites.append(
            BindingSite(
                pdb_id=pdb_id.upper(),
                site_id=str(site_id) if site_id else None,
                ligand_code=ligand_code,
                ligand_name=None,  # PDBe API는 이름 제공하지 않음
                chain_id=str(ligand_chain) if ligand_chain else None,
                residues=residues,
                source="PDBe",
                source_url=f"https://www.ebi.ac.uk/pdbe/entry/pdb/{pdb_id.lower()}/bound",
            )
        )
    return sites


# --------------------------------------------------------------------------
# RCSB GraphQL (보조 — ligand 이름 확인용)
# --------------------------------------------------------------------------

async def _fetch_rcsb_ligand_names(
    client: httpx.AsyncClient, pdb_id: str
) -> dict[str, str]:
    """RCSB GraphQL로 PDB chem code → 화합물 이름 매핑.

    API 장애 시 BindingSiteAPIError 발생.
    호출자는 이를 catch하여 "이름 보강 실패" 노트만 추가하고 결합부위 자체는 그대로 반환해야 한다.
    """
    payload = {"query": _RCSB_QUERY, "variables": {"id": pdb_id.upper()}}
    try:
        async with _semaphore:
            resp = await client.post(RCSB_GRAPHQL_URL, json=payload)
        if resp.status_code != 200:
            raise BindingSiteAPIError(
                f"RCSB GraphQL이 HTTP {resp.status_code}를 반환했습니다."
            )
        data = resp.json()
    except httpx.TimeoutException as exc:
        raise BindingSiteAPIError("RCSB GraphQL 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise BindingSiteAPIError(f"RCSB GraphQL 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise BindingSiteAPIError(f"RCSB GraphQL 응답 파싱 실패: {exc}") from exc

    entry = (data.get("data") or {}).get("entry") or {}
    nonpolys = entry.get("nonpolymer_entities") or []
    mapping: dict[str, str] = {}
    for ne in nonpolys:
        nonpoly_info = ne.get("pdbx_entity_nonpoly") or {}
        code = nonpoly_info.get("comp_id")
        name = nonpoly_info.get("name")
        if code and name:
            mapping[code.upper()] = name
    return mapping


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------

# PDBe API가 결합부위 미지원 구조에서 비어 있을 수 있는 화학성분 코드들 (이온/용매)
_NOT_INTERESTING_LIGANDS = {
    "HOH", "WAT", "EDO", "GOL", "PEG", "PG4", "MPD", "FMT", "ACT", "ACE",
    "SO4", "CL", "NA", "MG", "ZN", "CA", "K", "FE", "DMS",
}


async def fetch_binding_sites(
    pdb_id: str,
    *,
    ligand_filter: str | None = None,
    skip_solvents: bool = True,
) -> BindingSiteResult:
    """PDB 구조의 결합부위(잔기 목록) 묶음을 반환한다.

    - `ligand_filter`가 주어지면 그 PDB chem code의 결합부위만 반환.
    - `skip_solvents=True`이면 흔한 용매/이온 결합부위는 제외.
    이 함수는 예외를 던지지 않는다 — 실패 시 빈 결과 + notes 포함.
    """
    pdb_id = (pdb_id or "").strip().upper()
    notes: list[str] = []
    if not pdb_id or len(pdb_id) != 4:
        return BindingSiteResult(
            pdb_id=pdb_id, sites=[], notes=["올바른 4자리 PDB ID가 필요합니다."]
        )

    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        try:
            sites = await _fetch_pdbe_binding_sites(client, pdb_id)
        except BindingSiteAPIError as exc:
            # API 장애 — "결합부위 없음"과 구분해 명시
            notes.append(
                f"⚠️ PDBe API 일시 장애로 {pdb_id}의 결합부위 데이터를 조회하지 못했습니다. "
                f"잠시 후 다시 시도해주세요. (사유: {exc}) "
                f"— AI 모델은 임의의 결합부위 잔기를 생성하지 마십시오."
            )
            return BindingSiteResult(pdb_id=pdb_id, sites=[], notes=notes)

        if not sites:
            notes.append(f"PDBe에 {pdb_id}의 결합부위가 등록되어 있지 않습니다.")
            return BindingSiteResult(pdb_id=pdb_id, sites=[], notes=notes)

        # RCSB에서 리간드 이름 보강 (실패하면 노트만 추가하고 계속 진행)
        try:
            ligand_names = await _fetch_rcsb_ligand_names(client, pdb_id)
        except BindingSiteAPIError as exc:
            ligand_names = {}
            notes.append(
                f"리간드 이름 보강(RCSB) 일시 장애 — 리간드는 PDB chem code로만 표시됩니다. (사유: {exc})"
            )

        for site in sites:
            if site.ligand_code and site.ligand_code.upper() in ligand_names:
                site.ligand_name = ligand_names[site.ligand_code.upper()]

    # 필터
    if skip_solvents:
        sites = [
            s for s in sites
            if not s.ligand_code or s.ligand_code.upper() not in _NOT_INTERESTING_LIGANDS
        ]
    if ligand_filter:
        target = ligand_filter.strip().upper()
        sites = [s for s in sites if (s.ligand_code or "").upper() == target]
        if not sites:
            notes.append(
                f"리간드 '{ligand_filter}' 의 결합부위를 찾지 못했습니다."
            )

    return BindingSiteResult(pdb_id=pdb_id, sites=sites, notes=notes)


__all__ = ["fetch_binding_sites", "BindingSiteAPIError"]
