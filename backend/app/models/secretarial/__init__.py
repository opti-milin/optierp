"""Module 13 — Company Secretarial & Governance ORM models.

Split by concern; this package re-exports everything so callers keep using
``from app.models.secretarial import SecretarialEntity`` regardless of the file
a model lives in (same convention as ``app.models.accounts``).

See ``docs/SECRETARIAL_GAP_AND_PLAN.md`` for the design decisions these tables
encode — in particular §2.1 (``company_id`` means the *owning* tenant, not the
tenant currently looking at the row) and §2.2 (delegation).
"""

from app.models.secretarial.compliance import (
    CLOSED_STATUSES,
    DEFAULT_REMINDER_OFFSETS,
    ITEM_STATUSES,
    RULE_BASES,
    SecretarialComplianceItem,
    SecretarialComplianceReminder,
    SecretarialComplianceRule,
)
from app.models.secretarial.content import (
    REVIEW_STATUSES,
    SecretarialContentPack,
)
from app.models.secretarial.engagement import (
    FINANCIAL_ACCESS_LEVELS,
    PracticeClientIndex,
    SecretarialEngagement,
)
from app.models.secretarial.entity import (
    ENTITY_CLASSES,
    ENTITY_KINDS,
    ENTITY_STATUSES,
    SecretarialEntity,
    SecretarialSettings,
)
from app.models.secretarial.masters import (
    BO_CLASSIFICATIONS,
    GROUP_RELATIONS,
    MEMBER_TYPES,
    RELATED_PARTY_BASES,
    SecretarialAuditor,
    SecretarialBeneficialOwner,
    SecretarialCharge,
    SecretarialCommittee,
    SecretarialCommitteeMember,
    SecretarialDsc,
    SecretarialFile,
    SecretarialGroupLink,
    SecretarialMember,
    SecretarialRelatedParty,
)
from app.models.secretarial.persons import (
    ROLE_TYPES,
    SecretarialAppointment,
    SecretarialPerson,
)

__all__ = [
    "BO_CLASSIFICATIONS",
    "CLOSED_STATUSES",
    "DEFAULT_REMINDER_OFFSETS",
    "ENTITY_CLASSES",
    "ENTITY_KINDS",
    "ENTITY_STATUSES",
    "FINANCIAL_ACCESS_LEVELS",
    "GROUP_RELATIONS",
    "ITEM_STATUSES",
    "MEMBER_TYPES",
    "RELATED_PARTY_BASES",
    "REVIEW_STATUSES",
    "ROLE_TYPES",
    "RULE_BASES",
    "PracticeClientIndex",
    "SecretarialAppointment",
    "SecretarialAuditor",
    "SecretarialBeneficialOwner",
    "SecretarialCharge",
    "SecretarialCommittee",
    "SecretarialCommitteeMember",
    "SecretarialComplianceItem",
    "SecretarialComplianceReminder",
    "SecretarialComplianceRule",
    "SecretarialContentPack",
    "SecretarialDsc",
    "SecretarialEngagement",
    "SecretarialEntity",
    "SecretarialFile",
    "SecretarialGroupLink",
    "SecretarialMember",
    "SecretarialPerson",
    "SecretarialRelatedParty",
    "SecretarialSettings",
]
