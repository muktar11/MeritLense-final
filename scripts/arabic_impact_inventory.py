"""Deliverable F / D-07: READ-ONLY inventory of historical Arabic assessments.

Classifies every Arabic-language evaluation by whether it was scored through
the v1.2 Arabic indicator defect (each Arabic Must Include imported as one
step, so Arabic answers could not match the English scoring rule). Nothing is
written: the whole run happens inside a READ ONLY database transaction, and
no candidate names, emails or passport numbers are exported - only IDs.

Run on the server from the backend directory (exec, not `shell <`, which
breaks multi-line blocks in the interactive console):
    INVENTORY_OUT=/tmp/arabic_impact_inventory.csv venv/bin/python manage.py shell \
        -c "exec(open('scripts/arabic_impact_inventory.py').read())"

Outputs <INVENTORY_OUT> (one row per evaluation) and <INVENTORY_OUT>.summary.txt.
"""
import csv
import os
from collections import Counter

from django.db import connection, transaction

from api.evaluations.models import Evaluation, ResponseEvaluationResult
from api.reports.models import EvaluationReport
from api.sessions.models import CandidateResponse

OUT = os.environ.get("INVENTORY_OUT", "/tmp/arabic_impact_inventory.csv")
V12_RULE_VERSION = "governance-v1.2-corrected"
ARABIC_SEMICOLON = "؛"

CLASS_A = "A - Affected: scored under v1.2 with single-step Arabic indicators"
CLASS_B = "B - Arabic, scored under an earlier rule version (not this defect; review separately)"
CLASS_C = "C - Arabic, not yet scored (new pipeline would apply after release)"
CLASS_D = "D - Arabic session, no answers recorded"
CLASS_E = "E - Arabic, scored under v1.2 with repaired multi-step indicators (not this defect)"


def is_single_step_arabic(template):
    steps = getattr(template, "expected_steps", None) or []
    return (template is not None and (template.language or "").upper() == "AR"
            and len(steps) == 1 and ARABIC_SEMICOLON in str(steps[0]))


def run():
    rows = []
    evaluations = (
        Evaluation.objects.filter(session__candidate_language__iregex=r"^ar")
        .select_related("session", "company", "scoring_rule_set")
        .order_by("created_at")
    )
    for evaluation in evaluations:
        session = evaluation.session
        responses = list(
            CandidateResponse.objects.filter(session=session).select_related("question__question_template")
        )
        results = list(
            ResponseEvaluationResult.objects.filter(evaluation=evaluation).select_related("rule_set", "question__question_template")
        )
        versions = sorted({r.rule_set.version for r in results if r.rule_set_id})
        single_step = [r for r in results if is_single_step_arabic(getattr(r.question, "question_template", None))]
        zero_matched = sum(1 for r in results if not r.matched_indicators)

        if not responses:
            impact = CLASS_D
        elif not results:
            impact = CLASS_C
        elif V12_RULE_VERSION in versions and single_step:
            impact = CLASS_A
        elif V12_RULE_VERSION in versions:
            impact = CLASS_E
        else:
            impact = CLASS_B

        report = (EvaluationReport.objects.filter(evaluation=evaluation)
                  .order_by("-generated_at", "-created_at").first())
        rows.append({
            "evaluation_id": str(evaluation.public_id),
            "session_id": str(session.public_id),
            "company_id": evaluation.company_id or "",
            "company_name": evaluation.company.name if evaluation.company_id else "(individual employer)",
            "role_code": session.role_code,
            "evaluation_tier": evaluation.evaluation_tier,
            "session_created": session.created_at.isoformat() if session.created_at else "",
            "session_ended": session.ended_at.isoformat() if session.ended_at else "",
            "evaluation_status": evaluation.status,
            "evaluation_completed": evaluation.completed_at.isoformat() if evaluation.completed_at else "",
            "question_set_version": session.question_set_version,
            "scoring_rule_versions": ";".join(versions),
            "responses": len(responses),
            "scored_responses": len(results),
            "scored_single_step_arabic": len(single_step),
            "scored_with_zero_matched_indicators": zero_matched,
            "overall_score": str(evaluation.score) if evaluation.score is not None else "",
            "readiness_status": evaluation.readiness_status,
            "report_number": report.report_number if report else "",
            "report_status": report.report_status if report else "",
            "report_generated": report.generated_at.isoformat() if report and report.generated_at else "",
            "certificate_status": evaluation.certificate_status,
            "impact_class": impact,
        })
    return rows


with transaction.atomic():
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("SET TRANSACTION READ ONLY")
    inventory = run()
    transaction.set_rollback(True)  # belt and braces: nothing is ever committed

fields = list(inventory[0].keys()) if inventory else ["impact_class"]
with open(OUT, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    writer.writerows(inventory)

by_class = Counter(r["impact_class"] for r in inventory)
affected = [r for r in inventory if r["impact_class"] == CLASS_A]
lines = [
    f"Arabic evaluations found: {len(inventory)}",
    *[f"  {k}: {v}" for k, v in sorted(by_class.items())],
    f"Class A date range (session created): "
    f"{min((r['session_created'] for r in affected), default='-')} to {max((r['session_created'] for r in affected), default='-')}",
    f"Class A with a report: {sum(1 for r in affected if r['report_number'])}",
    f"Class A with certificate issued: {sum(1 for r in affected if r['certificate_status'] == 'ISSUED')}",
    f"Class A by readiness: {dict(Counter(r['readiness_status'] for r in affected))}",
    f"Class A companies: {len({r['company_id'] for r in affected})}",
    "Read-only run: no data was changed.",
]
with open(OUT + ".summary.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print("\n".join(lines))
print(f"Wrote {OUT} and {OUT}.summary.txt")
