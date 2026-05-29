"""tools/sequence.py 테스트."""

import asyncio

import pytest

from tools.sequence import (
    _extract_disease,
    _parse_feature,
    _parse_location,
    _parse_variation_feature,
    fetch_natural_variants,
    fetch_sequence_region,
)


# --------------------------------------------------------------------------
# 오프라인 단위 테스트
# --------------------------------------------------------------------------

def test_parse_location_full_range():
    loc = {"start": {"value": 10}, "end": {"value": 25}}
    assert _parse_location(loc) == (10, 25)


def test_parse_location_single_position():
    loc = {"position": {"value": 100}}
    assert _parse_location(loc) == (100, 100)


def test_parse_location_empty():
    assert _parse_location(None) == (None, None)
    assert _parse_location({}) == (None, None)


def test_parse_feature_active_site():
    raw = {
        "type": "Active site",
        "description": "Proton acceptor",
        "location": {"start": {"value": 95}, "end": {"value": 95}},
        "evidences": [{"evidenceCode": "ECO:0000269"}],
    }
    feat = _parse_feature(raw)
    assert feat is not None
    assert feat.type == "Active site"
    assert feat.start == 95
    assert feat.end == 95
    assert feat.description == "Proton acceptor"
    assert "ECO:0000269" in (feat.evidence or "")


def test_parse_feature_binding_with_ligand():
    raw = {
        "type": "Binding site",
        "location": {"start": {"value": 200}, "end": {"value": 200}},
        "ligand": {"name": "ATP"},
    }
    feat = _parse_feature(raw)
    assert feat is not None
    assert feat.ligand == "ATP"


def test_extract_disease_from_description():
    assert _extract_disease("in NSCLC; somatic mutation.") == "NSCLC"
    assert _extract_disease("in cis") is None
    assert _extract_disease(None) is None


def test_parse_variation_feature_basic():
    feature = {
        "type": "VARIANT",
        "begin": "858",
        "wildType": "L",
        "alternativeSequence": "R",
        "description": "in NSCLC; somatic mutation.",
        "clinicalSignificances": [{"type": "Likely pathogenic"}],
        "xrefs": [{"name": "dbSNP", "id": "rs121434568"}],
    }
    v = _parse_variation_feature(feature)
    assert v is not None
    assert v.position == 858
    assert v.wild_type == "L"
    assert v.variant == "R"
    assert v.disease == "NSCLC"
    assert v.clinical_significance == "Likely pathogenic"
    assert v.dbsnp_id == "rs121434568"


# --------------------------------------------------------------------------
# 네트워크 통합 테스트
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_sequence_region_egfr_range():
    region = asyncio.run(fetch_sequence_region("P00533", start=855, end=870))
    assert region.accession == "P00533"
    assert region.start == 855
    assert region.end == 870
    assert len(region.sequence) == 16
    assert region.full_length > 1000


@pytest.mark.network
def test_fetch_natural_variants_egfr_l858():
    result = asyncio.run(
        fetch_natural_variants("P00533", position=858, disease_only=False)
    )
    # L858R 변이는 NSCLC의 대표 변이 — 반드시 포함되어야 함
    has_l858r = any(
        v.position == 858 and v.variant == "R" for v in result.variants
    )
    assert has_l858r
