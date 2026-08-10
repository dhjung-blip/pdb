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
import re
from urllib.parse import quote

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


PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# RCSB 계통명의 이탤릭 인코딩(~{H}) 및 IUPAC 2013 치환기명 → PubChem이 아는 표기
_MARKUP_RE = re.compile(r"~\{([^}]*)\}")
_HALOGEN_ALIASES = {
    "iodanyl": "iodo",
    "bromanyl": "bromo",
    "chloranyl": "chloro",
    "fluoranyl": "fluoro",
}

# 계통명 → 통용명 캐시 (같은 리간드가 여러 구조에 반복 등장)
_COMMON_NAME_CACHE: dict[str, str | None] = {}


def strip_name_markup(name: str | None) -> str | None:
    """RCSB 계통명의 이탤릭 인코딩만 제거한다: `(2~{R})-...` → `(2R)-...`.

    화학명 자체는 바꾸지 않는다(표기 노이즈만 정리 — 추측 아님).
    """
    if not name:
        return name
    return _MARKUP_RE.sub(r"\1", name)


def _clean_systematic_name(name: str) -> str:
    """RCSB 계통명에서 이탤릭 마크업(~{H})과 IUPAC-2013 할로겐명을 정리한다."""
    out = _MARKUP_RE.sub(r"\1", name or "")
    for src, dst in _HALOGEN_ALIASES.items():
        out = out.replace(src, dst)
    return out


def _looks_systematic(name: str | None) -> bool:
    """IUPAC 계통명처럼 보이는지 — 통용명(Pimavanserin 등)은 변환 대상이 아니다."""
    if not name:
        return False
    if len(name) < 18:
        return False
    return bool(re.search(r"~\{|\d-|\[|\byl\b|-yl|amine$|-ol$|-one$|oxy", name))


async def _resolve_common_name(
    client: httpx.AsyncClient, raw_name: str | None, smiles: str | None
) -> str | None:
    """계통명 → 통용명(PubChem Title). 이름 조회 우선, 실패 시 SMILES 폴백.

    해석 불가/장애 시 None (호출자가 계통명을 그대로 쓴다 — 추측 금지).
    """
    if not _looks_systematic(raw_name):
        return None
    key = raw_name or smiles or ""
    if key in _COMMON_NAME_CACHE:
        return _COMMON_NAME_CACHE[key]

    resolved: str | None = None
    queries = [q for q in (_clean_systematic_name(raw_name or ""), raw_name) if q]
    for q in queries:
        try:
            resp = await client.get(
                f"{PUBCHEM_BASE}/compound/name/{quote(q, safe='')}/property/Title/JSON",
                timeout=httpx.Timeout(12.0),
            )
            if resp.status_code == 200:
                props = (resp.json().get("PropertyTable") or {}).get("Properties") or []
                if props and props[0].get("Title"):
                    resolved = props[0]["Title"]
                    break
        except (httpx.HTTPError, ValueError):
            continue

    if not resolved and smiles:
        try:
            resp = await client.post(
                f"{PUBCHEM_BASE}/compound/smiles/property/Title/JSON",
                data={"smiles": smiles},
                timeout=httpx.Timeout(12.0),
            )
            if resp.status_code == 200:
                props = (resp.json().get("PropertyTable") or {}).get("Properties") or []
                if props and props[0].get("Title"):
                    resolved = props[0]["Title"]
        except (httpx.HTTPError, ValueError):
            pass

    # 해석 결과가 원래 계통명과 사실상 같으면 의미 없음 → None
    if resolved and _looks_systematic(resolved) and len(resolved) > 40:
        resolved = None

    _COMMON_NAME_CACHE[key] = resolved
    return resolved


async def _enrich_common_names(client: httpx.AsyncClient, details: list[StructureDetail]) -> None:
    """구조 목록의 리간드에 common_name을 채운다(in-place). 중복 리간드는 1회만 조회."""
    uniq: dict[str, dict] = {}
    for d in details:
        for lig in d.ligands:
            k = lig.get("id") or lig.get("name") or ""
            if k and k not in uniq:
                uniq[k] = lig
    if not uniq:
        return
    keys = list(uniq)
    results = await asyncio.gather(
        *[_resolve_common_name(client, uniq[k].get("name"), uniq[k].get("smiles")) for k in keys],
        return_exceptions=True,
    )
    resolved: dict[str, str | None] = {}
    for k, r in zip(keys, results, strict=False):
        resolved[k] = None if isinstance(r, BaseException) else r
    for d in details:
        for lig in d.ligands:
            k = lig.get("id") or lig.get("name") or ""
            lig["common_name"] = resolved.get(k)


async def fetch_structure_detail(pdb_ids: list[str]) -> list[StructureDetail]:
    """여러 PDB ID의 구조상세를 병렬 조회한다. 개별 실패는 생략.

    리간드는 RCSB 계통명(name)과 함께 통용명(common_name)을 PubChem으로 해석해 채운다.
    """
    if not pdb_ids:
        return []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [_fetch_one(client, pid, semaphore) for pid in pdb_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        details = [r for r in results if isinstance(r, StructureDetail)]
        try:
            await _enrich_common_names(client, details)
        except Exception:  # noqa: BLE001 - 이름 해석 실패는 fatal이 아님
            pass
    return details


__all__ = ["fetch_structure_detail"]
