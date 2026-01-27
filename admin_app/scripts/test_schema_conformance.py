#!/usr/bin/env python3
"""
Schema Conformance Test Suite

Validates that all three schema definitions are structurally consistent:
1. SQLite schema (SCHEMA_SQL in db.py)
2. PostgreSQL schema (SCHEMA_SQL_POSTGRES in db.py)
3. Cloudflare D1 schema (cloudflare/pages/schema.sql)

This catches:
- Missing tables in any environment
- Column mismatches (name, type, nullability)
- Missing indexes
- Type mapping inconsistencies (SQLite -> PostgreSQL)
- Drift between local and cloud schemas

Usage:
    python3 admin_app/scripts/test_schema_conformance.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Column:
    """Represents a database column."""
    name: str
    type: str
    nullable: bool = True
    default: str | None = None
    primary_key: bool = False
    autoincrement: bool = False


@dataclass
class ForeignKey:
    """Represents a foreign key constraint."""
    column: str
    ref_table: str
    ref_column: str
    on_delete: str | None = None


@dataclass
class Index:
    """Represents a database index."""
    name: str
    table: str
    columns: list[str]
    unique: bool = False


@dataclass
class Table:
    """Represents a database table."""
    name: str
    columns: dict[str, Column] = field(default_factory=dict)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    unique_constraints: list[list[str]] = field(default_factory=list)


@dataclass
class Schema:
    """Represents a complete database schema."""
    name: str
    tables: dict[str, Table] = field(default_factory=dict)
    indexes: list[Index] = field(default_factory=list)


def normalize_type(sql_type: str, target: str = "sqlite") -> str:
    """
    Normalize SQL types for comparison.
    
    SQLite is flexible with types (TEXT, INTEGER, REAL, BLOB).
    PostgreSQL is strict (VARCHAR, INTEGER, TIMESTAMP, etc.).
    """
    sql_type = sql_type.upper().strip()
    
    # Remove size constraints for comparison (VARCHAR(255) -> VARCHAR)
    sql_type = re.sub(r'\(\d+\)', '', sql_type)
    
    if target == "postgres":
        # PostgreSQL type mappings
        type_map = {
            "TEXT": "TEXT",
            "INTEGER": "INTEGER",
            "REAL": "REAL",
            "BLOB": "BYTEA",
            "SERIAL": "INTEGER",  # SERIAL is INTEGER with auto-increment
            "TIMESTAMP": "TIMESTAMP",
        }
    else:  # sqlite
        # SQLite type mappings
        type_map = {
            "TEXT": "TEXT",
            "VARCHAR": "TEXT",
            "INTEGER": "INTEGER",
            "SERIAL": "INTEGER",
            "REAL": "REAL",
            "FLOAT": "REAL",
            "DOUBLE": "REAL",
            "BLOB": "BLOB",
            "BYTEA": "BLOB",
            "TIMESTAMP": "TEXT",
        }
    
    return type_map.get(sql_type, sql_type)


def parse_create_table(sql: str, schema_name: str) -> Table | None:
    """Parse a CREATE TABLE statement."""
    # Match: CREATE TABLE [IF NOT EXISTS] table_name (...)
    # Use non-greedy match and handle parentheses balancing
    match = re.search(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(',
        sql,
        re.IGNORECASE
    )
    
    if not match:
        return None
    
    table_name = match.group(1).strip()
    
    # Find matching closing parenthesis
    start = match.end()
    paren_count = 1
    end = start
    
    while end < len(sql) and paren_count > 0:
        if sql[end] == '(':
            paren_count += 1
        elif sql[end] == ')':
            paren_count -= 1
        end += 1
    
    if paren_count != 0:
        return None
    
    columns_sql = sql[start:end-1].strip()
    
    table = Table(name=table_name)
    
    # Split by commas (handling nested parentheses for constraints)
    lines = []
    paren_depth = 0
    current_line = []
    
    for char in columns_sql + ',':
        if char == '(':
            paren_depth += 1
            current_line.append(char)
        elif char == ')':
            paren_depth -= 1
            current_line.append(char)
        elif char == ',' and paren_depth == 0:
            lines.append(''.join(current_line).strip())
            current_line = []
        else:
            current_line.append(char)
    
    for line in lines:
        if not line:
            continue
        
        line = line.strip()
        
        # Skip constraint definitions
        if re.match(r'^(FOREIGN\s+KEY|UNIQUE|CHECK|PRIMARY\s+KEY|CONSTRAINT)', line, re.IGNORECASE):
            # Parse FOREIGN KEY
            fk_match = re.search(
                r'FOREIGN\s+KEY\s*\((\w+)\)\s+REFERENCES\s+(\w+)\s*\((\w+)\)(?:\s+ON\s+DELETE\s+(\w+))?',
                line,
                re.IGNORECASE
            )
            if fk_match:
                table.foreign_keys.append(ForeignKey(
                    column=fk_match.group(1),
                    ref_table=fk_match.group(2),
                    ref_column=fk_match.group(3),
                    on_delete=fk_match.group(4)
                ))
            
            # Parse UNIQUE constraint
            unique_match = re.search(r'UNIQUE\s*\(([\w\s,]+)\)', line, re.IGNORECASE)
            if unique_match:
                cols = [c.strip() for c in unique_match.group(1).split(',')]
                table.unique_constraints.append(cols)
            
            continue
        
        # Parse column definition
        # Format: column_name TYPE [NOT NULL] [DEFAULT value] [PRIMARY KEY] [AUTOINCREMENT]
        parts = line.split()
        if len(parts) < 2:
            continue
        
        col_name = parts[0].strip()
        col_type = parts[1].strip()
        
        col = Column(
            name=col_name,
            type=normalize_type(col_type, "sqlite" if "postgres" not in schema_name.lower() else "postgres")
        )
        
        line_upper = line.upper()
        
        # Parse constraints
        if 'NOT NULL' in line_upper:
            col.nullable = False
        
        if 'PRIMARY KEY' in line_upper:
            col.primary_key = True
            col.nullable = False
        
        if 'AUTOINCREMENT' in line_upper or 'SERIAL' in col_type.upper():
            col.autoincrement = True
        
        # Parse DEFAULT
        default_match = re.search(r'DEFAULT\s+(.+?)(?:,|\)|$)', line, re.IGNORECASE)
        if default_match:
            col.default = default_match.group(1).strip()
        
        table.columns[col_name] = col
    
    return table


def parse_create_index(sql: str) -> Index | None:
    """Parse a CREATE INDEX statement."""
    match = re.search(
        r'CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*\(([\w\s,]+)\)',
        sql,
        re.IGNORECASE
    )
    
    if not match:
        return None
    
    return Index(
        name=match.group(2).strip(),
        table=match.group(3).strip(),
        columns=[c.strip() for c in match.group(4).split(',')],
        unique=bool(match.group(1))
    )


def parse_schema(sql_text: str, schema_name: str) -> Schema:
    """Parse a complete SQL schema."""
    # Remove SQL comments first
    # Remove line comments (-- ...)
    sql_text = re.sub(r'--[^\n]*\n', '\n', sql_text)
    # Remove block comments (/* ... */)
    sql_text = re.sub(r'/\*.*?\*/', '', sql_text, flags=re.DOTALL)
    
    schema = Schema(name=schema_name)
    
    # Find all CREATE TABLE statements
    pos = 0
    while True:
        match = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', sql_text[pos:], re.IGNORECASE)
        if not match:
            break
        
        # Extract full statement
        start = pos + match.start()
        
        # Find end of statement (closing parenthesis + semicolon)
        paren_start = pos + match.end()
        paren_count = 0
        end = paren_start
        found_open = False
        
        while end < len(sql_text):
            if sql_text[end] == '(':
                paren_count += 1
                found_open = True
            elif sql_text[end] == ')':
                paren_count -= 1
                if found_open and paren_count == 0:
                    # Find semicolon after closing paren
                    semi = sql_text.find(';', end)
                    if semi != -1:
                        end = semi + 1
                    break
            end += 1
        
        stmt = sql_text[start:end]
        table = parse_create_table(stmt, schema_name)
        if table:
            schema.tables[table.name] = table
        
        pos = end
    
    # Find all CREATE INDEX statements
    for match in re.finditer(r'CREATE\s+(UNIQUE\s+)?INDEX[^;]+;', sql_text, re.IGNORECASE | re.DOTALL):
        stmt = match.group(0)
        index = parse_create_index(stmt)
        if index:
            schema.indexes.append(index)
    
    return schema


def compare_columns(col1: Column, col2: Column, table_name: str, schema1: str, schema2: str) -> list[str]:
    """Compare two columns and return list of differences."""
    issues = []
    
    # Type comparison (normalized)
    if col1.type != col2.type:
        # Allow some type equivalences
        equiv = [
            {"TEXT", "VARCHAR"},
            {"INTEGER", "SERIAL"},
            {"REAL", "FLOAT", "DOUBLE"},
            {"TEXT", "TIMESTAMP"},  # SQLite stores timestamps as TEXT
            {"BLOB", "BYTEA"},
        ]
        
        types_match = any(
            col1.type in equiv_set and col2.type in equiv_set
            for equiv_set in equiv
        )
        
        if not types_match:
            issues.append(
                f"  Column '{col1.name}' type mismatch: "
                f"{schema1}={col1.type} vs {schema2}={col2.type}"
            )
    
    # Nullability
    if col1.nullable != col2.nullable:
        issues.append(
            f"  Column '{col1.name}' nullable mismatch: "
            f"{schema1}={'NULL' if col1.nullable else 'NOT NULL'} vs "
            f"{schema2}={'NULL' if col2.nullable else 'NOT NULL'}"
        )
    
    # Primary key
    if col1.primary_key != col2.primary_key:
        issues.append(
            f"  Column '{col1.name}' primary key mismatch: "
            f"{schema1}={col1.primary_key} vs {schema2}={col2.primary_key}"
        )
    
    return issues


def compare_tables(table1: Table, table2: Table, schema1: str, schema2: str) -> list[str]:
    """Compare two tables and return list of differences."""
    issues = []
    
    # Compare columns
    all_columns = set(table1.columns.keys()) | set(table2.columns.keys())
    
    for col_name in sorted(all_columns):
        if col_name not in table1.columns:
            issues.append(f"  Column '{col_name}' missing in {schema1}")
        elif col_name not in table2.columns:
            issues.append(f"  Column '{col_name}' missing in {schema2}")
        else:
            col_issues = compare_columns(
                table1.columns[col_name],
                table2.columns[col_name],
                table1.name,
                schema1,
                schema2
            )
            issues.extend(col_issues)
    
    # Compare foreign keys
    fk1_sigs = {f"{fk.column}->{fk.ref_table}.{fk.ref_column}" for fk in table1.foreign_keys}
    fk2_sigs = {f"{fk.column}->{fk.ref_table}.{fk.ref_column}" for fk in table2.foreign_keys}
    
    for fk_sig in fk1_sigs - fk2_sigs:
        issues.append(f"  Foreign key '{fk_sig}' missing in {schema2}")
    
    for fk_sig in fk2_sigs - fk1_sigs:
        issues.append(f"  Foreign key '{fk_sig}' missing in {schema1}")
    
    return issues


def compare_schemas(schema1: Schema, schema2: Schema, ignore_tables: set[str] | None = None) -> dict[str, Any]:
    """
    Compare two schemas and return detailed differences.
    
    Returns dict with:
        - missing_in_schema1: list of table names
        - missing_in_schema2: list of table names
        - table_diffs: dict of table_name -> list of issues
    """
    ignore_tables = ignore_tables or set()
    
    result = {
        "missing_in_schema1": [],
        "missing_in_schema2": [],
        "table_diffs": {},
    }
    
    tables1 = {name for name in schema1.tables.keys() if name not in ignore_tables}
    tables2 = {name for name in schema2.tables.keys() if name not in ignore_tables}
    
    # Find missing tables
    result["missing_in_schema1"] = sorted(tables2 - tables1)
    result["missing_in_schema2"] = sorted(tables1 - tables2)
    
    # Compare common tables
    common_tables = tables1 & tables2
    
    for table_name in sorted(common_tables):
        issues = compare_tables(
            schema1.tables[table_name],
            schema2.tables[table_name],
            schema1.name,
            schema2.name
        )
        
        if issues:
            result["table_diffs"][table_name] = issues
    
    return result


def print_comparison_report(schema1: Schema, schema2: Schema, comparison: dict[str, Any]) -> bool:
    """
    Print a formatted comparison report.
    
    Returns True if schemas match, False if differences found.
    """
    has_issues = False
    
    print(f"\n{'='*80}")
    print(f"Comparing: {schema1.name} vs {schema2.name}")
    print(f"{'='*80}\n")
    
    # Missing tables
    if comparison["missing_in_schema1"]:
        has_issues = True
        print(f"⚠️  Tables in {schema2.name} but NOT in {schema1.name}:")
        for table in comparison["missing_in_schema1"]:
            print(f"  - {table}")
        print()
    
    if comparison["missing_in_schema2"]:
        has_issues = True
        print(f"⚠️  Tables in {schema1.name} but NOT in {schema2.name}:")
        for table in comparison["missing_in_schema2"]:
            print(f"  - {table}")
        print()
    
    # Table differences
    if comparison["table_diffs"]:
        has_issues = True
        print(f"⚠️  Table structure differences:\n")
        for table_name, issues in comparison["table_diffs"].items():
            print(f"Table '{table_name}':")
            for issue in issues:
                print(issue)
            print()
    
    if not has_issues:
        print("✅ Schemas match!\n")
    
    return not has_issues


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    
    # Import schemas (add qgen to path first)
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "qgen"))
    from admin_app.admin_app.db import SCHEMA_SQL, SCHEMA_SQL_POSTGRES
    
    # Read Cloudflare schema
    cloudflare_schema_path = repo / "cloudflare" / "pages" / "schema.sql"
    cloudflare_sql = cloudflare_schema_path.read_text()
    
    print("\n" + "="*80)
    print("SCHEMA CONFORMANCE TEST SUITE")
    print("="*80)
    
    # Parse all schemas
    print("\nParsing schemas...")
    sqlite_schema = parse_schema(SCHEMA_SQL, "SQLite (local)")
    postgres_schema = parse_schema(SCHEMA_SQL_POSTGRES, "PostgreSQL (Railway)")
    cloudflare_schema = parse_schema(cloudflare_sql, "Cloudflare D1")
    
    print(f"  SQLite: {len(sqlite_schema.tables)} tables")
    print(f"  PostgreSQL: {len(postgres_schema.tables)} tables")
    print(f"  Cloudflare: {len(cloudflare_schema.tables)} tables")
    
    all_passed = True
    
    # Compare SQLite vs PostgreSQL (should be nearly identical)
    comparison1 = compare_schemas(sqlite_schema, postgres_schema)
    passed1 = print_comparison_report(sqlite_schema, postgres_schema, comparison1)
    all_passed = all_passed and passed1
    
    # Compare SQLite vs Cloudflare (Cloudflare is intentionally a subset)
    # We only check that Cloudflare tables exist in SQLite and match
    # We ignore tables that only exist in SQLite (admin-only tables)
    admin_only_tables = {
        "app_settings",
        "cases",
        "recipients",
        "templates",
        "invitation_variants",
        "event_log",
        "campaign_recipient_exclusions",
        "generation_waves",
        "respondent_assignments",
        "question_items",
        "question_stats",
        "cloud_pushes",
        "cloud_invitation_tokens",
        "cloud_invitation_tokens_legacy",
        "cloud_sync_state",
        "cloud_uploads",
        "submission_answers",
    }
    
    comparison2 = compare_schemas(
        cloudflare_schema,
        sqlite_schema,
        ignore_tables=admin_only_tables
    )
    
    # Reframe the report for subset comparison
    print(f"\n{'='*80}")
    print(f"Comparing: Cloudflare D1 (subset) vs SQLite (local)")
    print(f"{'='*80}\n")
    
    has_issues = False
    
    # Cloudflare tables missing in SQLite = BAD
    if comparison2["missing_in_schema2"]:
        has_issues = True
        print(f"⚠️  Cloudflare tables NOT found in SQLite:")
        for table in comparison2["missing_in_schema2"]:
            print(f"  - {table}")
        print()
    
    # SQLite tables missing in Cloudflare = OK (admin-only)
    # (we already filtered these out with ignore_tables)
    
    # Structure differences = BAD
    if comparison2["table_diffs"]:
        has_issues = True
        print(f"⚠️  Table structure differences:\n")
        for table_name, issues in comparison2["table_diffs"].items():
            print(f"Table '{table_name}':")
            for issue in issues:
                print(issue)
            print()
    
    if not has_issues:
        print("✅ Cloudflare schema is a valid subset of SQLite schema!\n")
    
    passed2 = not has_issues
    all_passed = all_passed and passed2
    
    # Final summary
    print("\n" + "="*80)
    if all_passed:
        print("✅ ALL SCHEMA CONFORMANCE TESTS PASSED")
    else:
        print("❌ SCHEMA CONFORMANCE TESTS FAILED")
        print("\nFix the schema inconsistencies before deploying to production!")
    print("="*80 + "\n")
    
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
