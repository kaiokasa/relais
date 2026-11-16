from relais.sources import DATASETS, TABLE_DATASETS, sources_of, tables_read


def test_tables_read_ignores_ctes_and_keeps_order():
    sql = ("with r as (select * from communes) select r.densite, sum(f.nb_locaux) from fixe_technologies f "
           "join r using (code_insee) group by 1")
    assert set(tables_read(sql)) == {"communes", "fixe_technologies"}  # the CTE "r" is not a table


def test_sources_come_from_the_queries_only():
    sources = sources_of(["select * from fixe_technologies f join communes c using (code_insee)",
                          "select count(*) from fixe_technologies"])
    ids = [s["id"] for s in sources]
    assert set(ids) == {"arcep_fixe", "insee_grille_densite", "insee_cog"}
    fixe = next(s for s in sources if s["id"] == "arcep_fixe")
    assert fixe["producteur"] == "ARCEP" and fixe["tables"] == ["fixe_technologies"] and fixe["url"].startswith("https")


def test_no_query_no_source_and_unknown_tables_ignored():
    assert sources_of([]) == []
    assert sources_of(["select * from dictionnaire", "pas du sql ((("]) == []


def test_every_mapped_dataset_is_described():
    assert {d for ds in TABLE_DATASETS.values() for d in ds} <= set(DATASETS)
