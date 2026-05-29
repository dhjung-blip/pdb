"""AlphaFold DB API 클라이언트.

실험 구조(PDB)가 없는 단백질의 경우 Claude가 "구조 정보 없음"으로 끝내는 대신,
AlphaFold 예측 구조의 신뢰도(pLDDT)와 다운로드 URL을 알려준다.

API: GET https://alphafold.ebi.ac.uk/api/prediction/{accession}
응답: [{ entryId, organismScientificName, sequenceLength, globalMetricValue,
        pdbUrl, cifUrl, paeImageUrl, paeDocUrl, modelCreatedDate, latestVersion }]
"""

from __future__ import annotations

import asyncio

import httpx

from models.schemas import AlphaFoldModel

ALPHAFOLD_URL = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"

_TIMEOUT = httpx.Timeout(15.0)
_semaphore = asyncio.Semaphore(5)


class AlphaFoldUnavailableError(RuntimeError):
    """AlphaFold API 일시 장애를 나타내는 예외.

    "데이터 미수록(404)"과 "API 장애(타임아웃/5xx/파싱 실패)"를 구분하기 위함이다.
    호출자는 이 예외를 잡아 사용자에게 명시적인 장애 메시지를 보여야 한다.
    """


def _confidence_label(plddt: float | None) -> str | None:
    """pLDDT 평균값 → 신뢰도 한 줄 라벨 (AlphaFold 공식 기준)."""
    if plddt is None:
        return None
    if plddt > 90:
        return "Very high (pLDDT > 90)"
    if plddt > 70:
        return "Confident (pLDDT 70-90)"
    if plddt > 50:
        return "Low (pLDDT 50-70)"
    return "Very low (pLDDT < 50)"


async def fetch_alphafold_model(uniprot_accession: str) -> AlphaFoldModel | None:
    """UniProt accession으로 AlphaFold 예측 구조 메타데이터를 가져온다.

    Returns:
        AlphaFoldModel: 정상 응답.
        None: AlphaFold DB에 등록되지 않음 (404 또는 빈 응답).

    Raises:
        AlphaFoldUnavailableError: API 일시 장애(타임아웃, 5xx, 응답 파싱 실패 등).
            호출자가 "미수록"과 구분해 사용자에게 명시적인 장애 메시지를 보여야 한다.
    """
    accession = (uniprot_accession or "").strip().upper()
    if not accession:
        return None

    url = ALPHAFOLD_URL.format(accession=accession)
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            async with _semaphore:
                resp = await client.get(url)
            if resp.status_code == 404:
                return None  # 미수록 (정상 케이스)
            if resp.status_code != 200:
                raise AlphaFoldUnavailableError(
                    f"AlphaFold API가 HTTP {resp.status_code}를 반환했습니다."
                )
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise AlphaFoldUnavailableError(
            "AlphaFold API 응답 시간 초과"
        ) from exc
    except httpx.HTTPError as exc:
        raise AlphaFoldUnavailableError(
            f"AlphaFold API 연결 실패: {exc}"
        ) from exc
    except ValueError as exc:
        raise AlphaFoldUnavailableError(
            f"AlphaFold API 응답 파싱 실패: {exc}"
        ) from exc

    items = data if isinstance(data, list) else [data]
    if not items:
        return None
    item = items[0]

    plddt = None
    try:
        plddt = float(item["globalMetricValue"]) if item.get("globalMetricValue") is not None else None
    except (TypeError, ValueError):
        pass

    seq_len: int | None = None
    # 응답 키 변천에 대응: sequenceLength → uniprotEnd - uniprotStart + 1 → uniprotSequence 길이
    for key in ("sequenceLength", "uniprotSequenceLength"):
        if item.get(key) is not None:
            try:
                seq_len = int(item[key])
                break
            except (TypeError, ValueError):
                pass
    if seq_len is None and item.get("uniprotEnd") and item.get("uniprotStart"):
        try:
            seq_len = int(item["uniprotEnd"]) - int(item["uniprotStart"]) + 1
        except (TypeError, ValueError):
            pass
    if seq_len is None and item.get("uniprotSequence"):
        seq_len = len(str(item["uniprotSequence"]))

    return AlphaFoldModel(
        uniprot_accession=accession,
        entry_id=item.get("entryId"),
        organism=item.get("organismScientificName"),
        sequence_length=seq_len,
        mean_plddt=plddt,
        global_metric_value=plddt,
        confidence_summary=_confidence_label(plddt),
        model_url_pdb=item.get("pdbUrl"),
        model_url_cif=item.get("cifUrl"),
        pae_image_url=item.get("paeImageUrl"),
        pae_doc_url=item.get("paeDocUrl"),
        model_version=str(item.get("latestVersion")) if item.get("latestVersion") else None,
        source_url=f"https://alphafold.ebi.ac.uk/entry/{accession}",
    )


__all__ = ["fetch_alphafold_model", "AlphaFoldUnavailableError"]
