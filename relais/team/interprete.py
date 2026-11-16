"""The interpreter: the sixth agent, called only when the question contains a term whose meaning is ambiguous in the
data ("l'opérateur historique", "rural", "les zones blanches", "récemment"...).

Spelling variants ("Free" for "Free Mobile") are handled by code in the SQL tool (relais/tools/values.py), at no
cost. The interpreter deals with the meaning: it translates each ambiguous term into the exact columns and values of
the database, says which reading it chose, and the other agents follow it; the answer mentions it, so that the user
can correct it. Detection is done by code, so the interpreter costs nothing when the question is clear.
"""
import re

# ambiguous term (regular expression) -> what is ambiguous about it, and where the possible readings are
AMBIGUOUS_TERMS = {
    r"op[ée]rateurs? historiques?": "désigne un opérateur sans le nommer (operateurs.nom : Orange, l'ancien "
                                    "opérateur public)",
    r"\b(rural|rurale|rurales|ruraux|campagnes?)\b": "trois lectures possibles : la classe « Rural » de "
                                                     "communes.densite (3 classes), les niveaux 5 à 7 de densite_7, "
                                                     "ou « Rural périurbain / non périurbain » de densite_aav ; pour "
                                                     "le mobile, la strate « Zones rurales » des mesures ARCEP",
    r"zones? blanches?": "programme zones blanches centres-bourgs (mobile_sites.zone_blanche) ou dispositif de "
                         "couverture ciblée (mobile_sites.dispositif_couverture_ciblee)",
    r"\b(r[ée]cemment|derni[eè]rement|aujourd'hui|actuellement|en ce moment|l'an dernier|l'ann[ée]e derni[eè]re|"
    r"cette ann[ée]e)\b": "période relative : quel trimestre, quel mois ou quelle campagne (le plus récent : "
                          "2026_T2 pour le fixe et les sites, campagne 2025 pour la qualité de service)",
    r"\b(grandes? villes?|petites? (villes?|communes?)|villages?)\b": "taille de commune : seuil de population "
                                                                    "(communes.population) ou niveau de densite_7",
    r"\b(tr[eè]s )?haut d[ée]bit\b|\bbon d[ée]bit\b": "seuil de débit : haut débit (512 kbit/s ou 3 Mbit/s), bon "
                                                       "haut débit (8 Mbit/s), très haut débit (30 Mbit/s) dans "
                                                       "fixe_debits",
    r"\bcouverture\b": "nombre de sites (mobile_sites), qualité mesurée (mobile_qos_*) ou éligibilité fixe "
                       "(fixe_technologies) ; les cartes de couverture ne sont pas dans la base",
}


def ambiguous_terms(question):
    """The ambiguous terms found in a question: [(term as written, what is ambiguous)]."""
    found = []
    for pattern, explanation in AMBIGUOUS_TERMS.items():
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            found.append((match.group(0), explanation))
    return found


INTERPRETER_PROMPT = """Tu es l'interprète d'une équipe qui analyse les données de connectivité en France. Avant
que les autres agents travaillent, tu fixes le sens des termes ambigus de la question.

Question : « {question} »
Plan de l'équipe : {plan}

Termes ambigus repérés, et ce qui est ambigu :
{terms}

Pour chaque terme, choisis la lecture la plus probable compte tenu de la question et des données, et traduis-la en
colonnes et valeurs exactes de la base. Si la question précise déjà le sens, reprends simplement ce sens.

Réponds uniquement par un objet JSON :
{{"interpretations": [{{"terme": "...", "choix": "lecture retenue, en une phrase", "colonnes": "table.colonne = valeur, ..."}}]}}"""


def check_interpretation(decision):
    items = decision.get("interpretations")
    if not isinstance(items, list) or not items:
        raise ValueError("aucune interprétation")
    clean = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("terme", "")).strip() or not str(item.get("choix", "")).strip():
            raise ValueError(f"interprétation invalide : {item!r}")
        clean.append({"terme": str(item["terme"]).strip(), "choix": str(item["choix"]).strip(),
                      "colonnes": str(item.get("colonnes", "")).strip()})
    return clean


def as_text(interpretations):
    """The interpretations, as given to the other agents."""
    return "\n".join(f"- « {i['terme']} » : {i['choix']}" + (f" ({i['colonnes']})" if i["colonnes"] else "")
                     for i in interpretations)
