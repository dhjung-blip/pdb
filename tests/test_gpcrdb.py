"""tools/gpcrdb.py 및 tools/parser.py 테스트.

오프라인 단위 테스트와 실제 GPCRdb / PubChem API를 호출하는 네트워크 테스트로 구성된다.
네트워크 테스트는 `pytest -m "not network"` 로 제외할 수 있다.
"""

import asyncio

import pytest

from tools.gpcrdb import (
    _parse_structure_item,
    _pubchem_title,
    check_gpcr,
    get_gpcrdb_single,
    get_gpcrdb_structures,
    normalize_ligand_name,
    normalize_modality,
    parse_signaling_protein,
    parse_stabilizing_agents,
    resolve_ligand_name,
    select_primary_ligand,
)
from tools.parser import extract_antibody_from_title, extract_fusion_from_title


# --------------------------------------------------------------------------
# 오프라인 단위 테스트 — 정규화 / 파싱 로직
# --------------------------------------------------------------------------

def test_modality_normalization():
    assert normalize_modality("agonist") == "Agonist"
    assert normalize_modality("Agonist") == "Agonist"
    assert normalize_modality("inverse agonist") == "Inverse agonist"
    assert normalize_modality("Antagonist") == "Antagonist"
    assert normalize_modality(None) is None


def test_parse_signaling_protein_g_proteins():
    def sp(entry_name, sp_type="G protein"):
        return {"type": sp_type, "data": {"entity1": {"entry_name": entry_name}}}

    assert parse_signaling_protein(sp("gnas2_human")) == "Gs"
    assert parse_signaling_protein(sp("gnai1_human")) == "Gi/o"
    assert parse_signaling_protein(sp("gnaq_human")) == "Gq/11"
    assert parse_signaling_protein(sp("gna11_human")) == "Gq/11"
    assert parse_signaling_protein(sp("gna13_human")) == "G12/13"


def test_parse_signaling_protein_arrestin():
    sp = {"type": "Arrestin", "data": {"entity1": {"entry_name": "arrb2_human"}}}
    assert parse_signaling_protein(sp) == "β-Arrestin2"


def test_parse_signaling_protein_none():
    assert parse_signaling_protein(None) is None
    assert parse_signaling_protein({}) is None


# ── Bug 1 — select_primary_ligand ──

def test_select_primary_ligand_pharmacological():
    """약리학적 function이 있는 리간드를 용매보다 우선 선택."""
    ligands = [
        {"name": "glycerol", "PDB": "GOL", "function": "buffer"},          # 용매
        {"name": "IHCH-7179", "PDB": "EZX", "function": "antagonist"},     # 약물
    ]
    assert select_primary_ligand(ligands)["name"] == "IHCH-7179"


def test_select_primary_ligand_skip_solvent():
    """용매(GOL/SO4)만 있으면 None 반환. 실제 GPCRdb 응답처럼 PDB 키 사용."""
    ligands = [
        {"name": "glycerol", "PDB": "GOL", "function": "binding"},
        {"name": "sulfate", "PDB": "SO4", "function": "binding"},
    ]
    assert select_primary_ligand(ligands) is None


def test_select_primary_ligand_short_drug_name_not_solvent():
    """짧은 약물명이 SOLVENT_CODES와 겹쳐도(예: name='NA') PDB 키로만 판정하므로 폐기되지 않는다 (M2)."""
    ligands = [
        {"name": "NA", "PDB": "XYZ", "function": "antagonist"},
    ]
    result = select_primary_ligand(ligands)
    assert result is not None
    assert result["name"] == "NA"


def test_select_primary_ligand_empty():
    assert select_primary_ligand([]) is None


# ── Bug 3/4 — stabilizing agents / 제목 파싱 ──

def test_parse_stabilizing_agents_string_list():
    """stabilizing_agents가 문자열 배열인 경우."""
    fusion, antibody = parse_stabilizing_agents(["BRIL", "T4L"])
    assert "BRIL" in (fusion or "")


def test_parse_stabilizing_agents_dict_list():
    """stabilizing_agents가 딕셔너리 배열인 경우."""
    fusion, antibody = parse_stabilizing_agents([{"name": "BRIL"}, {"name": "P2C2-Fab"}])
    assert fusion == "BRIL"
    assert "Fab" in (antibody or "")


def test_parse_stabilizing_agents_empty():
    assert parse_stabilizing_agents([]) == (None, None)
    assert parse_stabilizing_agents(None) == (None, None)


def test_fusion_title_parser():
    assert extract_fusion_from_title("Crystal structure with BRIL fusion") == "BRIL"
    assert extract_fusion_from_title("Structure of receptor with T4L") == "T4L"
    assert extract_fusion_from_title("Cryo-EM structure of receptor") is None


def test_fusion_from_polymer_description_bril():
    """RCSB polymer 설명의 'Soluble cytochrome b562'를 BRIL로 인식."""
    text = "5-hydroxytryptamine receptor 2A,Soluble cytochrome b562"
    assert extract_fusion_from_title(text) == "BRIL"


def test_antibody_p2c2_fab():
    title = "Crystal Structure of 5-HT2A Receptor Bound to P2C2-Fab"
    assert extract_antibody_from_title(title) == "P2C2-Fab"


def test_antibody_fab_generic():
    result = extract_antibody_from_title("5-HT2B with Fab fragment")
    assert result is not None and "Fab" in result


def test_antibody_scfv_from_polymer():
    """polymer 설명의 single-chain variable fragment를 scFv로 인식."""
    assert extract_antibody_from_title("Single-chain variable fragment 16") == "scFv"


# ── Bug 5 — 리간드 이름 정규화 ──

def test_normalize_lumateperone():
    assert normalize_ligand_name("LUMATEPERONE") == "Lumateperone"


def test_normalize_lisuride():
    assert normalize_ligand_name("lisuride") == "Lisuride"


def test_normalize_lsd_alias():
    assert normalize_ligand_name("Lysergide") == "LSD"


def test_normalize_keeps_code_style_name():
    """IHCH-7179 같은 코드형 이름은 Title Case로 망가뜨리지 않는다."""
    assert normalize_ligand_name("IHCH-7179") == "IHCH-7179"


# ── Bug 2 — 리간드 이름 해석 (네트워크 불필요 경로) ──

def test_resolve_known_ligand_code():
    """KNOWN_LIGAND_NAMES에 있는 유일 엔트리 EZX → IHCH-7179 (네트워크 불필요).

    M2 정리 이후 사전에는 IHCH-7179만 남는다 (RCSB CCD/GPCRdb 검증 결과 다른
    엔트리는 RCSB CCD의 화합물과 다르거나 PDB 코드 자체가 존재하지 않음).
    """
    assert asyncio.run(resolve_ligand_name("EZX")) == "IHCH-7179"


def test_resolve_already_common_name():
    """이미 일반명이면 정규화만 거쳐 반환 (네트워크 불필요)."""
    assert asyncio.run(resolve_ligand_name("Lisuride")) == "Lisuride"


def test_parse_structure_item():
    raw = {
        "pdb_code": "6A93",
        "preferred_chain": "A",
        "state": "Inactive",
        "ligands": [{"name": "risperidone", "function": "Antagonist", "PDB": "8NU"}],
        "signalling_protein": None,
    }
    item = _parse_structure_item(raw)
    assert item["pref_chain"] == "A"
    assert item["state"] == "Inactive"
    assert item["ligand"] is None          # 호출자가 resolve 전이므로 None
    assert item["_ligand_raw"] == "risperidone"
    assert item["ligand_modality"] == "Antagonist"
    assert item["signaling_protein"] is None
    # m10: stabilizing_agents가 없으면 fusion/antibody는 None — server.py에서
    # polymer/title fallback이 채운다.
    assert item["fusion_protein"] is None
    assert item["antibody"] is None


def test_parse_structure_item_with_stabilizing_agents():
    """m10: GPCRdb structure에 stabilizing_agents가 들어오면 fusion/antibody가 채워진다."""
    raw = {
        "pdb_code": "8JT8",
        "preferred_chain": "A",
        "state": "Inactive",
        "ligands": [],
        "signalling_protein": None,
        "stabilizing_agents": [{"name": "BRIL"}, {"name": "P2C2-Fab"}],
    }
    item = _parse_structure_item(raw)
    assert item["fusion_protein"] == "BRIL"
    assert item["antibody"] is not None and "Fab" in item["antibody"]


# ── M1 — PubChem 응답 파싱 (Title → IUPACName fallback + 길이 상한) ──

def test_pubchem_url_includes_both_properties():
    """M1: PubChem URL 템플릿에 Title과 IUPACName이 모두 포함되어야 한다."""
    from tools.gpcrdb import PUBCHEM_NAME_URL
    assert "Title" in PUBCHEM_NAME_URL
    assert "IUPACName" in PUBCHEM_NAME_URL


def test_pubchem_title_with_mock_iupac_fallback(monkeypatch):
    """M1: Title이 없을 때 IUPACName으로 fallback 한다."""
    import json
    from tools.gpcrdb import _pubchem_title

    class FakeResp:
        status_code = 200

        def json(self):
            # Title은 비어 있고 IUPACName만 있음 — 짧은 일반명
            return {
                "PropertyTable": {
                    "Properties": [{"Title": None, "IUPACName": "ShortName"}]
                }
            }

    class FakeClient:
        async def get(self, url):
            return FakeResp()

    name = asyncio.run(_pubchem_title("FOO", client=FakeClient()))
    assert name == "ShortName"


def test_pubchem_title_skips_long_iupac(monkeypatch):
    """M1: Title이 비어있고 IUPACName이 길이 상한을 넘으면 None을 반환한다 (raw fallback 유도)."""
    from tools.gpcrdb import _MAX_READABLE_LIGAND_NAME_LEN, _pubchem_title

    very_long = "A" * (_MAX_READABLE_LIGAND_NAME_LEN + 5)

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "PropertyTable": {
                    "Properties": [{"Title": None, "IUPACName": very_long}]
                }
            }

    class FakeClient:
        async def get(self, url):
            return FakeResp()

    name = asyncio.run(_pubchem_title("FOO", client=FakeClient()))
    assert name is None


def test_pubchem_title_prefers_title_over_iupac():
    """M1: Title이 있으면 IUPACName보다 우선."""
    from tools.gpcrdb import _pubchem_title

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "PropertyTable": {
                    "Properties": [{"Title": "Risperidone", "IUPACName": "long-iupac-name-here"}]
                }
            }

    class FakeClient:
        async def get(self, url):
            return FakeResp()

    name = asyncio.run(_pubchem_title("FOO", client=FakeClient()))
    assert name == "Risperidone"


# ── M2 — KNOWN_LIGAND_NAMES 큐레이션 정리 ──

def test_known_ligand_names_minimal():
    """M2: 검증 안 된 매핑은 제거되었다. 남은 유일 엔트리는 EZX → IHCH-7179."""
    from tools.gpcrdb import KNOWN_LIGAND_NAMES
    # 검증 결과 RCSB CCD와 일치하지 않거나 코드 자체가 없는 엔트리는 모두 제거됨.
    for removed in ("LSD", "PSI", "LIS", "ZOL", "CLZ", "RSP", "LUM", "QTP",
                    "OLZ", "ARI", "5HT", "DOM", "NE", "EPI", "ALR", "TIM",
                    "MTH", "YEQ", "3IQ", "CAU", "ERM"):
        assert removed not in KNOWN_LIGAND_NAMES, (
            f"{removed}는 RCSB CCD에서 다른 화합물에 할당되어 있어 제거되어야 한다"
        )
    # EZX는 8JT8(IHCH-7179) 문헌 매핑이므로 유지
    assert KNOWN_LIGAND_NAMES.get("EZX") == "IHCH-7179"


# ── m2 — LIGAND_CACHE_LOCKS 상한 ──

def test_ligand_cache_locks_bounded():
    """m2: 락 dict가 _LIGAND_LOCK_MAX_SIZE를 넘지 않는다."""
    from tools.gpcrdb import (
        _LIGAND_CACHE_LOCKS,
        _LIGAND_LOCK_MAX_SIZE,
        _get_or_create_lock,
        clear_ligand_cache,
    )
    clear_ligand_cache()
    # 상한의 1.5배 만큼 lock 요청 → dict 크기는 상한 이하여야 한다
    for i in range(_LIGAND_LOCK_MAX_SIZE + 100):
        _get_or_create_lock(f"KEY_{i}")
    assert len(_LIGAND_CACHE_LOCKS) <= _LIGAND_LOCK_MAX_SIZE
    clear_ligand_cache()


# ── m3 — ContextVar 카운터 ──

def test_failure_counters_use_contextvar():
    """m3: 카운터가 ContextVar 기반이며 consume_*() 으로 정상 회수된다."""
    from tools.gpcrdb import (
        _incr_pubchem_failure,
        _incr_ligand_resolution_failure,
        consume_pubchem_failures,
        consume_ligand_resolution_failures,
    )

    # 사전 상태 정리
    consume_pubchem_failures()
    consume_ligand_resolution_failures()

    _incr_pubchem_failure()
    _incr_pubchem_failure()
    _incr_ligand_resolution_failure()
    assert consume_pubchem_failures() == 2
    assert consume_ligand_resolution_failures() == 1
    # 회수 후 0으로 리셋
    assert consume_pubchem_failures() == 0
    assert consume_ligand_resolution_failures() == 0


# ── m9 — PubChem 미수록 음의 캐시 ──

def test_pubchem_miss_negatively_cached():
    """m9: PubChem 404(미수록)에서도 raw가 캐시되어 같은 코드를 다시 두드리지 않는다."""
    from tools.gpcrdb import (
        LIGAND_NAME_CACHE,
        clear_ligand_cache,
        resolve_ligand_name,
    )

    clear_ligand_cache()

    call_count = {"n": 0}

    class FakeResp404:
        status_code = 404

        def json(self):  # 호출되어선 안 됨
            return {}

    class FakeClient:
        async def get(self, url):
            call_count["n"] += 1
            return FakeResp404()

    # 처음 호출 — PubChem 404 → raw 캐시 저장
    result1 = asyncio.run(resolve_ligand_name("ZZZ", client=FakeClient()))
    # 두 번째 호출 — 캐시에서 곧장 반환되어야 함
    result2 = asyncio.run(resolve_ligand_name("ZZZ", client=FakeClient()))

    assert result1 == "ZZZ"
    assert result2 == "ZZZ"
    # 두 번째 호출에서는 PubChem 을 다시 두드리지 않아야 한다
    assert call_count["n"] == 1
    # 음의 캐시 키 존재 확인
    assert "ZZZ" in LIGAND_NAME_CACHE
    clear_ligand_cache()


# ── m5/Nit — PHARMACOLOGICAL_FUNCTIONS / BINDING_FUNCTIONS 분리 ──

def test_pharmacological_functions_excludes_binding():
    """m5: 두 상수가 분리되어 있고 binding은 PHARMACOLOGICAL_FUNCTIONS 에서 제외된다."""
    from tools.gpcrdb import BINDING_FUNCTIONS, PHARMACOLOGICAL_FUNCTIONS
    assert "binding" not in PHARMACOLOGICAL_FUNCTIONS
    assert "binding" in BINDING_FUNCTIONS
    assert PHARMACOLOGICAL_FUNCTIONS.isdisjoint(BINDING_FUNCTIONS)


def test_select_primary_ligand_pharmacological_beats_binding():
    """m5: pharmacological function 이 'binding' function 보다 우선."""
    ligands = [
        {"name": "bound-thing", "PDB": "BND", "function": "binding"},
        {"name": "drug", "PDB": "DRG", "function": "antagonist"},
    ]
    result = select_primary_ligand(ligands)
    assert result is not None
    assert result["name"] == "drug"


# --------------------------------------------------------------------------
# 네트워크 테스트 — 실제 GPCRdb / PubChem API 호출
# --------------------------------------------------------------------------

@pytest.mark.network
def test_check_gpcr_htr2a():
    is_gpcr, slug = asyncio.run(check_gpcr("5HT2A_HUMAN"))
    assert is_gpcr is True
    assert slug == "5ht2a_human"


@pytest.mark.network
def test_check_gpcr_egfr():
    is_gpcr, slug = asyncio.run(check_gpcr("EGFR_HUMAN"))
    assert is_gpcr is False
    assert slug is None


@pytest.mark.network
def test_gpcrdb_structures_htr2a():
    """5-HT2A 구조 목록 — 6A93의 리간드가 일반명으로 정규화되는지 확인."""
    result = asyncio.run(get_gpcrdb_structures("5ht2a_human"))
    assert len(result) > 0
    assert "6A93" in result
    assert result["6A93"]["state"] == "Inactive"
    assert result["6A93"]["ligand"] == "Risperidone"
    assert result["6A93"]["ligand_modality"] == "Antagonist"


@pytest.mark.network
def test_gpcrdb_8jt8_ligand_resolved():
    """8JT8의 리간드가 PDB 코드(EZX)가 아닌 일반명으로 해석되는지 확인 (Bug 2)."""
    result = asyncio.run(get_gpcrdb_structures("5ht2a_human"))
    assert "8JT8" in result
    entry = result["8JT8"]
    assert entry["ligand"] == "IHCH-7179"   # EZX → 큐레이션 사전
    assert entry["ligand"] != "EZX"
    assert entry["ligand_modality"] is not None


@pytest.mark.network
def test_gpcrdb_single_7wc7():
    """7WC7 단일 구조 — 리간드 일반명/정규화 확인."""
    item = asyncio.run(get_gpcrdb_single("7WC7"))
    assert item is not None
    assert item["state"] == "Inactive"
    assert item["ligand"] == "Lisuride"
    assert item["ligand_modality"] == "Agonist"


@pytest.mark.network
def test_gpcrdb_single_non_gpcr():
    """비GPCR 구조(7T9K) → None."""
    assert asyncio.run(get_gpcrdb_single("7T9K")) is None


@pytest.mark.network
def test_pubchem_title_lookup():
    """PubChem Title 조회 동작 확인."""
    assert asyncio.run(_pubchem_title("aspirin")) == "Aspirin"
