"""Customization of the overview dashboards (B2B company-wide, B2C per user).

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

# Individual employers (B2C): same idea, their own widget set - there is no
# team, so no team-only widgets, and the candidate list is part of it.
B2C_DEFAULT_WIDGETS = (
    "requires_attention",
    "readiness_index",
    "recent_candidates",
    "recent_evaluations",
    "evaluation_status",
)

B2C_OPTIONAL_WIDGETS = (
    "evaluation_trend",
    "performance_trend",
    "job_role_distribution",
    "language_distribution",
    "time_of_day",
    "monthly_activity",
    "candidate_comparison",
)

B2C_WIDGET_IDS = B2C_DEFAULT_WIDGETS + B2C_OPTIONAL_WIDGETS


def validate_widgets(widgets, widget_ids=WIDGET_IDS):
    """Return the cleaned widget list, or raise ValueError with a message."""
    if not isinstance(widgets, list):
        raise ValueError("widgets must be a list of widget ids.")
    unknown = [w for w in widgets if w not in widget_ids]
    if unknown:
        raise ValueError(f"Unknown widget ids: {', '.join(map(str, unknown))}.")
    if len(set(widgets)) != len(widgets):
        raise ValueError("widgets must not contain duplicates.")
    return list(widgets)


def resolve_layout(owner, widget_ids=WIDGET_IDS, default_widgets=DEFAULT_WIDGETS):
    """The layout the dashboard should render for this company (or, with
    the B2C widget set, this individual employer profile)."""
    stored = (owner.dashboard_layout or {}).get("widgets")
    if isinstance(stored, list):
        # Drop ids that no longer exist (a widget retired after the owner
        # saved its layout) rather than failing the whole dashboard.
        widgets = [w for w in stored if w in widget_ids]
        return {"widgets": widgets, "is_default": False}
    return {"widgets": list(default_widgets), "is_default": True}
