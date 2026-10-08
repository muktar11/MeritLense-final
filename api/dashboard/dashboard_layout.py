"""Company-level customization of the B2B overview dashboard.

The layout is just an ordered list of the widget ids the company wants
visible. Widget ids not listed are hidden (still reachable from the
Analytics page). An empty/unset layout means "use the default", so the
dashboard is fully usable without any customization - and a company that
never customizes automatically picks up future changes to DEFAULT_WIDGETS.
"""

# Decision-oriented widgets first: the default overview answers "who needs
# attention / what should I review next / what is the readiness status /
# what is happening" without every statistical chart.
DEFAULT_WIDGETS = (
    "requires_attention",
    "readiness_index",
    "recent_evaluations",
    "evaluation_status",
    "evaluation_trend",
)

OPTIONAL_WIDGETS = (
    "performance_trend",
    "score_by_role",
    "job_role_distribution",
    "language_distribution",
    "time_of_day",
    "monthly_activity",
    "candidate_comparison",
)

WIDGET_IDS = DEFAULT_WIDGETS + OPTIONAL_WIDGETS


def validate_widgets(widgets):
    """Return the cleaned widget list, or raise ValueError with a message."""
    if not isinstance(widgets, list):
        raise ValueError("widgets must be a list of widget ids.")
    unknown = [w for w in widgets if w not in WIDGET_IDS]
    if unknown:
        raise ValueError(f"Unknown widget ids: {', '.join(map(str, unknown))}.")
    if len(set(widgets)) != len(widgets):
        raise ValueError("widgets must not contain duplicates.")
    return list(widgets)


def resolve_layout(company):
    """The layout the dashboard should render for this company."""
    stored = (company.dashboard_layout or {}).get("widgets")
    if isinstance(stored, list):
        # Drop ids that no longer exist (a widget retired after the company
        # saved its layout) rather than failing the whole dashboard.
        widgets = [w for w in stored if w in WIDGET_IDS]
        return {"widgets": widgets, "is_default": False}
    return {"widgets": list(DEFAULT_WIDGETS), "is_default": True}
