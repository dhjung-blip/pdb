"""tools/bioactivity.py 테스트."""

import asyncio

import pytest

from tools.bioactivity import (
    _chembl_activity_to_model,
    _iuphar_to_bioactivity,
    fetch_target_bioactivities,
)


def test_chembl_activity_to_model_basic():
    activity = {
        "molecule_chembl_id": "CHEMBL85",
        "molecule_pref_name": "RISPERIDONE",
        "target_chembl_id": "CHEMBL224",
        "standard_type": "Ki",
        "standard_relation": "=",
        "standard_value": "0.5",
        "standard_units": "nM",
        "pchembl_value": "9.3",
        "assay_type": "B",
        "assay_description": "Binding affinity at 5-HT2A receptor",
        "document_chembl_id": "CHEMBL123",
        "document_pubmed_id": "11111",
    }
    bio = _chembl_activity_to_model(activity)
    assert bio.ligand_name == "RISPERIDONE"
    assert bio.standard_type == "Ki"
    assert bio.standard_value == pytest.approx(0.5)
    assert bio.pchembl_value == pytest.approx(9.3)
    assert bio.pubmed_id == "11111"
    assert bio.source == "ChEMBL"
    assert "CHEMBL85" in (bio.source_url or "")


def test_iuphar_to_bioactivity_with_pX_affinity():
    interaction = {
        "ligand": "Risperidone",
        "ligandId": 39,
        "affinity": "9.3",
        "affinityType": "pKi",
        "type": "Antagonist",
        "refs": [{"pmid": "22222"}],
    }
    bio = _iuphar_to_bioactivity(interaction)
    assert bio is not None
    assert bio.source == "IUPHAR"
    # 9.3 pX → 0.5 nM 부근
    assert bio.standard_value is not None
    assert bio.standard_value < 1.0
    assert bio.pchembl_value == pytest.approx(9.3)


def test_iuphar_to_bioactivity_with_relation():
    interaction = {
        "ligand": "Test",
        "affinity": ">8.0",
        "affinityType": "pIC50",
    }
    bio = _iuphar_to_bioactivity(interaction)
    assert bio is not None
    assert bio.standard_relation == ">"


# --------------------------------------------------------------------------
# 네트워크 통합 테스트
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_bioactivities_for_htr2a():
    """HTR2A (P28223) — 활성 데이터가 풍부한 타깃."""
    result = asyncio.run(
        fetch_target_bioactivities(
            "P28223", gene_symbol="HTR2A", min_pchembl=8.0, max_results=10
        )
    )
    assert result.chembl_target_id is not None
    assert len(result.bioactivities) > 0
    # 모든 결과가 pChEMBL ≥ 8.0 이어야 함
    for b in result.bioactivities:
        assert b.pchembl_value is None or b.pchembl_value >= 8.0
