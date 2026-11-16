"""Build data/connectivite.duckdb from the raw files of data/raw/ (see download_data.py).

This is the ONLY place where the data is cleaned: encodings, leading zeros of commune codes, decimal commas,
merged communes mapped to the 2026 geography, sensitive columns left out (imei and imsi are checked empty and never
loaded), speed classes made cumulative in every quarter, daily outage files merged into incidents. The agents only
see the clean tables documented in relais/schema.py.

The checks made along the way are written to reports/controles_donnees.md.

    python scripts/build_database.py
"""
import calendar
import datetime as dt
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais.schema import TABLES, dictionary_rows  # noqa: E402

RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "connectivite.duckdb"
REPORT = ROOT / "reports" / "controles_donnees.md"

METRO_OPERATORS = {"orange": "20801", "sfr": "20810", "free": "20815", "free mobile": "20815",
                   "bouygues": "20820", "bouygues telecom": "20820"}
METRO_NAMES = {"20801": "Orange", "20810": "SFR", "20815": "Free Mobile", "20820": "Bouygues Telecom"}


class Report:
    """Collects the checks, written to reports/controles_donnees.md."""

    def __init__(self):
        self.lines = []

    def section(self, title):
        self.lines += ["", f"## {title}", ""]
        print(f"== {title}", flush=True)

    def add(self, text):
        self.lines.append(text)
        print("  " + text, flush=True)

    def table(self, frame):
        rows = [list(frame.columns), ["---"] * len(frame.columns)] + frame.astype(str).values.tolist()
        self.lines += ["| " + " | ".join(row) + " |" for row in rows] + [""]


def num(value):
    """1234567 -> '1 234 567' (French thousands separator)."""
    return f"{value:,}".replace(",", " ")


# ---------------------------------------------------------------------------------------------- readers

def decode(raw):
    """Text of a file: UTF-8 when it is (even with a few broken bytes, as in some ARCEP files), else Latin-1."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        as_latin1 = raw.decode("latin-1")
        # UTF-8 accents read as Latin-1 give "Ã" sequences: many of them means a UTF-8 file with a few bad bytes
        if as_latin1.count("Ã") > 20:
            return raw.decode("utf-8", errors="replace").lstrip("﻿")
        return as_latin1


def read_csv(path, header_marker):
    """A raw CSV as strings, whatever its encoding, separator or title lines: the header is the first line that
    contains `header_marker`."""
    text = decode(Path(path).read_bytes())
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines[:20]) if header_marker in line)
    header = lines[start]
    sep = ";" if header.count(";") >= header.count(",") else ","
    return pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=sep, dtype=str, keep_default_na=False,
                       na_values=["", "NA", "nan", "NaN"])


def to_float(series):
    return pd.to_numeric(series.astype("string").str.strip().str.replace(",", ".", regex=False),
                         errors="coerce")


def to_int(series):
    return to_float(series).round().astype("Int64")


def to_bool(series):
    values = series.astype("string").str.strip().str.lower()
    return values.map({"1": True, "0": False, "1.0": True, "0.0": False, "true": True, "false": False,
                       "hs": True, "ok": False}).astype("boolean")


def column(frame, name, convert=None):
    """A column of the frame (converted), or an empty one when the file does not have it."""
    if name not in frame:
        return pd.Series(pd.NA, index=frame.index, dtype="object" if convert is None else None) \
            if convert is None else convert(pd.Series(pd.NA, index=frame.index, dtype="string"))
    return frame[name] if convert is None else convert(frame[name])


def normalize_code(series):
    """Commune codes on 5 characters: 1053 -> 01053, '01053 ' -> 01053; 2A004 is kept."""
    codes = series.astype("string").str.strip().str.removesuffix(".0")
    return codes.where(~codes.str.fullmatch(r"\d{1,4}"), codes.str.zfill(5))


def quarter_end(quarter):
    year, q = int(quarter[:4]), int(quarter[-1])
    month = 3 * q
    return dt.date(year, month, calendar.monthrange(year, month)[1])


# ---------------------------------------------------------------------------------------------- geography

class Geography:
    """Communes of 2026 and the mapping of older codes (communes merged since 2017) to their 2026 code."""

    def __init__(self, report):
        self.report = report
        cog = pd.read_csv(RAW / "insee" / "v_commune_2026.csv", dtype=str)
        self.cog = cog[cog.TYPECOM == "COM"]
        self.codes = set(self.cog.COM)
        moves = pd.read_csv(RAW / "insee" / "v_mvt_commune_2026.csv", dtype=str)
        moves = moves[(moves.DATE_EFF >= "2017-01-01") & (moves.TYPECOM_AV == "COM") & (moves.TYPECOM_AP == "COM")
                      & (moves.COM_AV != moves.COM_AP) & ~moves.COM_AV.isin(self.codes)]
        targets = moves.groupby("COM_AV").agg(cibles=("COM_AP", lambda s: sorted(set(s))), date=("DATE_EFF", "max"))
        targets = targets[targets.cibles.map(len) == 1]  # a code split between several communes is kept as is
        direct = {old: row.cibles[0] for old, row in targets.iterrows()}

        def resolve(code, depth=0):
            nxt = direct.get(code)
            return code if nxt is None or depth > 20 else resolve(nxt, depth + 1)

        merged = {old: resolve(old) for old in direct}
        # municipal arrondissements of Paris, Lyon and Marseille -> their commune
        arrondissements = cog[cog.TYPECOM == "ARM"].set_index("COM").COMPARENT.to_dict()
        self.passage = {**merged, **arrondissements}
        self.passage_table = pd.DataFrame({
            "code_ancien": list(self.passage), "code_insee": list(self.passage.values()),
            "date_effet": pd.to_datetime([targets.date.get(o) for o in self.passage])})
        self.passage_table = self.passage_table[self.passage_table.code_insee.isin(self.codes)]
        report.add(f"Table de passage : {len(merged)} anciens codes de communes fusionnées depuis 2017 et "
                   f"{len(arrondissements)} arrondissements municipaux (Paris, Lyon, Marseille) ramenés à leur "
                   "commune.")

    def report_unknown(self, label, codes):
        """Reports the codes that match no 2026 commune (they are kept in the table)."""
        unknown = codes[~codes.isin(self.codes)].value_counts()
        if unknown.empty:
            self.report.add(f"{label} : tous les codes commune existent dans la géographie 2026.")
        else:
            self.report.add(f"{label} : {num(int(unknown.sum()))} lignes ({len(codes) and unknown.sum() / len(codes):.2%})"
                            f" avec un code commune hors géographie 2026, gardées telles quelles : "
                            + ", ".join(f"{c} ({num(int(n))})" for c, n in unknown.head(6).items())
                            + (" ..." if len(unknown) > 6 else "") + ".")

    def apply(self, codes):
        """2026 codes; the codes that still do not exist in 2026 are kept as they are."""
        codes = normalize_code(codes)
        return codes.map(lambda c: self.passage.get(c, c) if isinstance(c, str) else c).astype("string")


# ---------------------------------------------------------------------------------------------- tables

def build_communes(geo, report):
    grid = pd.read_excel(RAW / "insee" / "grille_densite_2026.xlsx", sheet_name="Maille communale", header=4,
                         dtype=str)
    attach = geo.cog.set_index("COM")[["DEP", "REG"]]
    communes = pd.DataFrame({
        "code_insee": grid.CODGEO.astype("string"),
        "nom": grid.LIBGEO.astype("string"),
        "dep_code": grid.CODGEO.map(attach.DEP).astype("string"),
        "reg_code": grid.CODGEO.map(attach.REG).astype("string"),
        "population": to_int(grid.PMUN23),
        "densite": to_int(grid.DENS),
        "libelle_densite": grid.LIBDENS.astype("string"),
        "densite_7": to_int(grid.DENS7),
        "libelle_densite_7": grid.LIBDENS7.astype("string"),
        "densite_aav": to_int(grid.DENS_AAV),
        "libelle_densite_aav": grid.LIBDENS_AAV.astype("string"),
        "part_pop_dense": to_float(grid.P1),
        "part_pop_intermediaire": to_float(grid.P2),
        "part_pop_rurale": to_float(grid.P3),
    })
    missing = communes.dep_code.isna().sum()
    report.add(f"Communes : {len(communes)} dans la grille de densité 2026, {missing} sans rattachement au COG "
               f"2026 ; population municipale totale {num(communes.population.sum())}")
    deps = pd.read_csv(RAW / "insee" / "v_departement_2026.csv", dtype=str)
    regs = pd.read_csv(RAW / "insee" / "v_region_2026.csv", dtype=str)
    departements = pd.DataFrame({"dep_code": deps.DEP, "nom": deps.LIBELLE, "reg_code": deps.REG}).astype("string")
    regions = pd.DataFrame({"reg_code": regs.REG, "nom": regs.LIBELLE}).astype("string")
    return communes, departements, regions


TECHNO = {"elig_ftth": "locaux_eligibles_ftth", "elig_coax": "locaux_eligibles_cable",
          "elig_cu": "locaux_eligibles_cuivre", "elig_thdr": "locaux_eligibles_thd_radio",
          "elig_4gf": "locaux_eligibles_4g_fixe", "elig_hdr": "locaux_eligibles_hd_radio",
          "elig_sat": "locaux_eligibles_satellite"}
SPEED = ["elig_hd05", "elig_hd3", "elig_bhd8", "elig_thd30", "elig_thd100", "elig_thd1g"]
SPEED_NAMES = {"inel_hd": "locaux_sans_haut_debit", "elig_hd05": "locaux_512k_ou_plus",
               "elig_hd3": "locaux_3m_ou_plus", "elig_bhd8": "locaux_8m_ou_plus", "elig_thd30": "locaux_30m_ou_plus",
               "elig_thd100": "locaux_100m_ou_plus", "elig_thd1g": "locaux_1g_ou_plus"}


def speed_classes_are_exclusive(frame):
    """True when the speed classes of a file are exclusive (they add up to the number of premises), False when
    they are cumulative (the published format since 2020_T3)."""
    total = frame[["inel_hd"] + SPEED].sum().sum()
    return abs(total / frame["nbr"].sum() - 1) < 0.01


def make_cumulative(frame):
    """Exclusive classes -> cumulative 'at least' classes (inel_hd does not change)."""
    out = frame.copy()
    for i, name in enumerate(SPEED):
        out[name] = frame[SPEED[i:]].sum(axis=1)
    return out


def build_fixe(geo, report):
    techno_frames, speed_frames, formats = [], [], []
    for folder in sorted((RAW / "arcep_fixe").iterdir()):
        quarter = folder.name
        techno = read_csv(folder / "commune_techno.csv", "code_insee").rename(columns={"elig_4fg": "elig_4gf"})
        speed = read_csv(folder / "commune_debit.csv", "code_insee")
        for frame, cols in ((techno, ["nbr"] + list(TECHNO)), (speed, ["nbr", "inel_hd"] + SPEED)):
            for col in cols:
                frame[col] = to_float(frame[col])
            frame["code_insee"] = geo.apply(frame["code_insee"])
            frame["trimestre"] = quarter
        exclusive = speed_classes_are_exclusive(speed)
        formats.append({"trimestre": quarter, "communes publiées": len(speed),
                        "classes de débit": "exclusives, converties" if exclusive else "cumulatives"})
        if exclusive:
            speed = make_cumulative(speed)
        techno_frames.append(techno[["trimestre", "code_insee", "nbr"] + list(TECHNO)])
        speed_frames.append(speed[["trimestre", "code_insee", "nbr", "inel_hd"] + SPEED])

    def finish(frames, names):
        data = pd.concat(frames).groupby(["trimestre", "code_insee"], as_index=False).sum(min_count=1)
        data.insert(1, "date_fin_trimestre", pd.to_datetime(data.trimestre.map(quarter_end)))
        data = data.rename(columns={"nbr": "nb_locaux", **names})
        for col in data.columns[3:]:
            data[col] = data[col].round().astype("Int64")
        return data

    technologies = finish(techno_frames, TECHNO)
    debits = finish(speed_frames, SPEED_NAMES)
    report.add(f"{len(formats)} trimestres de 2019_T3 à {formats[-1]['trimestre']}. Classes de débit exclusives "
               f"en {', '.join(f['trimestre'] for f in formats if f['classes de débit'].startswith('excl'))}, "
               "cumulatives ensuite : toutes rendues cumulatives. Colonne elig_4fg (faute de frappe de 2020_T4 et "
               "2021_T2) renommée elig_4gf.")
    unmatched = debits[~debits.code_insee.isin(geo.codes)]
    lost = unmatched.groupby("trimestre").nb_locaux.sum() / debits.groupby("trimestre").nb_locaux.sum()
    report.add(f"Codes absents de la géographie 2026 après passage : {sorted(unmatched.code_insee.unique())} "
               f"(Saint-Barthélemy et Saint-Martin, au plus {lost.max():.2%} des locaux d'un trimestre ; gardés "
               "dans les tables).")
    cumul = debits[["locaux_sans_haut_debit"] + list(SPEED_NAMES.values())[1:]]
    broken = ((cumul.iloc[:, 1:].diff(axis=1).iloc[:, 1:] > 0).any(axis=1)).sum()
    gap = (debits.locaux_sans_haut_debit + debits.locaux_512k_ou_plus - debits.nb_locaux).abs()
    report.add(f"Contrôles débits : {broken} lignes où une classe 'ou plus' dépasse la précédente ; "
               f"{(gap > 0).sum()} lignes où sans_haut_debit + 512k_ou_plus diffère de nb_locaux "
               f"(écart maximal {gap.max()} locaux).")
    over = (technologies[list(TECHNO.values())].gt(technologies.nb_locaux, axis=0)).any(axis=1).sum()
    report.add(f"Contrôles technologies : {over} lignes où une technologie dépasse le nombre de locaux.")
    check_national_totals(technologies, debits, report)
    return technologies, debits


def check_national_totals(technologies, debits, report):
    """The commune tables summed over France must give the national figures published by the ARCEP, in every
    quarter (this also checks the conversion of the exclusive speed classes of 2019)."""
    worst, inconsistent = [], []
    for folder in sorted((RAW / "arcep_fixe").iterdir()):
        quarter = folder.name
        published = {}
        for kind in ("techno", "debit"):
            frame = read_csv(folder / f"national_{kind}.csv", "nbr").rename(columns={"elig_4fg": "elig_4gf"})
            cols = ["nbr"] + (list(TECHNO) if kind == "techno" else ["inel_hd"] + SPEED)
            frame = frame[cols].apply(to_float)
            if kind == "debit":
                coherent = abs(frame.inel_hd.sum() + frame.elig_hd05.sum() - frame.nbr.sum()) < 1
                if speed_classes_are_exclusive(frame) or not coherent:
                    frame = make_cumulative(frame)
                if abs(frame.inel_hd.sum() + frame.elig_hd05.sum() - frame.nbr.sum()) >= 1:
                    # published national file whose inel_hd column does not hold the premises without high speed
                    inconsistent.append(quarter)
                    cols.remove("inel_hd")
            published.update({TECHNO.get(c) or SPEED_NAMES.get(c) or "nb_locaux": frame[c].sum() for c in cols})
        ours = {**technologies[technologies.trimestre == quarter].iloc[:, 3:].sum().to_dict(),
                **debits[debits.trimestre == quarter].iloc[:, 3:].sum().to_dict()}
        gaps = {col: abs(int(ours[col]) - int(value)) for col, value in published.items()}
        column_name, gap = max(gaps.items(), key=lambda item: item[1])
        worst.append((quarter, gap, column_name))
    largest = max(worst, key=lambda w: w[1])
    exact = sum(1 for _, gap, _ in worst if gap == 0)
    report.add(f"Contrôle externe : les totaux nationaux recalculés depuis les communes sont égaux à la publication "
               f"nationale de l'ARCEP dans {exact} trimestres sur {len(worst)}"
               + ("." if exact == len(worst) else
                  f" ; plus grand écart : {num(largest[1])} locaux ({largest[2]}, {largest[0]}).")
               + (f" Fichier national incohérent publié en {', '.join(inconsistent)} : sa colonne inel_hd contient "
                  "la somme des autres classes (qui sont exclusives) ; comparaison faite sur les autres colonnes."
                  if inconsistent else ""))


SITE_FLAGS = {"site_2g": "site_2g", "site_3g": "site_3g", "site_4g": "site_4g", "site_5g": "site_5g",
              "mes_4g_trim": "mise_en_service_4g_trimestre", "site_zb": "zone_blanche",
              "site_dcc": "dispositif_couverture_ciblee", "site_strategique": "site_strategique",
              "site_capa_240mbps": "capacite_240mbps", "site_5g_700_m_hz": "bande_5g_700mhz",
              "site_5g_800_m_hz": "bande_5g_800mhz", "site_5g_1800_m_hz": "bande_5g_1800mhz",
              "site_5g_2100_m_hz": "bande_5g_2100mhz", "site_5g_3500_m_hz": "bande_5g_3500mhz"}


def build_sites(geo, report):
    frames, names = [], {}
    for path in sorted((RAW / "arcep_sites").glob("*/sites_*.csv")):
        quarter, territory = path.parent.name, path.stem.removeprefix("sites_")
        raw = read_csv(path, "code_op" if quarter != "2017_T4" else "Operateur")
        raw.columns = [c.lower() for c in raw.columns]
        if "insee_com" not in raw:
            report.add(f"{quarter} {territory} : ni code commune ni nom d'opérateur dans le fichier, non chargé.")
            continue
        frame = pd.DataFrame({
            "trimestre": quarter,
            "operateur_code": raw.code_op.astype("string").str.strip(),
            "num_site": raw.num_site.astype("string"),
            "id_station_anfr": column(raw, "id_station_anfr").astype("string"),
            "code_insee": geo.apply(raw.insee_com),
            "territoire": "metropole" if territory == "metropole" else "outremer",
        })
        for source, name in SITE_FLAGS.items():
            frame[name] = column(raw, source, to_bool)
        frame["date_ouverture_5g"] = pd.to_datetime(column(raw, "date_ouverturecommerciale_5g").astype("string")
                                                    .str[:10], errors="coerce")
        frame["latitude"] = column(raw, "latitude", to_float)
        frame["longitude"] = column(raw, "longitude", to_float)
        frames.append(frame)
        for code, name in raw.groupby("code_op").nom_op.agg(lambda s: s.mode().iat[0]).items():
            names[code.strip()] = (name.strip(), "metropole" if territory == "metropole" else "outremer")
    sites = pd.concat(frames, ignore_index=True)
    sites.insert(0, "site_id", pd.RangeIndex(1, len(sites) + 1))
    order = ["site_id", "trimestre", "operateur_code", "num_site", "id_station_anfr", "code_insee", "territoire",
             "site_2g", "site_3g", "site_4g", "site_5g"] + list(SITE_FLAGS.values())[4:9] + \
        ["date_ouverture_5g"] + list(SITE_FLAGS.values())[9:] + ["latitude", "longitude"]
    sites = sites[order]
    scientific = sites.num_site.str.contains("E+", regex=False, na=False).sum()
    dup = sites.duplicated(["trimestre", "operateur_code", "num_site"]).sum()
    counts = sites.groupby("trimestre").size()
    report.add(f"{num(len(sites))} lignes, {counts.index[0]} à {counts.index[-1]} ({num(counts.iloc[0])} puis "
               f"{num(counts.iloc[-1])} sites).")
    report.add(f"Numéros de site publiés non fiables : {num(int(scientific))} abîmés en notation scientifique "
               f"(1,03E+05) et {num(int(dup))} doublons (trimestre, opérateur, numéro) : la clé est un identifiant "
               "technique, site_id.")
    geo.report_unknown("Sites", sites.code_insee)
    operators = pd.DataFrame([(code, METRO_NAMES.get(code, name), terr) for code, (name, terr) in sorted(names.items())],
                             columns=["operateur_code", "nom", "territoire"]).astype("string")
    return sites, operators


def build_sites_5g():
    raw = read_csv(RAW / "arcep_sites" / "sites_5g_historique.csv", "NIVGEO")
    level = {"FRNAT": "france", "ZONE": "zone", "REG": "region", "DEP": "departement"}
    return pd.DataFrame({
        "niveau": raw.NIVGEO.map(level).astype("string"),
        "code_geo": raw.CODGEO.astype("string"),
        "nom_geo": raw.LIBGEO.astype("string"),
        "operateur": raw.nom_operateur.astype("string"),
        "mois": pd.to_datetime(raw.date),
        "sites_5g_700mhz": to_int(raw.nb_sites5G_freq_700_MHz),
        "sites_5g_2100mhz": to_int(raw.nb_sites5G_freq_2100_MHz),
        "sites_5g_3500mhz": to_int(raw.nb_sites5G_freq_3500_MHz),
        "sites_5g_total": to_int(raw.nb_sites5G_total),
    })


def clean_label(series):
    return series.astype("string").str.replace(r"\s+", " ", regex=True).str.strip()


def unify_case(series):
    """Labels that differ only by their case ('Download' and 'DOWNLOAD') get the most frequent spelling."""
    key = series.str.casefold()
    preferred = series.groupby(key).agg(lambda s: s.value_counts().index[0])
    return key.map(preferred).astype("string")


def build_qos_indicators(report):
    frames = []
    operators = ["Bouygues Telecom", "Free Mobile", "Orange", "SFR"]
    for path in sorted((RAW / "arcep_qos").glob("*_indicateurs_globaux.xlsx")):
        year = int(path.name[:4])
        book = pd.ExcelFile(path)
        sheet = next(s for s in book.sheet_names if s.startswith("Indicateurs globaux"))
        raw = pd.read_excel(book, sheet, header=None, dtype=str)
        header = [str(v).strip() for v in raw.iloc[0]]
        assert header[5:13:2] == operators and header[13] == "Volumétrie totale" and header[14] == "Moyenne", \
            f"{path.name}: unexpected columns {header}"
        body = raw.iloc[1:].dropna(subset=[0, 4])
        keys = {"annee": year, "profil": clean_label(body[0]), "strate": clean_label(body[1]),
                "situation": clean_label(body[2]), "service": clean_label(body[3]),
                "indicateur": clean_label(body[4])}
        for i, name in enumerate(operators + ["Moyenne"]):
            col = 5 + 2 * i if name != "Moyenne" else 14
            frames.append(pd.DataFrame({**keys, "operateur": name,
                                        "operateur_code": METRO_OPERATORS.get(name.lower()),
                                        "valeur": to_float(body[col]), "precision": to_float(body[col + 1]),
                                        "volumetrie": to_int(body[13])}))
    data = pd.concat(frames, ignore_index=True).dropna(subset=["valeur"])
    data["operateur_code"] = data.operateur_code.astype("string")
    data["service"] = data.service.str.upper()
    for col in ("strate", "situation", "indicateur"):
        data[col] = unify_case(data[col])
    report.add(f"Indicateurs globaux : campagnes {data.annee.min()} à {data.annee.max()}, {num(len(data))} valeurs "
               "(2016 à 2018 non chargées : mise en page différente).")
    return data


def build_qos_measures(geo, report):
    frames = []
    for path in sorted((RAW / "arcep_qos").glob("*_mesures_*_habitations.csv")):
        service = "data" if "_data_" in path.name else "voix"
        raw = read_csv(path, "insee_com")
        leaked = raw[["imei", "imsi"]].notna().sum().sum()
        if leaked:
            sys.exit(f"{path.name}: {leaked} imei/imsi values are filled in, build stopped")
        frames.append(pd.DataFrame({
            "service": service,
            "date_mesure": pd.to_datetime(raw.date_start, format="%d/%m/%Y", errors="coerce"),
            "heure_debut": raw.hour_start.astype("string"),
            "code_insee": geo.apply(raw.insee_com),
            "operateur_code": raw.operator.str.strip().str.lower().map(METRO_OPERATORS).astype("string"),
            "protocole": raw.protocole.str.upper().astype("string"),
            "situation": raw.situation.str.strip().str.capitalize().astype("string"),
            "zone": raw.zone.str.strip().str.capitalize().astype("string"),
            "resultat": column(raw, "Result").astype("string"),
            "debit_descendant_mbps": column(raw, "bitrate_dl", to_float),
            "debit_montant_mbps": column(raw, "bitrate_ul", to_float),
            "page_web_moins_5s": column(raw, "loaded_in_less_5_secondes", to_bool),
            "page_web_moins_10s": column(raw, "loaded_in_less_10_secondes", to_bool),
            "video_qualite_parfaite": column(raw, "quality_perfect", to_bool),
            "duree_acces_s": column(raw, "acess_duration", to_float),
            "communication_sans_perturbation": column(raw, "crspa", to_bool),
            "duree_communication_s": column(raw, "real_communiation_time", to_float),
            "mos_moyen": column(raw, "mos_average", to_float),
            "sms_recu_moins_10s": column(raw, "sms_success", to_bool),
            "latitude": column(raw, "latitude_start", to_float),
            "longitude": column(raw, "longitude_start", to_float),
        }))
        report.add(f"{path.name} : {num(len(raw))} mesures, colonnes imei et imsi vides (vérifié) et non chargées.")
    data = pd.concat(frames, ignore_index=True)
    data.insert(0, "mesure_id", pd.RangeIndex(1, len(data) + 1))
    report.add(f"Mesures individuelles : {num(len(data))} tests ; {int(data.operateur_code.isna().sum())} sans "
               "opérateur reconnu.")
    geo.report_unknown("Mesures", data.code_insee)
    return data


OUTAGE_FLAGS = {"voix": "voix_coupee", "data": "data_coupee", "voix2g": "voix_2g_coupee",
                "voix3g": "voix_3g_coupee", "voix4g": "voix_4g_coupee", "data3g": "data_3g_coupee",
                "data4g": "data_4g_coupee", "data5g": "data_5g_coupee"}


MAX_GAP_DAYS = 2  # a daily file can be missing: a gap of 2 days does not end an outage without start date


def outage_episodes(daily):
    """The incident each daily row belongs to. With a start date, the start date identifies the incident. Without
    one (some operators do not always publish it), consecutive days of the same station form one incident."""
    episode = daily.debut.copy()
    missing = daily.debut.eq("")
    if missing.any():
        rows = daily[missing].sort_values(["operateur", "station_anfr", "jour"])
        day = pd.to_datetime(rows.jour)
        same_station = rows.operateur.eq(rows.operateur.shift()) & rows.station_anfr.eq(rows.station_anfr.shift())
        new = ~same_station | (day - day.shift()).dt.days.gt(MAX_GAP_DAYS)
        first_day = rows.jour.where(new).ffill()
        episode.loc[rows.index] = "sans début, vu le " + first_day
    return episode


def build_outages(geo, report):
    rows = []
    files = sorted((RAW / "arcep_pannes").glob("*.geojson"))
    for path in files:
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        for feature in features:
            props = feature["properties"]
            rows.append({"jour": path.stem, **{k: props.get(k) for k in
                         ["operateur", "code_insee", "station_anfr", "raison", "debut", "fin", *OUTAGE_FLAGS]}})
    daily = pd.DataFrame(rows)
    for col in ("operateur", "station_anfr", "debut"):
        daily[col] = daily[col].astype("string").str.strip().fillna("")
    for source in OUTAGE_FLAGS:
        daily[source] = to_bool(daily[source])
    daily = daily.sort_values("jour")
    daily["episode"] = outage_episodes(daily)
    keys = ["operateur", "station_anfr", "episode"]
    grouped = daily.groupby(keys, sort=False)
    incidents = grouped.agg(debut=("debut", "first"), code_insee=("code_insee", "last"), raison=("raison", "last"),
                            fin_declaree=("fin", lambda s: s.dropna().iat[-1] if s.notna().any() else None),
                            premier_jour_observe=("jour", "first"), dernier_jour_observe=("jour", "last"),
                            nb_jours_observes=("jour", "nunique"),
                            **{name: (source, "max") for source, name in OUTAGE_FLAGS.items()}).reset_index()
    last_day = daily.jour.max()
    incidents = pd.DataFrame({
        "incident_id": pd.RangeIndex(1, len(incidents) + 1),
        # the outage files write "Free" where every other table writes "Free Mobile": one name per operator
        "operateur": incidents.operateur.str.lower().map(METRO_OPERATORS).map(METRO_NAMES)
                     .fillna(incidents.operateur).astype("string"),
        "operateur_code": incidents.operateur.str.lower().map(METRO_OPERATORS).astype("string"),
        "id_station_anfr": incidents.station_anfr.astype("string"),
        "code_insee": geo.apply(incidents.code_insee),
        "raison": incidents.raison.astype("string"),
        "debut": pd.to_datetime(incidents.debut, errors="coerce"),
        "fin_declaree": pd.to_datetime(incidents.fin_declaree, errors="coerce", format="mixed"),
        "premier_jour_observe": pd.to_datetime(incidents.premier_jour_observe),
        "dernier_jour_observe": pd.to_datetime(incidents.dernier_jour_observe),
        "nb_jours_observes": incidents.nb_jours_observes.astype("Int64"),
        "en_cours_au_dernier_releve": (incidents.dernier_jour_observe == last_day).astype("boolean"),
        **{name: incidents[name].astype("boolean") for name in OUTAGE_FLAGS.values()},
    })
    old = (incidents.debut < incidents.premier_jour_observe - pd.Timedelta(days=365)).sum()
    no_start = daily.debut.eq("")
    report.add(f"{len(files)} fichiers quotidiens du {files[0].stem} au {files[-1].stem}, {num(len(daily))} lignes "
               f"(environ {len(daily) // len(files)} pannes en cours par jour) fusionnées en {num(len(incidents))} "
               "incidents (clé : opérateur, station ANFR, début).")
    report.add(f"{num(int(no_start.sum()))} lignes quotidiennes sans date de début "
               f"({', '.join(f'{k} {num(int(v))}' for k, v in daily[no_start].operateur.value_counts().items())}) : "
               f"regroupées en {num(int(incidents.debut.isna().sum()))} incidents par suite de jours consécutifs "
               f"(écart d'au plus {MAX_GAP_DAYS} jours), début laissé vide.")
    report.add(f"{old} incidents débutés plus d'un an avant leur premier relevé (pannes de longue durée encore "
               "déclarées en cours).")
    geo.report_unknown("Pannes", incidents.code_insee)
    report.add(f"Opérateurs : {', '.join(f'{k} {v}' for k, v in incidents.operateur.value_counts().items())}.")
    return incidents


def period_bounds(period):
    """'11/22 - 04/23' -> (2022-11-01, 2023-04-30)."""
    start, end = [p.strip() for p in period.split("-")]
    (m1, y1), (m2, y2) = [map(int, p.split("/")) for p in (start, end)]
    y1, y2 = 2000 + y1, 2000 + y2
    return dt.date(y1, m1, 1), dt.date(y2, m2, calendar.monthrange(y2, m2)[1])


def build_fibre(report):
    folder = RAW / "arcep_fibre"
    raw = read_csv(next(folder.glob("ftth-qualite-reseau-*.csv")), "periode")
    bounds = raw.periode.map(period_bounds)
    networks = pd.DataFrame({
        "periode": raw.periode.astype("string"),
        "periode_debut": pd.to_datetime(bounds.str[0]),
        "periode_fin": pd.to_datetime(bounds.str[1]),
        "reseau": raw.OI.astype("string"),
        "maison_mere": raw.maison_mere.astype("string"),
        "perimetre": raw.perimetre.astype("string"),
        "dep_code": raw.dep_code.where(raw.dep_code != "00").astype("string"),
        "taux_pannes": to_float(raw.taux_pannes),
        "taux_echecs_raccordement": to_float(raw.taux_echecs_raccordement),
        "taux_echecs_raccordement_retraite": to_float(raw.taux_echecs_raccordement_retraite),
        "taux_abonnes_avec_panne": to_float(raw.taux_abonnes_avec_panne),
    })
    raw = read_csv(next(folder.glob("ftth-qualite-historique-mm-*.csv")), "maison_mere")
    groups = pd.DataFrame({"mois": pd.to_datetime(raw.date + "-01"), "maison_mere": raw.maison_mere.astype("string"),
                           "taux_echecs_raccordement": to_float(raw.taux_echecs_raccordement),
                           "taux_pannes": to_float(raw.taux_pannes)})
    raw = read_csv(next(folder.glob("ftth-qualite-historique-national-*.csv")), "date")
    national = pd.DataFrame({"mois": pd.to_datetime(raw.date + "-01"),
                             "taux_mise_en_service_60j": to_float(raw.taux_mes_60j),
                             "taux_abonnes_avec_panne": to_float(raw.taux_abonnes_avec_panne)})
    report.add(f"Réseaux : {len(networks)} lignes, {networks.reseau.nunique()} réseaux, périodes "
               f"{networks.periode.iloc[0]} à {networks.periode_fin.max():%m/%Y} ; groupes : {len(groups)} lignes ; "
               f"national : {len(national)} mois. Fichier des malfaçons de raccordement non chargé (formats de "
               "période mélangés).")
    return networks, groups, national


FILOSOFI_MEASURES = {"MED_SL": "niveau_vie_median", "PR_MD60": "taux_pauvrete"}


def value_status(rows):
    """'diffusé', 'secret statistique' or 'non disponible' for each Filosofi value."""
    status = pd.Series("non disponible", index=rows.index, dtype="string")
    status[rows.CONF_STATUS.eq("C")] = "secret statistique"
    status[rows.OBS_VALUE.notna()] = "diffusé"
    return status


def build_revenus(geo, report):
    with zipfile.ZipFile(RAW / "insee" / "filosofi_2023.zip") as archive:
        name = next(n for n in archive.namelist() if n.endswith("_data.csv"))
        raw = pd.read_csv(io.BytesIO(archive.read(name)), sep=";", dtype=str)
    raw = raw[raw.FILOSOFI_MEASURE.isin(FILOSOFI_MEASURES)]
    year = int(raw.TIME_PERIOD.iat[0])

    communes = raw[raw.GEO_OBJECT == "COM"].copy()
    communes["code_insee"] = geo.apply(communes.GEO)
    communes["status"] = value_status(communes)
    wide = communes.pivot_table(index="code_insee", columns="FILOSOFI_MEASURE", values=["OBS_VALUE", "status"],
                                aggfunc="first")
    revenus = pd.DataFrame({
        "code_insee": wide.index.astype("string"),
        "annee": year,
        "niveau_vie_median": to_float(wide[("OBS_VALUE", "MED_SL")]).values,
        "statut_niveau_vie": wide[("status", "MED_SL")].astype("string").values,
        "taux_pauvrete": to_float(wide[("OBS_VALUE", "PR_MD60")]).values,
        "statut_taux_pauvrete": wide[("status", "PR_MD60")].astype("string").values,
    })
    for column_name, label in (("statut_niveau_vie", "niveau de vie médian"), ("statut_taux_pauvrete",
                                                                              "taux de pauvreté")):
        counts = revenus[column_name].value_counts()
        report.add(f"Filosofi {year}, {label} : " + ", ".join(f"{k} {num(int(v))}" for k, v in counts.items())
                   + f" (sur {num(len(revenus))} communes).")
    missing = len(geo.codes - set(revenus.code_insee))
    report.add(f"{missing} communes de 2026 absentes de Filosofi (départements d'outre-mer, îles de Sein et "
               "d'Ouessant).")
    geo.report_unknown("Revenus", revenus.code_insee)

    levels = {"FRANCE": "france_metropolitaine", "REG": "region", "DEP": "departement"}
    territories = raw[raw.GEO_OBJECT.isin(levels)]
    wide = territories.pivot_table(index=["GEO_OBJECT", "GEO"], columns="FILOSOFI_MEASURE", values="OBS_VALUE",
                                   aggfunc="first").reset_index()
    territoires = pd.DataFrame({
        "niveau": wide.GEO_OBJECT.map(levels).astype("string"),
        "code_geo": wide.GEO.astype("string"),
        "annee": year,
        "niveau_vie_median": to_float(wide.MED_SL),
        "taux_pauvrete": to_float(wide.PR_MD60),
    }).sort_values(["niveau", "code_geo"], ignore_index=True)
    national = territoires[territoires.niveau == "france_metropolitaine"].iloc[0]
    report.add(f"Territoires : {len(territoires)} lignes ; France métropolitaine : niveau de vie médian "
               f"{num(int(national.niveau_vie_median))} euros, taux de pauvreté {national.taux_pauvrete} %.")
    return revenus, territoires


def build_sources():
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    rows = [{"fichier": path, "jeu_de_donnees": e["dataset"], "url": e["url"], "licence": e["licence"],
             "page_licence": e["page_licence"], "sha256": e["sha256"], "octets": e["octets"],
             "telecharge_le": e["telecharge_le"]} for path, e in sorted(manifest.items()) if (RAW / path).exists()]
    sources = pd.DataFrame(rows)
    sources["octets"] = sources.octets.astype("Int64")
    sources["telecharge_le"] = pd.to_datetime(sources.telecharge_le)
    return sources.astype({c: "string" for c in sources.columns if sources[c].dtype == object})


SPEED_CLASSES_VIEW = """
CREATE VIEW fixe_debits_classes AS
SELECT trimestre, date_fin_trimestre, code_insee, nb_locaux,
       locaux_sans_haut_debit                         AS locaux_moins_512k,
       locaux_512k_ou_plus - locaux_3m_ou_plus        AS locaux_512k_3m,
       locaux_3m_ou_plus - locaux_8m_ou_plus          AS locaux_3m_8m,
       locaux_8m_ou_plus - locaux_30m_ou_plus         AS locaux_8m_30m,
       locaux_30m_ou_plus - locaux_100m_ou_plus       AS locaux_30m_100m,
       locaux_100m_ou_plus - locaux_1g_ou_plus        AS locaux_100m_1g,
       locaux_1g_ou_plus
FROM fixe_debits
"""


def check_dictionary(con):
    """Every table, view and column of the database is documented in relais/schema.py, and the reverse; the key
    documented for each table is unique (two empty values count as equal)."""
    found = {}
    for table, col in con.execute("SELECT table_name, column_name FROM information_schema.columns "
                                  "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position").fetchall():
        found.setdefault(table, []).append(col)
    errors = [f"{t}: table not documented" for t in found if t not in TABLES]
    errors += [f"{t}: documented table missing" for t in TABLES if t not in found]
    for table, cols in found.items():
        if table in TABLES:
            documented = list(TABLES[table]["colonnes"])
            if cols != documented:
                errors.append(f"{table}: columns {cols} != documented {documented}")
            key = TABLES[table]["cle"]
            duplicates = con.execute(f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL "
                                     "HAVING count(*) > 1)").fetchone()[0]
            if duplicates:
                errors.append(f"{table}: {duplicates} duplicated values of the key ({key})")
    if errors:
        sys.exit("Data dictionary out of date:\n  " + "\n  ".join(errors))


def main():
    started = time.time()
    report = Report()
    report.lines = ["# Contrôles des données", "",
                    "Généré par `scripts/build_database.py` : ce que le nettoyage a trouvé et corrigé dans les "
                    "fichiers publiés."]
    report.section("Géographie (INSEE)")
    geo = Geography(report)
    communes, departements, regions = build_communes(geo, report)
    report.section("Fixe : Ma connexion internet (ARCEP)")
    technologies, debits = build_fixe(geo, report)
    report.section("Mobile : sites (ARCEP)")
    sites, operateurs = build_sites(geo, report)
    sites_5g = build_sites_5g()
    report.section("Mobile : qualité de service (ARCEP)")
    qos_indicateurs = build_qos_indicators(report)
    qos_mesures = build_qos_measures(geo, report)
    report.section("Mobile : sites indisponibles (ARCEP)")
    pannes = build_outages(geo, report)
    report.section("Qualité des réseaux fibre (ARCEP)")
    fibre_reseaux, fibre_groupes, fibre_nationale = build_fibre(report)
    report.section("Revenus : Filosofi (INSEE)")
    revenus, revenus_territoires = build_revenus(geo, report)
    sources = build_sources()
    dictionnaire = pd.DataFrame(dictionary_rows(), columns=["table_nom", "colonne", "description"]).astype("string")

    tables = {"communes": communes, "departements": departements, "regions": regions, "operateurs": operateurs,
              "passage_communes": geo.passage_table.astype({"code_ancien": "string", "code_insee": "string"}),
              "fixe_technologies": technologies, "fixe_debits": debits, "mobile_sites": sites,
              "mobile_sites_5g_mensuel": sites_5g, "mobile_qos_indicateurs": qos_indicateurs,
              "mobile_qos_mesures": qos_mesures, "mobile_pannes": pannes, "fibre_qualite_reseaux": fibre_reseaux,
              "fibre_qualite_groupes": fibre_groupes, "fibre_qualite_nationale": fibre_nationale,
              "revenus_communes": revenus, "revenus_territoires": revenus_territoires,
              "sources": sources, "dictionnaire": dictionnaire}
    tmp = DB_PATH.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    for name, frame in tables.items():
        con.register("frame", frame)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
        con.unregister("frame")
    con.execute(SPEED_CLASSES_VIEW)
    check_dictionary(con)

    report.section("Base construite")
    sizes = pd.DataFrame([(name, num(len(frame)), TABLES[name]["grain"])
                          for name, frame in tables.items()], columns=["table", "lignes", "granularité"])
    report.table(sizes)
    con.close()
    DB_PATH.unlink(missing_ok=True)
    tmp.replace(DB_PATH)
    report.add(f"{DB_PATH.name} : {DB_PATH.stat().st_size / 1e6:.0f} Mo, construite en {time.time() - started:.0f} s.")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(report.lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
