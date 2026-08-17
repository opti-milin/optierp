"""Module 12 — Tally data migration.

``catalogue`` holds the Tally -> OptiERP mapping tables, ``parser`` reads the
export, ``mapping`` decides which record each Tally name refers to, ``importers``
create the documents, and ``runner`` sequences the whole thing.
"""

from app.services.tally import catalogue, context, mapping, parser, runner

__all__ = ["catalogue", "context", "mapping", "parser", "runner"]
