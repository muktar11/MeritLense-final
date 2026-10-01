import math

from django.template.loader import render_to_string
from django.utils import timezone

# Same 4-color palette the on-screen radar chart uses
# (CANDIDATE_COLORS in candidates/page.tsx) - kept identical so the PDF
# export doesn't look like a different chart.
CANDIDATE_COLORS = ["#6366F1", "#10B981", "#F59E0B", "#EF4444"]


def _wrap_label(label, max_chars=16):
    """Splits a long competency label onto up to 2 lines at a word
    boundary near max_chars, so radar-axis labels don't need enough
    horizontal margin to fit the whole phrase on one line - the single
    biggest source of label clipping at a fixed chart size."""
    words = (label or "").split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:2] if len(lines) <= 2 else [" ".join(lines[:-1]), lines[-1]]


def _radar_chart_svg(entries, radius=110, label_margin=92):
    """Pure-SVG radar chart: one axis per competency label that appears in
    the comparison table (same order, same data, no recalculation), one
    polygon per candidate, 0-100% scale. Returns None when there are fewer
    than 3 applicable competencies, since a 1-2-axis "polygon" isn't a
    meaningful radar shape.

    The viewBox is sized as radius + a fixed label_margin on every side
    (not a percentage of a single "size"), so a long axis label always has
    room to render without being clipped by the SVG's own bounds,
    regardless of how many axes there are."""
    labels = []
    seen = set()
    for entry in entries:
        for row in entry["competencies"]:
            if row["label"] not in seen:
                seen.add(row["label"])
                labels.append(row["label"])
    n = len(labels)
    if n < 3:
        return None

    size = (radius + label_margin) * 2
    cx = cy = size / 2
    angle_step = 2 * math.pi / n

    def point_for(value_pct, axis_index):
        angle = -math.pi / 2 + axis_index * angle_step
        r = radius * (max(0, min(100, value_pct)) / 100.0)
        return (cx + r * math.cos(angle), cy + r * math.sin(angle))

    rings = []
    for pct in (25, 50, 75, 100):
        pts = [point_for(pct, i) for i in range(n)]
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
        rings.append(f'<path d="{path}" fill="none" stroke="#e7ecf9" stroke-width="1"/>')

    axis_lines = []
    label_texts = []
    for i, label in enumerate(labels):
        x, y = point_for(100, i)
        axis_lines.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d7deec" stroke-width="1"/>')
        lx, ly = point_for(112, i)
        anchor = "middle"
        if lx < cx - 8:
            anchor = "end"
        elif lx > cx + 8:
            anchor = "start"
        safe_label = (label or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        wrapped = _wrap_label(safe_label)
        line_offset = -((len(wrapped) - 1) * 6)
        for line_idx, line_text in enumerate(wrapped):
            label_texts.append(
                f'<text x="{lx:.1f}" y="{ly + line_offset + line_idx * 12:.1f}" font-size="10.5" '
                f'fill="#4b5563" text-anchor="{anchor}">{line_text}</text>'
            )

    polygons = []
    for idx, entry in enumerate(entries):
        color = CANDIDATE_COLORS[idx % len(CANDIDATE_COLORS)]
        pct_by_label = {row["label"]: row["percentage"] for row in entry["competencies"]}
        pts = [point_for(pct_by_label.get(label, 0), i) for i, label in enumerate(labels)]
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
        polygons.append(f'<path d="{path}" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2"/>')

    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(rings)
        + "".join(axis_lines)
        + "".join(polygons)
        + "".join(label_texts)
        + "</svg>"
    )


def _legend_items(entries):
    return [
        {"name": entry["candidate_name"], "color": CANDIDATE_COLORS[idx % len(CANDIDATE_COLORS)]}
        for idx, entry in enumerate(entries)
    ]


def _competency_table_rows(entries):
    """Union of competency labels across all entries, in first-seen order
    (same as the radar chart axes), each row carrying every candidate's
    percentage for that label - exactly what the on-screen table shows."""
    labels = []
    seen = set()
    classification_by_label = {}
    for entry in entries:
        for row in entry["competencies"]:
            if row["label"] not in seen:
                seen.add(row["label"])
                labels.append(row["label"])
                classification_by_label[row["label"]] = row["classification"]

    rows = []
    for label in labels:
        values = []
        for entry in entries:
            pct = next((r["percentage"] for r in entry["competencies"] if r["label"] == label), None)
            values.append(round(pct) if pct is not None else None)
        rows.append({"label": label, "classification": classification_by_label[label], "values": values})
    return rows


def render_comparison_pdf(*, role_name, entries, key_differences, language="en"):
    from weasyprint import HTML
    from api.core.pdf_fonts import arabic_font_context
    from api.evaluations.certificate_services import _icon_data_uri

    radar_svg = _radar_chart_svg(entries)
    context = {
        "role_name": role_name,
        "entries": entries,
        "legend": _legend_items(entries),
        "competency_rows": _competency_table_rows(entries),
        "key_differences": key_differences,
        "radar_svg": radar_svg,
        "logo_icon_data_uri": _icon_data_uri(),
        "generated_at": timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
    }

    template_name = "dashboard/comparison_ar.html" if language == "ar" else "dashboard/comparison.html"
    if language == "ar":
        context.update(arabic_font_context())

    html_string = render_to_string(template_name, context)
    return HTML(string=html_string).write_pdf()
