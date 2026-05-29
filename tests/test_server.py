"""server.py의 후처리 필터·정렬·렌더링 로직 단위 테스트 (오프라인 전용)."""

from models.schemas import Citation, PDBEntry, SearchResult, UniProtResult
from server import (
    _apply_filters,
    _display_path,
    _render_basic_result,
    _render_gpcr_result,
    _sort_structures,
)


def _e(pdb_id: str, **kwargs) -> PDBEntry:
    """테스트용 PDBEntry 생성 헬퍼."""
    return PDBEntry(pdb_id=pdb_id, **kwargs)


def _sr(entry: PDBEntry, gpcr: bool = False) -> SearchResult:
    """테스트용 SearchResult 생성 헬퍼."""
    return SearchResult(
        query="X",
        uniprot=UniProtResult(
            accession="P00000", entry_name="X_HUMAN",
            protein_name="X protein", gene_name="X", is_gpcr=gpcr,
        ),
        structures=[entry],
        total_count=1,
    )


# --------------------------------------------------------------------------
# 필터 테스트
# --------------------------------------------------------------------------

def test_filter_max_resolution():
    entries = [
        _e("A", resolution=1.5),
        _e("B", resolution=3.0),
        _e("C", resolution=None),  # NMR — 해상도 필터 시 제외
    ]
    filtered, notes = _apply_filters(entries, max_resolution=2.5)
    assert [e.pdb_id for e in filtered] == ["A"]
    assert notes == ["해상도 ≤ 2.5Å"]


def test_filter_min_year():
    entries = [
        _e("A", released_date="2019-01-01"),
        _e("B", released_date="2023-05-01"),
        _e("C"),  # 날짜 없음 — 제외
    ]
    filtered, _ = _apply_filters(entries, min_year=2020)
    assert [e.pdb_id for e in filtered] == ["B"]


def test_filter_method():
    entries = [_e("A", method="X-RAY DIFFRACTION"), _e("B", method="EM")]
    em_only, _ = _apply_filters(entries, method_filter="EM")
    assert [e.pdb_id for e in em_only] == ["B"]
    xray_only, _ = _apply_filters(entries, method_filter="X-ray")
    assert [e.pdb_id for e in xray_only] == ["A"]


def test_filter_state_and_modality():
    entries = [
        _e("A", state="Active", ligand_modality="Agonist"),
        _e("B", state="Inactive", ligand_modality="Antagonist"),
        _e("C", state="Active", ligand_modality="Antagonist"),
    ]
    active, _ = _apply_filters(entries, state_filter="active")
    assert {e.pdb_id for e in active} == {"A", "C"}
    antagonist, _ = _apply_filters(entries, ligand_modality_filter="Antagonist")
    assert {e.pdb_id for e in antagonist} == {"B", "C"}


def test_filter_combined():
    entries = [
        _e("A", resolution=2.0, released_date="2022-01-01"),
        _e("B", resolution=2.0, released_date="2018-01-01"),
        _e("C", resolution=3.5, released_date="2023-01-01"),
    ]
    filtered, notes = _apply_filters(entries, max_resolution=2.5, min_year=2020)
    assert [e.pdb_id for e in filtered] == ["A"]
    assert len(notes) == 2


def test_filter_none_is_noop():
    entries = [_e("A"), _e("B")]
    filtered, notes = _apply_filters(entries)
    assert filtered == entries
    assert notes == []


# --------------------------------------------------------------------------
# 정렬 테스트
# --------------------------------------------------------------------------

def test_sort_state_then_date():
    entries = [
        _e("A", state="Active", released_date="2020-01-01"),
        _e("B", state="Inactive", released_date="2019-01-01"),
        _e("C", state="Inactive", released_date="2023-01-01"),
        _e("D", state=None, released_date="2025-01-01"),
        _e("E", state="Intermediate", released_date="2024-01-01"),
    ]
    ordered = _sort_structures(entries, "state_then_date")
    # Inactive(최신순) → Active → Intermediate → State 없음
    assert [e.pdb_id for e in ordered] == ["C", "B", "A", "E", "D"]


def test_sort_date_desc():
    entries = [
        _e("A", released_date="2020-01-01"),
        _e("B", released_date="2023-01-01"),
        _e("C", released_date="2019-01-01"),
    ]
    ordered = _sort_structures(entries, "date")
    assert [e.pdb_id for e in ordered] == ["B", "A", "C"]


def test_sort_gpcr_priority():
    """gpcr_priority=True면 GPCRdb 수록(pref_chain 존재) 구조가 앞으로."""
    entries = [
        _e("A", released_date="2025-01-01"),                 # 미수록
        _e("B", released_date="2020-01-01", pref_chain="A"), # 수록
    ]
    ordered = _sort_structures(entries, "date", gpcr_priority=True)
    assert [e.pdb_id for e in ordered] == ["B", "A"]


# --------------------------------------------------------------------------
# 테이블 '논문' 컬럼 — ACS 인용 형식
# --------------------------------------------------------------------------

def test_basic_table_citation_is_acs():
    """비GPCR 테이블의 논문 컬럼이 ACS 전체 인용 형식으로 출력된다."""
    entry = _e(
        "4AF3", resolution=2.75, method="X-ray", released_date="2012-04-11",
        citation=Citation(
            title="Crystal Structure of Aurora B",
            authors="Elkins, J.M.; Santaguida, S. et al.",
            journal="J.Med.Chem.", year=2012, volume="55",
            page_first="7841", page_last="7848", doi="10.1021/jm3008954",
        ),
    )
    text = _render_basic_result(_sr(entry), [entry], 1, 1, "date", [], None)
    assert "J.Med.Chem. 2012, 55, 7841–7848. DOI: 10.1021/jm3008954." in text


def test_gpcr_table_citation_is_acs():
    """GPCR 테이블의 논문 컬럼이 ACS 전체 인용 형식으로 출력된다."""
    entry = _e(
        "7SRR", resolution=2.9, method="EM", released_date="2022-09-01",
        state="Active", is_gpcr=True,
        citation=Citation(
            title="Signaling snapshots of a receptor",
            authors="Cao, C.; Barros-Alvarez, X. et al.",
            journal="Neuron", year=2022, volume="110",
            page_first="3154", page_last="3167", doi="10.1016/j.neuron.x",
        ),
    )
    text = _render_gpcr_result(
        _sr(entry, gpcr=True), [entry], 1, 1, "state_then_date", [], None, None
    )
    assert "Neuron 2022, 110, 3154–3167. DOI: 10.1016/j.neuron.x." in text


# --------------------------------------------------------------------------
# _display_path — Docker 컨테이너 경로 → 호스트 경로 변환
# --------------------------------------------------------------------------

def test_display_path_docker_translation(monkeypatch):
    """PDB_MCP_DISPLAY_DIR 지정 시 컨테이너 경로를 호스트 경로로 치환."""
    monkeypatch.setenv("PDB_MCP_OUTPUT_DIR", "/data/output")
    monkeypatch.setenv("PDB_MCP_DISPLAY_DIR", "/Users/x/Desktop/output")
    monkeypatch.delenv("PDB_MCP_PUBLIC_BASE_URL", raising=False)
    assert _display_path("/data/output/EGFR.xlsx") == "/Users/x/Desktop/output/EGFR.xlsx"


def test_display_path_public_base_url(monkeypatch):
    """PUBLIC_BASE_URL 지정 시 다운로드 URL을 반환."""
    monkeypatch.setenv("PDB_MCP_PUBLIC_BASE_URL", "https://node05.example/mcp/")
    assert (
        _display_path("/data/output/EGFR structures.xlsx")
        == "https://node05.example/mcp/files/EGFR%20structures.xlsx"
    )


def test_display_path_no_env_passthrough(monkeypatch):
    """DISPLAY_DIR 미지정이면 경로를 그대로 반환 (직접 실행 시)."""
    monkeypatch.delenv("PDB_MCP_DISPLAY_DIR", raising=False)
    monkeypatch.delenv("PDB_MCP_PUBLIC_BASE_URL", raising=False)
    assert _display_path("/abs/output/EGFR.xlsx") == "/abs/output/EGFR.xlsx"
