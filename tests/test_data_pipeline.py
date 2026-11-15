"""The cleaning and download helpers, on small hand-made inputs (no network, no raw files)."""
import datetime as dt

import pandas as pd
import pytest

from relais.schema import TABLES, dictionary_rows
from scripts import build_database as build
from scripts import download_data as download


# ---------------------------------------------------------------------------------------------- reading

def test_decode_utf8_latin1_and_utf8_with_a_few_broken_bytes():
    assert build.decode("Côte;Ain".encode("utf-8")) == "Côte;Ain"
    assert build.decode("Côte;Ain".encode("latin-1")) == "Côte;Ain"
    mostly_utf8 = ("Auvergne-Rhône-Alpes;" * 30).encode("utf-8") + b"\xa9"
    assert build.decode(mostly_utf8).startswith("Auvergne-Rhône-Alpes")


def test_read_csv_finds_the_header_after_title_lines_and_the_separator(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("Version beta : données en l'état;;\ncode_insee;nbr\n01053;12\n", encoding="utf-8")
    frame = build.read_csv(path, "code_insee")
    assert list(frame.columns) == ["code_insee", "nbr"] and frame.code_insee.iat[0] == "01053"
    path.write_text('code_op,latitude\n20801,"49,97"\n', encoding="utf-8")
    frame = build.read_csv(path, "code_op")
    assert build.to_float(frame.latitude).iat[0] == pytest.approx(49.97)


def test_conversions():
    assert build.to_float(pd.Series(["1,5", " 2.25 ", "NA", None])).tolist()[:2] == [1.5, 2.25]
    assert build.to_bool(pd.Series(["1", "0", "HS", "OK", None])).tolist()[:4] == [True, False, True, False]
    assert build.normalize_code(pd.Series(["1053", "01053", "2A004", " 75056 ", "1053.0"])).tolist() == \
        ["01053", "01053", "2A004", "75056", "01053"]


def test_quarter_end():
    assert build.quarter_end("2026_T2") == dt.date(2026, 6, 30)
    assert build.quarter_end("2024_T1") == dt.date(2024, 3, 31)


# ---------------------------------------------------------------------------------------------- speed classes

def speed_frame(inel, *classes):
    nbr = inel + (sum(classes) if len(classes) == 6 else 0)
    return pd.DataFrame([[nbr, inel, *classes]], columns=["nbr", "inel_hd"] + build.SPEED)


def test_exclusive_classes_are_detected_and_made_cumulative():
    # Baerendorf, 2019_T3 (exclusive): 10 premises at 8 Mbit/s, 138 at 1 Gbit/s
    frame = speed_frame(0, 0, 0, 10, 0, 0, 138)
    assert build.speed_classes_are_exclusive(frame)
    cumulative = build.make_cumulative(frame)
    assert cumulative[build.SPEED].iloc[0].tolist() == [148, 148, 148, 138, 138, 138]
    assert not build.speed_classes_are_exclusive(cumulative)


# ---------------------------------------------------------------------------------------------- outages

def test_outages_without_start_date_are_split_into_runs_of_consecutive_days():
    daily = pd.DataFrame({
        "operateur": ["Orange"] * 6 + ["SFR"],
        "station_anfr": ["A"] * 6 + ["A"],
        "debut": ["", "", "", "", "2026-01-01 10:00:00", "2026-01-01 10:00:00", ""],
        "jour": ["2026-01-01", "2026-01-02", "2026-01-04", "2026-01-10", "2026-01-01", "2026-01-02", "2026-01-02"],
    })
    episodes = build.outage_episodes(daily).tolist()
    # 01, 02 and 04 (gap of 2 days) are one outage; 10 is a new one; a dated outage keeps its start date;
    # another operator on the same station is another outage
    assert episodes[0] == episodes[1] == episodes[2] != episodes[3]
    assert episodes[4] == episodes[5] == "2026-01-01 10:00:00"
    assert episodes[6] != episodes[0]


def test_fibre_period_bounds():
    assert build.period_bounds("11/22 - 04/23") == (dt.date(2022, 11, 1), dt.date(2023, 4, 30))


# ---------------------------------------------------------------------------------------------- download

ARCEP_PAGE = """<a href="../">..</a><a href="https://www.arcep.fr">Arcep</a><a href="#top">top</a>
<a href="2019_T3/index.html">2019_T3</a><a href="2026_T2/index.html">2026_T2</a><a href="last/index.html">last</a>
<a href="Metropole/index.html">Metropole</a><a href="commune_techno_beta.csv">f</a><a href="commune_debit.csv">f</a>
<a href="commune_debit_filaire.csv">f</a>"""


def test_directory_listing_helpers():
    entries = [e.removesuffix("index.html") for e in download.links(ARCEP_PAGE)]
    assert "../" not in entries and not any(e.startswith(("http", "#")) for e in entries)
    assert download.quarters(entries) == ["2019_T3", "2026_T2"]
    assert download.fixe_files(entries) == {"techno": "commune_techno_beta.csv", "debit": "commune_debit.csv"}


def test_sites_selection_keeps_one_quarter_a_year_and_the_latest():
    assert download.sites_selection(["2024_T3", "2024_T4", "2025_T4", "2026_T1", "2026_T2"]) == \
        ["2024_T4", "2025_T4", "2026_T2"]


def test_outage_files_keep_recent_geojson_only():
    resources = [{"title": "2021-01-05.csv", "url": "u1"}, {"title": "2026-11-14.geojson", "url": "u2"},
                 {"title": "2025-01-01.geojson", "url": "u3"}, {"title": "documentation.pdf", "url": "u4"}]
    assert download.outage_files(resources, since="2025-11-15") == [("u2", "2026-11-14", "geojson")]
    assert [f[0] for f in download.outage_files(resources)] == ["u3", "u2"]


def test_download_stops_on_a_closed_licence():
    with pytest.raises(SystemExit):
        download.licence_of("arcep_fixe", {"license": "other-closed"})
    assert download.licence_of("arcep_fixe", {"license": "lov2"})[0].startswith("Licence Ouverte v2")


# ---------------------------------------------------------------------------------------------- dictionary

def test_every_table_is_fully_documented():
    for name, spec in TABLES.items():
        assert spec["description"] and spec["grain"] and spec["cle"], name
        assert all(text for text in spec["colonnes"].values()), name
        for key in spec["cle"].split(", "):
            assert key in spec["colonnes"], f"{name}: key {key} is not a column"
    rows = dictionary_rows()
    assert len(rows) == len(TABLES) + sum(len(s["colonnes"]) for s in TABLES.values())


def test_no_sensitive_column_is_documented():
    columns = {c for spec in TABLES.values() for c in spec["colonnes"]}
    assert not columns & {"imei", "imsi"}
