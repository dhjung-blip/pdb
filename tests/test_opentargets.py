"""tools/opentargets.py 테스트."""

import asyncio

import pytest

from tools.opentargets import _uniprot_from_protein_ids, fetch_target_intelligence


def test_uniprot_from_protein_ids_finds_swissprot():
    ids = [
        {"id": "ENST123", "source": "ensembl"},
        {"id": "P00533", "source": "uniprot_swissprot"},
    ]
    assert _uniprot_from_protein_ids(ids) == "P00533"


def test_uniprot_from_protein_ids_handles_missing():
    assert _uniprot_from_protein_ids(None) is None
    assert _uniprot_from_protein_ids([]) is None


def test_empty_query_returns_none():
    assert asyncio.run(fetch_target_intelligence("")) is None


@pytest.mark.network
def test_fetch_target_intelligence_egfr():
    intel = asyncio.run(fetch_target_intelligence("EGFR", max_diseases=5, max_drugs=5))
    assert intel is not None
    assert intel.gene_name == "EGFR"
    assert intel.ensembl_id and intel.ensembl_id.startswith("ENSG")
    assert intel.uniprot_accession == "P00533"
    # EGFR은 임상 약물이 많음
    assert len(intel.known_drugs) > 0
    assert len(intel.diseases) > 0
