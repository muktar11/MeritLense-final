"""Acceptance validation of the 22 Oct 2026 rule decisions on GOV2.0-RC1.

Runs every generated acceptance case of every question rule, and compares
the score of standard answer profiles before (templates PROPOSED: generic
band, two or more omissions held) and after (approved templates), including
the effect of each adopted additional Core point and of conditional points.
Writes CSV detail to docs/governance/validation_2026-10-22/ and prints the
summary. Run from the repository root:

    python scripts/validate_rule_decisions_2026_10_22.py
"""
import collections
import csv
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.evaluations import rubric_specs  # noqa: E402

DRAFT = "GOV2.0-RC1"
OUT = ROOT / "docs" / "governance" / "validation_2026-10-22"


def split_points(text):
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return [p.strip() for p in text.split(",") if p.strip()]


def score(code, matched, total, established=(), negatives=()):
    _, spec = rubric_specs.spec_for(DRAFT, code)
    result = rubric_specs.execute(spec, max_score=5, matched_ordinals=matched, total_points=total,
                                  negative_ordinals=negatives, established_ordinals=established)
    if result["status"] == rubric_specs.STATUS_PENDING:
        return "HELD"
    return str(int(result["score"])) if result["score"] == int(result["score"]) else str(result["score"])


_PROPOSED = None


def previous_configuration():
    global _PROPOSED
    if _PROPOSED is None:
        spec_set = rubric_specs.spec_set_for_version(DRAFT)
        classification = json.loads(json.dumps(spec_set["classification"]))
        for template in classification["templates"].values():
            template["status"] = "PROPOSED"
        _PROPOSED = {**spec_set, "classification": classification}
    return mock.patch.object(rubric_specs, "spec_set_for_version",
                             lambda version: _PROPOSED if version == DRAFT else None)


def main():
    bank = {q["question_code"]: q for q in json.loads(
        (ROOT / "api/interviews/fixtures/question_bank/governance_v2_0.json").read_text(encoding="utf-8"))["questions"]}
    classification = rubric_specs.spec_set_for_version(DRAFT)["classification"]
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Acceptance cases of every rule.
    acceptance = collections.Counter()
    failures = []
    rules = {}
    for code in classification["questions"]:
        rule = rubric_specs.spec_for(DRAFT, code)[1].get("rule")
        if rule is None:
            failures.append((code, "no active rule"))
            continue
        rules[code] = rule
        gaps = rubric_specs.acceptance_gaps(rule)
        acceptance["cases"] += len(rule["acceptance_cases"])
        acceptance["questions_passing"] += not gaps
        failures.extend((code, gap) for gap in gaps)

    # 2. Standard answer profiles, before and after.
    profiles = {}
    for code, entry in classification["questions"].items():
        total = len(split_points(bank[code]["must_include_en"]))
        rule = rules[code]
        core = [o for [o] in rule["full"]]
        central = sorted({o for group in rule["partial"] for o in group}) if rule["partial"] else core
        minimal = sorted({group[0] for group in rule["partial"]}) if rule["partial"] else core
        compensable = [o for o in core if o not in central and o not in rule["conditional"]]
        profiles[code] = {
            "all points": (list(range(1, total + 1)), rule["conditional"]),
            "every Core outcome": (core, rule["conditional"]),
            "central competence only": (minimal, rule["conditional"]),
            "one compensable Core gap": ([o for o in range(1, total + 1) if o != compensable[0]], rule["conditional"])
            if compensable else None,
            "central competence missing": ([o for o in core if o not in central], rule["conditional"]),
            "conditional points silent": ([o for o in core if o not in rule["conditional"]], [])
            if rule["conditional"] else None,
        }
    transitions = collections.defaultdict(collections.Counter)
    rows = []
    for code, by_profile in profiles.items():
        total = len(split_points(bank[code]["must_include_en"]))
        for name, profile in by_profile.items():
            if profile is None:
                continue
            matched, established = profile
            after = score(code, matched, total, established)
            with previous_configuration():
                before = score(code, matched, total, established)
            transitions[name][f"{before} -> {after}"] += 1
            rows.append([code, name, " ".join(map(str, matched)), " ".join(map(str, established)), before, after])
    with open(OUT / "profile_scores.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question", "profile", "matched points", "established conditions", "before", "after"])
        writer.writerows(rows)

    # 3. Each additional Core decision: answer with every previous Core
    # outcome but not the proposed point.
    additions = []
    for code, entry in classification["questions"].items():
        total = len(split_points(bank[code]["must_include_en"]))
        for addition in entry.get("core_additions", []):
            matched = [o for o in range(1, total + 1) if o != addition["point"]]
            established = rules[code]["conditional"]
            additions.append([code, addition["point"], addition["text"], addition["decision"], addition["basis"],
                              score(code, matched, total, established), addition["rationale"]])
    with open(OUT / "additional_core_decisions.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question", "point", "text", "decision", "basis",
                         "score when only this point is missing", "rationale"])
        writer.writerows(additions)

    # 4. The 24 all-central questions.
    with open(OUT / "all_central_questions.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question", "core", "treatment", "partial mapping", "justification"])
        for code, entry in classification["questions"].items():
            mapping = entry["mapping"]
            if mapping.get("partial") or mapping.get("exception"):
                writer.writerow([code, " ".join(map(str, entry["core"])),
                                 "BINARY" if mapping.get("exception") else "PARTIAL",
                                 json.dumps(mapping.get("partial")) if mapping.get("partial") else "",
                                 mapping.get("binary_justification") or mapping.get("partial_rationale")])

    # 5. Conditional points.
    with open(OUT / "conditional_points.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question", "point", "text", "central"])
        for code, rule in rules.items():
            points = split_points(bank[code]["must_include_en"])
            central = {o for group in rule["partial"] or [] for o in group}
            for o in rule["conditional"]:
                writer.writerow([code, o, points[o - 1], "yes" if o in central else "no"])

    print(f"rules: {len(rules)} active of {len(classification['questions'])}; acceptance cases run: "
          f"{acceptance['cases']}; questions passing: {acceptance['questions_passing']}; failures: {len(failures)}")
    for failure in failures[:20]:
        print("  FAIL", *failure)
    for name, counter in transitions.items():
        print(f"{name}: " + ", ".join(f"{k} x{v}" for k, v in sorted(counter.items())))
    adopted = [a for a in additions if a[3] == "ADOPTED"]
    print(f"additional Core: {len(adopted)} adopted, {len(additions) - len(adopted)} not adopted; "
          f"adopted point missing alone scores: {dict(collections.Counter(a[5] for a in adopted))}; "
          f"not-adopted point missing alone scores: "
          f"{dict(collections.Counter(a[5] for a in additions if a[3] != 'ADOPTED'))}; "
          f"by basis: {dict(collections.Counter(a[4] for a in additions))}")
    ecg = [(name, score("ECG-TSK-001", matched, 6, negatives=negatives)) for name, matched, negatives in (
        ("all six points", range(1, 7), ()),
        ("both safeguards + 3 of 4 outcomes", [1, 2, 3, 4, 6], ()),
        ("4 outcomes, consent missing", [1, 3, 4, 5, 6], ()),
        ("4 outcomes, pulls the arms (affirmed)", [3, 4, 5, 6], (1,)),
    )]
    print("ECG-TSK-001 (out of 5; HELD = escalated):", ecg)
    print("selection: ECG-TSK-001 selectable =", rubric_specs.rule_complete(DRAFT, "ECG-TSK-001"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
