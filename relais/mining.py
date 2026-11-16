"""Process mining of the team's traces (pm4py): discovery, conformance with the reference model, variants, handover.

The reference model is discovered (inductive miner) from the expected paths of the question set: it is the
behaviour the team is designed for. Each run is then compared with it (alignment fitness) and with the expected
path of its own question (edit distance between the two sequences of agents).
"""
from collections import Counter

import numpy as np
import pandas as pd

AGENT_ORDER = ["planificateur", "sql", "statistiques", "visualisation", "relecteur"]


def as_pm4py(frame):
    import pm4py
    return pm4py.format_dataframe(frame.copy(), case_id="case:concept:name", activity_key="concept:name",
                                  timestamp_key="time:timestamp")


def reference_frame(questions):
    """The expected paths as an event log: one trace per question."""
    rows = []
    base = pd.Timestamp("2026-01-01", tz="UTC")
    for q in questions:
        for i, agent in enumerate(q.parcours_attendu):
            rows.append({"case:concept:name": q.id, "concept:name": agent,
                         "time:timestamp": base + pd.Timedelta(seconds=i)})
    return pd.DataFrame(rows)


def discover_net(frame):
    import pm4py
    return pm4py.discover_petri_net_inductive(as_pm4py(frame), noise_threshold=0.0)


def alignment_fitness(frame, net):
    """Fitness of every case against a Petri net (alignments): {case: fitness between 0 and 1}."""
    import pm4py
    log = as_pm4py(frame)
    net_, initial, final = net
    diagnostics = pm4py.conformance_diagnostics_alignments(log, net_, initial, final)
    cases = log.groupby("case:concept:name", sort=False)["concept:name"].agg(tuple)
    # pm4py returns one diagnostic per case, in the order of the cases of the log
    order = list(dict.fromkeys(log["case:concept:name"]))
    return {case: d["fitness"] for case, d in zip(order, diagnostics)} if len(order) == len(diagnostics) else \
        {case: np.nan for case in cases.index}


def edit_distance(a, b):
    """Levenshtein distance between two sequences of activities."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def sequences(frame):
    """{case: tuple of activities}."""
    return frame.sort_values(["case:concept:name", "time:timestamp"]).groupby("case:concept:name", sort=False)[
        "concept:name"].agg(tuple).to_dict()


def variants(frame):
    """The variants (distinct sequences) and how many cases follow each one, most frequent first."""
    counts = Counter(sequences(frame).values())
    return pd.DataFrame([{"variante": " > ".join(v), "cas": n, "longueur": len(v)} for v, n in counts.most_common()])


def entropy(items):
    """Shannon entropy (bits) of the distribution of the items (0 when they are all the same)."""
    counts = np.array(list(Counter(items).values()), dtype=float)
    p = counts / counts.sum()
    return float(-(p * np.log2(p)).sum()) if len(counts) > 1 else 0.0


def mean_pairwise_distance(seqs):
    seqs = list(seqs)
    pairs = [(a, b) for i, a in enumerate(seqs) for b in seqs[i + 1:]]
    return float(np.mean([edit_distance(a, b) for a, b in pairs])) if pairs else 0.0


def directly_follows(frame):
    """Counts of directly-follows pairs, with the artificial start and end of each case."""
    counts = Counter()
    for seq in sequences(frame).values():
        path = ["début", *seq, "fin"]
        counts.update(zip(path, path[1:]))
    return counts


def handover(frame):
    """Handover of work between agents: how often agent A hands over to a different agent B."""
    counts = Counter()
    for seq in sequences(frame).values():
        counts.update((a, b) for a, b in zip(seq, seq[1:]) if a != b)
    return pd.DataFrame([{"de": a, "vers": b, "passations": n} for (a, b), n in counts.most_common()])


# ------------------------------------------------------------------------------------------- figures

POSITIONS = {"début": (0.0, 0.8), "planificateur": (1.3, 0.8), "sql": (3.0, 0.95), "statistiques": (3.6, 0.55),
             "visualisation": (3.0, 0.15), "relecteur": (1.3, 0.15), "fin": (0.0, 0.15)}


def graph_figure(edges, title, highlight=None, reference=None):
    """A directed graph of the team (fixed layout), edge width by count. Edges absent from `reference` are drawn in
    the deviation colour; `highlight` is the agent to emphasise (live dashboard)."""
    import plotly.graph_objects as go
    from relais.tools.charts import INK, INK_2, MUTED, PALETTE, SURFACE

    figure = go.Figure()
    top = max(edges.values(), default=1)
    for (a, b), n in edges.items():
        if a not in POSITIONS or b not in POSITIONS:
            continue
        (x0, y0), (x1, y1) = POSITIONS[a], POSITIONS[b]
        if a == b:  # an agent that takes two turns in a row: a loop, written next to the node
            deviant = reference is not None and (a, b) not in reference
            figure.add_annotation(x=x0 + 0.22, y=y0 + 0.07, text=f"↻ {n}", showarrow=False,
                                  font={"size": 12, "color": PALETTE[1] if deviant else INK_2}, bgcolor=SURFACE)
            continue
        bend = 0.16 if (b, a) in edges else 0.0  # the perpendicular flips with the direction: A->B and B->A part
        # bend perpendicular to the edge, so that A -> B and B -> A do not overlap
        length = max(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1e-9)
        xm, ym = (x0 + x1) / 2 - bend * (y1 - y0) / length, (y0 + y1) / 2 + bend * (x1 - x0) / length
        deviant = reference is not None and (a, b) not in reference
        colour = PALETTE[1] if deviant else MUTED
        figure.add_trace(go.Scatter(x=[x0, xm, x1], y=[y0, ym, y1], mode="lines", hoverinfo="text",
                                    text=f"{a} → {b} : {n}", line={"width": 1 + 7 * n / top, "color": colour,
                                                                    "shape": "spline"}, showlegend=False))
        figure.add_annotation(x=x1, y=y1, ax=xm, ay=ym, xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                              arrowhead=2, arrowsize=1, arrowwidth=1.5, arrowcolor=colour, standoff=18, text="")
        figure.add_annotation(x=(xm * 2 + x0) / 3, y=(ym * 2 + y0) / 3, text=str(n), showarrow=False,
                              font={"size": 11, "color": INK_2}, bgcolor=SURFACE)
    names = list(POSITIONS)
    figure.add_trace(go.Scatter(
        x=[POSITIONS[n][0] for n in names], y=[POSITIONS[n][1] for n in names], mode="markers+text", text=names,
        textposition="top center", hoverinfo="text", showlegend=False, textfont={"color": INK, "size": 13},
        marker={"size": [34 if n == highlight else 22 for n in names],
                "color": [PALETTE[3] if n == highlight else (MUTED if n in ("début", "fin") else PALETTE[0])
                          for n in names], "line": {"width": 2, "color": SURFACE}}))
    figure.update_layout(title={"text": title, "x": 0, "font": {"size": 15, "color": INK}}, paper_bgcolor=SURFACE,
                         plot_bgcolor=SURFACE, xaxis={"visible": False, "range": [-0.3, 4.0]},
                         yaxis={"visible": False, "range": [-0.02, 1.08]}, margin={"l": 10, "r": 10, "t": 50, "b": 10},
                         height=380)
    return figure
