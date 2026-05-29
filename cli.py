"""PDB 리서치 CLI — Claude Code Skill `/pdb`의 진입점.

사용:
    .venv/bin/python cli.py search HTR2A --json
    .venv/bin/python cli.py family 5-HT2 --targets HTR2A,HTR2B,HTR2C --json
    .venv/bin/python cli.py detail 7WC7 --md

설계:
- 모든 데이터 출력은 stdout. 진행 로그/에러 사유는 stderr.
- 기본 출력 모드는 JSON. `--md`로 마크다운 선택.
- Excel 파일은 절대 생성하지 않는다 (Skill 정책 §5 — xlsx 스킬이 담당).
- 종료 코드: 0=성공, 1=외부 API 실패, 2=입력 검증 실패, 3=부분 실패.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from adapters import runner, formatter


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdb",
        description="PDB 단백질 구조 검색·분석 CLI",
    )

    # 공통 출력 옵션 (subparser 위에 두면 일부 환경에서 인식 못해 각 서브커맨드에 부여)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    def _add_common(sp: argparse.ArgumentParser) -> None:
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--json", action="store_true", help="JSON 출력 (기본)")
        g.add_argument("--md", action="store_true", help="Markdown 출력")
        sp.add_argument("--quiet", action="store_true", help="stderr 진행 로그 억제")

    # 1) search
    s = sub.add_parser("search", help="단일 타깃의 모든 PDB 구조 검색")
    s.add_argument("target", help="유전자명/단백질명. 예: EGFR, HTR2A")
    s.add_argument("--max-resolution", type=float, dest="max_resolution",
                   help="해상도 상한 (Å). 예: 2.5")
    s.add_argument("--min-year", type=int, dest="min_year", help="공개 연도 하한")
    s.add_argument("--ligand-modality", dest="ligand_modality",
                   help="예: Agonist, Antagonist, Inverse agonist")
    s.add_argument("--state", help="예: Active, Inactive, Intermediate")
    s.add_argument("--method", help="예: X-ray, EM, NMR")
    s.add_argument("--sort-by", dest="sort_by",
                   choices=["date", "resolution", "state_then_date"],
                   help="정렬 기준 (기본 date, GPCR은 state_then_date 자동)")
    _add_common(s)

    # 2) family
    f = sub.add_parser("family", help="여러 타깃(수용체 패밀리) 일괄 검색")
    f.add_argument("label", nargs="?", default=None,
                   help="패밀리 표시 이름. 예: 5-HT2, DRD, ADRB")
    f.add_argument("--targets", required=True,
                   help="쉼표로 구분한 유전자명. 예: HTR2A,HTR2B,HTR2C")
    f.add_argument("--family-name", dest="family_name", help="(선택) 명시적 패밀리명")
    f.add_argument("--max-resolution", type=float, dest="max_resolution")
    f.add_argument("--min-year", type=int, dest="min_year")
    f.add_argument("--ligand-modality", dest="ligand_modality")
    f.add_argument("--state")
    f.add_argument("--method")
    f.add_argument("--sort-by", dest="sort_by",
                   choices=["date", "resolution", "state_then_date"])
    _add_common(f)

    # 3) detail
    d = sub.add_parser("detail", help="단일 PDB ID 상세 (GPCR 보강 포함)")
    d.add_argument("pdb_id", help="4자리 PDB ID. 예: 7WC7")
    _add_common(d)

    # 4) compare
    c = sub.add_parser("compare", help="여러 타깃 구조 수/해상도/최신 구조 비교")
    c.add_argument("--targets", required=True,
                   help="쉼표로 구분한 유전자명. 예: EGFR,HER2,MET")
    _add_common(c)

    # 5) ligand
    lg = sub.add_parser("ligand", help="화합물 상세 (PubChem/ChEMBL/IUPHAR)")
    lg.add_argument("query", help="리간드 이름/PDB code/ChEMBL ID/InChIKey")
    _add_common(lg)

    # 6) bioactivity
    bio = sub.add_parser("bioactivity", help="타깃 활성 (Ki/Kd/IC50)")
    bio.add_argument("accession", help="UniProt accession. 예: P28223")
    bio.add_argument("--gene", help="UniProt 매핑 실패 시 사용할 gene symbol")
    bio.add_argument("--min-pchembl", type=float, dest="min_pchembl",
                     help="pChEMBL 컷오프. 예: 6.0")
    bio.add_argument("--types", help="쉼표 구분. 예: Ki,Kd,IC50")
    bio.add_argument("--max", type=int, help="반환 최대 건수 (기본 30)")
    bio.add_argument("--no-iuphar", action="store_true", dest="no_iuphar",
                     help="IUPHAR 조회 건너뛰기 (ChEMBL만)")
    _add_common(bio)

    # 7) paper
    pp = sub.add_parser("paper", help="PMID/DOI 단일 논문 메타 + 초록")
    pp.add_argument("--pmid", help="PubMed ID")
    pp.add_argument("--doi", help="DOI")
    _add_common(pp)

    # 8) papers
    ps = sub.add_parser("papers", help="Europe PMC 논문 검색")
    ps.add_argument("query", help="검색 쿼리")
    ps.add_argument("--max", type=int, help="반환 최대 건수 (기본 5)")
    _add_common(ps)

    # 9) sequence
    sq = sub.add_parser("sequence", help="UniProt 서열 + feature")
    sq.add_argument("accession", help="UniProt accession. 예: P28223")
    sq.add_argument("--start", type=int, help="구간 시작 (1-based)")
    sq.add_argument("--end", type=int, help="구간 끝 (1-based, inclusive)")
    sq.add_argument("--feature-types", dest="feature_types",
                    help="쉼표 구분. 예: ACT_SITE,BINDING,DOMAIN")
    _add_common(sq)

    # 10) variants
    v = sub.add_parser("variants", help="UniProt 자연 변이 (SNP/질환)")
    v.add_argument("accession", help="UniProt accession")
    v.add_argument("--position", type=int, help="잔기 위치")
    v.add_argument("--disease-only", action="store_true", dest="disease_only",
                   help="질환 연관 변이만")
    v.add_argument("--max", type=int, help="최대 건수 (기본 200)")
    _add_common(v)

    # 11) binding
    bd = sub.add_parser("binding", help="PDB 결합부위 잔기")
    bd.add_argument("pdb_id", help="4자리 PDB ID")
    bd.add_argument("--ligand-filter", dest="ligand_filter",
                    help="특정 리간드 코드만")
    bd.add_argument("--include-solvents", action="store_true",
                    dest="include_solvents",
                    help="용매(HOH/EDO 등) 포함")
    _add_common(bd)

    # 12) alphafold
    af = sub.add_parser("alphafold", help="AlphaFold DB 예측 구조 메타")
    af.add_argument("accession", help="UniProt accession")
    _add_common(af)

    # 13) intel
    it = sub.add_parser("intel", help="OpenTargets 질환/약물 인텔리전스")
    it.add_argument("target", help="gene symbol 또는 Ensembl gene ID")
    it.add_argument("--max-diseases", type=int, dest="max_diseases",
                    help="연관 질환 최대 건수 (기본 15)")
    it.add_argument("--max-drugs", type=int, dest="max_drugs",
                    help="known drugs 최대 건수 (기본 15)")
    _add_common(it)

    return p


def _select_mode(args: argparse.Namespace) -> str:
    if getattr(args, "md", False):
        return "md"
    return "json"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "quiet", False):
        print(f"[pdb] dispatch: {args.cmd}", file=sys.stderr)

    try:
        result = asyncio.run(runner.dispatch(args))
    except KeyboardInterrupt:
        print("[pdb] interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # 최후 방어 — 비정상 종료보다는 에러 메시지 노출
        print(f"[pdb] 내부 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    mode = _select_mode(args)
    output = formatter.render(result, mode=mode)
    print(output)

    if not getattr(args, "quiet", False) and result.error:
        print(f"[pdb] error: {result.error}", file=sys.stderr)

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
