"""tools/pdb.py 테스트.

오프라인 단위 테스트와 실제 API를 호출하는 네트워크 테스트로 구성된다.
네트워크 테스트는 `pytest -m "not network"` 로 제외할 수 있다.
"""

import asyncio

import pytest

from tools.pdb import (
    _parse_citation,
    _parse_entry,
    fetch_single_pdb_entry,
    format_method,
)


# --------------------------------------------------------------------------
# 오프라인 단위 테스트 — 파싱 로직
# --------------------------------------------------------------------------

def test_format_method():
    assert format_method("X-RAY DIFFRACTION") == "X-ray"
    assert format_method("X-ray") == "X-ray"
    assert format_method("ELECTRON MICROSCOPY") == "Cryo-EM"
    assert format_method("EM") == "Cryo-EM"
    assert format_method("SOLUTION NMR") == "NMR"
    assert format_method("NMR") == "NMR"
    assert format_method(None) == "기타"


def test_parse_citation_primary_selection():
    citations = [
        {"id": "1", "title": "Secondary paper"},
        {"id": "primary", "title": "Primary paper", "year": 2021},
    ]
    citation = _parse_citation(citations)
    assert citation is not None
    assert citation.title == "Primary paper"
    assert citation.year == 2021


def test_parse_citation_authors_truncation():
    citations = [
        {
            "id": "primary",
            "rcsb_authors": ["A, A", "B, B", "C, C", "D, D", "E, E"],
        }
    ]
    citation = _parse_citation(citations)
    assert citation is not None
    # 저자 간 구분자는 세미콜론 (각 저자가 쉼표를 포함하므로)
    assert citation.authors == "A, A; B, B; C, C et al."


def test_parse_citation_volume_pages():
    citations = [
        {
            "id": "primary",
            "title": "A paper",
            "journal_abbrev": "Science",
            "year": 2022,
            "journal_volume": "375",
            "page_first": "403",
            "page_last": "411",
        }
    ]
    citation = _parse_citation(citations)
    assert citation is not None
    assert citation.volume == "375"
    assert citation.page_first == "403"
    assert citation.page_last == "411"


def test_parse_citation_empty():
    assert _parse_citation([]) is None


def test_parse_entry_basic():
    entry = {
        "rcsb_id": "7T9K",
        "struct": {"title": "Test structure"},
        "rcsb_entry_info": {
            "resolution_combined": [1.65],
            "experimental_method": "X-RAY DIFFRACTION",
        },
        "rcsb_accession_info": {"initial_release_date": "2022-01-12T00:00:00Z"},
        "citation": [
            {
                "id": "primary",
                "title": "A paper",
                "year": 2021,
                "pdbx_database_id_DOI": "10.1038/test",
                "pdbx_database_id_PubMed": 12345678,
            }
        ],
    }
    parsed = _parse_entry("7T9K", entry)
    assert parsed.pdb_id == "7T9K"
    assert parsed.resolution == 1.65
    assert parsed.released_date == "2022-01-12"
    assert parsed.method == "X-RAY DIFFRACTION"
    assert parsed.citation is not None
    assert parsed.citation.doi == "10.1038/test"
    assert parsed.citation.pmid == "12345678"


def test_parse_entry_nmr_no_resolution():
    entry = {
        "rcsb_entry_info": {
            "resolution_combined": None,
            "experimental_method": "SOLUTION NMR",
        },
        "rcsb_accession_info": {"initial_release_date": "2010-05-01T00:00:00Z"},
    }
    parsed = _parse_entry("2ABC", entry)
    assert parsed.resolution is None
    assert parsed.released_date == "2010-05-01"


def test_parse_entry_resolution_always_float():
    """resolution_combined가 정수로 와도 항상 float으로 저장 (Bug 6)."""
    entry = {
        "rcsb_entry_info": {"resolution_combined": [3], "experimental_method": "EM"},
    }
    parsed = _parse_entry("9ABC", entry)
    assert parsed.resolution == 3.0
    assert isinstance(parsed.resolution, float)


def test_parse_entry_polymer_descriptions():
    """polymer_entities의 pdbx_description이 polymer_descriptions로 수집됨 (Bug 3)."""
    entry = {
        "struct": {"title": "Test"},
        "rcsb_entry_info": {"resolution_combined": [2.5]},
        "polymer_entities": [
            {"rcsb_polymer_entity": {"pdbx_description": "5-HT2A receptor,Soluble cytochrome b562"}},
            {"rcsb_polymer_entity": {"pdbx_description": None}},
        ],
    }
    parsed = _parse_entry("9XYZ", entry)
    assert parsed.polymer_descriptions == ["5-HT2A receptor,Soluble cytochrome b562"]


# --------------------------------------------------------------------------
# 네트워크 테스트 — 실제 RCSB PDB API 호출
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_single_entry():
    entry = asyncio.run(fetch_single_pdb_entry("7T9K"))
    assert entry.pdb_id == "7T9K"
    assert entry.resolution is not None
    assert entry.released_date is not None
    assert entry.citation is not None


@pytest.mark.network
def test_fetch_unknown_entry_raises():
    with pytest.raises(ValueError):
        asyncio.run(fetch_single_pdb_entry("ZZZZ"))
