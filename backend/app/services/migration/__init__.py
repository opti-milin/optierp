"""Module 12 — Data Migration.

``catalogue`` holds the source -> OptiERP mapping tables, ``sources`` reads the
uploaded file (Tally XML/CSV, or a spreadsheet exported by any application) into
the pipeline's intermediate shape, ``mapping`` decides which record each source
name refers to, ``importers`` create the documents, and ``runner`` sequences the
whole thing.
"""

from app.services.migration import catalogue, context, mapping, runner, sources

__all__ = ["catalogue", "context", "mapping", "runner", "sources"]
