"""논문 메타데이터 + 초록 조회 (Europe PMC 우선, PubMed E-utils fallback).

연구원이 "이 논문 결론이 뭐였지?" 라고 물을 때 Claude가 본문을 추측하지 않도록,
권위 있는 원문(초록)을 그대로 가져와서 제공한다.

Europe PMC는 PubMed보다 응답이 깔끔하고 무인증 JSON으로 초록까지 한 번에
가져올 수 있어 1차 소스로 사용한다. 일부 신규/생명과학 외 논문은 Europe PMC에
없을 수 있어, 실패 시 PubMed EFetch(XML)로 fallback 한다.

이 모듈은 절대 예외를 호출자에게 던지지 않는다 — 실패 시 None 반환.
"""

from __future__ import annotations

import asyncio
import re
from html import unescape
from urllib.parse import quote

import httpx

from models.schemas import PaperAbstract

EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULL_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/article/{source}/{ext_id}"
)
EUTILS_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUTILS_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# Europe PMC / NCBI 동시 호출 제한 (예의상)
_semaphore = asyncio.Semaphore(5)
_TIMEOUT = httpx.Timeout(15.0)
RETRY_DELAY = 1.0


class LiteratureAPIError(RuntimeError):
    """Europe PMC / PubMed E-utils API 일시 장애를 나타내는 예외.

    "논문 미수록(빈 결과)"과 "API 장애(타임아웃/5xx/파싱 실패)"를 구분한다.
    fetch_paper_abstract는 두 소스(EPMC, PubMed)를 모두 시도하므로, 한쪽이 장애여도
    다른 쪽이 성공하면 결과를 반환한다. 두 소스 모두 장애일 때만 이 예외를 던진다.
    """


# --------------------------------------------------------------------------
# 식별자 정규화
# --------------------------------------------------------------------------

def _normalize_pmid(value: str | None) -> str | None:
    """PMID에서 숫자만 추출. 빈 값/이상한 입력은 None."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def _normalize_doi(value: str | None) -> str | None:
    """DOI를 정규화. 'doi:' 접두사와 'https://doi.org/' 접두사를 제거."""
    if not value:
        return None
    v = str(value).strip()
    v = re.sub(r"^(https?://(dx\.)?doi\.org/)", "", v, flags=re.IGNORECASE)
    v = re.sub(r"^doi:\s*", "", v, flags=re.IGNORECASE)
    return v or None


# --------------------------------------------------------------------------
# Europe PMC
# --------------------------------------------------------------------------

def _epmc_query(identifier: str, *, is_doi: bool) -> str:
    """Europe PMC 검색 쿼리 문자열을 만든다."""
    if is_doi:
        return f'DOI:"{identifier}"'
    return f"EXT_ID:{identifier} AND SRC:MED"


async def _epmc_search(
    client: httpx.AsyncClient, identifier: str, *, is_doi: bool
) -> dict | None:
    """Europe PMC search 엔드포인트에서 첫 매칭 결과를 가져온다.

    404/빈 결과 → None. 그 외 → LiteratureAPIError.
    """
    params = {
        "query": _epmc_query(identifier, is_doi=is_doi),
        "format": "json",
        "resultType": "core",  # 초록·저자·키워드 포함
        "pageSize": "1",
    }
    try:
        async with _semaphore:
            resp = await client.get(EPMC_SEARCH_URL, params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise LiteratureAPIError(
                f"Europe PMC search가 HTTP {resp.status_code}를 반환했습니다."
            )
        data = resp.json()
    except httpx.TimeoutException as exc:
        raise LiteratureAPIError("Europe PMC search 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise LiteratureAPIError(f"Europe PMC search 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise LiteratureAPIError(f"Europe PMC search 응답 파싱 실패: {exc}") from exc

    results = ((data.get("resultList") or {}).get("result")) or []
    return results[0] if results else None


def _epmc_to_paper(item: dict) -> PaperAbstract:
    """Europe PMC core 응답 → PaperAbstract."""
    authors_field = item.get("authorString") or ""
    authors: list[str] = []
    if authors_field:
        authors = [a.strip().rstrip(".") for a in authors_field.split(",") if a.strip()]

    mesh = []
    mesh_list = ((item.get("meshHeadingList") or {}).get("meshHeading")) or []
    for h in mesh_list:
        name = h.get("descriptorName")
        if name:
            mesh.append(name)

    keywords = []
    kw_list = ((item.get("keywordList") or {}).get("keyword")) or []
    if kw_list:
        keywords = [k for k in kw_list if isinstance(k, str)]

    pmid = item.get("pmid")
    doi = _normalize_doi(item.get("doi"))
    pmcid = item.get("pmcid")
    year = item.get("pubYear")
    try:
        year_int = int(year) if year else None
    except (TypeError, ValueError):
        year_int = None

    journal_info = item.get("journalInfo") or {}
    journal = journal_info.get("journal", {}).get("title") if journal_info else None
    volume = journal_info.get("volume") if journal_info else None
    issue = journal_info.get("issue") if journal_info else None
    pages = item.get("pageInfo")

    is_oa = None
    if item.get("isOpenAccess"):
        is_oa = item["isOpenAccess"] in ("Y", "y", True)

    if pmid:
        source_url = f"https://europepmc.org/article/MED/{pmid}"
    elif doi:
        source_url = f"https://doi.org/{doi}"
    else:
        source_url = "https://europepmc.org/"

    return PaperAbstract(
        pmid=str(pmid) if pmid else None,
        doi=doi,
        pmcid=pmcid,
        title=(item.get("title") or "").rstrip(" .") or None,
        authors=authors,
        journal=journal,
        year=year_int,
        volume=volume,
        issue=issue,
        pages=pages,
        abstract=item.get("abstractText"),
        mesh_terms=mesh,
        keywords=keywords,
        is_open_access=is_oa,
        source="Europe PMC",
        source_url=source_url,
    )


# --------------------------------------------------------------------------
# PubMed E-utils (fallback)
# --------------------------------------------------------------------------

def _strip_xml(text: str) -> str:
    """XML 태그를 제거하고 엔터티를 디코드한다."""
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _xml_get(xml: str, tag: str) -> str | None:
    """간단한 정규식 기반 XML 단일 태그 추출 (전체 파서 없이도 충분)."""
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.DOTALL)
    return _strip_xml(m.group(1)) if m else None


def _xml_get_all(xml: str, tag: str) -> list[str]:
    """동일 태그의 모든 값 목록을 추출한다."""
    return [
        _strip_xml(m.group(1))
        for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.DOTALL)
    ]


def _pubmed_xml_to_paper(xml: str) -> PaperAbstract | None:
    """PubMed EFetch XML(PubmedArticle)을 PaperAbstract로 변환한다."""
    pmid = _xml_get(xml, "PMID")
    if not pmid:
        return None

    title = _xml_get(xml, "ArticleTitle")

    # 초록은 여러 AbstractText 섹션이 있을 수 있다 (BACKGROUND/METHODS/RESULTS 등).
    abstract_parts: list[str] = []
    for m in re.finditer(
        r'<AbstractText(?:\s+Label="([^"]+)")?[^>]*>(.*?)</AbstractText>',
        xml,
        re.DOTALL,
    ):
        label, body = m.group(1), _strip_xml(m.group(2))
        if not body:
            continue
        abstract_parts.append(f"{label}: {body}" if label else body)
    abstract = "\n\n".join(abstract_parts) if abstract_parts else None

    journal = _xml_get(xml, "Title")  # <Journal><Title>
    year = _xml_get(xml, "Year")
    try:
        year_int = int(year) if year else None
    except ValueError:
        year_int = None

    volume = _xml_get(xml, "Volume")
    issue = _xml_get(xml, "Issue")
    pages = _xml_get(xml, "MedlinePgn")

    authors: list[str] = []
    for m in re.finditer(r"<Author[^>]*>(.*?)</Author>", xml, re.DOTALL):
        block = m.group(1)
        last = _xml_get(block, "LastName")
        initials = _xml_get(block, "Initials")
        if last:
            authors.append(f"{last} {initials}".strip() if initials else last)
    if not authors:
        for m in re.finditer(r"<CollectiveName[^>]*>(.*?)</CollectiveName>", xml, re.DOTALL):
            authors.append(_strip_xml(m.group(1)))

    mesh = _xml_get_all(xml, "DescriptorName")

    doi = None
    m = re.search(
        r'<ArticleId IdType="doi"[^>]*>(.*?)</ArticleId>', xml, re.DOTALL,
    )
    if m:
        doi = _strip_xml(m.group(1))
    pmcid = None
    m = re.search(
        r'<ArticleId IdType="pmc"[^>]*>(.*?)</ArticleId>', xml, re.DOTALL,
    )
    if m:
        pmcid = _strip_xml(m.group(1))

    return PaperAbstract(
        pmid=pmid,
        doi=_normalize_doi(doi),
        pmcid=pmcid,
        title=title,
        authors=authors,
        journal=journal,
        year=year_int,
        volume=volume,
        issue=issue,
        pages=pages,
        abstract=abstract,
        mesh_terms=mesh,
        source="PubMed",
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


async def _pubmed_efetch(client: httpx.AsyncClient, pmid: str) -> PaperAbstract | None:
    """PubMed EFetch XML로 단일 PMID의 메타데이터를 가져온다.

    404 또는 빈 본문 → None. 그 외 → LiteratureAPIError.
    """
    params = {"db": "pubmed", "id": pmid, "retmode": "xml"}
    try:
        async with _semaphore:
            resp = await client.get(EUTILS_EFETCH_URL, params=params)
        if resp.status_code == 404 or not resp.text:
            return None
        if resp.status_code != 200:
            raise LiteratureAPIError(
                f"PubMed EFetch가 HTTP {resp.status_code}를 반환했습니다."
            )
    except httpx.TimeoutException as exc:
        raise LiteratureAPIError("PubMed EFetch 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise LiteratureAPIError(f"PubMed EFetch 연결 실패: {exc}") from exc
    return _pubmed_xml_to_paper(resp.text)


async def _pubmed_esearch_by_doi(
    client: httpx.AsyncClient, doi: str
) -> str | None:
    """DOI로 PubMed esearch → PMID 한 개 반환.

    404/빈 결과 → None. 그 외 → LiteratureAPIError.
    """
    params = {
        "db": "pubmed",
        "term": f"{doi}[doi]",
        "retmode": "json",
        "retmax": "1",
    }
    try:
        async with _semaphore:
            resp = await client.get(EUTILS_ESEARCH_URL, params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise LiteratureAPIError(
                f"PubMed ESearch가 HTTP {resp.status_code}를 반환했습니다."
            )
        ids = (resp.json().get("esearchresult") or {}).get("idlist") or []
        return ids[0] if ids else None
    except httpx.TimeoutException as exc:
        raise LiteratureAPIError("PubMed ESearch 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise LiteratureAPIError(f"PubMed ESearch 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise LiteratureAPIError(f"PubMed ESearch 응답 파싱 실패: {exc}") from exc


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------

async def fetch_paper_abstract(
    pmid: str | None = None,
    doi: str | None = None,
) -> PaperAbstract | None:
    """PMID 또는 DOI로 논문 메타데이터 + 초록을 가져온다.

    우선순위: Europe PMC → PubMed EFetch.
    한 소스가 API 장애여도 다른 소스가 성공하면 그 결과를 반환한다.
    두 소스 모두 API 장애일 때만 LiteratureAPIError를 던진다.
    두 소스가 모두 "미수록"으로 응답하면 None을 반환한다.
    """
    pmid = _normalize_pmid(pmid)
    doi = _normalize_doi(doi)
    if not pmid and not doi:
        return None

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        identifier, is_doi = (pmid, False) if pmid else (doi, True)
        try:
            item = await _epmc_search(client, identifier, is_doi=is_doi)
        except LiteratureAPIError as exc:
            item = None
            errors.append(str(exc))

        if item:
            return _epmc_to_paper(item)

        # Europe PMC 미수록/장애 → PubMed로 fallback
        if not pmid and doi:
            try:
                pmid = await _pubmed_esearch_by_doi(client, doi)
            except LiteratureAPIError as exc:
                pmid = None
                errors.append(str(exc))
        if pmid:
            try:
                result = await _pubmed_efetch(client, pmid)
                if result:
                    return result
            except LiteratureAPIError as exc:
                errors.append(str(exc))

    # 두 소스 모두 장애일 때만 raise.
    # 미수록(빈 응답)이 한 번이라도 있었으면 None 반환으로 "찾지 못함"으로 처리.
    # errors 개수가 시도한 소스 수와 같으면 전부 장애.
    if errors and len(errors) >= 2:
        raise LiteratureAPIError(
            "Europe PMC와 PubMed 모두 일시 장애입니다: " + " / ".join(errors)
        )
    return None


async def search_papers(
    query: str, max_results: int = 5
) -> list[PaperAbstract]:
    """자유 텍스트로 Europe PMC를 검색해 상위 N개의 PaperAbstract를 반환한다.

    404/빈 결과 → 빈 리스트. API 장애 → LiteratureAPIError.
    """
    query = (query or "").strip()
    if not query:
        return []

    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(max(1, min(max_results, 25))),
    }
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            async with _semaphore:
                resp = await client.get(EPMC_SEARCH_URL, params=params)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                raise LiteratureAPIError(
                    f"Europe PMC search가 HTTP {resp.status_code}를 반환했습니다."
                )
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise LiteratureAPIError("Europe PMC search 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise LiteratureAPIError(f"Europe PMC search 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise LiteratureAPIError(f"Europe PMC search 응답 파싱 실패: {exc}") from exc

    results = ((data.get("resultList") or {}).get("result")) or []
    return [_epmc_to_paper(item) for item in results]


__all__ = ["fetch_paper_abstract", "search_papers", "LiteratureAPIError"]
