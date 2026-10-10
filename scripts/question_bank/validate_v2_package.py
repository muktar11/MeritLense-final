"""Automated content validation of a question bank package.

Read-only. Produces a summary JSON and a per-question exceptions CSV for
reviewers. Checks are deliberately conservative heuristics: they flag
questions for a human (linguist / SME) to look at; they do not prove a
question is wrong.

Usage:
    python scripts/question_bank/validate_v2_package.py [package.json] [out_dir]
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
DEFAULT_PACKAGE = REPO / "api/interviews/fixtures/question_bank/governance_v2_0_draft.json"

ARABIC = re.compile(r"[؀-ۿ]")
LATIN_WORD = re.compile(r"\b[A-Za-z]{3,}\b")
ALLOWED_LATIN = {"PPE", "CPR", "SDS", "MSDS", "LOTO", "SOP", "ID", "QR", "GPS", "ABC", "AED", "SPF", "HACCP", "COSHH", "PIN", "CCTV", "WiFi", "Wi-Fi", "VIP"}
RISKY_TOPICS = {
    "medication": re.compile(r"\b(medication|medicine|dose|tablet|pills?|insulin|inhaler)\b", re.I),
    "chemical": re.compile(r"\b(chemical|bleach|solvent|pesticide|disinfectant|acid)\b", re.I),
    "electrical": re.compile(r"\b(electric|electrical|wiring|live wire|socket|power tool)\b", re.I),
}
BOUNDARY = re.compile(
    r"\b(authori[sz]ed|prescri\w*|care plan|supervisor|nurse|doctor|vet\w*|label|SDS|PPE|manager|"
    r"report|escalat\w*|do not|don't|never|within (your )?(role|training)|lock|isolat\w*|guardian|parent|"
    r"procedure|policy|instruction)\b", re.I,
)


PROMPT_VERB = re.compile(r"^(describe|explain|list|tell|outline|walk|show|give|state|identify|demonstrate)\b|\b(describe|explain) (how|what|the)\b", re.I)
PROMPT_VERB_AR = re.compile(r"(صف|اشرح|اذكر|وضح|وضّح|بيّن|بين|حدد|كيف|ماذا|ما الذي|ما هي|متى|لماذا)")


def split_en(text):
    text = str(text or "")
    sep = ";" if ";" in text else ","
    return [p.strip(" .") for p in text.split(sep) if p.strip(" .")]


def split_ar(text):
    text = str(text or "")
    if re.search(r"[;؛]", text):
        return [p.strip(" .،") for p in re.split(r"[;؛]", text) if p.strip(" .،")]
    return [p.strip(" .") for p in re.split(r"[،,]", text) if p.strip(" .")]


def norm_tokens(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3}


def validate(package):
    from api.interviews.management.commands.import_governance_question_bank import _scoring_shape

    questions = package["questions"]
    profiles = {p["role"]: p for p in package["role_profiles"]}
    flags = defaultdict(list)

    def flag(q, check, detail=""):
        flags[q["question_code"]].append((check, detail))

    for q in questions:
        # -- linguistic ------------------------------------------------
        for f in ("question_ar", "must_include_ar"):
            if not ARABIC.search(q[f]):
                flag(q, "ar_field_without_arabic", f)
            latin = [w for w in LATIN_WORD.findall(q[f]) if w not in ALLOWED_LATIN]
            if latin:
                flag(q, "latin_words_in_arabic", f"{f}: {' '.join(sorted(set(latin)))}")
        for f in ("question_en", "must_include_en", "ideal_keywords", "negative_indicators"):
            if ARABIC.search(q[f]):
                flag(q, "arabic_in_english_field", f)
        for f in ("question_en", "question_ar", "must_include_en", "must_include_ar"):
            if "  " in q[f] or q[f] != q[f].strip():
                flag(q, "spacing", f)
            if q[f].count("(") != q[f].count(")"):
                flag(q, "unbalanced_parentheses", f)
        # A prompt is either a question or an instruction ("Describe how
        # you would ..."); a bare scenario asks the candidate nothing.
        if not q["question_en"].rstrip().endswith("?") and not PROMPT_VERB.search(q["question_en"]):
            flag(q, "scenario_without_a_prompt_en", q["question_en"][-60:])
        if not re.search(r"[?؟]\s*$", q["question_ar"]) and not PROMPT_VERB_AR.search(q["question_ar"]):
            flag(q, "scenario_without_a_prompt_ar", q["question_ar"][-60:])
        ratio = len(q["question_ar"]) / max(len(q["question_en"]), 1)
        if ratio < 0.55 or ratio > 1.5:
            flag(q, "ar_en_question_length_ratio", f"{ratio:.2f}")
        en_items, ar_items = split_en(q["must_include_en"]), split_ar(q["must_include_ar"])
        if len(en_items) != len(ar_items):
            flag(q, "must_include_item_count_en_ar", f"EN {len(en_items)} / AR {len(ar_items)}")
        if ";" not in q["must_include_en"] and len(en_items) > 1:
            flag(q, "must_include_en_comma_delimited", f"{len(en_items)} items")

        # -- scoring ---------------------------------------------------
        shape = _scoring_shape(q["score_note"])
        if shape["scoring_type"] != "0/3/5":
            flag(q, "score_shape_not_5_3_0", f"{shape['scoring_type']} ({q['source']})")
        if len(en_items) >= 7:
            flag(q, "many_required_indicators", f"{len(en_items)} required for full marks")
        if len(en_items) <= 1:
            flag(q, "single_required_indicator", "")
        if q["en_points"] != len(en_items):
            flag(q, "en_points_differ_from_items", f"points {q['en_points']} / items {len(en_items)}")
        neg = split_en(q["negative_indicators"])
        if not neg:
            flag(q, "no_negative_indicators", "")

        # -- structural / governance -----------------------------------
        profile_level = profiles[q["role"]]["criticality"].get(q["competency_code"])
        if profile_level and profile_level != q["criticality"] and "proposed_criticality" not in q:
            flag(q, "criticality_differs_from_role_profile", f"{q['criticality']} vs profile {profile_level}")

        # -- professional (safety-boundary heuristics) -----------------
        topics = [t for t, rx in RISKY_TOPICS.items() if rx.search(q["question_en"])]
        if topics and not BOUNDARY.search(q["must_include_en"]):
            flag(q, "risk_topic_without_boundary_in_must_include", ",".join(topics))

    # -- duplication (within role) -------------------------------------
    by_role = defaultdict(list)
    for q in questions:
        by_role[q["role"]].append(q)
    near_dupes = []
    for role, qs in by_role.items():
        # Ignore the role's shared template wording ("... using the hotel's
        # approved front-desk procedure"): compare only distinctive words,
        # i.e. those in fewer than 10% of the role's questions.
        doc_freq = Counter(w for q in qs for w in norm_tokens(q["question_en"]))
        common = {w for w, n in doc_freq.items() if n >= len(qs) * 0.1}
        tokens = {q["question_code"]: norm_tokens(q["question_en"]) - common for q in qs}
        for i, a in enumerate(qs):
            ta = tokens[a["question_code"]]
            for b in qs[i + 1:]:
                tb = tokens[b["question_code"]]
                if len(ta) < 2 or len(tb) < 2:
                    continue
                jac = len(ta & tb) / len(ta | tb)
                if jac >= 0.6:
                    near_dupes.append((role, a["question_code"], b["question_code"], round(jac, 2)))
                    flag(a, "near_duplicate_question", f"{b['question_code']} ({jac:.2f})")
                    flag(b, "near_duplicate_question", f"{a['question_code']} ({jac:.2f})")
    exact_ar = Counter(q["question_ar"] for q in questions)
    for q in questions:
        if exact_ar[q["question_ar"]] > 1:
            flag(q, "identical_arabic_question_text", f"x{exact_ar[q['question_ar']]}")

    counts = Counter(check for items in flags.values() for check, _ in items)
    by_source = defaultdict(Counter)
    src = {q["question_code"]: q["source"] for q in questions}
    for code, items in flags.items():
        for check in {c for c, _ in items}:
            by_source[check][src[code]] += 1
    return flags, counts, by_source, near_dupes


def main():
    package_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PACKAGE
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
    package = json.loads(package_path.read_text(encoding="utf-8"))
    flags, counts, by_source, near_dupes = validate(package)
    questions = {q["question_code"]: q for q in package["questions"]}

    with open(out_dir / "question_bank_v2_exceptions.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_code", "role", "source", "check", "detail", "question_en", "question_ar"])
        for code, items in sorted(flags.items()):
            q = questions[code]
            for check, detail in items:
                writer.writerow([code, q["role"], q["source"], check, detail, q["question_en"], q["question_ar"]])

    summary = {
        "questions": len(questions),
        "questions_with_any_flag": len(flags),
        "checks": {c: {"flags": n, **by_source[c]} for c, n in counts.most_common()},
        "near_duplicates": near_dupes,
    }
    (out_dir / "question_bank_v2_validation_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(flags)} of {len(questions)} questions have at least one flag")
    for c, n in counts.most_common():
        print(f"  {n:5}  {c:45} {dict(by_source[c])}")


if __name__ == "__main__":
    import django

    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")
    django.setup()
    main()
