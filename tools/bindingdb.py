"""BindingDB REST API 클라이언트 — UniProt accession 기준 결합 활성 보강.

ChEMBL/IUPHAR가 놓치는 화합물(특허·문헌 유래 Ki/Kd/IC50)을 BindingDB에서 추가로
수집해 '수집 완전성'을 높인다. 대형 타깃(EGFR 등)은 서버가 504로 응답하므로
graceful하게 생략하고 호출자에 알린다 (BindingDBUnavailable).

API:
  https://bindingdb.org/rest/getLigandsByUniprot?uniprot=<ACC>;<cutoff_nM>&response=application/json
응답(래퍼 키 이름에 의존하지 않고 bdb.affinities 리스트를 탐색):
  { ... "bdb.affinities": [
      {"bdb.monomerid": 2579, "bdb.smile": "C...",
       "bdb.affinity_type": "IC50"|"Ki"|"Kd"|"EC50", "bdb.affinity": " 1.000000"} ... ]}
  - bdb.affinity 는 nM 단위(문자열, ">10000" 같은 relation 포함 가능).
  - 화합물명은 제공되지 않음 → SMILES로 식별(ligand_name=None, smiles=...).
"""

from __future__ import annotations

import math
import re

import httpx

from models.schemas import Bioactivity

BINDINGDB_REST = "https://bindingdb.org/rest"
_MONOMER_URL = (
    "https://www.bindingdb.org/rwd/bind/chemsearch/marvin/"
    "MolStructure.jsp?monomerid={}"
)

# 관계(>, <, =) + 값. BindingDB affinity는 보통 깔끔한 숫자지만 ">10000"(비활성) 형태도 있다.
_BDB_AFF_RE = re.compile(r"^\s*(<|>|<=|>=|=)?\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$")


class BindingDBUnavailable(RuntimeError):
    """BindingDB 일시 장애/타임아웃(504 등) — 대형 타깃에서 흔함. 호출자가 note 처리."""


def _parse_affinity(raw: str) -> tuple[str | None, float | None]:
    """BindingDB affinity 문자열 → (relation, nM 값). 파싱 실패 시 (None, None)."""
    m = _BDB_AFF_RE.match(raw or "")
    if not m:
        return None, None
    rel = m.group(1) or "="
    try:
        return rel, float(m.group(2))
    except ValueError:
        return None, None


def _pchembl_from_nm(nm: float | None) -> float | None:
    """nM → pChEMBL(-log10 M). 0 이하/None은 None.  (1nM→9, 1µM→6, 10µM→5)"""
    if nm is None or nm <= 0:
        return None
    try:
        return round(9.0 - math.log10(nm), 2)
    except (ValueError, OverflowError):
        return None


def _find_affinities(obj: object) -> list:
    """응답 래퍼 키 이름에 의존하지 않고 'bdb.affinities' 리스트를 재귀 탐색한다.

    단일 결과일 때 dict로 내려오면 [dict]로 정규화한다.
    """
    if isinstance(obj, dict):
        v = obj.get("bdb.affinities")
        if v is not None:
            return v if isinstance(v, list) else [v]
        for sub in obj.values():
            found = _find_affinities(sub)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_affinities(it)
            if found:
                return found
    return []


def _affinity_to_model(item: dict) -> Bioactivity | None:
    """bdb.affinities 항목 → Bioactivity. 수치 파싱 불가 시 None(드롭)."""
    if not isinstance(item, dict):
        return None
    rel, nm = _parse_affinity(str(item.get("bdb.affinity", "")))
    if nm is None:
        return None
    monomer = item.get("bdb.monomerid")
    return Bioactivity(
        ligand_name=None,  # BindingDB는 화합물명을 주지 않음 → SMILES로 식별
        smiles=item.get("bdb.smile") or None,
        standard_type=item.get("bdb.affinity_type") or None,
        standard_relation=rel,
        standard_value=nm,
        standard_units="nM",
        pchembl_value=_pchembl_from_nm(nm),
        source="BindingDB",
        source_url=_MONOMER_URL.format(monomer) if monomer is not None else None,
    )


async def fetch_bindingdb_by_uniprot(
    client: httpx.AsyncClient,
    accession: str,
    *,
    cutoff_nm: int = 10000,
    timeout: float = 60.0,
) -> list[Bioactivity]:
    """UniProt accession의 BindingDB 활성을 가져온다.

    Args:
        cutoff_nm: 이 값(nM) 이하(=더 강한 결합)만 반환. 클수록 더 약한 화합물까지 포함.
        timeout: 대형 타깃은 서버 계산이 길어 넉넉히 둔다.

    Raises:
        BindingDBUnavailable: 504/타임아웃/5xx(대형 타깃) — 호출자가 graceful 처리.

    Returns:
        Bioactivity 리스트. 빈 데이터/404 → [].
    """
    acc = (accession or "").strip().upper()
    if not acc:
        return []
    url = f"{BINDINGDB_REST}/getLigandsByUniprot"
    params = {"uniprot": f"{acc};{int(cutoff_nm)}", "response": "application/json"}
    try:
        resp = await client.get(url, params=params, timeout=httpx.Timeout(timeout))
    except httpx.HTTPError as exc:
        detail = str(exc) or type(exc).__name__
        raise BindingDBUnavailable(f"BindingDB 연결 실패/타임아웃({detail})") from exc

    if resp.status_code in (500, 502, 503, 504):
        raise BindingDBUnavailable(
            f"BindingDB가 HTTP {resp.status_code} 반환 — 대용량 타깃일 수 있습니다(생략)."
        )
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise BindingDBUnavailable(f"BindingDB HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise BindingDBUnavailable(f"BindingDB 응답 파싱 실패: {exc}") from exc

    out: list[Bioactivity] = []
    for item in _find_affinities(data):
        bio = _affinity_to_model(item)
        if bio is not None:
            out.append(bio)
    return out


__all__ = ["fetch_bindingdb_by_uniprot", "BindingDBUnavailable"]
