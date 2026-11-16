"""The figures of the observability console (dark interface): the span waterfall, the risk gauge and its curve.

Kept apart from Streamlit so that they are tested without a browser.
"""
import time

import plotly.graph_objects as go

from relais.tools.charts import AXIS_DARK, GRID_DARK, INK_2_DARK, INK_DARK, MUTED, PALETTE_DARK, SURFACE_DARK

# one fixed colour per agent (validated dark palette, fixed order): colour follows the agent, never its rank
AGENT_COLOURS = {"planificateur": PALETTE_DARK[0], "interprete": PALETTE_DARK[4], "sql": PALETTE_DARK[1],
                 "statistiques": PALETTE_DARK[2], "visualisation": PALETTE_DARK[3], "relecteur": PALETTE_DARK[6]}
ACTION_LABELS = {"planifier": "planifie", "rediger": "rédige", "etape": "travaille", "relire": "relit",
                 "interpreter": "interprète", "appel_llm": "appel au modèle", "executer_sql": "requête SQL",
                 "executer_python": "code Python", "creer_graphique": "graphique"}
FAILURE = "#e66767"
ROW_HEIGHT = 22


def _depths(events):
    parents = {e.id: e.parent for e in events}
    depth = {}
    for e in events:
        d, p = 0, e.parent
        while p is not None:
            d, p = d + 1, parents.get(p)
        depth[e.id] = d
    return depth


def waterfall_figure(events, running=(), started_at=None, now=None, hide_llm_calls=False, max_rows=45):
    """The span waterfall: one bar per event (start, duration), nested under its agent, coloured by agent; failed
    events in the failure colour and marked; running events drawn up to now, lighter."""
    now = now or time.time()
    all_events = sorted(list(events) + list(running), key=lambda e: (e.start, e.id))
    if hide_llm_calls:
        all_events = [e for e in all_events if e.action != "appel_llm"]
    all_events = all_events[-max_rows:]
    figure = go.Figure()
    if not all_events:
        return _dark_layout(figure, 120, "Cascade des spans")
    origin = started_at or all_events[0].start
    depth = _depths(all_events)
    running_ids = {e.id for e in running}
    labels, names, starts, widths, colours, texts, hovers, opacities, outlines = [], [], [], [], [], [], [], [], []
    for i, e in enumerate(all_events):
        end = e.end if e.end is not None else now
        indent = " " * (depth[e.id] - 1) + "└ " if depth[e.id] else ""
        name = e.agent if depth[e.id] == 0 else ACTION_LABELS.get(e.action, e.action)
        labels.append(str(i))  # the axis keeps the order; the readable label is drawn on the left, aligned
        names.append((indent + name, depth[e.id] == 0))
        starts.append(e.start - origin)
        widths.append(max(end - e.start, 0.04))
        failed = not e.ok and e.id not in running_ids
        colours.append(FAILURE if failed else AGENT_COLOURS.get(e.agent, MUTED))
        outlines.append(FAILURE if failed else SURFACE_DARK)
        opacities.append(0.45 if e.id in running_ids else (0.75 if depth[e.id] else 1.0))
        duration = end - e.start
        texts.append(("✗ " if failed else "") + (f"{duration:.1f} s" if duration >= 0.3 else ""))
        detail = e.attributes.get("requete") or e.attributes.get("erreur") or e.attributes.get("verdict") or ""
        hovers.append(f"<b>{e.agent}</b> · {ACTION_LABELS.get(e.action, e.action)}<br>début +{e.start - origin:.2f} s, "
                      f"durée {duration:.2f} s{' (en cours)' if e.id in running_ids else ''}"
                      + (f"<br>{str(detail)[:160]}" if detail else ""))
    figure.add_trace(go.Bar(
        y=labels, x=widths, base=starts, orientation="h", marker={"color": colours, "opacity": opacities,
                                                                     "line": {"color": outlines, "width": 1.5}},
        text=texts, textposition="outside", textfont={"color": INK_2_DARK, "size": 11}, cliponaxis=False,
        hovertext=hovers, hoverinfo="text", showlegend=False))
    figure.update_yaxes(autorange="reversed", showticklabels=False, showgrid=False)
    figure.update_xaxes(title_text="secondes depuis le début du run", ticksuffix=" s", gridcolor=GRID_DARK,
                        zeroline=False, tickfont={"color": MUTED}, domain=[0.27, 1.0])
    for label, (name, top) in zip(labels, names):
        figure.add_annotation(x=0, xref="paper", y=label, yref="y", text=f"<b>{name}</b>" if top else name,
                              showarrow=False, xanchor="left",
                              font={"size": 12 if top else 11, "color": INK_DARK if top else MUTED})
    return _dark_layout(figure, 70 + ROW_HEIGHT * len(all_events), "Cascade des spans")


def risk_gauge(risk):
    value = None if risk is None else round(100 * risk)
    colour = PALETTE_DARK[2] if value is None or value < 30 else (PALETTE_DARK[3] if value < 60 else FAILURE)
    figure = go.Figure(go.Indicator(
        mode="gauge+number", value=value, number={"suffix": " %", "font": {"size": 30, "color": INK_DARK}},
        gauge={"axis": {"range": [0, 100], "tickcolor": MUTED, "tickfont": {"color": MUTED, "size": 10}},
               "bar": {"color": colour, "thickness": 0.6}, "bgcolor": GRID_DARK, "borderwidth": 0,
               "steps": [{"range": [0, 100], "color": GRID_DARK}]}))
    return _dark_layout(figure, 170, None, margin={"l": 25, "r": 25, "t": 10, "b": 5})


def risk_curve(risks):
    figure = go.Figure(go.Scatter(x=[k for k, _ in risks], y=[100 * r for _, r in risks], mode="lines",
                                  line={"color": PALETTE_DARK[0], "width": 2}, fill="tozeroy",
                                  fillcolor="rgba(57,135,229,0.15)", hovertemplate="%{y:.0f} %<extra></extra>"))
    figure.update_yaxes(range=[0, 100], ticksuffix=" %", gridcolor=GRID_DARK, tickfont={"color": MUTED})
    figure.update_xaxes(title_text="événements terminés", gridcolor=GRID_DARK, tickfont={"color": MUTED})
    return _dark_layout(figure, 150, None, margin={"l": 45, "r": 10, "t": 5, "b": 35})


def _dark_layout(figure, height, title, margin=None):
    figure.update_layout(
        height=height, paper_bgcolor=SURFACE_DARK, plot_bgcolor=SURFACE_DARK, bargap=0.25,
        font={"family": "Inter, Segoe UI, sans-serif", "color": INK_2_DARK, "size": 12},
        title={"text": title or "", "font": {"size": 14, "color": INK_DARK}, "x": 0, "xanchor": "left",
               "xref": "paper"},
        margin=margin or {"l": 10, "r": 40, "t": 40 if title else 10, "b": 40},
        hoverlabel={"bgcolor": "#262624", "font": {"color": INK_DARK}, "bordercolor": AXIS_DARK})
    return figure
