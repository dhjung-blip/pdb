"""PDB 구조 텍스트 파싱 유틸리티.

GPCRdb structure API는 Fusion protein / Antibody 정보를 제공하지 않으므로,
**RCSB polymer entity 설명**과 **PDB entry 제목**에서 패턴 매칭으로 추출한다.

추출 함수는 인자로 받은 임의의 텍스트(제목 + polymer 설명을 이어 붙인 문자열)를
검색하므로, 함수명에 `_from_title`이 있어도 polymer 설명에도 그대로 사용한다.
RCSB는 BRIL 융합을 "Soluble cytochrome b562"로, T4 lysozyme을 "Endolysin"으로
기술하므로 해당 표현도 패턴에 포함한다.
"""

from __future__ import annotations

import re

# ── Fusion protein 패턴 ── (앞쪽 우선 — 더 구체적인 패턴을 먼저 둔다)
FUSION_PATTERNS: list[tuple[str, str]] = [
    (r"\bBRIL\b", "BRIL"),
    (r"cytochrome\s*b[\s-]?562", "BRIL"),        # "Soluble cytochrome b562" = BRIL
    (r"\bmT4L\b", "mT4L"),
    (r"\bT4[\s-]?lysozyme\b", "T4L"),
    (r"\bT4L\b", "T4L"),
    (r"\bendolysin\b", "T4L"),                   # RCSB는 T4 lysozyme을 endolysin으로 기술
    (r"\bflavodoxin\b", "Flavodoxin"),
    (r"\brubredoxin\b", "Rubredoxin"),
    (r"\bPGS\b", "PGS"),                         # Pyrococcus glycogen synthase
    (r"glycogen\s*synthase", "PGS"),
]

# ── Antibody / 보조 단백질 패턴 ──
# label이 None이면 매칭된 문자열을 그대로 사용한다.
ANTIBODY_PATTERNS: list[tuple[str, str | None]] = [
    (r"\b[\w-]+-Fab\b", None),                          # "P2C2-Fab" 등 → 그대로
    (r"\bFab\d*\b", "Fab"),                             # "Fab", "Fab1"
    (r"single-chain\s+variable\s+fragment", "scFv"),
    (r"\bscFv\d*\b", "scFv"),                           # "scFv", "scFv16"
    (r"\bnanobody\b", "Nanobody"),
    (r"\bNb\s?\d+\b", None),                            # "Nb35" 등 → 그대로
    (r"\bsybody\b", "Sybody"),
    (r"\bmegabody\b", "Megabody"),
    (r"\bVHH\b", "VHH"),
    (r"\bantibody\b", "Antibody"),                      # 가장 일반적 — 마지막
]


def extract_fusion_from_title(text: str) -> str | None:
    """텍스트(PDB 제목 / polymer 설명)에서 Fusion protein을 추출한다. 없으면 None."""
    if not text:
        return None
    for pattern, label in FUSION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def extract_antibody_from_title(text: str) -> str | None:
    """텍스트(PDB 제목 / polymer 설명)에서 Antibody / 보조 단백질을 추출한다."""
    if not text:
        return None
    for pattern, label in ANTIBODY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return label if label else match.group(0)
    return None
