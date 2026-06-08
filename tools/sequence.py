"""UniProt 단백질 서열 + feature + natural variant 상세 조회.

연구원이 "HTR2A의 222번 잔기가 Ser 이지?", "L858R 변이가 알려진 변이인가?"
류의 질문을 할 때 Claude가 추측하지 않도록 권위 있는 UniProt 원문을 그대로
가져와서 답한다.

UniProt가 PDB ID 목록은 이미 `tools/uniprot.py` 에서 다루고 있으므로, 이 모듈은
서열·feature·variant 만을 다루는 별도 모듈로 분리한다.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from models.schemas import NaturalVariant, SequenceFeature, SequenceRegion, VariantList

UNIPROT_ENTRY_URL = "https://rest.uniprot.org/uniprotkb/{accession}.json"
UNIPROT_VARIATION_URL = (
    "https://www.ebi.ac.uk/proteins/api/variation/{accession}?format=json"
)

_TIMEOUT = httpx.Timeout(30.0)
_semaphore = asyncio.Semaphore(5)


class SequenceError(ValueError):
    """단백질 서열/feature 조회 관련 사용자 대상 에러."""


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_location(loc: dict | None) -> tuple[int | None, int | None]:
    """UniProt feature.location → (start, end) 정수 튜플."""
    if not loc:
        return None, None
    start = _safe_int((loc.get("start") or {}).get("value"))
    end = _safe_int((loc.get("end") or {}).get("value"))
    if start is None and end is None:
        # 일부 single-position feature는 position만 있다
        pos = _safe_int((loc.get("position") or {}).get("value"))
        return pos, pos
    if end is None:
        end = start
    if start is None:
        start = end
    return start, end


def _parse_feature(feature: dict) -> SequenceFeature | None:
    """UniProt feature 객체 → SequenceFeature."""
    start, end = _parse_location(feature.get("location"))
    if start is None and end is None:
        return None

    description = feature.get("description")
    ligand_name: str | None = None
    if feature.get("type") == "Binding site":
        # ligand 정보가 별도 dict
        lig = feature.get("ligand")
        if isinstance(lig, dict):
            ligand_name = lig.get("name") or lig.get("ligand_id")

    evidence_codes: list[str] = []
    for ev in feature.get("evidences") or []:
        code = ev.get("evidenceCode")
        if code:
            evidence_codes.append(code)
    evidence = ", ".join(evidence_codes) if evidence_codes else None

    return SequenceFeature(
        type=feature.get("type") or "Unknown",
        description=description,
        start=start,
        end=end,
        ligand=ligand_name,
        evidence=evidence,
    )


def _entry_protein_name(entry: dict) -> str | None:
    rec = (entry.get("proteinDescription") or {}).get("recommendedName") or {}
    return (rec.get("fullName") or {}).get("value")


async def _fetch_entry(client: httpx.AsyncClient, accession: str) -> dict:
    """UniProt full entry JSON을 가져온다."""
    url = UNIPROT_ENTRY_URL.format(accession=accession)
    async with _semaphore:
        resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def fetch_sequence_region(
    accession: str,
    start: int | None = None,
    end: int | None = None,
    feature_types: list[str] | None = None,
) -> SequenceRegion:
    """UniProt 단백질의 지정 구간 서열 + 해당 구간과 겹치는 feature 목록을 반환.

    `start`/`end`가 None이면 전체 서열을 반환한다 (긴 단백질은 큰 응답이 될 수 있음).
    `feature_types`가 주어지면 그 타입만 필터링 (예: ["Binding site", "Active site"]).
    """
    accession = (accession or "").strip().upper()
    if not accession:
        raise SequenceError("UniProt Accession이 비어 있습니다.")

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            entry = await _fetch_entry(client, accession)
    except httpx.HTTPError as exc:
        raise SequenceError(
            "UniProt 서열 조회에 실패했습니다 — accession 표기를 확인하거나 잠시 후 다시 시도해주세요."
        ) from exc

    sequence_full = (entry.get("sequence") or {}).get("value") or ""
    full_length = len(sequence_full)
    if full_length == 0:
        raise SequenceError(
            f"'{accession}'에 대한 서열을 UniProt에서 받지 못했습니다."
        )

    # 구간 정규화 (1-based, inclusive)
    s = max(1, start) if start else 1
    e = min(full_length, end) if end else full_length
    if s > e:
        raise SequenceError(f"잘못된 구간입니다: start={start}, end={end}")

    region_seq = sequence_full[s - 1: e]
    full_returned = (s == 1 and e == full_length)

    # Feature 추출 — 사용자가 단축 코드(ACT_SITE/BINDING/TRANSMEM)를 줘도 현 UniProt
    # REST long-name("Active site" 등)과 매칭되도록 정규화한다.
    wanted_types = {_normalize_feature_type(t) for t in (feature_types or [])}
    features: list[SequenceFeature] = []
    for raw in entry.get("features") or []:
        feat = _parse_feature(raw)
        if not feat:
            continue
        if wanted_types and feat.type.lower() not in wanted_types:
            continue
        # 구간과 겹치는 것만 포함
        f_start = feat.start or 0
        f_end = feat.end or 0
        if f_end < s or f_start > e:
            continue
        features.append(feat)

    return SequenceRegion(
        accession=accession,
        entry_name=entry.get("uniProtkbId"),
        protein_name=_entry_protein_name(entry),
        full_length=full_length,
        start=s,
        end=e,
        sequence=region_seq,
        full_sequence_returned=full_returned,
        features=features,
        source_url=f"https://www.uniprot.org/uniprotkb/{accession}/entry",
    )


# UniProt 단축 코드(구 API/문서) → 현 REST API long-name. 사용자가 ACT_SITE 등을 줘도 매칭되게.
_FEATURE_TYPE_ALIASES = {
    "act_site": "active site",
    "binding": "binding site",
    "metal": "binding site",
    "ca_bind": "binding site",
    "np_bind": "binding site",
    "dna_bind": "dna binding",
    "transmem": "transmembrane",
    "intramem": "intramembrane",
    "topo_dom": "topological domain",
    "signal": "signal",
    "transit": "transit peptide",
    "propep": "propeptide",
    "carbohyd": "glycosylation",
    "disulfid": "disulfide bond",
    "mod_res": "modified residue",
    "lipid": "lipidation",
    "mutagen": "mutagenesis",
    "variant": "natural variant",
    "strand": "beta strand",
    "helix": "helix",
    "turn": "turn",
}


def _normalize_feature_type(t: str) -> str:
    """사용자 입력 feature 타입을 현 UniProt REST long-name(소문자)으로 정규화.

    단축 코드(ACT_SITE/BINDING/TRANSMEM 등)와 long-name("Active site") 양쪽을 받는다.
    """
    raw = t.strip().lower()
    key = raw.replace(" ", "_").replace("-", "_")
    if key in _FEATURE_TYPE_ALIASES:
        return _FEATURE_TYPE_ALIASES[key]
    return raw.replace("_", " ")


# --------------------------------------------------------------------------
# Natural variants (질환 연관 변이)
# --------------------------------------------------------------------------

_PROTEIN_VARIANT_DESC_RE = re.compile(
    # 질환명 직후가 ' (약어)' 형태여도 첫 질환을 온전히 잡도록 종료 앵커에 '\s*\(' 추가.
    # 예: "in EGFR-related lung cancer (EGFR); in ..." → "EGFR-related lung cancer"
    r"in\s+(?P<disease>[A-Z][A-Za-z0-9 ,\-]+?)(?:\s*\(|[;\.\)]|$)"
)


def _extract_disease(description: str | None) -> str | None:
    """UniProt 변이 설명에서 '... in DISEASE; ...' 패턴으로 질환을 추출한다."""
    if not description:
        return None
    m = _PROTEIN_VARIANT_DESC_RE.search(description)
    if not m:
        return None
    candidate = m.group("disease").strip()
    # 흔한 noise 제거 (예: "in cis", "in trans" 등)
    if candidate.lower() in {"cis", "trans"}:
        return None
    return candidate


def _parse_variation_feature(feature: dict) -> NaturalVariant | None:
    """Proteins API의 features 한 항목 → NaturalVariant."""
    if feature.get("type") not in {"VARIANT", "Variant", "variant"}:
        return None
    position = _safe_int(feature.get("begin") or feature.get("position"))
    if position is None:
        return None

    description = feature.get("description")
    if not description:
        # 일부 항목은 association 배열로 질환 정보를 준다
        assocs = feature.get("association") or []
        desc_parts = []
        for assoc in assocs:
            name = assoc.get("name")
            if name:
                desc_parts.append(f"in {name}")
        description = "; ".join(desc_parts) or None

    clinical = None
    sig = feature.get("clinicalSignificances") or []
    if sig and isinstance(sig, list):
        first = sig[0]
        if isinstance(first, dict):
            clinical = first.get("type")
        elif isinstance(first, str):
            clinical = first

    dbsnp = None
    clinvar = None
    for xref in feature.get("xrefs") or []:
        name = (xref.get("name") or "").lower()
        if name == "dbsnp" and not dbsnp:
            dbsnp = xref.get("id")
        elif name == "clinvar" and not clinvar:
            clinvar = xref.get("id")

    return NaturalVariant(
        position=position,
        wild_type=feature.get("wildType"),
        variant=feature.get("alternativeSequence")
        or (feature.get("mutatedType") if feature.get("mutatedType") else None),
        description=description,
        disease=_extract_disease(description),
        clinical_significance=clinical,
        dbsnp_id=dbsnp,
        clinvar_id=clinvar,
    )


async def fetch_natural_variants(
    accession: str,
    *,
    position: int | None = None,
    disease_only: bool = False,
    max_results: int | None = 200,
) -> VariantList:
    """UniProt Proteins API에서 단백질의 자연 변이(missense 위주) 목록을 가져온다.

    - `position`이 주어지면 그 잔기에 보고된 변이만 필터링한다.
    - `disease_only=True`이면 description에 질환 정보가 추출된 변이만 반환한다.
    - 결과는 position 오름차순으로 정렬되며, `max_results`로 컷오프된다.
    """
    accession = (accession or "").strip().upper()
    if not accession:
        raise SequenceError("UniProt Accession이 비어 있습니다.")

    url = UNIPROT_VARIATION_URL.format(accession=accession)
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            async with _semaphore:
                resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                return VariantList(
                    accession=accession,
                    variants=[],
                    total_count=0,
                    source_url=url,
                )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 404, 422):
            raise SequenceError(
                f"'{accession}'에 대한 변이 데이터를 찾지 못했습니다 — accession 표기를 확인하세요."
            ) from exc
        raise SequenceError(
            "UniProt Variation 서버 일시 장애로 조회하지 못했습니다. 잠시 후 다시 시도해주세요."
        ) from exc
    except httpx.HTTPError as exc:
        raise SequenceError(
            "UniProt Variation 조회 연결에 실패했습니다. 네트워크 연결을 확인해주세요."
        ) from exc

    entry = data if isinstance(data, dict) else (data[0] if data else {})
    features = entry.get("features") or []

    variants: list[NaturalVariant] = []
    for f in features:
        v = _parse_variation_feature(f)
        if v is None:
            continue
        if position is not None and v.position != position:
            continue
        if disease_only and not v.disease:
            continue
        variants.append(v)

    variants.sort(key=lambda v: v.position)
    total = len(variants)
    if max_results and total > max_results:
        variants = variants[:max_results]

    return VariantList(
        accession=accession,
        entry_name=entry.get("entryName"),
        variants=variants,
        total_count=total,
        source_url=url,
    )


__all__ = [
    "SequenceError",
    "fetch_sequence_region",
    "fetch_natural_variants",
]
