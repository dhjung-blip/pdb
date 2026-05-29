"""tools/ligand.py 테스트."""

import asyncio

import pytest

from models.schemas import LigandDetail
from tools.ligand import (
    _CHEMBL_RE,
    _INCHIKEY_RE,
    _fill_from_chembl,
    _fill_from_pubchem,
    fetch_ligand_detail,
)


def test_chembl_regex():
    assert _CHEMBL_RE.match("CHEMBL85")
    assert _CHEMBL_RE.match("chembl1234")
    assert not _CHEMBL_RE.match("ABCD123")


def test_inchikey_regex():
    assert _INCHIKEY_RE.match("RZUSEABLOMUKTQ-UHFFFAOYSA-N")
    assert not _INCHIKEY_RE.match("not-an-inchikey")


def test_fill_from_pubchem_props():
    d = LigandDetail(query="risperidone")
    props = {
        "Title": "Risperidone",
        "MolecularFormula": "C23H27FN4O2",
        "MolecularWeight": "410.49",
        "CanonicalSMILES": "CC1=C(C(=O)N2CCCCC2=N1)CCN3CCC(CC3)C4=NOC5=C4C=CC(=C5)F",
        "IsomericSMILES": "CC1=C(C(=O)N2CCCCC2=N1)CCN3CCC(CC3)C4=NOC5=C4C=CC(=C5)F",
        "InChI": "InChI=1S/C23H27FN4O2/...",
        "InChIKey": "RAPZEAPATHNIPO-UHFFFAOYSA-N",
        "IUPACName": "3-[2-[4-(6-fluorobenzisoxazol-3-yl)piperidin-1-yl]ethyl]...",
        "XLogP": "3.5",
        "HBondDonorCount": 0,
        "HBondAcceptorCount": 6,
        "TPSA": "61.9",
        "RotatableBondCount": 4,
    }
    _fill_from_pubchem(d, props, ["Risperdal", "Risperidone"])
    assert d.common_name == "Risperidone"
    assert d.molecular_weight == pytest.approx(410.49, rel=0.01)
    assert d.xlogp == pytest.approx(3.5, rel=0.01)
    assert d.tpsa == pytest.approx(61.9, rel=0.01)
    assert d.rotatable_bonds == 4
    assert "Risperdal" in d.synonyms


def test_fill_from_chembl_basic():
    d = LigandDetail(query="risperidone")
    mol = {
        "molecule_chembl_id": "CHEMBL85",
        "pref_name": "RISPERIDONE",
        "max_phase": 4.0,
        "molecule_type": "Small molecule",
        "molecule_structures": {
            "canonical_smiles": "CC1=C(C(=O)N2CCCCC2=N1)CCN3CCC...",
            "standard_inchi_key": "RAPZEAPATHNIPO-UHFFFAOYSA-N",
        },
        "molecule_properties": {
            "full_mwt": "410.49",
            "alogp": "3.5",
            "hbd": 0,
            "hba": 6,
            "psa": "61.9",
            "rtb": 4,
            "full_molformula": "C23H27FN4O2",
        },
        "molecule_synonyms": [{"molecule_synonym": "RISPERDAL"}],
    }
    _fill_from_chembl(d, mol)
    assert d.chembl_id == "CHEMBL85"
    assert d.common_name == "RISPERIDONE"
    assert d.max_phase == 4


# --------------------------------------------------------------------------
# 네트워크 통합 테스트
# --------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_ligand_risperidone():
    detail = asyncio.run(fetch_ligand_detail("risperidone"))
    assert detail.pubchem_cid is not None
    assert detail.smiles is not None
    assert detail.molecular_weight is not None
    assert detail.molecular_weight > 400  # 약 410
    assert detail.max_phase == 4  # 승인 약물


@pytest.mark.network
def test_fetch_ligand_by_chembl_id():
    detail = asyncio.run(fetch_ligand_detail("CHEMBL85"))
    assert detail.chembl_id == "CHEMBL85"
