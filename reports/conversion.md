# Des traces OpenTelemetry aux journaux d'événements

Généré par `evaluation/analyze_experiment.py` à partir des 479 runs de l'expérience de variabilité
(8 044 spans OpenTelemetry). Journaux produits dans [`eventlogs/`](eventlogs/) : XES au niveau des agents
et au niveau détaillé, OCEL 2.0 (JSON), compressés.

## Ce qui se convertit bien

- **L'ordre et la durée des actions** : chaque span a un début et une fin ; XES garde la fin (`time:timestamp`) et
  le début (`start_timestamp`), OCEL l'horodatage de l'événement.
- **Le cas** : une trace OpenTelemetry (un run) devient une trace XES ; ses attributs de configuration (question,
  modèle, température, prompt, statut, justesse) deviennent des attributs de cas.
- **La ressource** : `gen_ai.agent.name` devient `org:resource`, ce qui donne directement le réseau de passation.
- **Les erreurs** : le statut du span (OK, ERROR) devient un attribut d'événement.

## Ce qui se perd ou se déforme

| Aspect | Traces | XES | OCEL 2.0 |
|---|---|---|---|
| Hiérarchie des spans (profondeur jusqu'à 2) | gardée (parent) | **perdue** : il faut choisir un niveau (2 635 événements « agents » ou 7 565 « détail ») | **perdue** comme hiérarchie, remplacée par des relations aux objets |
| Imbrication dans le temps | gardée | 3 123 événements du niveau détaillé chevauchent le précédent (un appel d'outil pendant le tour de son agent) : une suite ordonnée les présente comme successifs | idem |
| Attributs (26 clés distinctes) | typés, sur chaque span | gardés mais aplatis en chaînes (liste des problèmes du relecteur, texte SQL) | gardés sur les objets (texte de la requête, nombre de lignes du tableau) |
| Objets partagés (tableau lu par plusieurs agents) | implicites (identifiant en attribut) | **invisibles** : un événement n'appartient qu'à un cas | **explicites** : 25 130 relations événement-objet |
| Variabilité | - | 42 variantes au niveau agents, 281 au niveau détaillé | - |

Objets du journal OCEL : requete 678, tableau 630, run 479, code 269, graphique 180, question 20, agent 5.

## Aplatir l'OCEL : convergence et divergence

Revenir à un journal classique impose de choisir un type d'objet comme cas :

| Type d'objet choisi comme cas | Cas | Événements après aplatissement | Effet |
|---|---|---|---|
| question | 20 | 7 565 | les 479 runs d'une même question sont fusionnés en un seul cas : les répétitions s'entremêlent (divergence) |
| tableau | 630 | 1 308 | seuls les événements qui touchent un tableau restent (sur 7 565) ; un événement qui lit plusieurs tableaux est dupliqué (convergence) |
| agent | 5 | 7 565 | un cas par agent, qui mélange tous les runs : utile pour la charge d'un agent, pas pour un parcours |

**Conclusion.** Pour analyser les parcours de l'équipe (variantes, conformité), XES au niveau des agents avec un cas
par run suffit et c'est ce qu'utilise la suite. Pour suivre les artefacts (quelle requête a produit le tableau lu
par l'agent statistiques puis dessiné), seul l'OCEL garde l'information ; XES la perd et l'aplatissement de l'OCEL
la déforme dès qu'on choisit un autre objet que le run.
