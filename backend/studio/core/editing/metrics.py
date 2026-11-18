"""Métricas de edição e de "cara de IA" (ADR 0004 §6).

Encaixe da fundação: `fingerprint_from_timeline` devolve a impressão zerada até o item `metricas-cara-ia` implementar
o cálculo (exato, a partir da timeline, antes do render).
"""

from __future__ import annotations

from typing import Any

from studio.core.editing.profile import EditFingerprint
from studio.core.project.document import ProjectDoc


def fingerprint_from_timeline(timeline: dict[str, Any], doc: ProjectDoc) -> EditFingerprint:
    """A impressão digital da edição a partir da timeline resolvida. Encaixe vazio: zeros."""
    return EditFingerprint(source="timeline")
