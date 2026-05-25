"""Detect database connection strings that embed credentials.

We deliberately do **not** flag credential-less URIs (``postgres://host/db``)
— those are config metadata, not secrets. The pattern requires a
``user:password@`` segment.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

# scheme://user:password@host[:port]/db[?opts]
DATABASE_CONNECTION = PatternRecognizer(
    supported_entity="DATABASE_CONNECTION",
    name="db-connection-recognizer",
    patterns=[
        Pattern(
            name="db-uri-with-creds",
            regex=(
                r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|amqps|"
                r"mssql|mariadb|cockroachdb|clickhouse)://"
                r"[^:\s/@]+:[^@\s/]+@"
                r"[^\s/?#]+(?:/[^\s?#]*)?(?:\?[^\s#]*)?"
            ),
            score=0.9,
        ),
    ],
    context=["database", "db", "connection", "uri", "url", "dsn"],
)

RECOGNIZERS: list[EntityRecognizer] = [DATABASE_CONNECTION]
