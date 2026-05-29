"""OpenTargets Platform GraphQL 클라이언트.

연구원이 "이 타깃, 어떤 질환에 연관되어 있고 임상에 들어간 약물이 있나?" 를
물을 때 Claude가 종합하지 않도록, OpenTargets의 종합 점수와 known drugs를
원본 그대로 가져온다.

API: POST https://api.platform.opentargets.org/api/v4/graphql
무인증, 무제한 비공식 (rate-limit 권장 사항만 있음).
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import DiseaseAssociation, KnownDrug, TargetIntelligence

OPENTARGETS_URL = "https://api.platform.opentargets.org/api/v4/graphql"

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)

# Gene symbol → Ensembl gene ID
_SEARCH_QUERY = """
query SearchTarget($q: String!) {
  search(queryString: $q, entityNames: ["target"], page: { index: 0, size: 1 }) {
    hits {
      id
      name
      entity
      object {
        ... on Target {
          id
          approvedSymbol
          approvedName
          biotype
          proteinIds { id source }
        }
      }
    }
  }
}
"""

# Ensembl gene ID → 질환 연관 + 임상/승인 약물 후보
# 주의: OpenTargets v4 스키마 변경 — `knownDrugs` 가 사라지고
# `drugAndClinicalCandidates` 로 통합되었다. 약물별 mechanism 은 nested 로 가져온다.
_TARGET_QUERY = """
query TargetIntel($id: String!) {
  target(ensemblId: $id) {
    id
    approvedSymbol
    approvedName
    biotype
    proteinIds { id source }
    associatedDiseases(page: { index: 0, size: 20 }) {
      count
      rows {
        score
        disease {
          id
          name
          therapeuticAreas { id name }
        }
      }
    }
    drugAndClinicalCandidates {
      count
      rows {
        id
        maxClinicalStage
        drug {
          id
          name
          drugType
          mechanismsOfAction {
            rows { mechanismOfAction actionType }
          }
        }
        diseases {
          disease { id name }
          diseaseFromSource
        }
      }
    }
  }
}
"""

# OpenTargets v4: maxClinicalStage 는 "PHASE_4" 같은 문자열로 내려옴.
_PHASE_MAP = {
    "PHASE_4": 4,
    "PHASE_3": 3,
    "PHASE_2": 2,
    "PHASE_1": 1,
    "PRECLINICAL": 0,
    "EARLY_PHASE_1": 1,
}


def _phase_to_int(value) -> int | None:
    """OpenTargets maxClinicalStage 문자열을 0~4 정수로 변환."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    key = str(value).strip().upper().replace(" ", "_")
    return _PHASE_MAP.get(key)


class OpenTargetsAPIError(RuntimeError):
    """OpenTargets GraphQL API 일시 장애를 나타내는 예외.

    "데이터 미수록(검색 결과 없음)"과 "API 장애(타임아웃/5xx/GraphQL 오류)"를 구분한다.
    """


async def _post_graphql(
    client: httpx.AsyncClient, query: str, variables: dict
) -> dict | None:
    """OpenTargets GraphQL POST 요청.

    Returns:
        dict: 정상 응답의 data 필드.
        None: data가 비어 있는 경우 (정상 케이스: 검색 결과 없음).

    Raises:
        OpenTargetsAPIError: API 일시 장애(타임아웃, 5xx, GraphQL errors, 파싱 실패).
    """
    try:
        async with _semaphore:
            resp = await client.post(
                OPENTARGETS_URL,
                json={"query": query, "variables": variables},
            )
        if resp.status_code != 200:
            raise OpenTargetsAPIError(
                f"OpenTargets GraphQL이 HTTP {resp.status_code}를 반환했습니다."
            )
        body = resp.json()
        if body.get("errors"):
            # GraphQL이 오류를 반환한 경우 — 스키마 변경 또는 일시 장애
            raise OpenTargetsAPIError(
                f"OpenTargets GraphQL이 오류를 반환했습니다: {body['errors']}"
            )
        return body.get("data") or None
    except httpx.TimeoutException as exc:
        raise OpenTargetsAPIError("OpenTargets GraphQL 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise OpenTargetsAPIError(f"OpenTargets GraphQL 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise OpenTargetsAPIError(f"OpenTargets GraphQL 응답 파싱 실패: {exc}") from exc


def _uniprot_from_protein_ids(protein_ids: list[dict] | None) -> str | None:
    """proteinIds 배열에서 UniProt 정식 ID(보통 'uniprot_swissprot' source)를 찾는다."""
    if not protein_ids:
        return None
    for entry in protein_ids:
        source = (entry.get("source") or "").lower()
        if "uniprot" in source:
            return entry.get("id")
    return None


async def fetch_target_intelligence(
    target_query: str,
    *,
    max_diseases: int = 15,
    max_drugs: int = 15,
) -> TargetIntelligence | None:
    """gene symbol(또는 Ensembl ID)로 OpenTargets 타깃 인텔리전스를 가져온다.

    Returns:
        TargetIntelligence: 정상 결과.
        None: 검색 결과 없음(미수록).

    Raises:
        OpenTargetsAPIError: API 일시 장애. 호출자가 catch해서 사용자에게 명시.
    """
    q = (target_query or "").strip()
    if not q:
        return None

    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        # 1) Search → Ensembl ID
        search_data = await _post_graphql(client, _SEARCH_QUERY, {"q": q})
        if not search_data:
            return None
        hits = ((search_data.get("search") or {}).get("hits")) or []
        if not hits:
            return None
        hit = hits[0]
        obj = hit.get("object") or {}
        ensembl_id = obj.get("id") or hit.get("id")
        if not ensembl_id:
            return None

        # 2) Target detail
        detail_data = await _post_graphql(
            client, _TARGET_QUERY, {"id": ensembl_id}
        )
        if not detail_data:
            return None
        target = detail_data.get("target") or {}

    diseases: list[DiseaseAssociation] = []
    assoc = target.get("associatedDiseases") or {}
    for row in assoc.get("rows") or []:
        disease = row.get("disease") or {}
        ta_names = [
            ta.get("name") for ta in (disease.get("therapeuticAreas") or [])
            if ta.get("name")
        ]
        diseases.append(
            DiseaseAssociation(
                disease_id=disease.get("id") or "",
                disease_name=disease.get("name") or "",
                overall_score=row.get("score"),
                therapeutic_areas=ta_names,
            )
        )
        if len(diseases) >= max_diseases:
            break

    drugs: list[KnownDrug] = []
    candidates = target.get("drugAndClinicalCandidates") or {}
    seen_keys: set[tuple[str | None, str | None]] = set()
    for row in candidates.get("rows") or []:
        drug = row.get("drug") or {}
        drug_id = drug.get("id")
        drug_name = drug.get("name") or ""
        if not drug_name:
            continue

        # 대표 적응증 — diseases[*].disease.name 중 첫 non-null
        indication: str | None = None
        for d in row.get("diseases") or []:
            disease = (d or {}).get("disease") or {}
            if disease.get("name"):
                indication = disease["name"]
                break
        if indication is None:
            for d in row.get("diseases") or []:
                src = (d or {}).get("diseaseFromSource")
                if src:
                    indication = src
                    break

        # 대표 mechanism — 첫 row의 mechanismOfAction / actionType
        mech_text: str | None = None
        action_type: str | None = None
        mech_block = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        if mech_block:
            mech_text = mech_block[0].get("mechanismOfAction")
            action_type = mech_block[0].get("actionType")

        key = (drug_id, indication)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        max_phase = _phase_to_int(row.get("maxClinicalStage"))

        drugs.append(
            KnownDrug(
                drug_id=drug_id,
                drug_name=drug_name,
                drug_type=drug.get("drugType"),
                mechanism_of_action=mech_text,
                action_type=action_type,
                max_phase_for_indication=max_phase,
                indication=indication,
                target_status=None,
            )
        )
        if len(drugs) >= max_drugs:
            break

    return TargetIntelligence(
        target_query=target_query,
        ensembl_id=ensembl_id,
        uniprot_accession=_uniprot_from_protein_ids(target.get("proteinIds")),
        gene_name=target.get("approvedSymbol"),
        biotype=target.get("biotype"),
        diseases=diseases,
        known_drugs=drugs,
        source_url=f"https://platform.opentargets.org/target/{ensembl_id}",
    )


__all__ = ["fetch_target_intelligence", "OpenTargetsAPIError"]
