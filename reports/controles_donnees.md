# Contrôles des données

Généré par `scripts/build_database.py` : ce que le nettoyage a trouvé et corrigé dans les fichiers publiés.

## Géographie (INSEE)

Table de passage : 943 anciens codes de communes fusionnées depuis 2017 et 45 arrondissements municipaux (Paris, Lyon, Marseille) ramenés à leur commune.
Communes : 34875 dans la grille de densité 2026, 0 sans rattachement au COG 2026 ; population municipale totale 68 350 798

## Fixe : Ma connexion internet (ARCEP)

23 trimestres de 2019_T3 à 2026_T2. Classes de débit exclusives en 2019_T3, cumulatives ensuite : toutes rendues cumulatives. Colonne elig_4fg (faute de frappe de 2020_T4 et 2021_T2) renommée elig_4gf.
Codes absents de la géographie 2026 après passage : ['97701', '97801'] (Saint-Barthélemy et Saint-Martin, au plus 0.09% des locaux d'un trimestre ; gardés dans les tables).
Contrôles débits : 0 lignes où une classe 'ou plus' dépasse la précédente ; 0 lignes où sans_haut_debit + 512k_ou_plus diffère de nb_locaux (écart maximal 0 locaux).
Contrôles technologies : 0 lignes où une technologie dépasse le nombre de locaux.
Contrôle externe : les totaux nationaux recalculés depuis les communes sont égaux à la publication nationale de l'ARCEP dans 23 trimestres sur 23. Fichier national incohérent publié en 2020_T3 : sa colonne inel_hd contient la somme des autres classes (qui sont exclusives) ; comparaison faite sur les autres colonnes.

## Mobile : sites (ARCEP)

2017_T4 metropole : ni code commune ni nom d'opérateur dans le fichier, non chargé.
942 346 lignes, 2018_T4 à 2026_T2 (81 128 puis 129 043 sites).
Numéros de site publiés non fiables : 1 371 abîmés en notation scientifique (1,03E+05) et 1 233 doublons (trimestre, opérateur, numéro) : la clé est un identifiant technique, site_id.
Sites : 1 087 lignes (0.12%) avec un code commune hors géographie 2026, gardées telles quelles : 97801 (339), 97701 (141), 00000 (41), 59540 (11), 49103 (10), 50602 (8) ....

## Mobile : qualité de service (ARCEP)

Indicateurs globaux : campagnes 2019 à 2025, 10 327 valeurs (2016 à 2018 non chargées : mise en page différente).
2025_mesures_data_habitations.csv : 354 282 mesures, colonnes imei et imsi vides (vérifié) et non chargées.
2025_mesures_voix_habitations.csv : 223 024 mesures, colonnes imei et imsi vides (vérifié) et non chargées.
Mesures individuelles : 577 306 tests ; 0 sans opérateur reconnu.
Mesures : 3 471 lignes (0.60%) avec un code commune hors géographie 2026, gardées telles quelles : 44701 (3 159), 92201 (312).

## Mobile : sites indisponibles (ARCEP)

364 fichiers quotidiens du 2025-11-15 au 2026-11-15, 512 545 lignes (environ 1408 pannes en cours par jour) fusionnées en 147 967 incidents (clé : opérateur, station ANFR, début).
68 658 lignes quotidiennes sans date de début (Orange 63 219, SFR 5 439) : regroupées en 13 567 incidents par suite de jours consécutifs (écart d'au plus 2 jours), début laissé vide.
262 incidents débutés plus d'un an avant leur premier relevé (pannes de longue durée encore déclarées en cours).
Pannes : tous les codes commune existent dans la géographie 2026.
Opérateurs : Orange 69101, SFR 28119, Free 27422, Bouygues Telecom 23325.

## Qualité des réseaux fibre (ARCEP)

Réseaux : 4221 lignes, 223 réseaux, périodes 11/22 - 04/23 à 03/2026 ; groupes : 561 lignes ; national : 36 mois. Fichier des malfaçons de raccordement non chargé (formats de période mélangés).

## Revenus : Filosofi (INSEE)

Filosofi 2023, niveau de vie médian : diffusé 30 794, secret statistique 3 960, non disponible 14 (sur 34 768 communes).
Filosofi 2023, taux de pauvreté : secret statistique 29 512, diffusé 5 242, non disponible 14 (sur 34 768 communes).
107 communes de 2026 absentes de Filosofi (départements d'outre-mer, îles de Sein et d'Ouessant).
Revenus : tous les codes commune existent dans la géographie 2026.
Territoires : 112 lignes ; France métropolitaine : niveau de vie médian 25 920 euros, taux de pauvreté 15.9 %.

## Base construite

| table | lignes | granularité |
| --- | --- | --- |
| communes | 34 875 | une ligne par commune |
| departements | 101 | une ligne par département |
| regions | 18 | une ligne par région |
| operateurs | 15 | un opérateur (code réseau MCC-MNC) |
| passage_communes | 988 | un ancien code commune ou un arrondissement municipal |
| fixe_technologies | 802 064 | une ligne par commune et par trimestre (2019_T3 à 2026_T2, 23 trimestres) |
| fixe_debits | 802 064 | une ligne par commune et par trimestre (2019_T3 à 2026_T2) |
| mobile_sites | 942 346 | une ligne par site, par opérateur et par trimestre relevé |
| mobile_sites_5g_mensuel | 13 346 | une ligne par niveau géographique, zone, opérateur et mois |
| mobile_qos_indicateurs | 10 327 | une ligne par campagne, profil de terminal, strate, situation, service, indicateur et opérateur |
| mobile_qos_mesures | 577 306 | une ligne par test de mesure |
| mobile_pannes | 147 967 | une ligne par incident (opérateur, station, date de début) |
| fibre_qualite_reseaux | 4 221 | une ligne par période, réseau et périmètre (réseau entier ou département) |
| fibre_qualite_groupes | 561 | une ligne par mois et par groupe |
| fibre_qualite_nationale | 36 | une ligne par mois |
| revenus_communes | 34 768 | une ligne par commune de France métropolitaine |
| revenus_territoires | 112 | une ligne par territoire |
| sources | 491 | un fichier téléchargé |
| dictionnaire | 204 | une ligne par colonne (et une par table, colonne vide) |

connectivite.duckdb : 72 Mo, construite en 99 s.
