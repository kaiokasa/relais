"""The SQL tool of the SQL agent: one read-only SELECT at a time on an in-memory copy of the database.

Two independent locks:
1. Every query is parsed (sqlglot) before it runs: exactly one statement, a read query (SELECT, WITH, UNION), only
   tables of the database or names defined in the query itself (no file such as FROM 'x.csv', no table function
   such as read_csv()), and no function that reads files or the environment.
2. DuckDB itself is locked: the database is copied into memory, then external access is disabled
   (enable_external_access=false: no file, no network, no extension) and the configuration is frozen
   (lock_configuration=true), so a query that would get past the first lock still could not read anything else.

Each query has a timeout; its full result is kept (the statistics and chart agents use it), the LLM only sees a
preview.
"""
import itertools
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "connectivite.duckdb"

READ_QUERIES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
FORBIDDEN_FUNCTION = re.compile(r"^(read_.*|.*_scan|glob|getenv|query|query_table|sniff_csv|parquet_.*|iceberg_.*|"
                                r"delta_.*|http.*|load|install)$", re.IGNORECASE)
DEFAULT_TIMEOUT_S = 20
MAX_ROWS = 1_000_000      # rows kept for the other agents
PREVIEW_ROWS = 30         # rows shown to the LLM


class QueryRejected(ValueError):
    """The query breaks a rule: it is not run."""


class QueryFailed(RuntimeError):
    """The query was accepted but DuckDB could not run it (error, timeout, too many rows)."""


@dataclass
class ResultTable:
    """The result of a query, kept for the other agents under its id (t1, t2, ...)."""
    id: str
    sql: str
    data: pd.DataFrame
    elapsed_ms: float
    truncated: bool = False
    corrections: list = field(default_factory=list)   # values replaced by the exact ones of the data
    unknown_values: list = field(default_factory=list)  # values that match nothing in their column

    def preview(self, rows=PREVIEW_ROWS):
        """What the LLM sees: size, columns, the first rows, and the corrected or unknown values."""
        shown = self.data.head(rows).to_string(index=False, max_colwidth=40)
        more = f"\n... {len(self.data) - rows} lignes de plus" if len(self.data) > rows else ""
        notes = "".join(f"\nValeur corrigée : {c}." for c in self.corrections)
        notes += "".join(f"\nAttention : {u}. Corrige la requête." for u in self.unknown_values)
        return (f"Tableau {self.id} : {len(self.data)} lignes, colonnes {list(self.data.columns)}\n{shown}{more}"
                + notes)


def function_names(tree):
    names = set()
    for node in tree.find_all(exp.Func):
        names.add(node.name if isinstance(node, exp.Anonymous) else (node.sql_name() or node.key))
    return {n.lower() for n in names}


def check_query(sql, known_tables):
    """The parsed query, or QueryRejected with the reason (in French: the agent reads it)."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as error:
        raise QueryRejected(f"Requête illisible : {str(error).splitlines()[0]}") from None
    if len(statements) != 1:
        raise QueryRejected(f"Une seule requête à la fois ({len(statements)} trouvées).")
    tree = statements[0]
    if not isinstance(tree, READ_QUERIES):
        raise QueryRejected(f"Seules les requêtes de lecture (SELECT) sont permises, pas {tree.key.upper()}.")
    defined = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise QueryRejected(f"Source interdite : {table.sql('duckdb')} (seules les tables de la base sont "
                                "permises).")
        name = table.name.lower()
        if (table.db or table.catalog) or (name not in known_tables and name not in defined):
            raise QueryRejected(f"Table inconnue : {table.sql('duckdb')}. Tables disponibles : "
                                f"{', '.join(sorted(known_tables))}.")
    forbidden = sorted(f for f in function_names(tree) if FORBIDDEN_FUNCTION.match(f))
    if forbidden:
        raise QueryRejected(f"Fonction interdite : {', '.join(forbidden)}.")
    return tree


def open_locked_copy(path=DB_PATH, memory_limit="2GB", threads=4):
    """An in-memory copy of the database, with external access disabled and the configuration locked."""
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{Path(path).as_posix()}' AS source (READ_ONLY)")
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog = "
                                        "'source' AND table_type = 'BASE TABLE'").fetchall()]
    views = con.execute("SELECT sql FROM duckdb_views() WHERE database_name = 'source' AND NOT internal").fetchall()
    for table in tables:
        con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM source."{table}"')
    con.execute("DETACH source")
    for (view_sql,) in views:
        con.execute(view_sql)
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {int(threads)}")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


@dataclass
class SqlTool:
    """The tool given to the SQL agent; the results are kept in `tables` for the other agents."""
    con: duckdb.DuckDBPyConnection
    timeout_s: float = DEFAULT_TIMEOUT_S
    tables: dict = field(default_factory=dict)
    values: object = None  # a relais.tools.values.ValueIndex: literals are harmonised with the data

    def __post_init__(self):
        rows = self.con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        self.known_tables = {r[0].lower() for r in rows}
        self._ids = itertools.count(1)

    @classmethod
    def open(cls, path=DB_PATH, **kwargs):
        from relais.tools.values import ValueIndex
        con = open_locked_copy(path)
        return cls(con, values=kwargs.pop("values", None) or ValueIndex.from_connection(con), **kwargs)

    def run(self, sql):
        """Run one SELECT; returns a ResultTable (QueryRejected / QueryFailed otherwise)."""
        tree = check_query(sql, self.known_tables)
        corrections, unknown = self.values.harmonise(tree) if self.values is not None else ([], [])
        if corrections:
            sql = tree.sql(dialect="duckdb")
        timer = threading.Timer(self.timeout_s, self.con.interrupt)
        started = time.perf_counter()
        timer.start()
        try:
            cursor = self.con.execute(sql)
            data = cursor.fetch_df_chunk(vectors_per_chunk=MAX_ROWS // 2048 + 1)
            truncated = len(data) > MAX_ROWS or bool(cursor.fetchmany(1))
        except duckdb.InterruptException:
            raise QueryFailed(f"Requête interrompue : plus de {self.timeout_s:g} s.") from None
        except duckdb.Error as error:
            raise QueryFailed(f"Erreur DuckDB : {str(error).splitlines()[0]}") from None
        finally:
            timer.cancel()
        elapsed = (time.perf_counter() - started) * 1000
        table = ResultTable(f"t{next(self._ids)}", sql, data.head(MAX_ROWS), elapsed, truncated, corrections, unknown)
        self.tables[table.id] = table
        return table

    def describe(self):
        """The tables and their columns, as the SQL agent sees them in its prompt."""
        rows = self.con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                "ORDER BY table_name, ordinal_position").fetchall()
        lines, current = [], None
        for table, column, kind in rows:
            if table != current:
                lines.append(f"\n{table}:")
                current = table
            lines.append(f"  {column} {kind}")
        return "\n".join(lines).strip()
