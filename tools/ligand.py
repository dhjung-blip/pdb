"""PubChem + ChEMBL + IUPHAR/GtoPdb 통합 리간드 상세 조회.

연구원이 화합물 이름(또는 PDB chem code, ChEMBL ID, CHEMBL 코드)을 던질 때
Claude가 SMILES·MW·LogP·신약 phase를 추측하지 않도록 세 권위 있는 DB의
값을 그대로 가져와서 통합한다.

우선순위:
  1. PubChem: 가장 광범위, 화학 구조/물성에 강함
  2. ChEMBL : 신약 phase / 작용 메커니즘 / synonyms 풍부
  3. IUPHAR : GPCR/이온채널 약리 표준 ID (있을 때만 보강)

이 모듈은 외부 API 실패에 대해 graceful — 한 소스가 실패해도 다른 소스의
데이터로 응답을 채워 반환한다. 모두 실패하면 입력만 채운 LigandDetail 반환.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx

from models.schemas import LigandDetail

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
IUPHAR_BASE = "https://www.guidetopharmacology.org/services"

_TIMEOUT = httpx.Timeout(15.0)
_semaphore = asyncio.Semaphore(5)

PUBCHEM_PROPS = (
    "MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,"
    "CanonicalSMILES,IsomericSMILES,"
    "InChI,InChIKey,IUPACName,XLogP,HBondDonorCount,HBondAcceptorCount,"
    "TPSA,RotatableBondCount,Title"
)


# --------------------------------------------------------------------------
# PubChem
# --------------------------------------------------------------------------

async def _pubchem_cid_by_name(
    client: httpx.AsyncClient, name: str
) -> int | None:
    """이름 또는 PDB chem code → PubChem CID 한 개."""
    url = f"{PUBCHEM_BASE}/name/{quote(name)}/cids/JSON"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        cids = (resp.json().get("IdentifierList") or {}).get("CID") or []
        return int(cids[0]) if cids else None
    except (httpx.HTTPError, ValueError):
        return None


async def _pubchem_cid_by_inchikey(
    client: httpx.AsyncClient, inchikey: str
) -> int | None:
    """InChIKey → PubChem CID."""
    url = f"{PUBCHEM_BASE}/inchikey/{quote(inchikey)}/cids/JSON"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        cids = (resp.json().get("IdentifierList") or {}).get("CID") or []
        return int(cids[0]) if cids else None
    except (httpx.HTTPError, ValueError):
        return None


async def _pubchem_properties(
    client: httpx.AsyncClient, cid: int
) -> dict | None:
    """CID로 PubChem 화학 물성 + 대표명을 가져온다."""
    url = f"{PUBCHEM_BASE}/cid/{cid}/property/{PUBCHEM_PROPS}/JSON"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        props = (resp.json().get("PropertyTable") or {}).get("Properties") or []
        return props[0] if props else None
    except (httpx.HTTPError, ValueError):
        return None


async def _pubchem_synonyms(
    client: httpx.AsyncClient, cid: int, limit: int = 20
) -> list[str]:
    """CID로 PubChem synonyms 상위 N개."""
    url = f"{PUBCHEM_BASE}/cid/{cid}/synonyms/JSON"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return []
        info = (resp.json().get("InformationList") or {}).get("Information") or []
        if not info:
            return []
        return info[0].get("Synonym", [])[:limit]
    except (httpx.HTTPError, ValueError):
        return []


def _fill_from_pubchem(detail: LigandDetail, props: dict, synonyms: list[str]) -> None:
    """PubChem property dict → LigandDetail 필드 채우기."""
    if not detail.common_name and props.get("Title"):
        detail.common_name = props.get("Title")
    detail.canonical_smiles = (
        detail.canonical_smiles
        or props.get("ConnectivitySMILES")
        or props.get("CanonicalSMILES")
    )
    detail.smiles = (
        detail.smiles
        or props.get("SMILES")
        or props.get("IsomericSMILES")
        or props.get("ConnectivitySMILES")
        or props.get("CanonicalSMILES")
    )
    detail.inchi = detail.inchi or props.get("InChI")
    detail.inchi_key = detail.inchi_key or props.get("InChIKey")
    detail.iupac_name = detail.iupac_name or props.get("IUPACName")
    detail.molecular_formula = detail.molecular_formula or props.get("MolecularFormula")
    if detail.molecular_weight is None:
        mw = props.get("MolecularWeight")
        try:
            detail.molecular_weight = float(mw) if mw is not None else None
        except (TypeError, ValueError):
            pass
    if detail.xlogp is None:
        try:
            detail.xlogp = float(props["XLogP"]) if props.get("XLogP") is not None else None
        except (TypeError, ValueError):
            pass
    if detail.h_bond_donors is None:
        donors = props.get("HBondDonorCount")
        detail.h_bond_donors = int(donors) if donors is not None else None
    if detail.h_bond_acceptors is None:
        acc = props.get("HBondAcceptorCount")
        detail.h_bond_acceptors = int(acc) if acc is not None else None
    if detail.tpsa is None:
        try:
            detail.tpsa = float(props["TPSA"]) if props.get("TPSA") is not None else None
        except (TypeError, ValueError):
            pass
    if detail.rotatable_bonds is None:
        rb = props.get("RotatableBondCount")
        detail.rotatable_bonds = int(rb) if rb is not None else None
    if synonyms:
        existing = {s.lower() for s in detail.synonyms}
        for s in synonyms:
            if s.lower() not in existing:
                detail.synonyms.append(s)
                existing.add(s.lower())


# --------------------------------------------------------------------------
# ChEMBL
# --------------------------------------------------------------------------

async def _chembl_search_by_name(
    client: httpx.AsyncClient, name: str
) -> dict | None:
    """이름으로 ChEMBL molecule 검색 → 첫 결과."""
    url = f"{CHEMBL_BASE}/molecule/search.json"
    try:
        async with _semaphore:
            resp = await client.get(url, params={"q": name, "limit": 1})
        if resp.status_code != 200:
            return None
        molecules = resp.json().get("molecules") or []
        return molecules[0] if molecules else None
    except (httpx.HTTPError, ValueError):
        return None


async def _chembl_by_chembl_id(
    client: httpx.AsyncClient, chembl_id: str
) -> dict | None:
    """ChEMBL ID로 직접 조회."""
    url = f"{CHEMBL_BASE}/molecule/{chembl_id}.json"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


async def _chembl_by_inchikey(
    client: httpx.AsyncClient, inchikey: str
) -> dict | None:
    """InChIKey로 ChEMBL molecule 조회."""
    url = f"{CHEMBL_BASE}/molecule.json"
    try:
        async with _semaphore:
            resp = await client.get(
                url, params={"molecule_structures__standard_inchi_key": inchikey, "limit": 1}
            )
        if resp.status_code != 200:
            return None
        molecules = resp.json().get("molecules") or []
        return molecules[0] if molecules else None
    except (httpx.HTTPError, ValueError):
        return None


def _fill_from_chembl(detail: LigandDetail, mol: dict) -> None:
    """ChEMBL molecule dict → LigandDetail 보강."""
    detail.chembl_id = detail.chembl_id or mol.get("molecule_chembl_id")
    if not detail.common_name and mol.get("pref_name"):
        detail.common_name = mol["pref_name"]

    structures = mol.get("molecule_structures") or {}
    detail.canonical_smiles = detail.canonical_smiles or structures.get("canonical_smiles")
    detail.smiles = detail.smiles or structures.get("canonical_smiles")
    detail.inchi = detail.inchi or structures.get("standard_inchi")
    detail.inchi_key = detail.inchi_key or structures.get("standard_inchi_key")

    properties = mol.get("molecule_properties") or {}
    if detail.molecular_weight is None:
        try:
            mw = properties.get("full_mwt")
            detail.molecular_weight = float(mw) if mw is not None else None
        except (TypeError, ValueError):
            pass
    if detail.xlogp is None:
        try:
            alogp = properties.get("alogp")
            detail.xlogp = float(alogp) if alogp is not None else None
        except (TypeError, ValueError):
            pass
    if detail.h_bond_donors is None:
        d = properties.get("hbd")
        detail.h_bond_donors = int(d) if d is not None else None
    if detail.h_bond_acceptors is None:
        a = properties.get("hba")
        detail.h_bond_acceptors = int(a) if a is not None else None
    if detail.tpsa is None:
        try:
            t = properties.get("psa")
            detail.tpsa = float(t) if t is not None else None
        except (TypeError, ValueError):
            pass
    if detail.rotatable_bonds is None:
        r = properties.get("rtb")
        detail.rotatable_bonds = int(r) if r is not None else None
    if detail.molecular_formula is None:
        detail.molecular_formula = properties.get("full_molformula")

    if detail.max_phase is None:
        # max_phase 는 float (예: 4.0) 또는 정수
        try:
            mp = mol.get("max_phase")
            detail.max_phase = int(float(mp)) if mp is not None else None
        except (TypeError, ValueError):
            pass
    detail.drug_type = detail.drug_type or mol.get("molecule_type")
    detail.indication_class = detail.indication_class or mol.get("indication_class")

    syn_list = mol.get("molecule_synonyms") or []
    if syn_list:
        existing = {s.lower() for s in detail.synonyms}
        for syn in syn_list:
            name = syn.get("molecule_synonym") if isinstance(syn, dict) else None
            if name and name.lower() not in existing:
                detail.synonyms.append(name)
                existing.add(name.lower())


# --------------------------------------------------------------------------
# IUPHAR / GtoPdb
# --------------------------------------------------------------------------

async def _iuphar_search(client: httpx.AsyncClient, name: str) -> dict | None:
    """IUPHAR ligand 검색 (이름) → 첫 결과 ligand dict.

    404/빈 결과 → None (정상: 미수록).
    그 외 HTTP 오류/타임아웃/파싱 실패 → 예외를 그대로 raise해서 호출자가
    notes에 기록하도록 한다 (silent fail 방지).
    """
    url = f"{IUPHAR_BASE}/ligands"
    async with _semaphore:
        resp = await client.get(url, params={"name": name})
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise httpx.HTTPError(
            f"IUPHAR /ligands가 HTTP {resp.status_code}를 반환했습니다."
        )
    data = resp.json()
    if not data:
        return None
    # 정확히 일치하는 이름이 있으면 우선
    lower = name.lower()
    for item in data:
        if (item.get("name") or "").lower() == lower:
            return item
    return data[0]


def _fill_from_iuphar(detail: LigandDetail, ligand: dict) -> None:
    """IUPHAR ligand dict → LigandDetail 보강 (보조적)."""
    lig_id = ligand.get("ligandId") or ligand.get("LigandID")
    try:
        detail.iuphar_ligand_id = int(lig_id) if lig_id is not None else None
    except (TypeError, ValueError):
        pass
    if not detail.common_name and ligand.get("name"):
        detail.common_name = ligand["name"]
    if detail.smiles is None and ligand.get("smiles"):
        detail.smiles = ligand["smiles"]
    if detail.inchi is None and ligand.get("inchi"):
        detail.inchi = ligand["inchi"]
    if detail.inchi_key is None and ligand.get("inchiKey"):
        detail.inchi_key = ligand["inchiKey"]
    if detail.iupac_name is None and ligand.get("iupacName"):
        detail.iupac_name = ligand["iupacName"]


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------

_CHEMBL_RE = re.compile(r"^CHEMBL\d+$", re.IGNORECASE)
_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_PDB_CODE_RE = re.compile(r"^[A-Z0-9]{2,5}$")


async def fetch_ligand_detail(query: str) -> LigandDetail:
    """리간드 이름/코드/ChEMBL ID/InChIKey로 통합 상세 정보를 가져온다.

    절대 예외를 던지지 않는다 — 한 소스가 실패해도 부분 결과로 채워 반환.
    """
    q = (query or "").strip()
    if not q:
        return LigandDetail(query=query or "")

    detail = LigandDetail(query=q)
    detail.sources = {}

    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        # 1) ChEMBL ID 직접 입력
        if _CHEMBL_RE.match(q):
            mol = await _chembl_by_chembl_id(client, q.upper())
            if mol:
                _fill_from_chembl(detail, mol)
        # 2) InChIKey 직접 입력
        elif _INCHIKEY_RE.match(q):
            detail.inchi_key = q
            mol = await _chembl_by_inchikey(client, q)
            if mol:
                _fill_from_chembl(detail, mol)

        # 3) PubChem CID 검색
        cid: int | None = None
        if detail.inchi_key:
            cid = await _pubchem_cid_by_inchikey(client, detail.inchi_key)
        if cid is None:
            cid = await _pubchem_cid_by_name(client, q)
        if cid is not None:
            detail.pubchem_cid = cid
            props_task = _pubchem_properties(client, cid)
            syn_task = _pubchem_synonyms(client, cid)
            props, syns = await asyncio.gather(props_task, syn_task)
            if props:
                _fill_from_pubchem(detail, props, syns or [])
            elif syns:
                # property는 실패했지만 synonym은 가져온 경우
                _fill_from_pubchem(detail, {}, syns)

        # 4) ChEMBL — 아직 chembl_id가 없다면 이름/InChIKey로 보강
        if detail.chembl_id is None:
            mol = None
            if detail.inchi_key:
                mol = await _chembl_by_inchikey(client, detail.inchi_key)
            if mol is None:
                mol = await _chembl_search_by_name(client, q)
            if mol:
                _fill_from_chembl(detail, mol)

        # 5) IUPHAR — 이름으로 보강. 실패해도 다른 소스의 결과는 유지하되,
        #    장애 사실은 notes에 기록해 사용자가 "IUPHAR 데이터 없음"과 "IUPHAR 장애"를 구분할 수 있게 한다.
        try:
            iuphar = await _iuphar_search(client, detail.common_name or q)
            if iuphar:
                _fill_from_iuphar(detail, iuphar)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            detail.notes.append(
                f"IUPHAR 보강 일시 장애 — IUPHAR ID/약리 분류가 누락되었을 수 있습니다. "
                f"(사유: {exc})"
            )
        except ValueError as exc:
            detail.notes.append(
                f"IUPHAR 응답 파싱 실패 — 일부 약리 정보가 누락되었을 수 있습니다. (사유: {exc})"
            )

    if detail.common_name is None and detail.synonyms:
        # 마지막 fallback — 첫 synonym을 common name으로
        detail.common_name = detail.synonyms[0]

    if detail.pubchem_cid:
        detail.sources["PubChem"] = (
            f"https://pubchem.ncbi.nlm.nih.gov/compound/{detail.pubchem_cid}"
        )
    if detail.chembl_id:
        detail.sources["ChEMBL"] = (
            f"https://www.ebi.ac.uk/chembl/explore/compound/{detail.chembl_id}"
        )
    if detail.iuphar_ligand_id:
        detail.sources["IUPHAR/GtoPdb"] = (
            f"https://www.guidetopharmacology.org/GRAC/LigandDisplayForward?ligandId={detail.iuphar_ligand_id}"
        )

    return detail


__all__ = ["fetch_ligand_detail"]
