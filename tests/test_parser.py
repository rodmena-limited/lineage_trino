"""Tests for the SQL parser module."""

import pytest
from sqlglot import exp

from lineage_trino.parser import SQLParseError, SQLParser


@pytest.fixture
def parser():
    return SQLParser(dialect="trino")


class TestSQLParser:
    """SQLParser test suite (UBIQ-2: docstrings, UBIQ-4: sqlglot only)."""

    def test_parse_simple_select(self, parser):
        """Parse basic SELECT statement (EVENT-2)."""
        stmts = parser.parse("SELECT 1")
        assert len(stmts) == 1
        assert isinstance(stmts[0], exp.Select)

    def test_parse_multiple_statements(self, parser):
        """Parse semicolon-separated statements."""
        sql = "SELECT 1; SELECT 2;"
        stmts = parser.parse(sql)
        assert len(stmts) == 2

    def test_parse_invalid_sql_raises(self, parser):
        """Invalid SQL raises SQLParseError (UNWNT-1)."""
        with pytest.raises(SQLParseError):
            parser.parse("SEL ECT 1")

    def test_parse_empty_sql(self, parser):
        """Empty string returns empty list."""
        assert parser.parse("") == []
        assert parser.parse("  ") == []
        assert parser.parse(None) == []

    def test_parse_create_table_as_select(self, parser):
        """Parse CREATE TABLE AS SELECT."""
        sql = "CREATE TABLE t AS SELECT a, b FROM src"
        stmts = parser.parse(sql)
        assert len(stmts) == 1
        assert isinstance(stmts[0], exp.Create)
        assert parser.get_target_table(stmts[0]) is not None

    def test_parse_insert_into_select(self, parser):
        """Parse INSERT INTO ... SELECT."""
        sql = "INSERT INTO t SELECT a, b FROM src"
        stmts = parser.parse(sql)
        assert len(stmts) == 1
        assert isinstance(stmts[0], exp.Insert)

    def test_parse_with_cte(self, parser):
        """Parse WITH clause CTE (EVENT-4)."""
        sql = "WITH cte AS (SELECT a FROM t) SELECT a FROM cte"
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_is_select(self, parser):
        """is_select identifies SELECT and UNION."""
        select = parser.parse("SELECT 1")[0]
        union = parser.parse("SELECT 1 UNION SELECT 2")[0]
        create = parser.parse("CREATE TABLE t AS SELECT 1")[0]
        assert parser.is_select(select) is True
        assert parser.is_select(union) is True
        assert parser.is_select(create) is False

    def test_get_target_table_create(self, parser):
        """get_target_table returns table name for CREATE."""
        stmt = parser.parse("CREATE TABLE schema.table AS SELECT 1")[0]
        target = parser.get_target_table(stmt)
        assert target is not None
        assert "schema" in target.lower()

    def test_get_target_table_select(self, parser):
        """get_target_table returns None for plain SELECT."""
        stmt = parser.parse("SELECT 1")[0]
        assert parser.get_target_table(stmt) is None

    def test_get_select_from_statement(self, parser):
        """Get inner SELECT from various statement types."""
        select = parser.parse("SELECT 1")[0]
        assert parser.get_select_from_statement(select) is not None

        create = parser.parse("CREATE TABLE t AS SELECT 1")[0]
        assert parser.get_select_from_statement(create) is not None

        insert = parser.parse("INSERT INTO t SELECT 1")[0]
        assert parser.get_select_from_statement(insert) is not None

    def test_extract_ctes(self, parser):
        """Extract CTE definitions."""
        sql = "WITH cte1 AS (SELECT a FROM t1), cte2 AS (SELECT b FROM t2) SELECT * FROM cte1"
        stmt = parser.parse(sql)[0]
        select = parser.get_select_from_statement(stmt)
        ctes = parser.extract_ctes(select)
        assert "cte1" in ctes
        assert "cte2" in ctes

    def test_get_select_expressions(self, parser):
        """Get output column expressions from SELECT."""
        sql = "SELECT a, b + c AS d, COUNT(*) FROM t"
        stmt = parser.parse(sql)[0]
        select = parser.get_select_from_statement(stmt)
        exprs = parser.get_select_expressions(select)
        assert len(exprs) == 3

    def test_get_table_aliases(self, parser):
        """Extract table alias mapping (EVENT-5)."""
        sql = "SELECT * FROM schema.orders AS o JOIN customers c ON o.id = c.id"
        stmt = parser.parse(sql)[0]
        select = parser.get_select_from_statement(stmt)
        aliases = parser.get_table_aliases(select)
        assert "o" in aliases
        assert "c" in aliases
        assert "schema.orders" in aliases["o"].lower()

    def test_parse_files(self, parser, tmp_path):
        """Parse multiple SQL files (STATE-1)."""
        f1 = tmp_path / "q1.sql"
        f1.write_text("SELECT 1")
        f2 = tmp_path / "q2.sql"
        f2.write_text("SELECT 2")

        result = parser.parse_files([str(f1), str(f2)])
        assert len(result) == 2
        assert len(result[str(f1)]) == 1

    def test_parse_files_missing(self, parser):
        """Missing file is skipped with warning."""
        result = parser.parse_files(["/nonexistent/file.sql"])
        assert result == {}


class TestSQLParserComplex:
    """Complex SQL parsing scenarios."""

    def test_parse_with_nested_subqueries(self, parser):
        """Parse deeply nested subqueries (COMPLX-2)."""
        sql = """
        SELECT a.col1
        FROM (
            SELECT b.col1
            FROM (
                SELECT c.col1 FROM source c
            ) b
        ) a
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_parse_complex_aggregation(self, parser):
        """Parse complex aggregation (EVENT-6)."""
        sql = """
        SELECT
            department,
            COUNT(DISTINCT employee_id) AS emp_count,
            SUM(salary * 12 + bonus) AS total_comp,
            AVG(performance_score) OVER (PARTITION BY department) as avg_score
        FROM employees
        GROUP BY department
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_parse_union(self, parser):
        """Parse UNION query (COMPLX-4)."""
        sql = """
        SELECT id, name FROM active_users
        UNION ALL
        SELECT id, name FROM archived_users
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1
        assert isinstance(stmts[0], exp.Union)

    def test_cast_and_extract_expressions(self, parser):
        """Parse CAST and EXTRACT."""
        sql = "SELECT CAST(price AS DECIMAL(10,2)), EXTRACT(YEAR FROM order_date) FROM orders"
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_complex_join_conditions(self, parser):
        """Parse complex JOIN with multiple conditions."""
        sql = """
        SELECT o.*, c.name, a.city
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        LEFT JOIN addresses a ON c.id = a.customer_id AND a.is_primary = TRUE
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_window_functions(self, parser):
        """Parse window functions (STATE-2)."""
        sql = """
        SELECT
            id,
            ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) as rn,
            LAG(salary, 1) OVER (PARTITION BY dept ORDER BY hire_date) as prev_salary,
            SUM(amount) OVER (PARTITION BY region ORDER BY date ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) as rolling_sum
        FROM payroll
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_star_expansion(self, parser):
        """Parse SELECT *."""
        sql = "SELECT t.*, t2.col1 FROM t, t2"
        stmts = parser.parse(sql)
        assert len(stmts) == 1

    def test_case_when(self, parser):
        """Parse CASE WHEN expressions."""
        sql = """
        SELECT
            id,
            CASE
                WHEN score >= 90 THEN 'A'
                WHEN score >= 80 THEN 'B'
                ELSE 'C'
            END AS grade
        FROM results
        """
        stmts = parser.parse(sql)
        assert len(stmts) == 1
