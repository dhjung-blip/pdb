"""tools/alphafold.py 테스트."""

import asyncio

import pytest

from tools.alphafold import _confidence_label, fetch_alphafold_model


def test_confidence_label():
    assert _confidence_label(95) == "Very high (pLDDT > 90)"
    assert _confidence_label(80) == "Confident (pLDDT 70-90)"
    assert _confidence_label(60) == "Low (pLDDT 50-70)"
    assert _confidence_label(40) == "Very low (pLDDT < 50)"
    assert _confidence_label(None) is None


def test_empty_accession_returns_none():
    assert asyncio.run(fetch_alphafold_model("")) is None
    assert asyncio.run(fetch_alphafold_model("   ")) is None


@pytest.mark.network
def test_fetch_alphafold_egfr():
    """EGFR — AlphaFold DB에 인간 단백질로 등재되어 있음."""
    model = asyncio.run(fetch_alphafold_model("P00533"))
    assert model is not None
    assert model.uniprot_accession == "P00533"
    assert model.model_url_pdb is not None
    assert model.mean_plddt is not None
    assert model.sequence_length and model.sequence_length > 1000
