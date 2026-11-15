"""The data dictionary of the database: every table and every column, with its meaning.

It is the single source of truth: build_database.py writes it into the `dictionnaire` table and checks that every
column of the database is documented here (and the reverse), and the SQL agent reads it in its prompt.
Descriptions are in French, like the data.
"""

TABLES = {
    "communes": {
        "description": "Communes de France au 1er janvier 2026 (grille communale de densité INSEE 2026, "
                       "rattachement départemental et régional du Code officiel géographique 2026). Mayotte "
                       "incluse ; Saint-Barthélemy et Saint-Martin absents (hors grille INSEE).",
        "grain": "une ligne par commune",
        "cle": "code_insee",
        "colonnes": {
            "code_insee": "Code commune INSEE sur 5 caractères (01053, 2A004). Clé de jointure de toute la base",
            "nom": "Nom officiel de la commune (INSEE)",
            "dep_code": "Code du département (01, 2A, 971...)",
            "reg_code": "Code de la région",
            "population": "Population municipale 2023 (INSEE, nombre d'habitants)",
            "densite": "Niveau de densité en 3 classes : 1 urbain dense, 2 urbain intermédiaire, 3 rural",
            "libelle_densite": "Libellé du niveau de densité en 3 classes",
            "densite_7": "Niveau de densité en 7 classes (1 grands centres urbains ... 7 rural à habitat très "
                         "dispersé)",
            "libelle_densite_7": "Libellé du niveau de densité en 7 classes",
            "densite_aav": "Densité croisée avec les aires d'attraction des villes : 1 urbain dense, 2 urbain "
                           "intermédiaire, 3 rural périurbain, 4 rural non périurbain",
            "libelle_densite_aav": "Libellé de densite_aav",
            "part_pop_dense": "Part de la population vivant dans des mailles denses (en %, 0 à 100)",
            "part_pop_intermediaire": "Part de la population vivant dans des mailles de densité intermédiaire "
                                      "(en %)",
            "part_pop_rurale": "Part de la population vivant dans des mailles rurales (en %)",
        },
    },
    "departements": {
        "description": "Départements au 1er janvier 2026 (Code officiel géographique INSEE).",
        "grain": "une ligne par département",
        "cle": "dep_code",
        "colonnes": {
            "dep_code": "Code du département",
            "nom": "Nom du département",
            "reg_code": "Code de la région du département",
        },
    },
    "regions": {
        "description": "Régions au 1er janvier 2026 (Code officiel géographique INSEE).",
        "grain": "une ligne par région",
        "cle": "reg_code",
        "colonnes": {
            "reg_code": "Code de la région",
            "nom": "Nom de la région",
        },
    },
    "operateurs": {
        "description": "Opérateurs de réseau mobile présents dans les données de l'ARCEP. Aucune donnée de "
                       "prix, d'abonnés ou de satisfaction n'existe dans la base.",
        "grain": "un opérateur (code réseau MCC-MNC)",
        "cle": "operateur_code",
        "colonnes": {
            "operateur_code": "Code réseau mobile MCC-MNC (20801 Orange, 20810 SFR, 20815 Free Mobile, "
                              "20820 Bouygues Telecom ; 340xx et 647xx en outre-mer)",
            "nom": "Nom de l'opérateur",
            "territoire": "metropole ou outremer",
        },
    },
    "passage_communes": {
        "description": "Codes anciens (communes fusionnées depuis 2017) et codes d'arrondissements municipaux "
                       "(Paris, Lyon, Marseille), avec leur commune au 1er janvier 2026. Déjà appliqué à toutes "
                       "les tables : utile seulement pour l'audit.",
        "grain": "un ancien code commune ou un arrondissement municipal",
        "cle": "code_ancien",
        "colonnes": {
            "code_ancien": "Code commune avant fusion",
            "code_insee": "Code commune au 1er janvier 2026",
            "date_effet": "Date d'effet de la fusion (vide pour un arrondissement)",
        },
    },
    "fixe_technologies": {
        "description": "Éligibilité des locaux (logements et locaux professionnels) aux technologies d'accès "
                       "fixe, par commune et par trimestre (ARCEP, Ma connexion internet). Un local peut être "
                       "éligible à plusieurs technologies : les colonnes ne s'additionnent pas. Pour un taux, "
                       "diviser par nb_locaux, et pour un agrégat (département, région, France) sommer les "
                       "locaux avant de diviser.",
        "grain": "une ligne par commune et par trimestre (2019_T3 à 2026_T2, 23 trimestres)",
        "cle": "trimestre, code_insee",
        "colonnes": {
            "trimestre": "Trimestre au format AAAA_Tn (2026_T2)",
            "date_fin_trimestre": "Date de fin du trimestre",
            "code_insee": "Code commune INSEE (géographie 2026)",
            "nb_locaux": "Nombre de locaux de la commune",
            "locaux_eligibles_ftth": "Locaux éligibles à la fibre optique jusqu'à l'abonné (FttH)",
            "locaux_eligibles_cable": "Locaux éligibles au câble coaxial",
            "locaux_eligibles_cuivre": "Locaux éligibles au réseau cuivre (DSL)",
            "locaux_eligibles_thd_radio": "Locaux éligibles au très haut débit radio",
            "locaux_eligibles_4g_fixe": "Locaux éligibles à la 4G fixe",
            "locaux_eligibles_hd_radio": "Locaux éligibles au haut débit radio",
            "locaux_eligibles_satellite": "Locaux éligibles au satellite",
        },
    },
    "fixe_debits": {
        "description": "Locaux par débit maximal disponible, toutes technologies fixes confondues, par commune "
                       "et par trimestre (ARCEP). ATTENTION : classes CUMULATIVES (un local à 1 Gbit/s est "
                       "compté dans toutes les colonnes 'ou_plus'). Ne pas les additionner : pour des classes "
                       "exclusives, utiliser la vue fixe_debits_classes.",
        "grain": "une ligne par commune et par trimestre (2019_T3 à 2026_T2)",
        "cle": "trimestre, code_insee",
        "colonnes": {
            "trimestre": "Trimestre au format AAAA_Tn",
            "date_fin_trimestre": "Date de fin du trimestre",
            "code_insee": "Code commune INSEE (géographie 2026)",
            "nb_locaux": "Nombre de locaux de la commune",
            "locaux_sans_haut_debit": "Locaux sans accès au haut débit (moins de 512 kbit/s)",
            "locaux_512k_ou_plus": "Locaux avec au moins 512 kbit/s",
            "locaux_3m_ou_plus": "Locaux avec au moins 3 Mbit/s",
            "locaux_8m_ou_plus": "Locaux avec au moins 8 Mbit/s (bon haut débit)",
            "locaux_30m_ou_plus": "Locaux avec au moins 30 Mbit/s (très haut débit)",
            "locaux_100m_ou_plus": "Locaux avec au moins 100 Mbit/s",
            "locaux_1g_ou_plus": "Locaux avec au moins 1 Gbit/s",
        },
    },
    "fixe_debits_classes": {
        "description": "Vue : mêmes données que fixe_debits en classes EXCLUSIVES (chaque local dans une seule "
                       "classe, la somme des classes vaut nb_locaux).",
        "grain": "une ligne par commune et par trimestre",
        "cle": "trimestre, code_insee",
        "vue": True,
        "colonnes": {
            "trimestre": "Trimestre au format AAAA_Tn",
            "date_fin_trimestre": "Date de fin du trimestre",
            "code_insee": "Code commune INSEE (géographie 2026)",
            "nb_locaux": "Nombre de locaux de la commune",
            "locaux_moins_512k": "Locaux à moins de 512 kbit/s",
            "locaux_512k_3m": "Locaux entre 512 kbit/s et 3 Mbit/s",
            "locaux_3m_8m": "Locaux entre 3 et 8 Mbit/s",
            "locaux_8m_30m": "Locaux entre 8 et 30 Mbit/s",
            "locaux_30m_100m": "Locaux entre 30 et 100 Mbit/s",
            "locaux_100m_1g": "Locaux entre 100 Mbit/s et 1 Gbit/s",
            "locaux_1g_ou_plus": "Locaux à 1 Gbit/s ou plus",
        },
    },
    "mobile_sites": {
        "description": "Sites (antennes) des opérateurs mobiles, un relevé par an (T4) de 2018 à 2025 plus le "
                       "dernier trimestre (ARCEP, Mon réseau mobile). Un même pylône partagé apparaît une fois "
                       "par opérateur. Les colonnes absentes des anciens fichiers sont vides (5G avant 2020, "
                       "bandes 5G et coordonnées avant 2021).",
        "grain": "une ligne par site, par opérateur et par trimestre relevé",
        "cle": "site_id",
        "colonnes": {
            "site_id": "Identifiant technique de la ligne (les numéros de site de l'ARCEP ne sont pas "
                       "uniques : certains ont été abîmés par un tableur)",
            "trimestre": "Trimestre du relevé (AAAA_Tn)",
            "operateur_code": "Code de l'opérateur (voir table operateurs)",
            "num_site": "Numéro du site chez l'opérateur, tel que publié",
            "id_station_anfr": "Identifiant de la station à l'ANFR (vide avant 2021)",
            "code_insee": "Code commune INSEE (géographie 2026)",
            "territoire": "metropole ou outremer",
            "site_2g": "Le site émet en 2G",
            "site_3g": "Le site émet en 3G",
            "site_4g": "Le site émet en 4G",
            "site_5g": "Le site émet en 5G (vide avant 2020)",
            "mise_en_service_4g_trimestre": "4G mise en service pendant le trimestre (métropole)",
            "zone_blanche": "Site du programme zones blanches centres-bourgs (métropole)",
            "dispositif_couverture_ciblee": "Site du dispositif de couverture ciblée du New Deal mobile "
                                           "(métropole)",
            "site_strategique": "Site stratégique (métropole)",
            "capacite_240mbps": "Site offrant au moins 240 Mbit/s (métropole, depuis 2021)",
            "date_ouverture_5g": "Date d'ouverture commerciale de la 5G sur le site",
            "bande_5g_700mhz": "5G dans la bande 700 MHz",
            "bande_5g_800mhz": "5G dans la bande 800 MHz",
            "bande_5g_1800mhz": "5G dans la bande 1800 MHz",
            "bande_5g_2100mhz": "5G dans la bande 2100 MHz",
            "bande_5g_3500mhz": "5G dans la bande 3,5 GHz",
            "latitude": "Latitude WGS84 (vide avant 2021)",
            "longitude": "Longitude WGS84 (vide avant 2021)",
        },
    },
    "mobile_sites_5g_mensuel": {
        "description": "Nombre de sites 5G par opérateur, par mois, publié par l'ARCEP (déjà agrégé), au niveau "
                       "national, régional et départemental.",
        "grain": "une ligne par niveau géographique, zone, opérateur et mois",
        "cle": "niveau, code_geo, operateur, mois",
        "colonnes": {
            "niveau": "Niveau géographique : france, zone, region ou departement",
            "code_geo": "Code de la zone (FRNAT pour la France, code région ou département)",
            "nom_geo": "Nom de la zone",
            "operateur": "Nom de l'opérateur",
            "mois": "Dernier jour du mois",
            "sites_5g_700mhz": "Sites 5G dans la bande 700 MHz",
            "sites_5g_2100mhz": "Sites 5G dans la bande 2100 MHz",
            "sites_5g_3500mhz": "Sites 5G dans la bande 3,5 GHz",
            "sites_5g_total": "Sites 5G toutes bandes (un site à plusieurs bandes compte une fois)",
        },
    },
    "mobile_qos_indicateurs": {
        "description": "Indicateurs officiels de qualité de service mobile, campagnes annuelles de l'ARCEP "
                       "2019 à 2025 en métropole (déjà agrégés et pondérés par l'ARCEP), avec leur précision "
                       "statistique (demi-largeur de l'intervalle de confiance à 95 %). Comparer des "
                       "opérateurs : écart significatif seulement si les intervalles ne se recouvrent pas. "
                       "Réserves de l'ARCEP : en 2019 l'automatisation de l'évaluation de la voix crée une "
                       "rupture de série pour tous les opérateurs ; en 2023 les indicateurs de voix OTT "
                       "d'Orange ne sont pas représentatifs (décision ARCEP n°2025-1144-RDPI).",
        "grain": "une ligne par campagne, profil de terminal, strate, situation, service, indicateur et "
                 "opérateur",
        "cle": "annee, profil, strate, situation, service, indicateur, operateur",
        "colonnes": {
            "annee": "Année de la campagne",
            "profil": "Profil de terminal (2G/3G/4G ou 2G/3G/4G/5G)",
            "strate": "Zone ou axe mesuré (zones denses, intermédiaires, rurales, touristiques, transports...)",
            "situation": "Situation de mesure (Indoor, Outdoor, Tous usages, Incar, Intrain)",
            "service": "Service mesuré (DOWNLOAD, UPLOAD, WEB, VIDEO, VOIX, SMS...)",
            "indicateur": "Nom de l'indicateur, tel que publié par l'ARCEP",
            "operateur": "Nom de l'opérateur, ou 'Moyenne' pour la moyenne publiée des 4 opérateurs",
            "operateur_code": "Code de l'opérateur (vide pour la moyenne)",
            "valeur": "Valeur de l'indicateur (taux entre 0 et 1, débit en Mbit/s...)",
            "precision": "Précision statistique de la valeur",
            "volumetrie": "Nombre total de mesures de la ligne, tous opérateurs (vide si non publié)",
        },
    },
    "mobile_qos_mesures": {
        "description": "Mesures individuelles de la campagne officielle 2025 de l'ARCEP dans les lieux de vie "
                       "en métropole (zones denses, intermédiaires, rurales, touristiques), services data et "
                       "voix. Une ligne est UN test : pour un indicateur par zone, compter les tests réussis "
                       "sur les tests réalisés, pas la moyenne des moyennes communales.",
        "grain": "une ligne par test de mesure",
        "cle": "mesure_id",
        "colonnes": {
            "mesure_id": "Identifiant technique du test",
            "service": "data ou voix",
            "date_mesure": "Date du test",
            "heure_debut": "Heure de début du test",
            "code_insee": "Commune où le test a été réalisé",
            "operateur_code": "Code de l'opérateur testé",
            "protocole": "Type de test : WEB, DOWNLOAD, UPLOAD, STREAM, ICMP, ULH (data) ; VOIX, OTT (appel "
                         "par application de messagerie), SMS (voix)",
            "situation": "Indoor, Outdoor ou Incar",
            "zone": "Strate de la commune : Zones denses, intermediaires, rurales ou touristiques",
            "resultat": "Résultat du test (Success, Traffic Fail, TimeOut, Drop, Setup Fail...)",
            "debit_descendant_mbps": "Débit descendant mesuré en Mbit/s (tests DOWNLOAD)",
            "debit_montant_mbps": "Débit montant mesuré en Mbit/s (tests UPLOAD)",
            "page_web_moins_5s": "Page web chargée en moins de 5 secondes (tests WEB)",
            "page_web_moins_10s": "Page web chargée en moins de 10 secondes (tests WEB)",
            "video_qualite_parfaite": "Vidéo regardée en qualité parfaite (tests STREAM)",
            "duree_acces_s": "Durée d'accès au service en secondes",
            "communication_sans_perturbation": "Appel maintenu 2 minutes sans perturbation audible (indicateur "
                                               "CRSPA de l'ARCEP, tests VOIX et OTT)",
            "duree_communication_s": "Durée réelle de la communication en secondes (120 au maximum)",
            "mos_moyen": "Note moyenne de qualité audio (MOS) de l'appel",
            "sms_recu_moins_10s": "SMS reçu en moins de 10 secondes (tests SMS)",
            "latitude": "Latitude du point de mesure",
            "longitude": "Longitude du point de mesure",
        },
    },
    "mobile_pannes": {
        "description": "Pannes et maintenances de sites mobiles déclarées par les opérateurs (ARCEP, sites "
                       "indisponibles). Les fichiers quotidiens listent les pannes EN COURS : chaque panne est "
                       "dédoublonnée ici en un seul incident. Période observée : voir premier_jour_observe.",
        "grain": "une ligne par incident (opérateur, station, date de début)",
        "cle": "incident_id",
        "colonnes": {
            "incident_id": "Identifiant technique de l'incident",
            "operateur": "Nom de l'opérateur tel que publié",
            "operateur_code": "Code de l'opérateur (métropole)",
            "id_station_anfr": "Identifiant ANFR de la station touchée",
            "code_insee": "Commune de la station",
            "raison": "INT (incident) ou MAINT (maintenance programmée)",
            "debut": "Début de l'indisponibilité déclaré par l'opérateur",
            "fin_declaree": "Fin déclarée ou prévue (vide si non renseignée)",
            "premier_jour_observe": "Premier fichier quotidien où l'incident apparaît",
            "dernier_jour_observe": "Dernier fichier quotidien où l'incident apparaît",
            "nb_jours_observes": "Nombre de fichiers quotidiens où l'incident apparaît",
            "en_cours_au_dernier_releve": "L'incident figure dans le fichier le plus récent",
            "voix_coupee": "La voix a été coupée pendant l'incident (au moins un relevé)",
            "data_coupee": "La data a été coupée pendant l'incident (au moins un relevé)",
            "voix_2g_coupee": "Voix 2G coupée (au moins un relevé)",
            "voix_3g_coupee": "Voix 3G coupée (au moins un relevé)",
            "voix_4g_coupee": "Voix 4G coupée (au moins un relevé)",
            "data_3g_coupee": "Data 3G coupée (au moins un relevé)",
            "data_4g_coupee": "Data 4G coupée (au moins un relevé)",
            "data_5g_coupee": "Data 5G coupée (au moins un relevé)",
        },
    },
    "fibre_qualite_reseaux": {
        "description": "Indicateurs de qualité des réseaux FttH par opérateur d'infrastructure (ARCEP), sur "
                       "des périodes glissantes de 6 mois, pour l'ensemble du réseau ou par département. Taux "
                       "entre 0 et 1.",
        "grain": "une ligne par période, réseau et périmètre (réseau entier ou département)",
        "cle": "periode, reseau, perimetre, dep_code",
        "colonnes": {
            "periode": "Période telle que publiée (11/22 - 04/23)",
            "periode_debut": "Premier jour de la période",
            "periode_fin": "Dernier jour de la période",
            "reseau": "Opérateur d'infrastructure (nom du réseau)",
            "maison_mere": "Groupe auquel appartient le réseau",
            "perimetre": "reseau (ensemble du réseau) ou departement",
            "dep_code": "Code du département (vide pour l'ensemble du réseau)",
            "taux_pannes": "Taux de pannes sur le réseau",
            "taux_echecs_raccordement": "Taux d'échec des raccordements",
            "taux_echecs_raccordement_retraite": "Taux d'échec des raccordements, retraité",
            "taux_abonnes_avec_panne": "Part des abonnés ayant subi une panne",
        },
    },
    "fibre_qualite_groupes": {
        "description": "Historique mensuel des indicateurs de qualité FttH par groupe (maison mère), ARCEP.",
        "grain": "une ligne par mois et par groupe",
        "cle": "mois, maison_mere",
        "colonnes": {
            "mois": "Premier jour du mois",
            "maison_mere": "Groupe",
            "taux_echecs_raccordement": "Taux d'échec des raccordements",
            "taux_pannes": "Taux de pannes",
        },
    },
    "fibre_qualite_nationale": {
        "description": "Historique mensuel national des indicateurs de qualité FttH (ARCEP).",
        "grain": "une ligne par mois",
        "cle": "mois",
        "colonnes": {
            "mois": "Premier jour du mois",
            "taux_mise_en_service_60j": "Part des raccordements mis en service en moins de 60 jours",
            "taux_abonnes_avec_panne": "Part des abonnés ayant subi une panne",
        },
    },
    "sources": {
        "description": "Fichiers sources de la base : URL, licence, empreinte sha256, date de téléchargement.",
        "grain": "un fichier téléchargé",
        "cle": "fichier",
        "colonnes": {
            "fichier": "Chemin du fichier dans data/raw",
            "jeu_de_donnees": "Identifiant du jeu de données",
            "url": "URL de téléchargement",
            "licence": "Licence du jeu de données",
            "page_licence": "Page où la licence est indiquée",
            "sha256": "Empreinte du fichier",
            "octets": "Taille du fichier",
            "telecharge_le": "Date de téléchargement",
        },
    },
    "dictionnaire": {
        "description": "Ce dictionnaire : description de chaque table et de chaque colonne.",
        "grain": "une ligne par colonne (et une par table, colonne vide)",
        "cle": "table_nom, colonne",
        "colonnes": {
            "table_nom": "Nom de la table ou de la vue",
            "colonne": "Nom de la colonne (vide pour la ligne qui décrit la table)",
            "description": "Description",
        },
    },
}


def dictionary_rows():
    """(table, column, description) rows; the table itself has an empty column name."""
    rows = []
    for table, spec in TABLES.items():
        rows.append((table, "", f"{spec['description']} Granularité : {spec['grain']}. Clé : {spec['cle']}."))
        rows += [(table, column, text) for column, text in spec["colonnes"].items()]
    return rows
