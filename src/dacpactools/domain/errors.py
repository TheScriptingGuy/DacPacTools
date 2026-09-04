from __future__ import annotations


class DomainError(Exception):
    """Base for domain-layer errors."""


class InvalidObjectName(DomainError):
    """Raised when an object identifier cannot be parsed."""


class UnresolvableObject(DomainError):
    """Raised when a referenced object cannot be located in the model."""


class UnresolvableColumn(DomainError):
    """Raised when a column reference cannot be traced to a source."""


class UnresolvableOrchestratorArtifact(DomainError):
    """Raised when no orchestrator artifacts can be read under the given path."""
