"""Read-only PostgreSQL mirror audit.

Compares schema, Django migrations, row counts, row-content fingerprints, and
sequence state between two databases. Connection URLs are read from environment
variables so credentials never appear in the report or command output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2 import sql


def fetch_all(cursor, query, params=None):
    cursor.execute(query, params)
    return cursor.fetchall()


def database_snapshot(url: str, label: str) -> dict:
    connection = psycopg2.connect(url, connect_timeout=30)
    connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
    cursor = connection.cursor()

    cursor.execute(
        "SELECT current_database(), current_user, "
        "COALESCE(inet_server_addr()::text, 'local-socket'), version()"
    )
    database, user, server, version = cursor.fetchone()

    tables = [
        row[0]
        for row in fetch_all(
            cursor,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
        )
    ]

    columns = fetch_all(
        cursor,
        """
        SELECT table_name, ordinal_position, column_name, data_type, udt_name,
               is_nullable, COALESCE(column_default, ''),
               COALESCE(character_maximum_length::text, ''),
               COALESCE(numeric_precision::text, ''),
               COALESCE(numeric_scale::text, '')
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """,
    )

    constraints = fetch_all(
        cursor,
        """
        SELECT c.relname, con.conname, con.contype,
               pg_get_constraintdef(con.oid, true)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY c.relname, con.conname
        """,
    )

    indexes = fetch_all(
        cursor,
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """,
    )

    sequences = fetch_all(
        cursor,
        """
        SELECT sequencename, data_type, start_value, min_value, max_value,
               increment_by, cycle, cache_size, last_value
        FROM pg_sequences
        WHERE schemaname = 'public'
        ORDER BY sequencename
        """,
    )

    migrations = []
    if "django_migrations" in tables:
        migrations = fetch_all(
            cursor,
            "SELECT app, name FROM django_migrations ORDER BY app, name",
        )

    table_data = {}
    for table in tables:
        try:
            cursor.execute(
                sql.SQL(
                    """
                    SELECT COUNT(*)::bigint,
                           COALESCE(SUM((('x' || SUBSTR(MD5(ROW_TO_JSON(t)::text), 1, 16))::bit(64)::bigint)::numeric), 0)::text,
                           COALESCE(SUM((('x' || SUBSTR(MD5(ROW_TO_JSON(t)::text), 17, 16))::bit(64)::bigint)::numeric), 0)::text
                    FROM {} AS t
                    """
                ).format(sql.Identifier("public", table))
            )
            count, checksum_a, checksum_b = cursor.fetchone()
            table_data[table] = {
                "row_count": count,
                "fingerprint": hashlib.sha256(
                    f"{count}:{checksum_a}:{checksum_b}".encode()
                ).hexdigest(),
            }
        except Exception as exc:  # Keep auditing other tables after a permission/type error.
            connection.rollback()
            connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
            cursor = connection.cursor()
            table_data[table] = {"error": f"{type(exc).__name__}: {exc}"}

    cursor.close()
    connection.close()
    return {
        "label": label,
        "identity": {
            "database": database,
            "user": user,
            "server": server,
            "postgres": version.split(",")[0],
        },
        "tables": tables,
        "columns": [list(row) for row in columns],
        "constraints": [list(row) for row in constraints],
        "indexes": [list(row) for row in indexes],
        "sequences": [list(row) for row in sequences],
        "migrations": [list(row) for row in migrations],
        "table_data": table_data,
    }


def compare(left: dict, right: dict) -> dict:
    left_tables = set(left["tables"])
    right_tables = set(right["tables"])
    common = sorted(left_tables & right_tables)
    count_differences = []
    content_differences = []
    errors = []

    for table in common:
        left_data = left["table_data"][table]
        right_data = right["table_data"][table]
        if "error" in left_data or "error" in right_data:
            errors.append(
                {"table": table, "left": left_data.get("error"), "right": right_data.get("error")}
            )
            continue
        if left_data["row_count"] != right_data["row_count"]:
            count_differences.append(
                {
                    "table": table,
                    "left": left_data["row_count"],
                    "right": right_data["row_count"],
                    "difference": right_data["row_count"] - left_data["row_count"],
                }
            )
        if left_data["fingerprint"] != right_data["fingerprint"]:
            content_differences.append(table)

    same_table_set = left_tables == right_tables
    checks = {
        "table_set": same_table_set,
        "columns": left["columns"] == right["columns"],
        "constraints": left["constraints"] == right["constraints"],
        "indexes": left["indexes"] == right["indexes"],
        "migrations": left["migrations"] == right["migrations"],
        # Do not report vacuous success when one side has no/common fewer tables.
        "row_counts": same_table_set and not count_differences and not errors,
        "row_content": same_table_set and not content_differences and not errors,
        "sequences": left["sequences"] == right["sequences"],
        "read_errors": not errors,
    }
    return {
        "exact_mirror": all(checks.values()),
        "checks": checks,
        "missing_on_right": sorted(left_tables - right_tables),
        "extra_on_right": sorted(right_tables - left_tables),
        "count_differences": count_differences,
        "content_differences": content_differences,
        "read_errors": errors,
    }


def markdown_report(audit: dict) -> str:
    result = audit["comparison"]
    left = audit["left"]
    right = audit["right"]
    mark = lambda value: "x" if value else " "
    lines = [
        "# Database Mirror Checklist",
        "",
        f"Audit time (UTC): {audit['audited_at']}",
        "",
        f"Overall result: **{'EXACT MIRROR' if result['exact_mirror'] else 'NOT AN EXACT MIRROR'}**",
        "",
        "## Endpoints checked",
        "",
        f"- Left: `{left['label']}` — database `{left['identity']['database']}`, server `{left['identity']['server']}`",
        f"- Right: `{right['label']}` — database `{right['identity']['database']}`, server `{right['identity']['server']}`",
        f"- Left public tables: **{len(left['tables'])}**",
        f"- Right public tables: **{len(right['tables'])}**",
        "",
        "## Verification checklist",
        "",
        f"- [{mark(result['checks']['table_set'])}] Same public table set",
        f"- [{mark(result['checks']['columns'])}] Same columns, types, defaults, and nullability",
        f"- [{mark(result['checks']['constraints'])}] Same primary keys, foreign keys, unique constraints, and checks",
        f"- [{mark(result['checks']['indexes'])}] Same indexes",
        f"- [{mark(result['checks']['migrations'])}] Same applied Django migration set",
        f"- [{mark(result['checks']['row_counts'])}] Same row count across the complete table set",
        f"- [{mark(result['checks']['row_content'])}] Same content fingerprint across the complete table set",
        f"- [{mark(result['checks']['sequences'])}] Same sequence definitions and current values",
        f"- [{mark(result['checks']['read_errors'])}] Every table was readable on both sides",
        "",
        "## Differences",
        "",
        f"- Tables missing on right: {len(result['missing_on_right'])}",
        f"- Tables extra on right: {len(result['extra_on_right'])}",
        f"- Tables with different row counts: {len(result['count_differences'])}",
        f"- Tables with different content: {len(result['content_differences'])}",
        f"- Tables with audit errors: {len(result['read_errors'])}",
    ]

    if not left["tables"] and right["tables"]:
        lines.extend(
            [
                "",
                "## Diagnosis",
                "",
                "The left database is empty; the right database contains the application schema. This is not a partially completed Django migration.",
                "",
                "Django `migrate` creates/updates schema but does not copy application rows between databases. A dump/restore or controlled data-sync operation is required to mirror data.",
                "",
                "## Synchronization safety checklist",
                "",
                "- [ ] Confirm which database is the authoritative source",
                "- [ ] Stop application writes or schedule a maintenance window",
                "- [ ] Take timestamped backups of both databases",
                "- [ ] Verify the target connection separately from the source connection",
                "- [ ] Apply the complete Django migration set to the target",
                "- [ ] Copy data with a transactional dump/restore or approved sync tool",
                "- [ ] Reset and verify every PostgreSQL sequence",
                "- [ ] Rerun this audit until every verification checkbox passes",
                "- [ ] Run application smoke tests against the target before reopening writes",
            ]
        )

    if result["missing_on_right"]:
        lines.extend(["", "### Missing on right", "", *[f"- `{name}`" for name in result["missing_on_right"]]])
    if result["extra_on_right"]:
        lines.extend(["", "### Extra on right", "", *[f"- `{name}`" for name in result["extra_on_right"]]])
    if result["count_differences"]:
        lines.extend(["", "### Row-count differences", ""])
        lines.extend(
            f"- `{item['table']}`: left={item['left']}, right={item['right']}, difference={item['difference']:+d}"
            for item in result["count_differences"]
        )
    if result["content_differences"]:
        lines.extend(["", "### Content differences", "", *[f"- `{name}`" for name in result["content_differences"]]])
    if result["read_errors"]:
        lines.extend(["", "### Audit errors", "", *[f"- `{item['table']}`" for item in result["read_errors"]]])

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "An exact mirror requires every checkbox above to pass. Matching migration names or row counts alone is not sufficient: rows can differ while counts match, and sequence drift can cause later insert failures.",
            "",
            "For a definitive cutover check, pause writes to both databases and rerun this audit. If either database accepts writes during the two snapshots, a reported data mismatch may simply reflect concurrent activity.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-env", default="MIRROR_LEFT_DATABASE_URL")
    parser.add_argument("--right-env", default="MIRROR_RIGHT_DATABASE_URL")
    parser.add_argument("--left-label", default="local")
    parser.add_argument("--right-label", default="remote")
    parser.add_argument("--json-output", default="database_mirror_audit.json")
    parser.add_argument("--markdown-output", default="DATABASE_MIRROR_CHECKLIST.md")
    args = parser.parse_args()

    left_url = os.environ.get(args.left_env, "")
    right_url = os.environ.get(args.right_env, "")
    if not left_url or not right_url:
        parser.error(f"Set both {args.left_env} and {args.right_env}")

    print(f"Auditing {args.left_label} (read-only)...", flush=True)
    left = database_snapshot(left_url, args.left_label)
    print(f"Auditing {args.right_label} (read-only)...", flush=True)
    right = database_snapshot(right_url, args.right_label)
    audit = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "left": left,
        "right": right,
        "comparison": compare(left, right),
    }

    Path(args.json_output).write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    Path(args.markdown_output).write_text(markdown_report(audit), encoding="utf-8")
    print("EXACT_MIRROR=" + str(audit["comparison"]["exact_mirror"]).lower())
    print(f"JSON={args.json_output}")
    print(f"CHECKLIST={args.markdown_output}")
    return 0 if audit["comparison"]["exact_mirror"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
