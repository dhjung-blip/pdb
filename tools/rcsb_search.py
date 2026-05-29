"""RCSB Search API 클라이언트.

UniProt cross-reference는 RCSB 신규 등록 후 며칠~수주 동기화 지연이 있다.
이 모듈은 RCSB Search API를 직접 호출하여 UniProt accession에 매핑된
모든 실험 구조 PDB ID를 즉시 가져온다. `tools/uniprot.py`의 결과와
union 처리하면 동기화 지연 구간의 신규 구조도 결과에 포함된다.
"""

from __future__ import annotations

import httpx

RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"

# 단일 단백질에 대한 PDB 등록 수는 보통 < 1000. EGFR(P00533)이 ~350, HIV protease ~700.
# 1만은 안전 마진으로 충분하다.
_DEFAULT_PAGE_ROWS = 10000

# httpx 공통 타임아웃 — UniProt(30s)보다 짧게: Search는 보통 1~2초 안에 응답.
_TIMEOUT = httpx.Timeout(20.0)

_USER_AGENT = "pdb-mcp-server/0.1 (https://github.com/anthropic/pdb-mcp-server)"


class RCSBSearchError(ValueError):
    """RCSB Search 일시 장애 — 상위에서 잡아 graceful degrade한다."""


def _build_query(accession: str, rows: int = _DEFAULT_PAGE_ROWS) -> dict:
    """UniProt accession을 가진 모든 experimental entry를 찾는 쿼리 본문.

    `rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers
    .database_accession` 속성은 polymer entity가 매핑된 외부 서열 DB의
    accession을 가리킨다. UniProt 매핑이 있는 모든 entry를 즉시 잡는다.
    """
    return {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": (
                    "rcsb_polymer_entity_container_identifiers"
                    ".reference_sequence_identifiers.database_accession"
                ),
                "operator": "exact_match",
                "value": accession,
            },
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": rows},
            "results_content_type": ["experimental"],
        },
    }


def _parse_response(data: dict) -> list[str]:
    """RCSB Search 응답에서 PDB ID 목록을 추출한다 (대문자 정규화)."""
    result_set = data.get("result_set") or []
    pdb_ids: list[str] = []
    for item in result_set:
        if isinstance(item, dict):
            ident = item.get("identifier")
        elif isinstance(item, str):
            ident = item
        else:
            ident = None
        if ident:
            pdb_ids.append(str(ident).upper())
    return pdb_ids


async def search_pdb_ids_by_uniprot(
    accession: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """UniProt accession에 매핑된 모든 실험 구조 PDB ID를 반환한다.

    동작:
    - 200 + result_set 채워짐 → PDB ID 리스트
    - 200 + result_set 비어있음 → 빈 리스트
    - 204 No Content (RCSB가 빈 결과에 종종 반환) → 빈 리스트
    - 404 → 빈 리스트
    - 5xx / 타임아웃 / 연결 실패 → RCSBSearchError

    Args:
        accession: UniProt accession (예: "P28223")
        client: 외부에서 주입한 AsyncClient (있으면 재사용, 없으면 임시 생성).

    Raises:
        RCSBSearchError: 일시 장애 — 상위에서 잡아 UniProt-only로 강등.
    """
    accession = (accession or "").strip()
    if not accession:
        return []

    body = _build_query(accession)
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

    own_client = client is None
    try:
        if own_client:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as own:
                resp = await own.post(
                    RCSB_SEARCH_URL, json=body, headers=headers
                )
        else:
            resp = await client.post(
                RCSB_SEARCH_URL, json=body, headers=headers, timeout=_TIMEOUT
            )
    except httpx.TimeoutException as exc:
        raise RCSBSearchError(
            "RCSB Search API 응답이 지연되고 있습니다."
        ) from exc
    except httpx.HTTPError as exc:
        raise RCSBSearchError(
            "RCSB Search API 연결에 실패했습니다."
        ) from exc

    # 빈 결과를 알리는 상태 코드들은 정상 처리.
    if resp.status_code in (204, 404):
        return []
    if 500 <= resp.status_code < 600:
        raise RCSBSearchError(
            f"RCSB Search API 일시 장애 (HTTP {resp.status_code})."
        )
    if resp.status_code != 200:
        # 400 등 잘못된 요청은 호출자에게 알리지 않고 빈 리스트로 강등.
        # (예: accession 형식이 비표준 — 안전한 fallback)
        return []

    try:
        data = resp.json()
    except ValueError as exc:
        raise RCSBSearchError(
            "RCSB Search API 응답을 파싱할 수 없습니다."
        ) from exc

    return _parse_response(data)
