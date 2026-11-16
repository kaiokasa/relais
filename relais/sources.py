"""The sources of an answer: the open datasets behind the tables its SQL queries actually read.

They are derived by code from the queries that ran, never written by the LLM: a source cannot be invented.
"""
import sqlglot
from sqlglot import exp

DATASETS = {
    "arcep_fixe": {"producteur": "ARCEP", "nom": "Ma connexion internet",
                   "url": "https://www.data.gouv.fr/datasets/ma-connexion-internet/",
                   "licence": "Licence Ouverte v2.0"},
    "arcep_sites": {"producteur": "ARCEP", "nom": "Mon réseau mobile : sites",
                    "url": "https://www.data.gouv.fr/datasets/mon-reseau-mobile",
                    "licence": "Licence Ouverte v1.0"},
    "arcep_qos": {"producteur": "ARCEP", "nom": "Mon réseau mobile : mesures de qualité de service",
                  "url": "https://www.data.gouv.fr/datasets/mon-reseau-mobile",
                  "licence": "Licence Ouverte v1.0"},
    "arcep_pannes": {"producteur": "ARCEP", "nom": "Sites indisponibles",
                     "url": "https://www.data.gouv.fr/datasets/sites-indisponibles",
                     "licence": "Licence Ouverte v1.0"},
    "arcep_fibre": {"producteur": "ARCEP", "nom": "Qualité des réseaux en fibre optique",
                    "url": "https://www.data.gouv.fr/datasets/qualite-des-reseaux-en-fibre-optique",
                    "licence": "Licence Ouverte v2.0"},
    "insee_grille_densite": {"producteur": "INSEE", "nom": "Grille communale de densité 2026",
                             "url": "https://www.insee.fr/fr/information/8571524",
                             "licence": "Licence Ouverte v2.0"},
    "insee_cog": {"producteur": "INSEE", "nom": "Code officiel géographique 2026",
                  "url": "https://www.data.gouv.fr/datasets/code-officiel-geographique-1",
                  "licence": "Licence Ouverte v1.0"},
    "insee_filosofi": {"producteur": "INSEE", "nom": "Filosofi 2023 (revenus et pauvreté)",
                       "url": "https://catalogue-donnees.insee.fr/fr/catalogue/recherche/DS_FILOSOFI_CC",
                       "licence": "Licence Ouverte v2.0"},
}

# table of the database -> datasets it is built from
TABLE_DATASETS = {
    "communes": ["insee_grille_densite", "insee_cog"],
    "departements": ["insee_cog"],
    "regions": ["insee_cog"],
    "passage_communes": ["insee_cog"],
    "operateurs": ["arcep_sites"],
    "fixe_technologies": ["arcep_fixe"],
    "fixe_debits": ["arcep_fixe"],
    "fixe_debits_classes": ["arcep_fixe"],
    "mobile_sites": ["arcep_sites"],
    "mobile_sites_5g_mensuel": ["arcep_sites"],
    "mobile_qos_indicateurs": ["arcep_qos"],
    "mobile_qos_mesures": ["arcep_qos"],
    "mobile_pannes": ["arcep_pannes"],
    "fibre_qualite_reseaux": ["arcep_fibre"],
    "fibre_qualite_groupes": ["arcep_fibre"],
    "fibre_qualite_nationale": ["arcep_fibre"],
    "revenus_communes": ["insee_filosofi"],
    "revenus_territoires": ["insee_filosofi"],
}


def tables_read(sql):
    """The database tables a query reads (names defined in the query itself, such as CTEs, are left out)."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return []
    defined = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    names = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name and name not in defined and name not in names:
            names.append(name)
    return names


def download_dates(cursor):
    """Date each dataset was downloaded, read in the `sources` table of the database."""
    try:
        rows = cursor.execute("select jeu_de_donnees, max(telecharge_le)::date from sources group by 1").fetchall()
    except Exception:
        return {}
    return {dataset: day for dataset, day in rows}


def sources_of(queries, cursor=None):
    """The datasets behind a list of SQL queries, in order of first use: [{id, producteur, nom, url, licence,
    tables, telecharge_le}]. Tables that come from no dataset (the dictionary, the list of sources) are left out;
    with a cursor on the database, the download date of each dataset is added."""
    found = {}
    for sql in queries:
        for table in tables_read(sql):
            for dataset in TABLE_DATASETS.get(table, []):
                entry = found.setdefault(dataset, {**DATASETS[dataset], "id": dataset, "tables": []})
                if table not in entry["tables"]:
                    entry["tables"].append(table)
    dates = download_dates(cursor) if cursor is not None and found else {}
    for entry in found.values():
        entry["telecharge_le"] = dates.get(entry["id"])
    return list(found.values())
