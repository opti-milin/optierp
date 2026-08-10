"""Resolve package — (company, AY, class, regime) → frozen ResolvedRuleSet."""

from app.services.taxation.resolve.resolver import resolve_ruleset
from app.services.taxation.resolve.types import ResolvedRuleSet, ResolvedSchedule, ruleset_as_debug

__all__ = [
    "ResolvedRuleSet",
    "ResolvedSchedule",
    "resolve_ruleset",
    "ruleset_as_debug",
]
