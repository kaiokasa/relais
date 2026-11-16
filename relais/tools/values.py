"""Value harmonisation for the SQL tool: the quiproquos between what an agent writes and what the data contains.

The values of every text column with few distinct values (operators, zones, situations, services...) are indexed
once. Then, in each query, the text literals compared to such a column (=, <>, IN) are:

- corrected when they are a known synonym ("Free" -> "Free Mobile", "Bouygues" -> "Bouygues Telecom",
  "intérieur" -> "Indoor") or differ only by case, accents or spaces ("zones rurales" -> "Zones rurales");
- reported when they match no value of the column, with the closest values, instead of silently returning nothing.

Every correction is reported back (the agent sees it, and it goes into the trace).
"""
import difflib
import re
import unicodedata

from sqlglot import exp

MAX_DISTINCT = 60
SKIPPED_COLUMNS = re.compile(r"^(code_|id_|num_|nom|libelle_geo|heure_|periode$|trimestre$|fichier$|url$|sha256$|"
                             r"description$|texte|consigne)")
# synonyms by column: what people write -> the value in the data (looked up after normalisation)
SYNONYMS = {
    "operateur": {"free": "Free Mobile", "free mobile": "Free Mobile", "iliad": "Free Mobile",
                  "bouygues": "Bouygues Telecom", "bytel": "Bouygues Telecom", "orange": "Orange", "sfr": "SFR"},
    "nom": {"free": "Free Mobile", "bouygues": "Bouygues Telecom"},
    "situation": {"interieur": "Indoor", "a l'interieur": "Indoor", "exterieur": "Outdoor",
                  "a l'exterieur": "Outdoor", "voiture": "Incar", "en voiture": "Incar", "in car": "Incar"},
    "zone": {"rural": "Zones rurales", "rurales": "Zones rurales", "zone rurale": "Zones rurales",
             "dense": "Zones denses", "denses": "Zones denses", "zone dense": "Zones denses",
             "intermediaire": "Zones intermediaires", "zone intermediaire": "Zones intermediaires",
             "touristique": "Zones touristiques", "zone touristique": "Zones touristiques"},
    "strate": {"rural": "Zones rurales", "zone rurale": "Zones rurales", "dense": "Zones denses",
               "zone dense": "Zones denses", "intermediaire": "Zones intermediaires"},
    "libelle_densite": {"rurale": "Rural", "dense": "Urbain dense", "urbain": "Urbain dense",
                        "intermediaire": "Urbain intermédiaire"},
    "raison": {"incident": "INT", "panne": "INT", "maintenance": "MAINT"},
}


def normalise(text):
    """Lower case, no accents, single spaces: 'Zones  Rurales ' -> 'zones rurales'."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().replace("_", " ").split())


class ValueIndex:
    """The values of the categorical text columns, by column name (all tables together)."""

    def __init__(self, values_by_column):
        self.values = {column.lower(): set(values) for column, values in values_by_column.items()}
        self.lookup = {}
        for column, values in self.values.items():
            table = {normalise(v): v for v in sorted(values)}
            for alias, target in SYNONYMS.get(column, {}).items():
                if target in values:
                    table.setdefault(normalise(alias), target)
            self.lookup[column] = table

    @classmethod
    def from_connection(cls, con, max_distinct=MAX_DISTINCT):
        rows = con.execute("SELECT table_name, column_name FROM information_schema.columns WHERE data_type = "
                           "'VARCHAR' AND table_name NOT IN ('sources', 'dictionnaire')").fetchall()
        values = {}
        for table, column in rows:
            if SKIPPED_COLUMNS.match(column) and column not in SYNONYMS:
                continue
            found = con.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL '
                                f"LIMIT {max_distinct + 1}").fetchall()
            if 0 < len(found) <= max_distinct:
                values.setdefault(column, set()).update(str(v[0]) for v in found)
        return cls(values)

    def resolve(self, column, value):
        """(value to use, 'exact' | 'corrected' | 'unknown') for a literal compared to a column."""
        column = column.lower()
        if column not in self.values or value in self.values[column]:
            return value, "exact"
        target = self.lookup[column].get(normalise(value))
        if target is not None:
            return target, "corrected"
        return value, "unknown"

    def closest(self, column, value, n=3):
        candidates = {normalise(v): v for v in self.values.get(column.lower(), ())}
        matches = difflib.get_close_matches(normalise(value), list(candidates), n=n, cutoff=0.4)
        if not matches:  # a value that contains what was written: "Free" in "Free Mobile"
            matches = [k for k in candidates if normalise(value) in k][:n]
        return [candidates[m] for m in matches]

    def harmonise(self, tree):
        """Correct the literals of a parsed query in place; returns (corrections, unknown values) as text."""
        corrections, unknown = [], []
        for literal, column in self._compared_literals(tree):
            value = literal.this
            target, status = self.resolve(column, value)
            if status == "corrected":
                literal.replace(exp.Literal.string(target))
                corrections.append(f"'{value}' remplacé par '{target}' (colonne {column})")
            elif status == "unknown":
                close = self.closest(column, value)
                unknown.append(f"la valeur '{value}' n'existe pas dans la colonne {column}"
                               + (f" ; valeurs proches : {', '.join(repr(c) for c in close)}" if close else ""))
        return corrections, unknown

    def _compared_literals(self, tree):
        found = []
        for node in tree.find_all(exp.EQ, exp.NEQ, exp.In):
            if isinstance(node, exp.In):
                column = node.this
                literals = [e for e in node.expressions if isinstance(e, exp.Literal) and e.is_string]
            else:
                left, right = node.left, node.right
                column, other = (left, right) if isinstance(left, exp.Column) else (right, left)
                literals = [other] if isinstance(other, exp.Literal) and other.is_string else []
            if isinstance(column, exp.Column):
                found += [(literal, column.name) for literal in literals]
        return found
