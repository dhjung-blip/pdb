"""PDB 결합부위 잔기 추출 — RCSB rcsb_target_neighbors 기반.

연구원이 "이 구조의 결합 포켓 핵심 잔기가 뭐야?"를 물을 때 Claude가 추측하지 않도록
RCSB의 리간드-주변 단백질 잔기(target neighbors)를 그대로 가져온다.

※ 이전 구현은 PDBe `binding_sites` API를 썼으나 해당 엔드포인트가 폐기되어(2026-06 기준
   /api/·/graph-api/ 모두 HTTP 404) 동작하지 않았다. RCSB GraphQL의
   nonpolymer_entity_instance.rcsb_target_neighbors(리간드 원자 주변 폴리머 잔기)로 재배선했다.
   같은 RCSB GraphQL 엔드포인트에서 리간드 이름(pdbx_entity_nonpoly.name)도 함께 가져온다.
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import BindingSite, BindingSiteResidue, BindingSiteResult

RCSB_GRAPHQL_URL = "https://data.rcsb.org/graphql"

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)


class BindingSiteAPIError(RuntimeError):
    """결합부위 보조 API(RCSB GraphQL)의 일시 장애를 나타내는 예외.

    "결합 리간드 없음(apo/용매뿐)"과 "API 장애(타임아웃/5xx)"를 구분하기 위함이다.
    """


_RCSB_QUERY = """
query GetBindingSites($id: String!) {
  entry(entry_id: $id) {
    nonpolymer_entities {
      pdbx_entity_nonpoly { comp_id name }
      nonpolymer_entity_instances {
        rcsb_nonpolymer_entity_instance_container_identifiers {
          auth_asym_id
          auth_seq_id
          comp_id
        }
        rcsb_target_neighbors {
          target_asym_id
          target_auth_seq_id
          target_comp_id
          target_seq_id
          distance
        }
      }
    }
  }
}
"""

# 결합부위 분석에 무의미한 화학성분: 물·이온·결정화 첨가물·PEG·LCP 지질/콜레스테롤/세제.
# (GPCR 구조는 LCP 지질 CLR/OLC 등이 다수 — 약물 포켓이 아니므로 기본 제외)
_NOT_INTERESTING_LIGANDS = {
    # 물·이온
    "HOH", "WAT", "DOD",
    "CL", "NA", "MG", "ZN", "CA", "K", "FE", "MN", "NI", "CD", "CO", "CU", "BR", "IOD",
    # 결정화 첨가물·극저온보호제·완충 이온
    "EDO", "GOL", "MPD", "FMT", "ACT", "ACE", "DMS", "TRS", "EPE", "MES", "BME", "IMD",
    "SO4", "PO4", "NO3", "ACY", "CIT", "TLA", "FLC",
    # PEG 및 변종
    "PEG", "PG4", "1PE", "2PE", "PGE", "PG0", "P6G", "7PE", "PE4", "XPE", "PGO",
    # LCP 지질·콜레스테롤·지방산·세제 (GPCR/막단백질 구조)
    "CLR", "Y01", "OLA", "OLB", "OLC", "PLM", "MYR", "STE", "PX4", "LMT", "LMN", "LDA", "D10",
}

# target neighbor에서 "잔기"로 셀 수 없는 것(물 등). 이온/보조인자는 실제 접촉이므로 남긴다.
_NON_RESIDUE_NEIGHBORS = {"HOH", "WAT", "DOD"}


def _residue_from_rcsb_neighbor(tn: dict) -> BindingSiteResidue | None:
    """rcsb_target_neighbors 한 항목 → BindingSiteResidue (물/누락이면 None)."""
    comp = (tn.get("target_comp_id") or "").strip()
    chain = tn.get("target_asym_id")
    num = tn.get("target_auth_seq_id")
    if not comp or comp.upper() in _NON_RESIDUE_NEIGHBORS or chain is None or num is None:
        return None
    try:
        num_int = int(num)
    except (TypeError, ValueError):
        return None
    label = tn.get("target_seq_id")
    try:
        label_int = int(label) if label is not None else None
    except (TypeError, ValueError):
        label_int = None
    return BindingSiteResidue(
        chain_id=str(chain),
        residue_number=num_int,
        residue_name=comp.upper(),
        label_seq_id=label_int,
    )


def _build_sites(data: dict, pdb_id: str) -> list[BindingSite]:
    """RCSB GraphQL 응답 → BindingSite 리스트(리간드 인스턴스별 1개)."""
    entry = (data.get("data") or {}).get("entry") or {}
    nonpolys = entry.get("nonpolymer_entities") or []
    sites: list[BindingSite] = []
    for ne in nonpolys:
        nonpoly_info = ne.get("pdbx_entity_nonpoly") or {}
        ligand_name = nonpoly_info.get("name")
        for inst in ne.get("nonpolymer_entity_instances") or []:
            ci = inst.get("rcsb_nonpolymer_entity_instance_container_identifiers") or {}
            ligand_code = ci.get("comp_id") or nonpoly_info.get("comp_id")
            ligand_chain = ci.get("auth_asym_id")

            # target neighbors → 잔기 (중복 제거: 여러 원자가 같은 잔기를 가리킴)
            seen: set[tuple[str, int, str]] = set()
            residues: list[BindingSiteResidue] = []
            for tn in inst.get("rcsb_target_neighbors") or []:
                r = _residue_from_rcsb_neighbor(tn)
                if r is None:
                    continue
                key = (r.chain_id, r.residue_number, r.residue_name)
                if key in seen:
                    continue
                seen.add(key)
                residues.append(r)
            residues.sort(key=lambda r: (r.chain_id, r.residue_number))

            sites.append(
                BindingSite(
                    pdb_id=pdb_id.upper(),
                    site_id=str(ligand_chain) if ligand_chain else None,
                    ligand_code=str(ligand_code) if ligand_code else None,
                    ligand_name=ligand_name,
                    chain_id=str(ligand_chain) if ligand_chain else None,
                    residues=residues,
                    source="RCSB",
                    source_url=f"https://www.rcsb.org/structure/{pdb_id.upper()}",
                )
            )
    return sites


async def _fetch_rcsb(client: httpx.AsyncClient, pdb_id: str) -> dict:
    """RCSB GraphQL 호출. API 장애 시 BindingSiteAPIError."""
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
    if data.get("errors"):
        raise BindingSiteAPIError(f"RCSB GraphQL 오류: {data['errors']}")
    return data


async def fetch_binding_sites(
    pdb_id: str,
    *,
    ligand_filter: str | None = None,
    skip_solvents: bool = True,
) -> BindingSiteResult:
    """PDB 구조의 결합부위(리간드별 주변 잔기 목록) 묶음을 반환한다.

    - `ligand_filter`가 주어지면 그 PDB chem code의 결합부위만 반환.
    - `skip_solvents=True`이면 물·이온·결정화 첨가물·LCP 지질 결합부위는 제외(기본).
    이 함수는 예외를 던지지 않는다 — 실패 시 빈 결과 + notes 포함.
    """
    pdb_id = (pdb_id or "").strip().upper()
    notes: list[str] = []
    if not pdb_id or len(pdb_id) != 4:
        return BindingSiteResult(
            pdb_id=pdb_id, sites=[], notes=["올바른 4자리 PDB ID가 필요합니다."]
        )

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        try:
            data = await _fetch_rcsb(client, pdb_id)
        except BindingSiteAPIError as exc:
            notes.append(
                f"⚠️ RCSB API 일시 장애로 {pdb_id}의 결합부위 데이터를 조회하지 못했습니다. "
                f"잠시 후 다시 시도해주세요. (사유: {exc}) "
                f"— AI 모델은 임의의 결합부위 잔기를 생성하지 마십시오."
            )
            return BindingSiteResult(pdb_id=pdb_id, sites=[], notes=notes)

    sites = _build_sites(data, pdb_id)

    if not sites:
        notes.append(
            f"{pdb_id}에는 결합 리간드(비고분자)가 없습니다 — apo 구조이거나 용매만 포함된 구조일 수 있습니다."
        )
        return BindingSiteResult(pdb_id=pdb_id, sites=[], notes=notes)

    # 필터: 용매/이온/지질
    if skip_solvents:
        sites = [
            s for s in sites
            if not s.ligand_code or s.ligand_code.upper() not in _NOT_INTERESTING_LIGANDS
        ]
        if not sites:
            notes.append(
                f"{pdb_id}의 결합 분자가 모두 용매/이온/지질이었습니다 "
                f"(--include-solvents로 포함 조회 가능)."
            )
    # 필터: 특정 리간드
    if ligand_filter:
        target = ligand_filter.strip().upper()
        sites = [s for s in sites if (s.ligand_code or "").upper() == target]
        if not sites:
            notes.append(f"리간드 '{ligand_filter}' 의 결합부위를 찾지 못했습니다.")

    # 잔기 0개인 site(접촉 정보 없음) 안내
    empty = [s.ligand_code for s in sites if not s.residues]
    if empty:
        notes.append(
            f"다음 리간드는 주변 잔기 정보가 없습니다(RCSB target neighbors 미제공): {', '.join(c for c in empty if c)}"
        )

    return BindingSiteResult(pdb_id=pdb_id, sites=sites, notes=notes)


__all__ = ["fetch_binding_sites", "BindingSiteAPIError"]
