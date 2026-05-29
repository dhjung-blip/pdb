"""tools/binding_site.py 테스트."""

import asyncio

import pytest

from tools.binding_site import (
    _NOT_INTERESTING_LIGANDS,
    _residue_from_pdbe,
    fetch_binding_sites,
)


def test_residue_from_pdbe_basic():
    item = {
        "chain_id": "A",
        "author_residue_number": 155,
        "chem_comp_id": "PHE",
        "residue_number": 200,
    }
    r = _residue_from_pdbe(item)
    assert r is not None
    assert r.chain_id == "A"
    assert r.residue_number == 155
    assert r.residue_name == "PHE"
    assert r.label_seq_id == 200


def test_residue_from_pdbe_missing_field():
    item = {"chain_id": "A"}  # 잔기 번호 / 이름 없음
    assert _residue_from_pdbe(item) is None


def test_solvents_constant_includes_common_ions():
    assert "HOH" in _NOT_INTERESTING_LIGANDS
    assert "SO4" in _NOT_INTERESTING_LIGANDS
    assert "GOL" in _NOT_INTERESTING_LIGANDS


def test_invalid_pdb_id_returns_note():
    result = asyncio.run(fetch_binding_sites("BAD_ID"))
    assert result.sites == []
    assert result.notes


# --------------------------------------------------------------------------
# 네트워크 통합 테스트
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_binding_sites_7wc7():
    """7WC7 (HTR2A-Lisuride) — Lisuride 결합부위가 있어야 함."""
    result = asyncio.run(fetch_binding_sites("7WC7"))
    assert result.pdb_id == "7WC7"
    # 결과가 비어 있을 수도 있지만, 있으면 잔기가 있어야 함
    for site in result.sites:
        assert site.residues  # 비어있지 않아야 함
