"""The figures of the observability console, for the dark and the light (beige) interface: the span waterfall, the
risk gauge and its curve.

Each mode has its own steps of the same validated hues (not an automatic inversion). Kept apart from Streamlit so
that they are tested without a browser.
"""
import time

import plotly.graph_objects as go

from relais.tools.charts import PALETTE, PALETTE_DARK

THEMES = {
    "dark": {"surface": "#262624", "ink": "#f5f4ef", "ink_2": "#c2c0b6", "muted": "#9b9890", "grid": "#3a3936",
             "axis": "#4a4945", "hover": "#30302e", "series": PALETTE_DARK, "failure": "#e66767"},
    "light": {"surface": "#f4efe6", "ink": "#1f1e1d", "ink_2": "#4a4740", "muted": "#68635a", "grid": "#e2d9c8",
              "axis": "#cfc4b0", "hover": "#fbf8f2", "series": PALETTE, "failure": "#c43c3b"},
}
# one fixed slot per agent in the categorical palette: colour follows the agent, never its rank
AGENT_SLOTS = {"planificateur": 0, "interprete": 4, "sql": 1, "statistiques": 2, "visualisation": 3, "relecteur": 6}
ACTION_LABELS = {"planifier": "planifie", "rediger": "rédige", "etape": "travaille", "relire": "relit",
                 "interpreter": "interprète", "appel_llm": "appel au modèle", "executer_sql": "requête SQL",
                 "executer_python": "code Python", "creer_graphique": "graphique"}
ROW_HEIGHT = 22


def theme(mode):
    return THEMES["light" if mode == "light" else "dark"]


def agent_colours(mode="dark"):
    series = theme(mode)["series"]
    return {agent: series[slot] for agent, slot in AGENT_SLOTS.items()}


AGENT_COLOURS = agent_colours("dark")
FAILURE = THEMES["dark"]["failure"]


def _depths(events):
    parents = {e.id: e.parent for e in events}
    depth = {}
    for e in events:
        d, p = 0, e.parent
        while p is not None:
            d, p = d + 1, parents.get(p)
        depth[e.id] = d
    return depth


def waterfall_figure(events, running=(), started_at=None, now=None, hide_llm_calls=False, max_rows=45,
                     mode="dark"):
    """The span waterfall: one bar per event (start, duration), nested under its agent, coloured by agent; failed
    events in the failure colour and marked; running events drawn up to now, lighter."""
    t = theme(mode)
    colours_by_agent = agent_colours(mode)
    now = now or time.time()
    all_events = sorted(list(events) + list(running), key=lambda e: (e.start, e.id))
    if hide_llm_calls:
        all_events = [e for e in all_events if e.action != "appel_llm"]
    all_events = all_events[-max_rows:]
    figure = go.Figure()
    if not all_events:
        return _layout(figure, 120, "Cascade des spans", t)
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
        colours.append(t["failure"] if failed else colours_by_agent.get(e.agent, t["muted"]))
        outlines.append(t["failure"] if failed else t["surface"])
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
        text=texts, textposition="outside", textfont={"color": t["ink_2"], "size": 11}, cliponaxis=False,
        hovertext=hovers, hoverinfo="text", showlegend=False))
    figure.update_yaxes(autorange="reversed", showticklabels=False, showgrid=False)
    figure.update_xaxes(title_text="secondes depuis le début du run", ticksuffix=" s", gridcolor=t["grid"],
                        zeroline=False, tickfont={"color": t["muted"]}, domain=[0.27, 1.0])
    for label, (name, top) in zip(labels, names):
        figure.add_annotation(x=0, xref="paper", y=label, yref="y", text=f"<b>{name}</b>" if top else name,
                              showarrow=False, xanchor="left",
                              font={"size": 12 if top else 11, "color": t["ink"] if top else t["muted"]})
    return _layout(figure, 70 + ROW_HEIGHT * len(all_events), "Cascade des spans", t)


def risk_gauge(risk, mode="dark"):
    t = theme(mode)
    value = None if risk is None else round(100 * risk)
    series = t["series"]
    colour = series[2] if value is None or value < 30 else (series[3] if value < 60 else t["failure"])
    figure = go.Figure(go.Indicator(
        mode="gauge+number", value=value, number={"suffix": " %", "font": {"size": 30, "color": t["ink"]}},
        gauge={"axis": {"range": [0, 100], "tickcolor": t["muted"], "tickfont": {"color": t["muted"], "size": 10}},
               "bar": {"color": colour, "thickness": 0.6}, "bgcolor": t["grid"], "borderwidth": 0,
               "steps": [{"range": [0, 100], "color": t["grid"]}]}))
    return _layout(figure, 170, None, t, margin={"l": 25, "r": 25, "t": 10, "b": 5})


def risk_curve(risks, mode="dark"):
    t = theme(mode)
    line = t["series"][0]
    rgb = tuple(int(line[i:i + 2], 16) for i in (1, 3, 5))
    figure = go.Figure(go.Scatter(x=[k for k, _ in risks], y=[100 * r for _, r in risks], mode="lines",
                                  line={"color": line, "width": 2}, fill="tozeroy",
                                  fillcolor=f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.15)",
                                  hovertemplate="%{y:.0f} %<extra></extra>"))
    figure.update_yaxes(range=[0, 100], ticksuffix=" %", gridcolor=t["grid"], tickfont={"color": t["muted"]})
    figure.update_xaxes(title_text="événements terminés", gridcolor=t["grid"], tickfont={"color": t["muted"]})
    return _layout(figure, 150, None, t, margin={"l": 45, "r": 10, "t": 5, "b": 35})


def _layout(figure, height, title, t, margin=None):
    figure.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", bargap=0.25,
        font={"family": "Inter, Segoe UI, sans-serif", "color": t["ink_2"], "size": 12},
        title={"text": title or "", "font": {"size": 14, "color": t["ink"]}, "x": 0, "xanchor": "left",
               "xref": "paper"},
        margin=margin or {"l": 10, "r": 40, "t": 40 if title else 10, "b": 40},
        hoverlabel={"bgcolor": t["hover"], "font": {"color": t["ink"]}, "bordercolor": t["axis"]})
    return figure
