"""Ask the team a question from the command line.

    python -m relais.ask "Quelle part des locaux est éligible à la fibre en 2026 ?"
    python -m relais.ask --trace "..."        # also prints the turns, the tool calls and their duration
"""
import argparse
import sys

from relais.llm import chat_model, model_name
from relais.team.graph import Team
from relais.tools.sql import open_locked_copy


def make_team(model=None, temperature=0.0, planner_prompt="standard"):
    return Team(chat_model(model, temperature), open_locked_copy(), planner_prompt=planner_prompt)


def print_answer(answer, trace=False):
    print(f"\n[{answer.statut}{'' if answer.approuve else ', non approuvée'}] {answer.reponse}\n")
    if answer.problemes:
        print("Problèmes relevés par le relecteur : " + " | ".join(answer.problemes))
    if answer.graphique:
        print(f"Graphique : {answer.graphique.type}, {answer.graphique.series} série(s)")
    tokens_in, tokens_out = answer.tokens
    cost = answer.cout_usd
    print(f"Parcours : {' > '.join(answer.path)}")
    print(f"{answer.latence_s:.1f} s, {tokens_in} tokens en entrée, {tokens_out} en sortie"
          + (f", {cost:.4f} $" if cost is not None else ""))
    if trace:
        for event in answer.events:
            depth = 0
            parent = event.parent
            while parent:
                depth += 1
                parent = next(e.parent for e in answer.events if e.id == parent)
            detail = {k: v for k, v in event.attributes.items() if k in ("requete", "erreur", "tableau", "lignes",
                                                                        "verdict", "problemes", "type")}
            print(f"{'  ' * depth}{event.agent}.{event.action} {event.duration_ms:.0f} ms"
                  f"{'' if event.ok else ' ECHEC'} {detail if detail else ''}"[:400])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--modele", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    team = make_team(args.modele, args.temperature)
    print(f"Modèle : {model_name(args.modele)}", file=sys.stderr)
    print_answer(team.ask(args.question), trace=args.trace)


if __name__ == "__main__":
    main()
