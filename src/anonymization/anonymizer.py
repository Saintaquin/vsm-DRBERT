"""Anonymisation stricte (irréversible) : remplacement par [REDACTED:<TYPE>].

À utiliser pour tout export, partage, ou jeu de données d'évaluation.
Aucun mapping n'est conservé — la réversibilité est impossible par
construction (conformité RGPD : anonymisation au sens strict)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pii_detector import PIIDetector, PIIMatch


@dataclass
class AnonymizationResult:
    text: str
    matches: list[PIIMatch] = field(default_factory=list)

    @property
    def pii_count(self) -> int:
        return len(self.matches)


def anonymize(text: str, detector: PIIDetector | None = None) -> AnonymizationResult:
    detector = detector or PIIDetector()
    matches = detector.detect(text)
    out, cursor = [], 0
    for m in matches:
        out.append(text[cursor : m.start])
        out.append(f"[REDACTED:{m.pii_type}]")
        cursor = m.end
    out.append(text[cursor:])
    result = "".join(out)
    # Passe 2 : même logique que le pseudonymiseur — toute occurrence
    # restante d'une valeur connue comme PII est masquée.
    for m in sorted(matches, key=lambda x: -len(x.value)):
        if len(m.value) >= 4 and m.value in result:
            result = result.replace(m.value, f"[REDACTED:{m.pii_type}]")
    return AnonymizationResult(result, matches)
