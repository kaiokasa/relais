# Relais

Observabilité et process mining d'une équipe d'agents LLM qui analyse la connectivité en France (fixe et mobile).

Une équipe multi-agent (planificateur, agent SQL, agent statistiques, agent visualisation, relecteur) répond à des
questions sur les données ouvertes de l'ARCEP et de l'INSEE. Chaque action des agents est journalisée avec
OpenTelemetry, convertie en journaux d'événements (XES, OCEL 2.0) et étudiée par process mining : variabilité d'une
exécution à l'autre, facteurs qui l'influencent, prédiction d'échec en cours de run.

Projet en construction. Étape actuelle : **jeu de questions** (étape 2 sur 11).

## Données

Toutes les données sont ouvertes, sous Licence Ouverte Etalab. La licence est relue sur data.gouv.fr au moment du
téléchargement (le script s'arrête si elle n'est pas ouverte) et notée, avec l'URL et l'empreinte sha256 de chaque
fichier, dans la table `sources` de la base.

| Source | Producteur | Licence | Utilisation |
|---|---|---|---|
| [Ma connexion internet](https://www.data.gouv.fr/datasets/ma-connexion-internet/) | ARCEP | Licence Ouverte v2.0 | Éligibilité des locaux par technologie et par débit, par commune, 23 trimestres (2019_T3 à 2026_T2) |
| [Mon réseau mobile](https://www.data.gouv.fr/datasets/mon-reseau-mobile) | ARCEP | Licence Ouverte v1.0 | Sites mobiles (un relevé par an depuis 2018 et le dernier trimestre), campagnes officielles de qualité de service (indicateurs 2019 à 2025, mesures individuelles 2025) |
| [Sites indisponibles](https://www.data.gouv.fr/datasets/sites-indisponibles) | ARCEP | Licence Ouverte v1.0 | Pannes et maintenances de sites mobiles, 12 derniers mois |
| [Qualité des réseaux en fibre optique](https://www.data.gouv.fr/datasets/qualite-des-reseaux-en-fibre-optique) | ARCEP | Licence Ouverte v2.0 | Taux de pannes et d'échecs de raccordement FttH |
| [Grille communale de densité 2026](https://www.insee.fr/fr/information/8571524) | INSEE | Licence Ouverte v2.0 ([conditions INSEE](https://www.insee.fr/fr/information/2008466)) | Communes, population municipale 2023, niveaux de densité |
| [Filosofi 2023](https://catalogue-donnees.insee.fr/fr/catalogue/recherche/DS_FILOSOFI_CC) | INSEE | Licence Ouverte v2.0 ([conditions INSEE](https://www.insee.fr/fr/information/2008466)) | Niveau de vie médian et taux de pauvreté par commune (avec le secret statistique), département, région |
| [Code officiel géographique 2026](https://www.data.gouv.fr/datasets/code-officiel-geographique-1) | INSEE | Licence Ouverte v1.0 | Départements, régions, fusions de communes |

Ces données ne contiennent ni prix d'abonnement, ni nombre d'abonnés, ni satisfaction client : les questions qui
en dépendent n'ont pas de réponse dans la base, et l'analyse reste factuelle entre opérateurs.

## Construire la base

```bash
pip install -r requirements.txt
python scripts/download_data.py      # ~730 Mo dans data/raw/ (ignoré par git)
python scripts/build_database.py     # data/connectivite.duckdb (~70 Mo), environ 2 minutes
```

Tout le nettoyage se fait une seule fois dans `build_database.py` ; les agents ne voient que des tables propres,
décrites colonne par colonne dans [`relais/schema.py`](relais/schema.py) (aussi copié dans la table
`dictionnaire`). La construction échoue si une colonne n'est pas documentée ou si une clé n'est pas unique.

Ce que le nettoyage a trouvé dans les fichiers publiés est détaillé dans
[`reports/controles_donnees.md`](reports/controles_donnees.md), notamment :

- les classes de débit sont exclusives en 2019_T3 puis cumulatives : toutes sont rendues cumulatives, et une vue
  `fixe_debits_classes` donne les classes exclusives ;
- les totaux nationaux recalculés depuis les communes sont égaux à la publication nationale de l'ARCEP dans les
  23 trimestres (le fichier national de 2020_T3 a lui-même une colonne incohérente) ;
- 943 codes de communes fusionnées depuis 2017 et les arrondissements de Paris, Lyon et Marseille sont ramenés à
  la géographie 2026 ;
- des numéros de sites mobiles ont été abîmés par un tableur (1,03E+05) : la table utilise une clé technique ;
- les pannes sont publiées en fichiers quotidiens des pannes en cours : elles sont fusionnées en incidents ;
- les colonnes imei et imsi des mesures sont vérifiées vides et ne sont jamais chargées ;
- Filosofi 2023 : le taux de pauvreté est couvert par le secret statistique dans 29 512 communes sur 34 768 ; chaque valeur porte son statut (diffusé, secret statistique, non disponible).

## Jeu de questions

[`benchmark/questions.yaml`](benchmark/questions.yaml) contient 50 questions, écrites avant les agents pour ne pas
être ajustées à leurs réponses : SQL seul (12), SQL + statistiques (8), SQL + visualisation (7), équipe complète
(6), plusieurs étapes (5) et 12 pièges (questions ambiguës, réponses absentes des données, questions causales
déguisées, comparaisons d'opérateurs qui doivent rester factuelles).

Chaque question porte :

- le statut attendu de la réponse (répondue, demande de clarification, ou impossible avec ces données) ;
- le **parcours attendu** dans l'équipe (par exemple planificateur, SQL, statistiques, relecteur), qui sert de
  modèle de référence pour le conformance checking ;
- un SQL de référence et, pour les questions statistiques, le calcul de référence (scipy) ;
- les vérifications automatiques de la réponse : chiffres attendus avec leur tolérance, mentions obligatoires
  (par exemple « corrélation » et « causalité » pour une question causale), formulations interdites (« meilleur
  opérateur »), type de graphique.

```bash
python benchmark/build_references.py   # calcule les réponses de référence -> benchmark/references.json
```

Les réponses de référence ne sont pas saisies à la main : elles sont calculées depuis la base et figées avec la
version des données utilisée.

## Tests

```bash
python -m pytest
```

Les tests n'utilisent ni LLM ni réseau ; ils tournent dans la CI GitHub Actions à chaque push.
