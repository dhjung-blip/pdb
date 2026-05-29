"""PDB 연구 자동화 MCP 서버의 Pydantic 데이터 모델."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """PDB entry에 연결된 논문 정보."""

    title: Optional[str] = None
    authors: Optional[str] = None  # "Last, F. M.; Last2, F. M.; ..." 형태 (세미콜론 구분)
    journal: Optional[str] = None  # 저널 약어. 예: "Science", "J.Med.Chem."
    year: Optional[int] = None
    volume: Optional[str] = None  # 저널 권(volume)
    page_first: Optional[str] = None  # 시작 페이지
    page_last: Optional[str] = None  # 끝 페이지
    doi: Optional[str] = None
    pmid: Optional[str] = None


class PDBEntry(BaseModel):
    """단일 PDB 실험 구조의 메타데이터."""

    pdb_id: str  # 예: "7T9K"
    resolution: Optional[float] = None  # 단위: Å, NMR 등은 None
    method: Optional[str] = None  # "X-RAY DIFFRACTION", "ELECTRON MICROSCOPY", "SOLUTION NMR"
    released_date: Optional[str] = None  # "YYYY-MM-DD" 형태
    title: Optional[str] = None  # PDB entry 제목
    citation: Optional[Citation] = None
    # RCSB polymer entity 설명 목록 (Fusion protein / Antibody 추출용)
    polymer_descriptions: List[str] = Field(default_factory=list)

    # ── GPCR 확장 필드 (GPCR 타깃일 때만 채워짐, 아니면 None) ──
    pref_chain: Optional[str] = None  # 예: "A"
    state: Optional[str] = None  # "Active" | "Inactive" | "Intermediate"
    ligand: Optional[str] = None  # 예: "risperidone", "lisuride"
    ligand_modality: Optional[str] = None  # "Agonist" | "Antagonist" | "Inverse agonist" 등
    signaling_protein: Optional[str] = None  # "Gs" | "Gi/o" | "Gq/11" | "G12/13" 등
    fusion_protein: Optional[str] = None  # "BRIL" | "T4L" | "mT4L" 등
    antibody: Optional[str] = None  # "P2C2-Fab" | "Nanobody" 등
    is_gpcr: bool = False  # GPCR 타깃의 구조로 처리되었는지 여부


class UniProtResult(BaseModel):
    """UniProt 검색 결과 — 단백질 식별 정보 + PDB ID 목록."""

    accession: str  # 예: "P00533"
    entry_name: str  # 예: "EGFR_HUMAN"
    protein_name: str  # 예: "Epidermal growth factor receptor"
    gene_name: Optional[str] = None
    organism: Optional[str] = None
    pdb_ids: List[str] = Field(default_factory=list)  # UniProt에 등록된 PDB ID 목록

    # ── GPCR 확장 필드 ──
    is_gpcr: bool = False  # GPCRdb에서 GPCR로 인식되는지 여부
    gpcrdb_slug: Optional[str] = None  # GPCRdb 내부 식별자. 예: "5ht2a_human"


class SearchResult(BaseModel):
    """search_target 워크플로우의 최종 결과."""

    query: str
    uniprot: UniProtResult
    structures: List[PDBEntry] = Field(default_factory=list)
    total_count: int = 0
    exported_file: Optional[str] = None  # Excel 저장 시 파일 경로
    gpcrdb_count: Optional[int] = None  # GPCRdb 메타데이터가 병합된 구조 수
    # RCSB Search API로 추가 발견된 PDB ID (UniProt cross-reference에 아직 없는 신규 구조)
    unindexed_pdb_ids: List[str] = Field(default_factory=list)
    # UniProt cross-reference에 등록된 PDB ID 수 — union 이전 카운트
    uniprot_indexed_count: int = 0


# ==========================================================================
# Phase 5 — 리서치 보조용(할루시네이션 방지) 응답 모델
# 모든 모델은 외부 권위 있는 소스에서 가져온 원본을 그대로 담고, 알 수 없는
# 값은 None / 빈 리스트로 둔다. 모든 모델은 출처 URL(`source_url`)을 포함한다.
# ==========================================================================


class LigandDetail(BaseModel):
    """PubChem + ChEMBL + IUPHAR/GtoPdb 통합 리간드 상세 정보."""

    query: str  # 원본 입력 (이름 또는 코드)
    common_name: Optional[str] = None  # 사람이 읽기 좋은 대표 이름
    pubchem_cid: Optional[int] = None
    chembl_id: Optional[str] = None
    iuphar_ligand_id: Optional[int] = None
    smiles: Optional[str] = None
    canonical_smiles: Optional[str] = None
    inchi: Optional[str] = None
    inchi_key: Optional[str] = None
    iupac_name: Optional[str] = None
    molecular_formula: Optional[str] = None
    molecular_weight: Optional[float] = None  # g/mol
    xlogp: Optional[float] = None
    h_bond_donors: Optional[int] = None
    h_bond_acceptors: Optional[int] = None
    tpsa: Optional[float] = None  # Topological polar surface area
    rotatable_bonds: Optional[int] = None
    max_phase: Optional[int] = None  # ChEMBL development phase (-1~4)
    drug_type: Optional[str] = None
    indication_class: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    sources: dict = Field(default_factory=dict)  # 라벨 → URL
    notes: List[str] = Field(default_factory=list)  # 부분 실패/장애 안내


class Bioactivity(BaseModel):
    """단일 화합물-타깃 활성 측정값."""

    ligand_name: Optional[str] = None
    ligand_chembl_id: Optional[str] = None
    target_chembl_id: Optional[str] = None
    standard_type: Optional[str] = None  # Ki / Kd / IC50 / EC50
    standard_relation: Optional[str] = None  # "=" / "<" / ">"
    standard_value: Optional[float] = None  # 보통 nM 단위
    standard_units: Optional[str] = None
    pchembl_value: Optional[float] = None  # -log10(value in M)
    assay_type: Optional[str] = None  # B(inding) / F(unctional) / A(DME) 등
    assay_description: Optional[str] = None
    document_chembl_id: Optional[str] = None
    pubmed_id: Optional[str] = None
    source: str = "ChEMBL"  # "ChEMBL" | "IUPHAR"
    source_url: Optional[str] = None


class TargetBioactivities(BaseModel):
    """특정 타깃에 대해 보고된 활성 데이터 묶음."""

    target_query: str
    uniprot_accession: Optional[str] = None
    gene_name: Optional[str] = None
    chembl_target_id: Optional[str] = None
    iuphar_target_id: Optional[int] = None
    bioactivities: List[Bioactivity] = Field(default_factory=list)
    total_count: int = 0  # 필터링 전 ChEMBL이 보고한 활성 총 개수
    sources: dict = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class PaperAbstract(BaseModel):
    """PubMed / Europe PMC 한 논문의 메타데이터 + 초록."""

    pmid: Optional[str] = None
    doi: Optional[str] = None
    pmcid: Optional[str] = None
    title: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    abstract: Optional[str] = None
    mesh_terms: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    is_open_access: Optional[bool] = None
    source: str = "Europe PMC"
    source_url: Optional[str] = None


class SequenceFeature(BaseModel):
    """UniProt 서열 feature (ACT_SITE / BINDING / DOMAIN / TRANSMEM 등)."""

    type: str  # feature.type 원본 값
    description: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    ligand: Optional[str] = None  # BINDING 타입에서 결합 리간드명
    evidence: Optional[str] = None  # 근거 코드 (ECO 등)


class SequenceRegion(BaseModel):
    """UniProt 단백질 서열 + 지정 구간 feature 묶음."""

    accession: str
    entry_name: Optional[str] = None
    protein_name: Optional[str] = None
    full_length: int
    start: int  # 1-based
    end: int  # 1-based, inclusive
    sequence: str  # start~end 구간 서열
    full_sequence_returned: bool = False  # 구간이 전체일 때 True
    features: List[SequenceFeature] = Field(default_factory=list)
    source_url: Optional[str] = None


class NaturalVariant(BaseModel):
    """UniProt natural variant — 알려진 missense/SNP 변이."""

    position: int
    wild_type: Optional[str] = None  # 원래 아미노산 (1글자)
    variant: Optional[str] = None  # 치환된 아미노산 (1글자)
    description: Optional[str] = None  # 변이 설명 (질환 포함)
    disease: Optional[str] = None  # 추출된 질환명
    clinical_significance: Optional[str] = None
    dbsnp_id: Optional[str] = None
    clinvar_id: Optional[str] = None


class VariantList(BaseModel):
    """단백질의 알려진 자연 변이 목록."""

    accession: str
    entry_name: Optional[str] = None
    variants: List[NaturalVariant] = Field(default_factory=list)
    total_count: int = 0
    source_url: Optional[str] = None


class BindingSiteResidue(BaseModel):
    """결합부위 잔기 한 개."""

    chain_id: str
    residue_number: int  # auth_seq_id (PDB에 표시되는 번호)
    residue_name: str  # 3글자 코드 (HIS, PHE 등)
    label_seq_id: Optional[int] = None  # SIFTS UniProt 매핑용 라벨 번호


class BindingSite(BaseModel):
    """단일 PDB 구조의 한 리간드 결합부위."""

    pdb_id: str
    site_id: Optional[str] = None  # PDB SITE 레코드 ID
    ligand_code: Optional[str] = None  # PDB 화학성분 코드
    ligand_name: Optional[str] = None
    chain_id: Optional[str] = None  # 리간드가 위치한 체인
    residues: List[BindingSiteResidue] = Field(default_factory=list)
    source: str = "PDBe"  # "PDBe" | "RCSB"
    source_url: Optional[str] = None


class BindingSiteResult(BaseModel):
    """get_binding_site 도구 응답 — 여러 결합부위/리간드 묶음."""

    pdb_id: str
    sites: List[BindingSite] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class AlphaFoldModel(BaseModel):
    """AlphaFold DB 단일 단백질 예측 구조 메타데이터."""

    uniprot_accession: str
    entry_id: Optional[str] = None
    organism: Optional[str] = None
    sequence_length: Optional[int] = None
    mean_plddt: Optional[float] = None
    global_metric_value: Optional[float] = None
    confidence_summary: Optional[str] = None  # "Very high" 등 (mean_plddt 기반)
    model_url_pdb: Optional[str] = None
    model_url_cif: Optional[str] = None
    pae_image_url: Optional[str] = None
    pae_doc_url: Optional[str] = None
    model_version: Optional[str] = None
    source_url: Optional[str] = None


class DiseaseAssociation(BaseModel):
    """OpenTargets target-disease 연관 한 건."""

    disease_id: str  # EFO ID
    disease_name: str
    overall_score: Optional[float] = None  # 0~1, OpenTargets 종합 점수
    therapeutic_areas: List[str] = Field(default_factory=list)


class KnownDrug(BaseModel):
    """OpenTargets 알려진 약물(임상~승인) 한 건."""

    drug_id: Optional[str] = None  # ChEMBL ID
    drug_name: str
    drug_type: Optional[str] = None
    mechanism_of_action: Optional[str] = None
    action_type: Optional[str] = None  # "AGONIST" / "INHIBITOR" 등
    max_phase_for_indication: Optional[int] = None
    indication: Optional[str] = None
    target_status: Optional[str] = None


class TargetIntelligence(BaseModel):
    """OpenTargets에서 본 타깃의 질환·약물 인텔리전스."""

    target_query: str
    ensembl_id: Optional[str] = None
    uniprot_accession: Optional[str] = None
    gene_name: Optional[str] = None
    biotype: Optional[str] = None
    diseases: List[DiseaseAssociation] = Field(default_factory=list)
    known_drugs: List[KnownDrug] = Field(default_factory=list)
    source_url: Optional[str] = None
