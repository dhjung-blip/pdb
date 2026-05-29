"""tools/uniprot.py 테스트.

오프라인 단위 테스트와 실제 API를 호출하는 네트워크 테스트로 구성된다.
네트워크 테스트는 `pytest -m "not network"` 로 제외할 수 있다.
"""

import asyncio

import pytest

from tools.uniprot import (
    UniProtError,
    _extract_gene_name,
    _extract_protein_name,
    search_uniprot,
)


# --------------------------------------------------------------------------
# 오프라인 단위 테스트 — 파싱 로직
# --------------------------------------------------------------------------

def test_extract_protein_name_recommended():
    entry = {
        "proteinDescription": {
            "recommendedName": {"fullName": {"value": "Epidermal growth factor receptor"}}
        }
    }
    assert _extract_protein_name(entry) == "Epidermal growth factor receptor"


def test_extract_protein_name_fallback_submission():
    entry = {
        "uniProtkbId": "TEST_HUMAN",
        "proteinDescription": {
            "submissionNames": [{"fullName": {"value": "Submitted name"}}]
        },
    }
    assert _extract_protein_name(entry) == "Submitted name"


def test_extract_protein_name_no_description():
    entry = {"uniProtkbId": "TEST_HUMAN"}
    assert _extract_protein_name(entry) == "TEST_HUMAN"


def test_extract_gene_name():
    entry = {"genes": [{"geneName": {"value": "EGFR"}}]}
    assert _extract_gene_name(entry) == "EGFR"


def test_extract_gene_name_missing():
    assert _extract_gene_name({}) is None


# --------------------------------------------------------------------------
# 네트워크 테스트 — 실제 UniProt API 호출
# --------------------------------------------------------------------------

@pytest.mark.network
def test_search_egfr():
    result = asyncio.run(search_uniprot("EGFR"))
    assert result.accession == "P00533"
    assert result.gene_name == "EGFR"
    assert len(result.pdb_ids) > 100  # EGFR은 구조가 매우 많음


@pytest.mark.network
def test_search_tp53():
    result = asyncio.run(search_uniprot("TP53"))
    assert result.accession == "P04637"


@pytest.mark.network
def test_unknown_target():
    # 존재하지 않는 타겟 → 에러 메시지 반환 확인
    with pytest.raises(ValueError, match="찾지 못했습니다"):
        asyncio.run(search_uniprot("XYZXYZ_NOTEXIST_12345"))


def test_empty_target_raises():
    with pytest.raises(UniProtError):
        asyncio.run(search_uniprot("   "))
