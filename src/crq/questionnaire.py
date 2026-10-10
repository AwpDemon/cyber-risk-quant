"""Underwriting-readiness questionnaire: answers -> score, tier, gaps, model posture."""

from __future__ import annotations

from dataclasses import dataclass, field

from .controls import CONTROLS, Posture, patch_exposure_reduction
from .parameters import load_yaml


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    section: str
    weight: float
    options: dict[str, dict]
    control: str | None = None
    critical: bool = False
    critical_min: float = 1.0
    modifies: str | None = None
    evidence: tuple[str, ...] = ()
    note: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)

    @property
    def modelled(self) -> bool:
        return self.control is not None or self.modifies is not None

    @property
    def best_option(self) -> str:
        return max(self.options, key=lambda k: self.options[k]["score"])

    def annual_cost(self, employees: int, endpoints: int) -> float:
        c = self.cost
        return float(c.get("fixed", 0) + 12 * (c.get("per_user_month", 0) * employees + c.get("per_endpoint_month", 0) * endpoints))


def load_questions() -> dict[str, Question]:
    raw = load_yaml("questionnaire.yaml")["questions"]
    out = {}
    for qid, q in raw.items():
        out[qid] = Question(
            id=qid, text=q["text"], section=q["section"], weight=float(q["weight"]), options=q["options"],
            control=q.get("control"), critical=bool(q.get("critical", False)), critical_min=float(q.get("critical_min", 1.0)),
            modifies=q.get("modifies"), evidence=tuple(q.get("evidence", ())), note=q.get("note", {}), cost=q.get("cost", {}),
        )
    return out


def load_tiers() -> tuple[list[dict], dict]:
    raw = load_yaml("questionnaire.yaml")
    return raw["tiers"], raw["referral_tier"]


@dataclass
class Assessment:
    answers: dict[str, str]
    score: float
    tier: str
    tier_meaning: str
    critical_failures: list[str]
    posture: Posture
    question_scores: dict[str, float]


def validate_answers(answers: dict[str, str], questions: dict[str, Question] | None = None) -> None:
    questions = questions or load_questions()
    missing = set(questions) - set(answers)
    if missing:
        raise ValueError(f"unanswered questions: {sorted(missing)}")
    for qid, opt in answers.items():
        if qid not in questions:
            raise KeyError(f"unknown question {qid!r}")
        if opt not in questions[qid].options:
            raise ValueError(f"{qid}: unknown option {opt!r}; choose from {list(questions[qid].options)}")


def posture_from_answers(answers: dict[str, str], questions: dict[str, Question] | None = None) -> Posture:
    """Map questionnaire answers to model control levels in [0, 1]."""
    questions = questions or load_questions()
    levels = {c: 0.0 for c in CONTROLS}
    multipliers: dict[str, float] = {}
    for qid, q in questions.items():
        opt = q.options[answers[qid]]
        if q.control == "patching":
            levels["patching"] = patch_exposure_reduction(opt["sla_days"])
        elif q.control:
            levels[q.control] = float(opt["level"])
        if q.modifies:
            multipliers[q.modifies] = multipliers.get(q.modifies, 1.0) * float(opt["multiplier"])
    for c, m in multipliers.items():
        levels[c] *= m
    return Posture(levels)


def assess(answers: dict[str, str], questions: dict[str, Question] | None = None) -> Assessment:
    questions = questions or load_questions()
    validate_answers(answers, questions)
    qscores = {qid: float(q.options[answers[qid]]["score"]) for qid, q in questions.items()}
    total_w = sum(q.weight for q in questions.values())
    score = 100.0 * sum(questions[qid].weight * s for qid, s in qscores.items()) / total_w
    failures = [qid for qid, q in questions.items() if q.critical and qscores[qid] < q.critical_min]
    tiers, referral = load_tiers()
    if failures:
        tier, meaning = referral["name"], referral["meaning"]
    else:
        t = next(t for t in sorted(tiers, key=lambda t: -t["min_score"]) if score >= t["min_score"])
        tier, meaning = t["name"], t["meaning"]
    return Assessment(answers=dict(answers), score=round(score, 1), tier=tier, tier_meaning=meaning,
                      critical_failures=failures, posture=posture_from_answers(answers, questions), question_scores=qscores)


def best_answers(questions: dict[str, Question] | None = None) -> dict[str, str]:
    questions = questions or load_questions()
    return {qid: q.best_option for qid, q in questions.items()}
