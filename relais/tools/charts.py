"""Charts: the visualisation agent writes a specification, our code checks it and draws it with Plotly.

The LLM never writes plotting code. A specification names a result table of the SQL agent and its columns:

    {"type": "line", "tableau": "t1", "x": "trimestre", "y": "taux_ftth_pct", "couleur": null,
     "titre": "...", "titre_x": "...", "titre_y": "..."}

Design rules (fixed here, not left to the LLM): categorical colours in a fixed order (validated palette: colour-blind
separation checked), one y axis only, thin marks, a legend as soon as there are two series, hover on every mark.
"""
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

CHART_TYPES = {"line": "courbe", "bar": "barres", "scatter": "nuage de points", "box": "boîtes à moustaches",
               "histogram": "histogramme"}
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
MAX_SERIES = len(PALETTE)
MAX_CATEGORIES = 60
MAX_POINTS = 100_000
SPEC_KEYS = {"type", "tableau", "x", "y", "couleur", "titre", "titre_x", "titre_y", "orientation"}


class ChartError(ValueError):
    """The specification cannot be drawn (the message tells the agent what to fix)."""


@dataclass
class Chart:
    figure: go.Figure
    type: str
    series: int
    points: int
    spec: dict


def series_count(spec, data):
    kind, y, colour = spec["type"], spec.get("y"), spec.get("couleur")
    if colour:
        return int(data[colour].nunique())
    if kind == "box":
        return int(data[spec["x"]].nunique()) if spec.get("x") else len(y if isinstance(y, list) else [y])
    if isinstance(y, list):
        return len(y)
    return 1


def orient_bars(spec, tables):
    """Bars written the Plotly way for a horizontal chart (x = values, y = categories) are accepted: the columns are
    swapped and the bars made horizontal."""
    table = tables.get(spec.get("tableau")) if isinstance(spec, dict) else None
    if table is None or spec.get("type") != "bar":
        return spec
    data = table.data if hasattr(table, "data") else table
    x, y = spec.get("x"), spec.get("y")
    if isinstance(x, str) and isinstance(y, str) and x in data.columns and y in data.columns:
        if pd.api.types.is_numeric_dtype(data[x]) and not pd.api.types.is_numeric_dtype(data[y]):
            return {**spec, "x": y, "y": x, "orientation": "h",
                    "titre_x": spec.get("titre_y", ""), "titre_y": spec.get("titre_x", "")}
    return spec


def check_spec(spec, tables):
    """The table to draw, or ChartError listing every problem of the specification."""
    if not isinstance(spec, dict):
        raise ChartError("La spécification doit être un objet JSON.")
    problems = [f"Clé inconnue : {k}." for k in spec if k not in SPEC_KEYS]
    kind = spec.get("type")
    if kind not in CHART_TYPES:
        problems.append(f"Type inconnu : {kind!r}. Types possibles : {', '.join(CHART_TYPES)}.")
    table = tables.get(spec.get("tableau"))
    if table is None:
        raise ChartError("\n".join(problems + [f"Tableau inconnu : {spec.get('tableau')!r}. Tableaux disponibles : "
                                               f"{', '.join(tables) or 'aucun'}."]))
    data = table.data if hasattr(table, "data") else table
    y = spec.get("y")
    y_columns = y if isinstance(y, list) else ([y] if y else [])
    needed = [c for c in [spec.get("x"), spec.get("couleur"), *y_columns] if c]
    missing = [c for c in needed if c not in data.columns]
    if missing:
        problems.append(f"Colonnes absentes du tableau {spec['tableau']} : {missing}. Colonnes : "
                        f"{list(data.columns)}.")
    if kind in ("line", "bar", "scatter") and (not spec.get("x") or not y_columns):
        problems.append(f"Un graphique {CHART_TYPES[kind]} demande x et y.")
    if kind == "histogram" and not (spec.get("x") or y_columns):
        problems.append("Un histogramme demande la colonne à répartir (x).")
    if kind == "box" and not y_columns:
        problems.append("Des boîtes à moustaches demandent la colonne de valeurs (y).")
    if spec.get("couleur") and len(y_columns) > 1:
        problems.append("Utiliser soit plusieurs colonnes y, soit une colonne couleur, pas les deux.")
    if not missing:
        for column in y_columns:
            if not pd.api.types.is_numeric_dtype(data[column]):
                problems.append(f"La colonne {column} doit être numérique.")
    if problems:
        raise ChartError("\n".join(problems))
    if data.empty:
        raise ChartError(f"Le tableau {spec['tableau']} est vide.")
    if len(data) > MAX_POINTS:
        raise ChartError(f"{len(data)} lignes : trop pour un graphique (au plus {MAX_POINTS}). Agréger d'abord.")
    series = series_count(spec, data)
    if series > MAX_SERIES:
        raise ChartError(f"{series} séries : au plus {MAX_SERIES}. Regrouper les plus petites ou filtrer.")
    if kind == "bar" and data[spec["x"]].nunique() > MAX_CATEGORIES:
        raise ChartError(f"Plus de {MAX_CATEGORIES} barres : garder les plus grandes ou agréger.")
    return data


def style(figure, spec, series):
    figure.update_layout(
        template="none", paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font={"family": "Inter, Segoe UI, sans-serif", "size": 13, "color": INK_2},
        title={"text": spec.get("titre") or "", "font": {"size": 16, "color": INK}, "x": 0, "xanchor": "left",
               "xref": "paper"},
        showlegend=series >= 2, legend={"title": None, "orientation": "h", "y": -0.18, "font": {"color": INK_2}},
        margin={"l": 20, "r": 20, "t": 60, "b": 20}, hovermode="closest", bargap=0.25, bargroupgap=0.05)
    axis = {"gridcolor": GRID, "linecolor": AXIS, "zeroline": False, "tickfont": {"color": MUTED}, "automargin": True,
            "title": {"font": {"color": INK_2}}}
    figure.update_xaxes(**axis, showgrid=False, title_text=spec.get("titre_x") or spec.get("x") or "")
    figure.update_yaxes(**axis, showgrid=True, title_text=spec.get("titre_y") or "")
    figure.update_traces(selector={"type": "scatter", "mode": "lines"}, line={"width": 2})
    figure.update_traces(selector={"type": "scatter"}, marker={"size": 8, "line": {"width": 2, "color": SURFACE}})
    figure.update_traces(selector={"type": "bar"}, marker={"line": {"width": 2, "color": SURFACE}})
    return figure


def spread_labels(positions, min_gap):
    """Label heights moved apart by at least min_gap, as little as possible (order kept)."""
    order = sorted(range(len(positions)), key=lambda i: positions[i])
    placed = [positions[i] for i in order]
    for k in range(1, len(placed)):
        placed[k] = max(placed[k], placed[k - 1] + min_gap)
    shift = (sum(positions[i] for i in order) - sum(placed)) / len(placed)  # keep the group centred
    result = [0.0] * len(positions)
    for k, i in enumerate(order):
        result[i] = placed[k] + shift
    return result


def label_line_ends(figure):
    """Direct labels at the end of each line (up to 4 series): identity never rests on colour alone. Labels that
    would overlap are moved apart; the x axis keeps the range of the data."""
    traces = [t for t in figure.data if len(t.x)]
    if not traces:
        return
    ends = [float(t.y[-1]) for t in traces]
    values = [float(v) for t in traces for v in t.y if v is not None]
    gap = 0.06 * ((max(values) - min(values)) or 1.0)
    for trace, y in zip(traces, spread_labels(ends, gap)):
        figure.add_annotation(x=trace.x[-1], y=y, text=trace.name, showarrow=False, xanchor="left", xshift=8,
                              font={"size": 12, "color": INK_2})
    xs = [x for t in traces for x in t.x]
    figure.update_xaxes(range=[min(xs), max(xs)])
    figure.update_layout(margin={"r": 140})


def render(spec, tables):
    """Check the specification and draw it; returns a Chart (ChartError if the specification is wrong)."""
    spec = orient_bars(spec, tables)
    data = check_spec(spec, tables)
    kind, x, colour = spec["type"], spec.get("x"), spec.get("couleur")
    y = spec.get("y")
    common = {"color_discrete_sequence": PALETTE}
    if colour:
        common["color"] = colour
    if kind == "line":
        figure = px.line(data, x=x, y=y, markers=len(data) <= 40, **common)
    elif kind == "bar":
        horizontal = spec.get("orientation") == "h"
        figure = px.bar(data, x=y if horizontal else x, y=x if horizontal else y, barmode="group",
                        orientation="h" if horizontal else "v", **common)
    elif kind == "scatter":
        figure = px.scatter(data, x=x, y=y, opacity=0.7 if len(data) > 500 else 1.0, **common)
    elif kind == "box":
        figure = px.box(data, x=x, y=y, points=False, **common)
    else:
        figure = px.histogram(data, x=x or y, **common)
    series = series_count(spec, data)
    figure = style(figure, spec, series)
    if kind == "line" and isinstance(y, str) and not colour:
        figure.update_yaxes(title_text=spec.get("titre_y") or y)
    if kind == "line" and 2 <= series <= 4:
        label_line_ends(figure)
    return Chart(figure=figure, type=kind, series=series, points=len(data), spec=dict(spec))
