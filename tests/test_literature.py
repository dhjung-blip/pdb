"""tools/literature.py 테스트 — 오프라인 파싱 단위 테스트 + 네트워크 통합."""

import asyncio

import pytest

from tools.literature import (
    _epmc_to_paper,
    _normalize_doi,
    _normalize_pmid,
    _pubmed_xml_to_paper,
    fetch_paper_abstract,
    search_papers,
)


# --------------------------------------------------------------------------
# 오프라인 단위 테스트
# --------------------------------------------------------------------------

def test_normalize_pmid_from_url():
    assert _normalize_pmid("PMID: 12345") == "12345"
    assert _normalize_pmid("12345") == "12345"
    assert _normalize_pmid(None) is None
    assert _normalize_pmid("") is None


def test_normalize_doi_strips_prefixes():
    assert _normalize_doi("https://doi.org/10.1038/nature12345") == "10.1038/nature12345"
    assert _normalize_doi("doi: 10.1038/nature12345") == "10.1038/nature12345"
    assert _normalize_doi("10.1038/nature12345") == "10.1038/nature12345"
    assert _normalize_doi(None) is None


def test_epmc_to_paper_parses_core_fields():
    item = {
        "pmid": "32555340",
        "doi": "10.1038/s41586-020-1968-7",
        "title": "Structure of NTR1 with beta-arrestin.",
        "authorString": "Huang J, Chen S, Zhang JJ",
        "abstractText": "We report the cryo-EM structure...",
        "journalInfo": {
            "journal": {"title": "Nature"},
            "volume": "579",
        },
        "pageInfo": "303-308",
        "pubYear": "2020",
        "meshHeadingList": {
            "meshHeading": [{"descriptorName": "Receptors"}, {"descriptorName": "Cryo-EM"}]
        },
        "isOpenAccess": "Y",
    }
    paper = _epmc_to_paper(item)
    assert paper.pmid == "32555340"
    assert paper.doi == "10.1038/s41586-020-1968-7"
    assert paper.year == 2020
    assert paper.journal == "Nature"
    assert paper.is_open_access is True
    assert "Receptors" in paper.mesh_terms
    assert paper.source == "Europe PMC"
    assert "europepmc.org" in (paper.source_url or "")


def test_pubmed_xml_parses_basic():
    xml = """
    <PubmedArticle>
      <MedlineCitation>
        <PMID Version="1">12345</PMID>
        <Article>
          <ArticleTitle>Test article.</ArticleTitle>
          <Abstract>
            <AbstractText Label="RESULTS">key finding.</AbstractText>
            <AbstractText>conclusion.</AbstractText>
          </Abstract>
          <Journal>
            <Title>J. Test</Title>
            <JournalIssue>
              <Volume>10</Volume>
              <PubDate><Year>2024</Year></PubDate>
            </JournalIssue>
          </Journal>
          <Pagination><MedlinePgn>1-5</MedlinePgn></Pagination>
          <AuthorList>
            <Author><LastName>Lee</LastName><Initials>JS</Initials></Author>
          </AuthorList>
        </Article>
        <MeshHeadingList>
          <MeshHeading><DescriptorName>Test</DescriptorName></MeshHeading>
        </MeshHeadingList>
      </MedlineCitation>
      <PubmedData>
        <ArticleIdList>
          <ArticleId IdType="doi">10.1000/test</ArticleId>
        </ArticleIdList>
      </PubmedData>
    </PubmedArticle>
    """
    paper = _pubmed_xml_to_paper(xml)
    assert paper is not None
    assert paper.pmid == "12345"
    assert paper.title == "Test article."
    assert paper.year == 2024
    assert "Lee JS" in paper.authors
    assert "RESULTS: key finding." in (paper.abstract or "")
    assert paper.doi == "10.1000/test"
    assert "Test" in paper.mesh_terms


# --------------------------------------------------------------------------
# 네트워크 통합 테스트
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_paper_by_pmid():
    paper = asyncio.run(fetch_paper_abstract(pmid="32555340"))
    assert paper is not None
    assert paper.pmid == "32555340"
    assert paper.year == 2020
    assert paper.abstract  # 초록이 있어야 함


@pytest.mark.network
def test_fetch_paper_by_doi():
    paper = asyncio.run(
        fetch_paper_abstract(doi="10.1038/s41586-020-1968-7")
    )
    assert paper is not None
    assert paper.doi == "10.1038/s41586-020-1968-7"


@pytest.mark.network
def test_search_papers_returns_results():
    papers = asyncio.run(search_papers("HTR2A psychedelic", max_results=3))
    assert len(papers) > 0
    assert papers[0].title is not None
