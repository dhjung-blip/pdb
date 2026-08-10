"""RCSB '구조상세(structure-detail)' 데이터 조회.

기존 GPCR 약리 요약 포맷과 **별개의 전용 포맷**용 — 폴리머 엔티티 단위 정보
(macromolecule / mutation / sequence length / organism / gene / chain) + 리간드
(코드·이름·SMILES)를 RCSB GraphQL에서 가져온다. '구조상세' 트리거로만 사용된다.

필드는 6A93 등으로 실측 검증됨:
  pdbx_mutation="S162K, M164W, ...", seq_length=376, chains=[A,B],
  organism=[Homo sapiens, E. coli(cybC)], gene=[HTR2A, cybC], SMILES(chem_comp).
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import StructureDetail, StructureEntity
from tools.pdb import (
    _BUFFER_LIGANDS,
    MAX_CONCURRENCY,
    RCSB_GRAPHQL_URL,
    _safe_float,
)

_TIMEOUT = httpx.Timeout(30.0)

_DETAIL_QUERY = """
query GetStructureDetail($id: String!) {
  entry(entry_id: $id) {
    struct { title }
    rcsb_entry_info { experimental_method resolution_combined }
    rcsb_accession_info { initial_release_date }
    polymer_entities {
      rcsb_polymer_entity { pdbx_description pdbx_mutation }
      entity_poly { rcsb_sample_sequence_length }
      rcsb_entity_source_organism { ncbi_scientific_name rcsb_gene_name { value } }
      rcsb_polymer_entity_container_identifiers { auth_asym_ids }
    }
    nonpolymer_entities {
      nonpolymer_comp {
        chem_comp { id name }
        rcsb_chem_comp_descriptor { SMILES SMILES_stereo }
      }
    }
  }
}
"""


def _parse_detail(pdb_id: str, entry: dict) -> StructureDetail:
    info = entry.get("rcsb_entry_info") or {}
    res_list = info.get("resolution_combined")
    resolution = _safe_float(res_list[0]) if res_list else None
    released = (entry.get("rcsb_accession_info") or {}).get("initial_release_date")
    if released:
        released = released[:10]

    entities: list[StructureEntity] = []
    for pe in entry.get("polymer_entities") or []:
        rp = pe.get("rcsb_polymer_entity") or {}
        sources: list[dict] = []
        seen: set = set()
        for o in pe.get("rcsb_entity_source_organism") or []:
            org = o.get("ncbi_scientific_name")
            genes = [g.get("value") for g in (o.get("rcsb_gene_name") or []) if g.get("value")]
            key = (org, tuple(genes))
            if key in seen:
                continue
            seen.add(key)
            sources.append({"organism": org, "genes": genes})
        seq = (pe.get("entity_poly") or {}).get("rcsb_sample_sequence_length")
        chains = (pe.get("rcsb_polymer_entity_container_identifiers") or {}).get(
            "auth_asym_ids"
        ) or []
        entities.append(
            StructureEntity(
                macromolecule_name=rp.get("pdbx_description"),
                chain_ids=list(chains),
                seq_length=int(seq) if isinstance(seq, int) else None,
                mutation=rp.get("pdbx_mutation"),
                sources=sources,
            )
        )

    ligands: list[dict] = []
    for ne in entry.get("nonpolymer_entities") or []:
        comp = ne.get("nonpolymer_comp") or {}
        cc = comp.get("chem_comp") or {}
        cid = cc.get("id")
        if not cid or cid.upper() in _BUFFER_LIGANDS:
            continue
        desc = comp.get("rcsb_chem_comp_descriptor") or {}
        ligands.append(
            {
                "id": cid,
                "name": cc.get("name"),
                "smiles": desc.get("SMILES_stereo") or desc.get("SMILES"),
            }
        )

    return StructureDetail(
        pdb_id=pdb_id,
        method=info.get("experimental_method"),
        resolution=resolution,
        released_date=released,
        title=(entry.get("struct") or {}).get("title"),
        entities=entities,
        ligands=ligands,
    )


async def _fetch_one(
    client: httpx.AsyncClient, pdb_id: str, semaphore: asyncio.Semaphore
) -> StructureDetail | None:
    pdb_id = pdb_id.upper()
    async with semaphore:
        for attempt in range(2):
            try:
                resp = await client.post(
                    RCSB_GRAPHQL_URL,
                    json={"query": _DETAIL_QUERY, "variables": {"id": pdb_id}},
                )
                resp.raise_for_status()
                entry = (resp.json().get("data") or {}).get("entry")
                return _parse_detail(pdb_id, entry) if entry else None
            except (httpx.HTTPError, ValueError):
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                return None
    return None


async def fetch_structure_detail(pdb_ids: list[str]) -> list[StructureDetail]:
    """여러 PDB ID의 구조상세를 병렬 조회한다. 개별 실패는 생략."""
    if not pdb_ids:
        return []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [_fetch_one(client, pid, semaphore) for pid in pdb_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, StructureDetail)]


__all__ = ["fetch_structure_detail"]
