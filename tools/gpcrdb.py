"""GPCRdb API 클라이언트 — GPCR 확장 데이터 조회.

GPCRdb는 GPCR 전용 큐레이션 DB로, 구조별 State / Ligand / Ligand modality /
Signaling protein / Preferred chain 정보를 제공한다.

실제 GPCRdb structure API 응답 기준 메모:
- structure 엔드포인트는 `stabilizing_agents` 필드를 제공하지 않는다.
  → Fusion protein / Antibody는 RCSB polymer entity 설명 + PDB 제목 파싱으로 채운다.
- 리간드 modality는 ligand의 `function` 필드에 직접 들어 있다.
- 리간드 `name`이 PDB 화학성분 코드(EZX, 3IQ 등)나 긴 IUPAC명일 수 있어,
  큐레이션 사전 + PubChem 조회로 사람이 읽을 수 있는 이름으로 변환한다.
- `signalling_protein`은 G단백질/arrestin 복합체 구조에만 존재하는 중첩 dict 이다.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from collections import OrderedDict
from urllib.parse import quote

import httpx

GPCRDB_BASE = "https://gpcrdb.org/services"
PUBCHEM_NAME_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}"
    "/property/Title,IUPACName/JSON"
)

# PubChem이 반환하는 IUPACName(또는 Title)이 이 길이를 넘으면
# 사람이 읽을 수 있는 일반명으로 보기 어렵다고 판단해 raw 식별자로 fallback 한다.
_MAX_READABLE_LIGAND_NAME_LEN = 80

# GPCRdb API 병렬 동시 요청 최대 개수 (기본 — GPCRdb 호출에만 사용)
MAX_CONCURRENCY = 5
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# PubChem은 독립된 API이므로 별도 세마포어로 분리해
# GPCRdb 호출과 동시성 한도가 서로를 막지 않게 한다 (m7).
_PUBCHEM_CONCURRENCY = 5
_pubchem_semaphore = asyncio.Semaphore(_PUBCHEM_CONCURRENCY)

RETRY_DELAY = 1.0


# --------------------------------------------------------------------------
# Modality / Signaling protein 정규화
# --------------------------------------------------------------------------

# 리간드 modality 정규화 맵
MODALITY_MAP = {
    "agonist": "Agonist",
    "partial agonist": "Partial agonist",
    "antagonist": "Antagonist",
    "inverse agonist": "Inverse agonist",
    "ago-antagonist": "Ago-antagonist",
    "positive allosteric modulator": "PAM",
    "negative allosteric modulator": "NAM",
    "allosteric agonist": "Allosteric agonist",
}


def normalize_modality(raw: str | None) -> str | None:
    """GPCRdb 리간드 function 값을 표준 modality 표기로 정규화한다."""
    if not raw:
        return None
    return MODALITY_MAP.get(raw.lower().strip(), raw.strip().capitalize())


# Gα 서브유닛 entry_name 접두사 → G단백질 패밀리 라벨
_G_ALPHA_FAMILY: list[tuple[str, str]] = [
    ("gnas", "Gs"),
    ("gnal", "Gs"),       # Golf (Gs 계열)
    ("gnao", "Gi/o"),
    ("gnai", "Gi/o"),
    ("gnaz", "Gi/o"),
    ("gnat", "Gt"),       # Transducin
    ("gna11", "Gq/11"),
    ("gna14", "Gq/11"),
    ("gna15", "Gq/11"),
    ("gnaq", "Gq/11"),
    ("gna12", "G12/13"),
    ("gna13", "G12/13"),
]


def parse_signaling_protein(signalling_protein) -> str | None:
    """GPCRdb structure의 signalling_protein dict에서 신호단백질 라벨을 추출한다."""
    if not signalling_protein or not isinstance(signalling_protein, dict):
        return None

    sp_type = (signalling_protein.get("type") or "").strip()
    data = signalling_protein.get("data") or {}
    first = data.get("entity1") or {}
    entry_name = (first.get("entry_name") or "").lower()

    # Arrestin 복합체
    if "arrestin" in sp_type.lower() or "arrb" in entry_name or "arrestin" in entry_name:
        if "arrb2" in entry_name or "arrestin-3" in entry_name:
            return "β-Arrestin2"
        if "arrb1" in entry_name or "arrestin-2" in entry_name:
            return "β-Arrestin1"
        return "Arrestin"

    # G단백질 복합체 — Gα 서브유닛으로 패밀리 판별
    for prefix, label in _G_ALPHA_FAMILY:
        if entry_name.startswith(prefix):
            return label

    return sp_type or None


# --------------------------------------------------------------------------
# Stabilizing agents 분류 (Fusion / Antibody)
# --------------------------------------------------------------------------

FUSION_KEYWORDS = [
    "bril", "t4l", "t4 lysozyme", "mt4l", "flavodoxin",
    "rubredoxin", "thermostabilized", "apocytochrome", "cytochrome b562",
    "endolysin", "pgs", "glycogen synthase",
]
ANTIBODY_KEYWORDS = [
    "fab", "nanobody", "nb", "vhh", "scfv", "sybody", "megabody", "antibody",
]


def parse_stabilizing_agents(agents) -> tuple[str | None, str | None]:
    """stabilizing_agents → (fusion_protein, antibody) 튜플.

    GPCRdb structure 엔드포인트는 현재 이 필드를 제공하지 않으나, 입력이 문자열
    리스트 / 딕셔너리 리스트 등 어떤 형태로 와도 견디도록 방어적으로 처리한다.
    """
    if not agents:
        return None, None

    fusions: list[str] = []
    antibodies: list[str] = []

    items = agents if isinstance(agents, list) else [agents]
    for agent in items:
        if isinstance(agent, dict):
            display = (
                agent.get("name")
                or agent.get("display_name")
                or agent.get("protein_name")
                or ""
            )
        elif isinstance(agent, str):
            display = agent
        else:
            continue

        name = display.lower()
        if any(k in name for k in FUSION_KEYWORDS):
            fusions.append(display)
        elif any(k in name for k in ANTIBODY_KEYWORDS):
            antibodies.append(display)

    return (
        " / ".join(fusions) if fusions else None,
        " / ".join(antibodies) if antibodies else None,
    )


# --------------------------------------------------------------------------
# Primary ligand 선택
# --------------------------------------------------------------------------

# GPCRdb가 ligand function 필드에 쓰는 약리학적 값 (실제 약물 분류용).
# "binding"은 비약리학적 일반 결합 — 별도 BINDING_FUNCTIONS 으로 분리해 selector
# 의 우선순위(1순위: 약리학적, 2순위: binding)가 가독적으로 드러나게 한다 (m5).
PHARMACOLOGICAL_FUNCTIONS = {
    "agonist", "antagonist", "inverse_agonist", "inverse agonist",
    "partial_agonist", "partial agonist", "ago-antagonist", "ago_antagonist",
    "allosteric_modulator", "pam", "nam",
}
BINDING_FUNCTIONS = {"binding"}

# 일반적인 용매/버퍼/이온 PDB 화학성분 코드 (primary ligand 아님)
SOLVENT_CODES = {
    "EDO", "GOL", "PEG", "PG4", "PGE", "1PE", "MPD", "FMT", "ACE", "ACT",
    "DMS", "SO4", "CL", "NA", "MG", "ZN", "CA", "K", "FE", "NAG", "BMA",
    "OLC", "OLA", "CLR", "PLM", "HOH", "WAT", "H2O", "BU3", "P6G", "TRS",
}


def _is_solvent(ligand: dict) -> bool:
    """리간드가 용매/버퍼/이온인지 PDB chem code로만 판정한다.

    `name` 필드는 일반명(예: "NA" — 짧은 약물명)이 SOLVENT_CODES와 우연히 겹쳐
    정상 약물이 폐기되는 사고를 막기 위해 체크에서 제외한다. 식별은 chem code
    (`PDB` / `pdb_code`)에 한정.
    """
    for key in ("PDB", "pdb_code"):
        value = (ligand.get(key) or "").strip().upper()
        if value in SOLVENT_CODES:
            return True
    return False


def select_primary_ligand(ligands: list[dict]) -> dict | None:
    """GPCRdb ligands 배열에서 연구 대상 리간드(약물)를 선택한다.

    우선순위:
      1. 약리학적 function이 명시된 것 (binding 제외, 용매 제외)
      2. function == "binding" 인 것 (용매 제외)
      3. 용매가 아닌 첫 번째 항목
      4. 그래도 없으면 None
    """
    if not ligands:
        return None

    # 1순위: 약리학적 function이 명시된 것 (binding 제외, 용매 제외) — m5
    for ligand in ligands:
        fn = (ligand.get("function") or ligand.get("function_label") or "").lower().strip()
        if fn in PHARMACOLOGICAL_FUNCTIONS and not _is_solvent(ligand):
            return ligand

    # 2순위: function == "binding" 인 것 (용매 제외) — m5
    for ligand in ligands:
        fn = (ligand.get("function") or ligand.get("function_label") or "").lower().strip()
        if fn in BINDING_FUNCTIONS and not _is_solvent(ligand):
            return ligand

    # 3순위: 용매가 아닌 첫 번째 항목
    for ligand in ligands:
        if not _is_solvent(ligand):
            return ligand

    return None


# --------------------------------------------------------------------------
# 리간드 이름 해석 / 정규화
# --------------------------------------------------------------------------

# 모듈 레벨 LRU 캐시 (조회 키 대문자 → 일반명).
# OrderedDict + 상한 + clear() 메서드로 무제한 성장 / stale 영구 회귀를 막는다.
_LIGAND_CACHE_MAX_SIZE = 1024
LIGAND_NAME_CACHE: "OrderedDict[str, str]" = OrderedDict()

# 키별 asyncio Lock — 동일 키에 대한 동시 PubChem 호출을 dedup.
# 캐시 evict 시 함께 정리되어 무한 성장하지 않는다 (m2).
_LIGAND_CACHE_LOCKS: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
_LIGAND_LOCK_MAX_SIZE = 1024


def _lru_get(key: str) -> str | None:
    """캐시에서 가져오고 LRU 순서를 갱신한다 (최근 접근을 끝으로)."""
    value = LIGAND_NAME_CACHE.get(key)
    if value is not None:
        LIGAND_NAME_CACHE.move_to_end(key)
    return value


def _lru_put(key: str, value: str) -> None:
    """캐시에 저장하고 상한을 넘으면 가장 오래된 항목을 제거한다."""
    LIGAND_NAME_CACHE[key] = value
    LIGAND_NAME_CACHE.move_to_end(key)
    while len(LIGAND_NAME_CACHE) > _LIGAND_CACHE_MAX_SIZE:
        evicted, _ = LIGAND_NAME_CACHE.popitem(last=False)
        # m2: 캐시에서 빠진 키의 lock도 함께 제거하여 _LIGAND_CACHE_LOCKS도
        # 무한 성장하지 않게 한다.
        _LIGAND_CACHE_LOCKS.pop(evicted, None)


def _get_or_create_lock(key: str) -> asyncio.Lock:
    """키별 asyncio.Lock 을 가져오되, 락 dict 자체도 LRU 상한을 둔다 (m2)."""
    lock = _LIGAND_CACHE_LOCKS.get(key)
    if lock is not None:
        _LIGAND_CACHE_LOCKS.move_to_end(key)
        return lock
    lock = asyncio.Lock()
    _LIGAND_CACHE_LOCKS[key] = lock
    while len(_LIGAND_CACHE_LOCKS) > _LIGAND_LOCK_MAX_SIZE:
        _LIGAND_CACHE_LOCKS.popitem(last=False)
    return lock


def clear_ligand_cache() -> None:
    """리간드 이름 캐시와 in-flight lock 사전을 초기화한다.

    테스트 격리, 장기 실행 프로세스의 stale 회귀 방지, 또는 PubChem이 한 번
    잘못된 값을 캐시했을 때 수동 복구용.
    """
    LIGAND_NAME_CACHE.clear()
    _LIGAND_CACHE_LOCKS.clear()

# 알려진 PDB 화학성분 코드 → 일반명.
# 큐레이션 원칙: RCSB Chemical Component Dictionary 와 GPCRdb의 실제 ligand 매핑으로
# 대조한 결과, 대부분의 "직관적 약물 약자(LSD/PSI/LIS/ZOL/CLZ/RSP/LUM/QTP/OLZ/ARI/5HT
# /DOM/NE/EPI/ALR/TIM/MTH/CAU/YEQ)"는 실제 PDB CCD에서 전혀 다른 화합물에 할당되어
# 있거나(예: LSD = Lasalocid A, 5HT = 5-Hydroxy-Thymidine, ZOL = Zoledronic acid,
# LUM = Lumichrome) 코드 자체가 PDB에 존재하지 않는다(DOM, NE).
# 또한 GPCRdb는 risperidone/lisuride/psilocin 등의 약물 이름을 이미 일반명 텍스트로
# 내려주므로 PDB 코드 매핑이 호출되지 않는다.
#
# 따라서 사전을 다음 원칙으로 정리한다 (M2):
#   1. 검증 가능한 (RCSB CCD + 문헌) 매핑만 남긴다.
#   2. PubChem이 풀어줄 수 있는 일반 약물은 PubChem에 위임한다 (KNOWN에서 제거).
#
# 유일하게 남는 엔트리는 EZX → IHCH-7179. 8JT8 (Zhang et al. 2024, Nat Chem Biol,
# DOI:10.1038/s41589-024-01692-4) 의 IHCH-7179 화합물에 해당하며 PubChem Title은
# IUPAC명만 제공해 일반 코드명을 풀어줄 수 없기 때문이다.
KNOWN_LIGAND_NAMES: dict[str, str] = {
    "EZX": "IHCH-7179",
}

# 약물 이름 별칭 사전 (표준화)
LIGAND_ALIASES: dict[str, str] = {
    "5-hydroxytryptamine": "Serotonin",
    "5ht": "Serotonin",
    "lysergide": "LSD",
    "lysergic acid diethylamide": "LSD",
    "lumateperone": "Lumateperone",
    "lisuride": "Lisuride",
    "psilocin": "Psilocin",
    "risperidone": "Risperidone",
    "ketanserin": "Ketanserin",
    "clozapine": "Clozapine",
    "olanzapine": "Olanzapine",
    "aripiprazole": "Aripiprazole",
    "quetiapine": "Quetiapine",
    "ergotamine": "Ergotamine",
    "methiothepin": "Methiothepin",
    "zolpidem": "Zolpidem",
    "zotepine": "Zotepine",
    "pimavanserin": "Pimavanserin",
}


def normalize_ligand_name(name: str | None) -> str | None:
    """리간드 이름을 표준 형태로 정규화한다.

    1. 별칭 사전 우선 적용
    2. 전체 대문자 + 5자 이상 + 순수 알파벳이면 Title Case (예: LUMATEPERONE → Lumateperone)
       — IHCH-7179, R-69, LSD 같은 코드형 이름은 건드리지 않는다.
    """
    if not name:
        return None

    lower = name.lower().strip()
    if lower in LIGAND_ALIASES:
        return LIGAND_ALIASES[lower]

    if name.isupper() and len(name) > 4 and name.isalpha():
        return name.title()

    return name


def _looks_readable(name: str) -> bool:
    """이름이 이미 사람이 읽을 수 있는 일반명인지 판정한다."""
    if not name or len(name) > 40:
        return False  # 빈 값 또는 긴 IUPAC명
    if name.upper().startswith("CHEMBL"):
        return False
    # 순수 2-5자 영숫자 코드 (소문자 일반명 'lsd' 등은 예외로 코드로 보지 않음)
    if re.fullmatch(r"[A-Za-z0-9]{2,5}", name) and not name.islower():
        return False
    return True


def _raw_ligand_identifier(ligand: dict | None) -> str | None:
    """리간드 dict에서 이름 해석에 쓸 raw 식별자를 고른다.

    이름이 이미 읽을 만하면 이름을, 아니면(코드/IUPAC) PDB 화학성분 코드를 쓴다.
    """
    if not ligand:
        return None
    name = (ligand.get("name") or "").strip()
    code = (ligand.get("PDB") or ligand.get("pdb_code") or "").strip()
    if name and _looks_readable(name):
        return name
    return code or name or None


class PubChemUnavailableError(RuntimeError):
    """PubChem API 일시 장애를 나타내는 예외.

    "화합물 미수록(404, 빈 Properties)"과 "API 장애(타임아웃/5xx/파싱 실패)"를 구분한다.
    상위 호출자(resolve_ligand_name)는 catch해서 module-level 카운터를 증가시키고
    raw 이름으로 graceful fallback한다.
    """


# m3: PubChem / 리간드 해석 실패 카운터는 ContextVar 로 관리한다.
# 모듈 전역 변수를 쓰면 SSE 다중 세션·동시 요청에서 카운터가 race한다.
# 각 batch 호출자는 `_pubchem_failure_var.set(0)` 으로 초기화한 뒤 `consume_*()` 으로 회수.
_pubchem_failure_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "pdbmcp_pubchem_failure_count", default=0
)
_ligand_resolution_failure_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "pdbmcp_ligand_resolution_failure_count", default=0
)


def _incr_pubchem_failure() -> None:
    _pubchem_failure_var.set(_pubchem_failure_var.get() + 1)


def _incr_ligand_resolution_failure() -> None:
    _ligand_resolution_failure_var.set(_ligand_resolution_failure_var.get() + 1)


def consume_pubchem_failures() -> int:
    """누적된 PubChem 실패 수를 반환하고 카운터를 0으로 리셋한다 (현재 컨텍스트 한정).

    `get_gpcrdb_structures` 등의 batch 호출 직후에 호출하여
    그 batch 동안 발생한 PubChem 장애 횟수를 얻는다.
    """
    n = _pubchem_failure_var.get()
    _pubchem_failure_var.set(0)
    return n


def consume_ligand_resolution_failures() -> int:
    """`resolve_ligand_name` 호출 중 발생한 예상치 못한 예외 수를 반환/리셋."""
    n = _ligand_resolution_failure_var.get()
    _ligand_resolution_failure_var.set(0)
    return n


async def _pubchem_title(query: str, client: httpx.AsyncClient | None = None) -> str | None:
    """PubChem에서 화합물 이름/코드의 대표명을 조회한다.

    Title이 있으면 그 값을, 없으면 IUPACName으로 fallback (M1).
    IUPACName은 매우 길 수 있으므로 `_MAX_READABLE_LIGAND_NAME_LEN`을 넘으면
    "사람이 읽을 만한 일반명"이 아니라고 보고 None을 반환해 호출자가 raw로 fallback 하게 한다.

    Returns:
        str: 정상 응답에서 추출한 일반명.
        None: 404 / 빈 Properties / 모든 후보가 길이 상한 초과 (PubChem 미수록 또는 IUPAC-only).

    Raises:
        PubChemUnavailableError: API 일시 장애(타임아웃, 5xx, 파싱 실패).
            호출자가 catch하여 raw 이름 fallback + 실패 카운트 누적을 하도록 한다.
    """
    url = PUBCHEM_NAME_URL.format(name=quote(query))

    async def _do_request(c: httpx.AsyncClient) -> str | None:
        async with _pubchem_semaphore:
            resp = await c.get(url)
        if resp.status_code == 404:
            return None  # 미수록 (정상 케이스)
        if resp.status_code != 200:
            raise PubChemUnavailableError(
                f"PubChem이 HTTP {resp.status_code}를 반환했습니다."
            )
        props = resp.json().get("PropertyTable", {}).get("Properties", [])
        if not props:
            return None  # 빈 Properties (미수록)

        # M1: Title이 없으면 IUPACName 으로 fallback. 둘 다 길이 상한을 넘으면
        # raw 식별자 fallback이 더 낫다고 보고 None.
        for key in ("Title", "IUPACName"):
            candidate = (props[0].get(key) or "").strip()
            if candidate and len(candidate) <= _MAX_READABLE_LIGAND_NAME_LEN:
                return candidate
        return None

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as own:
                return await _do_request(own)
        return await _do_request(client)
    except httpx.TimeoutException as exc:
        raise PubChemUnavailableError("PubChem API 응답 시간 초과") from exc
    except httpx.HTTPError as exc:
        raise PubChemUnavailableError(f"PubChem API 연결 실패: {exc}") from exc
    except ValueError as exc:
        raise PubChemUnavailableError(f"PubChem API 응답 파싱 실패: {exc}") from exc


async def resolve_ligand_name(
    raw_name: str | None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """GPCRdb 리간드 이름/코드를 사람이 읽을 수 있는 일반명으로 변환한다.

    우선순위: LRU 캐시 → 큐레이션 사전 → (코드형이 아니면 그대로) → PubChem → 원본.
    PubChem 일시 장애는 silent하게 raw fallback하지만 ContextVar 카운터에 기록되어
    `consume_pubchem_failures()`로 상위 호출자가 워닝을 띄울 수 있다 (m3).
    동일 키에 대한 동시 호출은 키별 lock으로 dedup되어 PubChem 중복 호출을 막는다.
    """
    if not raw_name:
        return None
    raw = str(raw_name).strip()
    if not raw:
        return None
    upper = raw.upper()

    # 1) LRU 캐시 (락 외부에서 빠른 경로)
    cached = _lru_get(upper)
    if cached is not None:
        return normalize_ligand_name(cached)

    # 2) 큐레이션 사전
    if upper in KNOWN_LIGAND_NAMES:
        resolved = KNOWN_LIGAND_NAMES[upper]
        _lru_put(upper, resolved)
        return normalize_ligand_name(resolved)

    # 3) PDB 3-4자 코드 또는 CHEMBL ID가 아니면 이미 일반명으로 간주
    is_code = bool(re.fullmatch(r"[A-Z0-9]{3,4}", upper)) or upper.startswith("CHEMBL")
    if not is_code:
        return normalize_ligand_name(raw)

    # 4) PubChem — 동일 키 동시 호출은 키별 lock으로 직렬화 (dedup).
    #    락 안에서 캐시를 다시 확인해 먼저 끝난 코루틴의 결과를 재사용.
    #    락 dict 자체도 LRU 상한이 있어 무한 성장을 막는다 (m2).
    lock = _get_or_create_lock(upper)
    async with lock:
        cached = _lru_get(upper)
        if cached is not None:
            return normalize_ligand_name(cached)

        try:
            resolved = await _pubchem_title(raw, client)
        except PubChemUnavailableError:
            # PubChem 일시 장애는 음의 캐시로 저장하지 않는다 — 다음 호출에서
            # 재시도되어야 정상 복구 시 stale을 만들지 않는다.
            _incr_pubchem_failure()
            return normalize_ligand_name(raw)

        if resolved:
            _lru_put(upper, resolved)
            return normalize_ligand_name(resolved)

        # m9: PubChem 미수록(404 / 빈 Properties)인 경우 raw 식별자를 음의 캐시로 저장한다.
        # 같은 미수록 코드(예: GPCRdb-only 화합물)가 또 들어왔을 때 PubChem을 다시
        # 두드리지 않는다. 음의 캐시는 일시 장애가 아니라 "확정 미수록" 응답에만 적용한다.
        _lru_put(upper, raw)
        return normalize_ligand_name(raw)


# --------------------------------------------------------------------------
# 구조 항목 파싱
# --------------------------------------------------------------------------

def _parse_structure_item(s: dict) -> dict:
    """GPCRdb structure 항목 → PDBEntry GPCR 필드 dict.

    `ligand`는 None으로 두고 `_ligand_raw`(해석용 식별자)를 함께 반환한다.
    호출자가 resolve_ligand_name으로 `ligand`를 채운 뒤 `_ligand_raw`를 제거한다.
    """
    ligands = s.get("ligands") or []
    primary = select_primary_ligand(ligands)

    modality = None
    if primary:
        modality = normalize_modality(
            primary.get("function") or primary.get("function_label")
        )

    # m10: GPCRdb structure 엔드포인트는 일반적으로 stabilizing_agents를 제공하지 않지만,
    # 일부 구조/스냅샷에는 들어 있을 수 있다 — truthy 일 때만 parse 한다.
    # None인 필드는 server.py의 _annotate_fusion_antibody 에서 RCSB polymer/title fallback이
    # 채우므로 1차 채움이 더 우선되도록 둔다.
    fusion, antibody = parse_stabilizing_agents(s.get("stabilizing_agents"))

    return {
        "pref_chain": s.get("preferred_chain"),
        "state": s.get("state"),
        "ligand": None,
        "_ligand_raw": _raw_ligand_identifier(primary),
        "ligand_modality": modality,
        "signaling_protein": parse_signaling_protein(s.get("signalling_protein")),
        "fusion_protein": fusion,
        "antibody": antibody,
    }


# --------------------------------------------------------------------------
# API 호출
# --------------------------------------------------------------------------

async def _get_with_retry(client: httpx.AsyncClient, url: str):
    """GET 요청 — 네트워크 오류 시 1회 재시도. raise_for_status 적용."""
    for attempt in range(2):
        try:
            async with _semaphore:
                resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
                continue
            raise


# GPCR 패밀리 유전자명 prefix (UniProt entry_name의 앞부분 기준).
# GPCRdb 서버가 다운되어도 GPCR 여부를 즉시 판정하기 위한 로컬 휴리스틱.
# 보수적으로 유지 — 정말 GPCR로 확정된 family만 포함한다.
# entry_name 형식: '<GENE>_<SPECIES>' (예: '5HT2A_HUMAN', 'EGFR_HUMAN').
# prefix는 GENE 부분이 시작해야 하는 문자열이며, 매칭 후 알파벳/숫자가 더 따라올 수 있다.
GPCR_GENE_PREFIXES: tuple[str, ...] = (
    # Aminergic
    "5HT", "HTR",          # Serotonin (HTR1A, 5HT2A 등)
    "DRD",                 # Dopamine (DRD1~DRD5)
    "ADRA", "ADRB",        # Adrenergic
    "CHRM",                # Muscarinic acetylcholine
    "HRH",                 # Histamine
    "TAAR",                # Trace amine
    # Peptide
    "OPRM", "OPRD", "OPRK", "OPRL",  # Opioid
    "AGTR", "APLNR",                 # Angiotensin / Apelin (APJ prefix는 dead — APLNR이 정식 entry_name)
    "BDKRB",                         # Bradykinin
    "EDNR",                          # Endothelin
    "CCKAR", "CCKBR",                # CCK
    "GHSR", "GHRHR",                 # Ghrelin / GHRH
    "GLP1R", "GLP2R", "GCGR", "GIPR", "SCTR",  # Secretin family
    "NPY", "NPFFR",                  # NPY / NPFF. 'NPY' 단독은 비-GPCR(NPY 펩타이드 P01303)이므로 NUMBERED_ONLY_PREFIXES로 가드.
    "NMUR", "NMBR", "GRPR",          # Neuromedin / GRP
    "PTH1R", "PTH2R", "CALCR", "CALCRL", "CRHR",  # PTH / Calcitonin / CRH
    "VIPR",                          # VIP/PACAP
    "NTSR",                          # Neurotensin
    "GALR",                          # Galanin
    "MCHR",                          # Melanin-concentrating hormone
    "PRLHR", "KISS1R", "GNRHR",      # Prolactin/Kisspeptin/GnRH
    "OXTR", "AVPR",                  # Oxytocin / Vasopressin
    "MC1R", "MC2R", "MC3R", "MC4R", "MC5R",  # Melanocortin (broad 'MC' prefix는 위험 — 명시)
    "SSTR",                          # Somatostatin
    # Chemokine
    "CXCR", "CCR", "CX3CR", "XCR",   # Chemokine receptors
    # Lipid / Nucleotide
    "S1PR", "LPAR", "CNR",           # S1P / LPA / Cannabinoid
    "P2RY", "ADORA",                 # Purinergic / Adenosine
    "FFAR", "GPR", "GPRC",           # Free fatty acid / Orphan GPRs / Class C
    "PTGER", "PTGFR", "PTGIR", "PTGDR", "TBXA2R",  # Prostanoid
    "LTB4R", "CYSLTR",               # Leukotriene
    # Class C
    "GRM",                           # Metabotropic glutamate
    "GABBR",                         # GABA-B
    "CASR",                          # Ca-sensing
    # Other
    "MTNR",                          # Melatonin
    "FSHR", "LHCGR", "TSHR",         # Glycoprotein hormone
    "F2R", "F2RL",                   # Protease-activated (PAR1=F2R, PAR2=F2RL1 — PAR1~4 prefix는 dead)
    "RHO", "OPN",                    # Rhodopsin / Opsins (OPN1SW, OPN1LW, OPN3~5 등 — 'OPN' 뒤가 숫자로 시작)
    "SUCNR", "HCAR",                 # Succinate / Hydroxy-carboxylic
    "CMKLR", "C3AR", "C5AR",         # Chemerin / Complement
    "FPR", "FZD",                    # Formyl peptide / Frizzled (Class F)
    "SMO",                           # Smoothened
    "BAI", "ADGR",                   # Adhesion GPCRs
)


# prefix 단독(예: gene == "NPY")으로는 GPCR이 아닌 prefix들.
# 반드시 prefix + 숫자 형태여야 GPCR로 인정.
NUMBERED_ONLY_PREFIXES: frozenset[str] = frozenset({
    "NPY",   # 'NPY' 단독 = NPY 펩타이드(P01303, 비-GPCR). NPY1R~NPY5R만 GPCR.
})


def _entry_name_looks_gpcr(entry_name: str) -> bool:
    """UniProt entry_name 패턴으로 GPCR 여부를 빠르게 판정한다.

    매칭 규칙 (false positive 방지):
      (1) gene == prefix, 단 NUMBERED_ONLY_PREFIXES에 없는 prefix만 (예: 'RHO', 'SMO')
      (2) gene가 prefix로 시작하고 그 다음 문자가 숫자 (예: 'HTR2A' = HTR+2A, 'NPY1R' = NPY+1R)
    이렇게 하면 'SMOC1', 'BAIAP2', 'RHOA', 'NPY'(펩타이드) 같은 비GPCR을 막는다.

    예시:
      '5HT2A_HUMAN' → True ('5HT' + '2A')
      'EGFR_HUMAN'  → False
      'RHO_HUMAN'   → True (rhodopsin, exact match 허용)
      'NPY_HUMAN'   → False (NPY 펩타이드, NUMBERED_ONLY로 차단)
      'NPY1R_HUMAN' → True ('NPY' + '1R')
      'RHOA_HUMAN'  → False (Rho GTPase, 'RHO' 뒤가 'A')
      'SMOC1_HUMAN' → False
      'BAIAP2_HUMAN' → False
    """
    if not entry_name:
        return False
    gene = entry_name.split("_", 1)[0].upper()
    for prefix in GPCR_GENE_PREFIXES:
        if gene == prefix and prefix not in NUMBERED_ONLY_PREFIXES:
            return True
        if gene.startswith(prefix):
            tail = gene[len(prefix):]
            if tail and tail[0].isdigit():
                return True
    return False


async def check_gpcr(entry_name: str) -> tuple[bool, str | None]:
    """UniProt entry_name으로 GPCRdb 단백질 여부를 확인한다.

    1) 로컬 휴리스틱 (entry_name 패턴) — GPCRdb 서버가 다운돼도 즉시 판정.
    2) 휴리스틱이 모르는 경우에만 GPCRdb HTTP 호출로 fallback (짧은 타임아웃).

    Returns:
        (is_gpcr, gpcrdb_slug). GPCR이 아니거나 조회 실패 시 (False, None).
        이 함수는 절대 예외를 던지지 않는다 — GPCRdb는 선택적 강화 단계이다.
    """
    slug = (entry_name or "").strip().lower()  # "5HT2A_HUMAN" → "5ht2a_human"
    if not slug:
        return False, None

    # 1) 로컬 휴리스틱 — GPCRdb의 슬러그는 항상 entry_name.lower() 형태이므로
    #    HTTP 확인 없이 슬러그를 결정할 수 있다.
    if _entry_name_looks_gpcr(entry_name):
        return True, slug

    # 2) 휴리스틱이 모르는 경우에만 HTTP fallback.
    #    GPCRdb의 protein 엔드포인트가 종종 매우 느리므로 5초로 제한한다.
    url = f"{GPCRDB_BASE}/protein/{slug}/"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0), follow_redirects=True
        ) as client:
            async with _semaphore:
                resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return True, data.get("entry_name", slug)
            return False, None
    except Exception:
        return False, None


async def _resolve_ligand_names_for(
    raw_items: list[tuple[str, dict]],
    client: httpx.AsyncClient,
) -> dict[str, dict]:
    """raw_items의 각 항목에 대해 리간드 이름을 병렬로 해석해 dict로 묶어 반환한다.

    `resolve_ligand_name`은 정상 경로에서 str(이름) 또는 None(미수록)을 반환하며
    PubChem 장애는 내부에서 catch 후 raw fallback한다. 그 외 예외(프로그래밍 오류 등)는
    ContextVar 카운터에 누적되어 상위에서 워닝으로 surface 된다.
    """
    # m4: raw 추출과 pop을 명시적 루프로 분리 (이전: list-comprehension 안 pop은 부작용 가독성↓)
    raws: list[str | None] = []
    for _, item in raw_items:
        raws.append(item.pop("_ligand_raw", None))

    tasks = [resolve_ligand_name(raw, client) for raw in raws]
    resolved = await asyncio.gather(*tasks, return_exceptions=True)

    result: dict[str, dict] = {}
    for (pdb_id, item), name in zip(raw_items, resolved):
        if isinstance(name, BaseException):
            # 예상치 못한 예외 — 카운터에 누적하고 ligand=None으로 처리
            _incr_ligand_resolution_failure()
            item["ligand"] = None
        else:
            item["ligand"] = name  # str(일반명) 또는 None(미수록)
        result[pdb_id] = item
    return result


async def _fetch_single_structure(
    pdb_id: str,
    client: httpx.AsyncClient,
) -> tuple[str, dict] | None:
    """GPCRdb의 단일 구조 엔드포인트를 호출한다. 404/오류 시 None 반환."""
    pdb_id = pdb_id.upper()
    url = f"{GPCRDB_BASE}/structure/{pdb_id}/"
    try:
        async with _semaphore:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return pdb_id, _parse_structure_item(resp.json())
    except Exception:
        return None


async def get_gpcrdb_structures(
    slug: str,
    pdb_ids: list[str] | None = None,
) -> dict[str, dict]:
    """GPCRdb 슬러그로 구조 목록을 조회한다 (리간드 이름까지 해석).

    1) 배치 엔드포인트 `/services/structure/protein/{slug}/` 우선 시도.
    2) 배치 실패/빈 응답 시 `pdb_ids`가 있으면 단일 구조 엔드포인트
       `/services/structure/{pdb_id}/`로 폴백 (GPCRdb는 batch는 자주 죽어도
       단일 조회는 안정적임).
    3) 둘 다 실패하면 빈 dict 반환 (절대 예외를 던지지 않음).

    Returns:
        {PDB_ID(대문자): GPCR 필드 dict} 형태의 매핑.
    """
    batch_url = f"{GPCRDB_BASE}/structure/protein/{slug}/"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0), follow_redirects=True
    ) as client:
        # 1) 배치 시도 (재시도 1회 포함, 타임아웃 20초)
        batch_items: list[tuple[str, dict]] = []
        try:
            data = await _get_with_retry(client, batch_url)
            for s in data or []:
                pdb_id = (s.get("pdb_code") or "").upper()
                if pdb_id:
                    batch_items.append((pdb_id, _parse_structure_item(s)))
        except Exception:
            batch_items = []

        # 2) 폴백 — 배치가 비어있거나 pdb_ids에 비해 커버리지가 부족하면
        #    개별 PDB 엔드포인트를 호출해 보강한다.
        existing_ids = {pid for pid, _ in batch_items}
        missing_ids: list[str] = []
        if pdb_ids:
            missing_ids = [pid.upper() for pid in pdb_ids if pid.upper() not in existing_ids]

        if missing_ids:
            tasks = [_fetch_single_structure(pid, client) for pid in missing_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, tuple):
                    batch_items.append(r)

        if not batch_items:
            return {}

        return await _resolve_ligand_names_for(batch_items, client)


async def get_gpcrdb_single(pdb_id: str) -> dict | None:
    """단일 PDB ID의 GPCRdb 구조 데이터를 조회한다 (리간드 이름까지 해석).

    GPCR 구조가 아니거나 조회 실패 시 None을 반환한다 (예외를 던지지 않음).
    """
    pdb_id = (pdb_id or "").strip().upper()
    if not pdb_id:
        return None

    url = f"{GPCRDB_BASE}/structure/{pdb_id}/"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0), follow_redirects=True
        ) as client:
            async with _semaphore:
                resp = await client.get(url)
            if resp.status_code != 200:
                return None
            item = _parse_structure_item(resp.json())
            item["ligand"] = await resolve_ligand_name(item.pop("_ligand_raw"), client)
            return item
    except Exception:
        return None
