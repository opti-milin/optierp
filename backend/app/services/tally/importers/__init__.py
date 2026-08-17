"""Entity importers — one module for masters, one for vouchers."""

from app.services.tally.importers import masters, vouchers

__all__ = ["masters", "vouchers"]
