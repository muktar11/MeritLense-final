"""Offline selection validation for a pooled question bank package.

Runs `question_allocation.select_questions` against a package fixture (no
database access) for every role x tier x language, simulating first
attempts and retakes, and checks every selection against the approved
methodology. Exits non-zero if any rule is broken.
"""
import json
import random
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.interviews.question_allocation import (
    FULL, SCREENING, TIER_TARGETS, AllocationGap, QuestionCandidate,
    critical_competencies, select_questions,
)

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "question_bank" / "governance_v2_0_draft.json"
LANGUAGES = ("EN", "AR")


def simulate(package, sessions, seed, retake_rate=0.3):
    rng = random.Random(seed)
    role_map = package["role_code_map"]
    by_role = {}
    for q in package["questions"]:
        by_role.setdefault(q["role"], []).append(q)

    results = []
    for profile in package["role_profiles"]:
        role = profile["role"]
        role_code = role_map[role]
        critical = critical_competencies(profile)
        skill_pool = sorted({k.strip() for q in by_role[role] for k in q["ideal_keywords"].split(";") if k.strip()})
        for tier in (FULL, SCREENING):
            allocation = profile["allocation"][tier]
            for language in LANGUAGES:
                pool = [QuestionCandidate.from_fixture(q, language) for q in by_role[role]]
                eligible = {q.code for q in pool if tier == FULL or q.screening_eligible}
                violations = Counter()
                exposure = Counter()
                retakes = 0
                retake_reused = []
                gaps = []
                for _ in range(sessions):
                    skills = rng.sample(skill_pool, k=rng.randint(0, 3))
                    attempts = [()]
                    if rng.random() < retake_rate:
                        attempts.append(None)
                    previous = ()
                    for attempt in attempts:
                        try:
                            result = select_questions(
                                pool=pool, allocation=allocation, critical=critical, tier=tier,
                                role_code=role_code, language=language, candidate_skills=skills,
                                previous_codes=previous, rng=rng,
                            )
                        except AllocationGap as exc:
                            gaps.extend(exc.gaps)
                            break
                        chosen = result.questions
                        codes = [q.code for q in chosen]
                        if len(chosen) != TIER_TARGETS[tier]:
                            violations["wrong_count"] += 1
                        if len(set(codes)) != len(codes):
                            violations["duplicate_question"] += 1
                        if Counter(q.competency_code for q in chosen) != Counter(allocation):
                            violations["allocation_not_met"] += 1
                        if not critical <= {q.competency_code for q in chosen}:
                            violations["critical_not_covered"] += 1
                        if any(q.role_code != role_code or q.language != language for q in chosen):
                            violations["wrong_role_or_language"] += 1
                        if any(q.code not in eligible for q in chosen):
                            violations["not_screening_eligible"] += 1
                        exposure.update(codes)
                        if attempt is None:
                            retakes += 1
                            retake_reused.append(len(result.reused_codes))
                        previous = tuple(codes)
                used = len(exposure)
                results.append({
                    "role": role, "role_code": role_code, "tier": tier, "language": language,
                    "sessions": sessions, "retakes": retakes,
                    "eligible_pool": len(eligible), "distinct_questions_used": used,
                    "pool_utilisation_pct": round(100 * used / len(eligible), 1) if eligible else 0,
                    "max_question_exposure_pct": round(100 * max(exposure.values()) / (sessions + retakes), 1) if exposure else 0,
                    "retakes_fully_fresh_pct": round(100 * sum(1 for n in retake_reused if n == 0) / retakes, 1) if retakes else None,
                    "retake_max_reused": max(retake_reused) if retake_reused else 0,
                    "violations": dict(violations),
                    "gaps": sorted(set(gaps)),
                })
    return results


class Command(BaseCommand):
    help = "Simulate competency-allocation question selection against a question bank package (no DB writes)."

    def add_arguments(self, parser):
        parser.add_argument("fixture_path", nargs="?", default=str(DEFAULT_FIXTURE))
        parser.add_argument("--sessions", type=int, default=1000, help="First attempts per role/tier/language.")
        parser.add_argument("--seed", type=int, default=20261010)
        parser.add_argument("--output", help="Write the per-role results as JSON to this path.")

    def handle(self, *args, **options):
        package = json.loads(Path(options["fixture_path"]).read_text(encoding="utf-8"))
        results = simulate(package, options["sessions"], options["seed"])
        if options["output"]:
            Path(options["output"]).write_text(json.dumps(results, indent=1), encoding="utf-8")

        selections = sum(r["sessions"] + r["retakes"] for r in results)
        broken = [r for r in results if r["violations"] or r["gaps"]]
        self.stdout.write(f"{len(results)} role/tier/language combinations, {selections} selections simulated.")
        for r in broken:
            self.stdout.write(self.style.ERROR(f"{r['role']} {r['tier']} {r['language']}: {r['violations']} {r['gaps']}"))
        if broken:
            raise CommandError(f"{len(broken)} combinations broke the selection methodology.")
        self.stdout.write(self.style.SUCCESS("All selections met the allocation matrix, counts, Critical coverage, role and language rules."))
