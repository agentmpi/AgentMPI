"""Shared infrastructure for the AgentMPI experiments.

Two things live here.  First, the plumbing every experiment needs: choosing an
executor, writing results in a stable schema, and recording enough provenance
that a number in the paper can be traced back to the run that produced it.
Second, the *objective* metrics.  That second part deserves a note, because it is
where most agent-systems evaluation goes wrong.

An experiment whose only quality signal is a language model's opinion cannot
distinguish a protocol improvement from a change in the judge's mood, and it
cannot be replicated by a reader with a different model.  Every headline number
in these experiments is therefore computed by deterministic code from artifacts
the agents produced: how many acceptance tests passed, how many distinct
renderings a proper noun received across sections, whether a reported rendering
actually appears in the text that reported it, how many facts survived a
reduction.  Model-judged scores are reported as a secondary signal, clearly
labelled, and never as the basis of a comparison between protocol variants.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import agentmpi as ampi
from agentmpi.executor import BrokerExecutor, Executor, FunctionExecutor, ReplayExecutor, SimulatedExecutor

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
DATA = REPO / "experiments" / "data"


# ============================================================================
# provenance and result files
# ============================================================================


def provenance(**extra: Any) -> dict[str, Any]:
    """Everything needed to interpret a result later."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": commit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "agentmpi_version": ampi.__version__,
        "tokenizer_exact": ampi.tokens.COUNTER.exact,
        **extra,
    }


def write_result(name: str, payload: dict[str, Any], *, subdir: str = "") -> Path:
    out_dir = RESULTS / subdir if subdir else RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_result(name: str, *, subdir: str = "") -> dict[str, Any] | None:
    path = (RESULTS / subdir if subdir else RESULTS) / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================================
# executors
# ============================================================================


def make_executor_factory(
    kind: str,
    *,
    fabric_root: Path | None = None,
    seed: int = 0,
    fn: Callable[..., Any] | None = None,
    latency_s: float = 30.0,
    sigma: float = 0.7,
    fail_rate: float = 0.0,
    stall_rate: float = 0.0,
    timeout: float = 5400.0,
) -> Callable[[int], Executor]:
    """Build a per-rank executor factory.

    ``broker`` is the executor used for the real-agent runs: it publishes each
    invocation to the fabric and blocks until an external worker completes it.
    ``simulated`` and ``function`` exist so that the protocol-level results
    (message counts, algorithm crossovers, deadlock behaviour) can be produced
    deterministically and for free, which is the only way they can be
    regression-tested.
    """
    if kind == "broker":
        if fabric_root is None:
            raise ValueError("broker executor needs the fabric root")
        fabric = ampi.Fabric(fabric_root)
        shared = BrokerExecutor(fabric=fabric, default_timeout=timeout)
        return lambda rank: shared
    if kind == "simulated":
        return lambda rank: SimulatedExecutor(
            median_latency_s=latency_s,
            sigma=sigma,
            fail_rate=fail_rate,
            stall_rate=stall_rate,
            seed=seed + 1000 * rank,
        )
    if kind == "function":
        if fn is None:
            raise ValueError("function executor needs fn=")
        return lambda rank: FunctionExecutor(fn=fn)
    if kind == "replay":
        if fabric_root is None:
            raise ValueError("replay executor needs the fabric root")
        fabric = ampi.Fabric(fabric_root)
        shared_replay = ReplayExecutor(fabric=fabric, strict=False)
        return lambda rank: shared_replay
    raise ValueError(f"unknown executor kind {kind}")


# ============================================================================
# the book, for the translation experiment
# ============================================================================

#: Canonical entities whose rendering must be consistent across the whole book.
#: Fixed in advance, in the source language, so that consistency is measured
#: against a pre-registered list rather than against whatever the agents happened
#: to notice -- which would let a lazy run score well by reporting nothing.
SHERLOCK_ENTITIES: tuple[str, ...] = (
    "Sherlock Holmes",
    "Watson",
    "Baker Street",
    "Irene Adler",
    "Scotland Yard",
    "Mrs. Hudson",
    "Lestrade",
    "Bohemia",
    "Briony Lodge",
    "Godfrey Norton",
)

GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK", re.I)


def load_book(path: Path | None = None) -> str:
    """Load the source text with Project Gutenberg boilerplate stripped."""
    path = path or (DATA / "sherlock.txt")
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = GUTENBERG_START.search(raw)
    if m:
        raw = raw[m.end() :]
    m = GUTENBERG_END.search(raw)
    if m:
        raw = raw[: m.start()]
    return raw.strip()


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


@dataclass
class Unit:
    """One unit of translation work."""

    index: int
    text: str
    n_paragraphs: int
    n_words: int
    entities: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "n_paragraphs": self.n_paragraphs,
            "n_words": self.n_words,
            "entities": list(self.entities),
        }


def split_units(
    text: str,
    n_units: int,
    *,
    max_words_per_unit: int = 900,
    entities: Sequence[str] = SHERLOCK_ENTITIES,
) -> list[Unit]:
    """Split ``text`` into ``n_units`` contiguous units at paragraph boundaries.

    Deliberately *contiguous* rather than block-cyclic.  A contiguous
    decomposition creates a real boundary dependence between neighbouring units
    -- the register, the tense, and the rendering of a name introduced just before
    the cut -- which is what makes the halo exchange meaningful.  A block-cyclic
    split would scatter the dependence over all pairs and turn a ring exchange
    into an all-to-all, which is exactly the trade-off the paper discusses.
    """
    paras = paragraphs(text)
    budget = max_words_per_unit
    units: list[Unit] = []
    cursor = 0
    for i in range(n_units):
        chunk: list[str] = []
        words = 0
        while cursor < len(paras) and words < budget:
            p = paras[cursor]
            chunk.append(p)
            words += len(p.split())
            cursor += 1
        if not chunk:
            break
        body = "\n\n".join(chunk)
        present = tuple(e for e in entities if e.lower() in body.lower())
        units.append(
            Unit(index=i, text=body, n_paragraphs=len(chunk), n_words=len(body.split()), entities=present)
        )
    return units


# ============================================================================
# translation metrics (deterministic)
# ============================================================================

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
LATIN_WORD = re.compile(r"\b[A-Za-z]{4,}\b")


@dataclass
class TranslationMetrics:
    n_units: int
    n_units_nonempty: int
    coverage: float
    #: Entities reported by at least two units that received a single rendering.
    consistency: float
    n_shared_entities: int
    n_divergent_entities: int
    divergent_detail: dict[str, list[str]] = field(default_factory=dict)
    #: Fraction of reported renderings that actually occur in the reported text.
    rendering_verified: float = 0.0
    n_renderings: int = 0
    n_unverified: int = 0
    #: Fraction of units whose paragraph count matches the source.
    paragraph_fidelity: float = 0.0
    #: Fraction of units whose target/source length ratio is plausible.
    length_plausibility: float = 0.0
    #: Mean fraction of target characters that are CJK (target-language purity).
    target_script_ratio: float = 0.0
    #: Mean count of residual source-language words per 1000 target characters.
    residual_source_per_kchar: float = 0.0
    per_unit: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_units": self.n_units,
            "n_units_nonempty": self.n_units_nonempty,
            "coverage": round(self.coverage, 4),
            "consistency": round(self.consistency, 4),
            "n_shared_entities": self.n_shared_entities,
            "n_divergent_entities": self.n_divergent_entities,
            "divergent_detail": self.divergent_detail,
            "rendering_verified": round(self.rendering_verified, 4),
            "n_renderings": self.n_renderings,
            "n_unverified": self.n_unverified,
            "paragraph_fidelity": round(self.paragraph_fidelity, 4),
            "length_plausibility": round(self.length_plausibility, 4),
            "target_script_ratio": round(self.target_script_ratio, 4),
            "residual_source_per_kchar": round(self.residual_source_per_kchar, 3),
            "per_unit": self.per_unit,
        }


def score_translation(
    units: Sequence[Unit],
    outputs: Sequence[dict[str, Any] | None],
    *,
    entities: Sequence[str] = SHERLOCK_ENTITIES,
    min_ratio: float = 0.20,
    max_ratio: float = 1.20,
) -> TranslationMetrics:
    """Score a translated book against deterministic criteria.

    ``outputs[i]`` is rank *i*'s artifact: ``{"translation": str, "renderings":
    {entity: rendering}}``.  Every metric below is computable from the artifacts
    and the source alone.

    The *consistency* metric is the one the glossary machinery exists to move.  It
    counts entities that more than one unit rendered, and asks how many of those
    received a single agreed rendering.  Without a glossary exchange the units
    have no way to agree except by luck; with one they should agree by
    construction, and the gap is the measurement.

    ``rendering_verified`` guards against the obvious way to cheat the
    consistency metric: an agent can report the glossary's rendering while writing
    something else.  Checking that the reported string actually occurs in the
    reported translation closes that hole, and the rate at which it fails is
    itself an interesting number -- it is a direct measurement of *fail-plausible*
    behaviour.
    """
    renderings: dict[str, dict[int, str]] = {e: {} for e in entities}
    per_unit: list[dict[str, Any]] = []
    n_nonempty = 0
    n_reported = 0
    n_unverified = 0
    para_ok = 0
    len_ok = 0
    script_ratios: list[float] = []
    residuals: list[float] = []
    n_expected_reports = 0
    n_actual_reports = 0

    for unit, out in zip(units, outputs, strict=True):
        row: dict[str, Any] = {"index": unit.index, "ok": False}
        text = (out or {}).get("translation") or ""
        reported = (out or {}).get("renderings") or {}
        if not isinstance(reported, dict):
            reported = {}
        row["n_target_chars"] = len(text)
        row["n_source_words"] = unit.n_words
        if text.strip():
            n_nonempty += 1
            row["ok"] = True
        n_expected_reports += len(unit.entities)
        for ent in unit.entities:
            if ent in reported and isinstance(reported[ent], str) and reported[ent].strip():
                n_actual_reports += 1
        for ent, rend in reported.items():
            if ent not in renderings or not isinstance(rend, str) or not rend.strip():
                continue
            n_reported += 1
            renderings[ent][unit.index] = rend.strip()
            if rend.strip() not in text:
                n_unverified += 1
        tgt_paras = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
        row["n_target_paragraphs"] = tgt_paras
        row["n_source_paragraphs"] = unit.n_paragraphs
        if tgt_paras == unit.n_paragraphs:
            para_ok += 1
        ratio = (len(text) / max(1, len(unit.text)))
        row["length_ratio"] = round(ratio, 3)
        if min_ratio <= ratio <= max_ratio:
            len_ok += 1
        if text:
            cjk = len(CJK.findall(text))
            script = cjk / max(1, len(re.sub(r"\s", "", text)))
            script_ratios.append(script)
            row["target_script_ratio"] = round(script, 3)
            residual = len(LATIN_WORD.findall(text)) / max(1.0, len(text) / 1000.0)
            residuals.append(residual)
            row["residual_source_per_kchar"] = round(residual, 2)
        per_unit.append(row)

    shared = {e: v for e, v in renderings.items() if len(v) >= 2}
    divergent = {e: sorted(set(v.values())) for e, v in shared.items() if len(set(v.values())) > 1}
    n = len(units)
    return TranslationMetrics(
        n_units=n,
        n_units_nonempty=n_nonempty,
        coverage=n_actual_reports / n_expected_reports if n_expected_reports else 0.0,
        consistency=(1.0 - len(divergent) / len(shared)) if shared else 1.0,
        n_shared_entities=len(shared),
        n_divergent_entities=len(divergent),
        divergent_detail=divergent,
        rendering_verified=(1.0 - n_unverified / n_reported) if n_reported else 0.0,
        n_renderings=n_reported,
        n_unverified=n_unverified,
        paragraph_fidelity=para_ok / n if n else 0.0,
        length_plausibility=len_ok / n if n else 0.0,
        target_script_ratio=sum(script_ratios) / len(script_ratios) if script_ratios else 0.0,
        residual_source_per_kchar=sum(residuals) / len(residuals) if residuals else 0.0,
        per_unit=per_unit,
    )


# ============================================================================
# fact-retention metrics (for the reduction-fidelity experiment)
# ============================================================================

FACT_ID = re.compile(r"\bF-(\d+)-(\d+)\b")


def make_fact_report(rank: int, n_facts: int, *, topic: str = "system") -> dict[str, Any]:
    """Build a report containing ``n_facts`` uniquely identified factual items.

    The identifiers are the trick that makes reduction fidelity *objectively*
    measurable: a merged report either still contains ``F-3-2`` or it does not,
    and no judgement is required to tell.  Each item is phrased as a real
    sentence so that a summarising operator behaves as it would on real content
    rather than treating the input as a list to be copied.
    """
    facts = []
    for i in range(n_facts):
        fid = f"F-{rank}-{i}"
        facts.append(
            f"[{fid}] Component {topic}-{rank}.{i} reported a measured throughput of "
            f"{100 + 7 * rank + i} units per second under the {['nominal', 'degraded', 'saturated'][i % 3]} workload."
        )
    return {
        "source_rank": rank,
        "title": f"Report from component group {rank}",
        "findings": facts,
    }


def fact_ids(payload: Any) -> set[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return {f"F-{a}-{b}" for a, b in FACT_ID.findall(text)}


def fact_retention(result: Any, n_ranks: int, n_facts: int) -> dict[str, Any]:
    """Fraction of the input facts that survived to the reduction's result.

    Also reports *positional bias*: whether the surviving facts come
    disproportionately from high or low ranks.  Bias is the interesting part.  A
    reduction that keeps 40% of the facts uniformly is a lossy but fair summary; a
    reduction that keeps 100% of the last two ranks and 0% of the rest is a
    *broken* summary that happens to have the same retention rate, and only the
    per-rank breakdown distinguishes them.  MPI's ``MPI_SUM`` cannot exhibit this
    failure; a summarising operator exhibits it by default.
    """
    survived = fact_ids(result)
    expected = {f"F-{r}-{i}" for r in range(n_ranks) for i in range(n_facts)}
    kept = survived & expected
    per_rank = {r: sum(1 for i in range(n_facts) if f"F-{r}-{i}" in kept) / n_facts for r in range(n_ranks)}
    values = list(per_rank.values())
    mean = sum(values) / len(values) if values else 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
    # Spearman-free monotonicity proxy: correlation of retention with rank index.
    if len(values) >= 2:
        idx_mean = (len(values) - 1) / 2
        num = sum((r - idx_mean) * (per_rank[r] - mean) for r in per_rank)
        den = (
            sum((r - idx_mean) ** 2 for r in per_rank) ** 0.5
            * sum((per_rank[r] - mean) ** 2 for r in per_rank) ** 0.5
        )
        rank_corr = num / den if den else 0.0
    else:
        rank_corr = 0.0
    return {
        "n_expected": len(expected),
        "n_retained": len(kept),
        "retention": round(len(kept) / len(expected), 4) if expected else 0.0,
        "n_hallucinated_ids": len(survived - expected),
        "per_rank_retention": {str(k): round(v, 3) for k, v in per_rank.items()},
        "retention_stdev": round(var**0.5, 4),
        "rank_position_correlation": round(rank_corr, 4),
    }


# ============================================================================
# misc
# ============================================================================


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def fresh_root(label: str) -> Path:
    root = REPO / "runs" / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}"
    root.parent.mkdir(parents=True, exist_ok=True)
    return root
