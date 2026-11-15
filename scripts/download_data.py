"""Download the raw open data used by Relais into data/raw/ (ignored by git).

Every file comes from an official source under the Licence Ouverte (Etalab). For the data.gouv.fr datasets the
licence is read from the data.gouv.fr API at download time, and the download stops if it is not an open licence.
Each file is recorded in data/raw/manifest.json: dataset, source URL, licence, sha256, size and date. The build
script turns this manifest into the `sources` table of the database.

Already downloaded files are kept (use --force to download them again).

    python scripts/download_data.py                     # everything (outages: the last 365 days)
    python scripts/download_data.py --pannes-completes  # outages since March 2021 (about 1.6 GB)
    python scripts/download_data.py --seulement arcep_fixe insee
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "manifest.json"
ARCEP = "https://data.arcep.fr"
DATAGOUV_API = "https://www.data.gouv.fr/api/1/datasets/"
HEADERS = {"User-Agent": "relais-data-downloader"}
OPEN_LICENCES = {"lov2": "Licence Ouverte v2.0 (Etalab)", "fr-lo": "Licence Ouverte v1.0 (Etalab)"}
INSEE_CONDITIONS = "https://www.insee.fr/fr/information/2008466"

# dataset id -> data.gouv.fr slug (licence checked through the API), None for the INSEE density grid (licence:
# INSEE reuse conditions, Licence Ouverte v2)
DATASETS = {
    "insee_grille_densite": None,
    "insee_cog": "code-officiel-geographique-1",
    "arcep_fixe": "ma-connexion-internet",
    "arcep_sites": "mon-reseau-mobile",
    "arcep_qos": "mon-reseau-mobile",
    "arcep_pannes": "sites-indisponibles",
    "arcep_fibre": "qualite-des-reseaux-en-fibre-optique",
}
GROUPS = {"insee": ["insee_grille_densite", "insee_cog"]}


# ---------------------------------------------------------------------------------------------- HTTP helpers

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def download(url, dest, retries=3):
    """Stream url to dest (through a .part file, so an interrupted download is never kept)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=300) as response, \
                    open(part, "wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            part.replace(dest)
            return
        except OSError:
            part.unlink(missing_ok=True)
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def links(html):
    """Relative links of an ARCEP directory page (parent, absolute and anchor links are left out)."""
    found = re.findall(r'href="([^"?#]+)"', html)
    return [link for link in found if not link.startswith(("../", "http://", "https://", "/"))]


def list_dir(url):
    """Files and sub-folders of an ARCEP directory page ('Metropole/index.html' becomes 'Metropole/')."""
    entries = []
    for link in links(fetch(url).decode("utf-8", errors="replace")):
        entry = link.removesuffix("index.html")
        if entry and entry not in entries:
            entries.append(entry)
    return entries


def quarters(entries):
    """Sorted quarter folders ('2026_T2/') of a directory listing."""
    return sorted(e.rstrip("/") for e in entries if re.fullmatch(r"\d{4}_T[1-4]/", e))


# ---------------------------------------------------------------------------------------------- the sources

def datagouv(slug):
    return json.loads(fetch(DATAGOUV_API + slug + "/"))


def insee_grille_densite():
    yield ("https://www.insee.fr/fr/statistiques/fichier/8571524/fichier_diffusion_2026.xlsx",
           "insee/grille_densite_2026.xlsx")


def insee_cog():
    base = "https://www.insee.fr/fr/statistiques/fichier/8740222/"
    for name in ("v_commune_2026", "v_departement_2026", "v_region_2026", "v_mvt_commune_2026"):
        yield base + name + ".csv", f"insee/{name}.csv"


def fixe_files(entries, level="commune"):
    """The technology and speed files of a quarter at one level ('_beta' names in 2019)."""
    wanted = {}
    for name in entries:
        kind = re.fullmatch(rf"{level}_(techno|debit)(_beta)?\.csv", name)
        if kind:
            wanted[kind.group(1)] = name
    return wanted


def arcep_fixe():
    """Commune files of every quarter, and the national files used to check the commune totals."""
    base = f"{ARCEP}/fixe/maconnexioninternet/statistiques/"
    for quarter in quarters(list_dir(base)):
        for level in ("commune", "national"):
            files = fixe_files(list_dir(f"{base}{quarter}/{level}/"), level)
            for kind, name in sorted(files.items()):
                yield f"{base}{quarter}/{level}/{name}", f"arcep_fixe/{quarter}/{level}_{kind}.csv"


def sites_selection(all_quarters):
    """One quarter a year (the T4) and the latest one."""
    chosen = [q for q in all_quarters if q.endswith("_T4")]
    if all_quarters and all_quarters[-1] not in chosen:
        chosen.append(all_quarters[-1])
    return chosen


def arcep_sites():
    base = f"{ARCEP}/mobile/sites/"
    all_quarters = quarters(list_dir(base))
    for quarter in sites_selection(all_quarters):
        entries = list_dir(f"{base}{quarter}/")
        # recent quarters: files at the root; older ones: a Metropole/ and an Outremer/ folder
        candidates = [(f"{base}{quarter}/", e) for e in entries if e.endswith(".csv")]
        for folder in ("Metropole/", "Outremer/"):
            if folder in entries:
                candidates += [(f"{base}{quarter}/{folder}", e) for e in list_dir(f"{base}{quarter}/{folder}")
                               if e.endswith(".csv")]
        for url_dir, name in candidates:
            zone = re.search(r"sites_(Metropole|Outremer)\.csv$", name)
            if zone:
                yield url_dir + name, f"arcep_sites/{quarter}/sites_{zone.group(1).lower()}.csv"
            elif "5G_historique_comptage" in name and quarter == all_quarters[-1]:
                yield url_dir + name, "arcep_sites/sites_5g_historique.csv"


def arcep_qos(year_of_measures=2025, first_year=2019):
    """Global indicators of every campaign since 2019 (the layout of the 2016-2018 files differs) and the
    individual measures of one campaign in places of living (data and voice)."""
    base = f"{ARCEP}/mobile/mesures_qualite_arcep/"
    years = sorted(e.rstrip("/") for e in list_dir(base)
                   if re.fullmatch(r"\d{4}/", e) and int(e[:4]) >= first_year)
    for year in years:
        folder = f"{base}{year}/Metropole/"
        try:
            entries = list_dir(folder)
        except OSError:
            continue  # no Metropole campaign that year (2026: only overseas so far)
        for name in entries:
            if name.endswith("indicateurs_globaux.xlsx"):
                yield folder + name, f"arcep_qos/{year}_indicateurs_globaux.xlsx"
            elif year == str(year_of_measures) and re.search(r"_(data|voix)_habitations\.csv$", name):
                service = re.search(r"_(data|voix)_habitations", name).group(1)
                yield folder + name, f"arcep_qos/{year}_mesures_{service}_habitations.csv"


def outage_files(resources, since=None):
    """(url, day, extension) of the daily GeoJSON outage files, from the data.gouv.fr resources, newer than
    `since`. The first files (October 2020 to March 2021) are CSV files with another layout: left out."""
    files = []
    for resource in resources:
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(geojson)", resource.get("title", ""))
        if match and (since is None or match.group(1) >= since):
            files.append((resource["url"], match.group(1), match.group(2)))
    return sorted(files, key=lambda f: f[1])


def arcep_pannes(complete=False, metadata=None):
    since = None if complete else (dt.date.today() - dt.timedelta(days=365)).isoformat()
    for url, day, extension in outage_files(metadata["resources"], since):
        yield url, f"arcep_pannes/{day}.{extension}"


def arcep_fibre(metadata=None):
    for resource in metadata["resources"]:
        if resource.get("format", "").lower() == "csv":
            name = resource["url"].rsplit("/", 1)[-1]
            yield resource["url"], f"arcep_fibre/{name}"


# ---------------------------------------------------------------------------------------------- main

def licence_of(dataset, metadata):
    """(licence, page where it is stated); stops the download if the licence is not an open one."""
    if DATASETS[dataset] is None:
        return OPEN_LICENCES["lov2"], INSEE_CONDITIONS
    code = metadata.get("license")
    if code not in OPEN_LICENCES:
        sys.exit(f"{dataset}: licence '{code}' is not an open licence, download stopped")
    return OPEN_LICENCES[code], metadata.get("page") or f"https://www.data.gouv.fr/datasets/{DATASETS[dataset]}/"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seulement", nargs="+", help="datasets to download (ids or 'insee')")
    parser.add_argument("--pannes-completes", action="store_true", help="outages since March 2021")
    parser.add_argument("--force", action="store_true", help="download again the files already present")
    args = parser.parse_args()

    chosen = list(DATASETS)
    if args.seulement:
        chosen = [d for name in args.seulement for d in GROUPS.get(name, [name])]
        unknown = set(chosen) - set(DATASETS)
        if unknown:
            sys.exit(f"Unknown datasets: {sorted(unknown)}. Known: {list(DATASETS)}")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    for dataset in chosen:
        slug = DATASETS[dataset]
        metadata = datagouv(slug) if slug else {}
        licence, licence_page = licence_of(dataset, metadata)
        print(f"== {dataset} ({licence})", flush=True)
        if dataset == "arcep_pannes":
            files = arcep_pannes(args.pannes_completes, metadata)
        elif dataset == "arcep_fibre":
            files = arcep_fibre(metadata)
        else:
            files = globals()[dataset]()
        count = 0
        for url, relpath in files:
            dest = RAW / relpath
            if args.force or not dest.exists():
                download(url, dest)
            entry = manifest.get(relpath)
            if args.force or not entry or entry.get("octets") != dest.stat().st_size:
                manifest[relpath] = {"dataset": dataset, "url": url, "licence": licence,
                                     "page_licence": licence_page, "sha256": sha256(dest),
                                     "octets": dest.stat().st_size, "telecharge_le": dt.date.today().isoformat()}
            count += 1
            if count % 50 == 0:
                print(f"   {count} files", flush=True)
                MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"   {count} files", flush=True)
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")

    total = sum(e["octets"] for e in manifest.values()) / 1e6
    print(f"Done: {len(manifest)} files, {total:.0f} MB in {RAW}")


if __name__ == "__main__":
    main()
