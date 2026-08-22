"""Pseudonymisation réversible : chaque PII est remplacée par un token stable
au sein d'un même dossier ([PATIENT_001], [DATE_NAISSANCE_001]…).

Le mapping PII↔token n'est JAMAIS retourné en clair par défaut : il est
destiné au coffre-fort chiffré (mapping_vault.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pii_detector import PIIDetector, PIIMatch

_TOKEN_PREFIX = {
    "NOM_PERSONNE": "PATIENT",
    "NIR": "NIR",
    "INS": "INS",
    "RPPS": "RPPS",
    "ADELI": "ADELI",
    "FINESS": "FINESS",
    "DATE_NAISSANCE": "DATE_NAISSANCE",
    "DATE_DECES": "DATE_DECES",
    "TELEPHONE": "TEL",
    "EMAIL": "EMAIL",
    "ADRESSE": "ADRESSE",
    "NUMERO_DOSSIER": "DOSSIER",
    "NUMERO_SEJOUR": "SEJOUR",
}


@dataclass
class PseudonymizationResult:
    text: str
    mapping: dict[str, str]  # token -> valeur originale
    matches: list[PIIMatch] = field(default_factory=list)

    @property
    def pii_count(self) -> int:
        return len(self.matches)


class Pseudonymizer:
    """Stateful au niveau d'un dossier : même PII → même token."""

    def __init__(self, detector: PIIDetector | None = None):
        self.detector = detector or PIIDetector()
        self._value_to_token: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def _token_for(self, match: PIIMatch) -> str:
        key = (match.pii_type, match.value.strip().lower())
        if key in self._value_to_token:
            return self._value_to_token[key]
        prefix = _TOKEN_PREFIX.get(match.pii_type, match.pii_type)
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        token = f"[{prefix}_{self._counters[prefix]:03d}]"
        self._value_to_token[key] = token
        return token

    def pseudonymize(self, text: str) -> PseudonymizationResult:
        matches = self.detector.detect(text)
        out, cursor, mapping = [], 0, {}
        for m in matches:
            token = self._token_for(m)
            mapping[token] = m.value
            out.append(text[cursor : m.start])
            out.append(token)
            cursor = m.end
        out.append(text[cursor:])
        result = "".join(out)
        # Passe 2 : une valeur détectée comme PII dans ce document l'est
        # partout. Toute occurrence restante de la même valeur (ex. date de
        # naissance répétée dans un tableau sans contexte) est remplacée par
        # le même token. Valeurs courtes exclues (évite de sur-masquer).
        for token, value in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
            if len(value) >= 4 and value in result:
                result = result.replace(value, token)
        return PseudonymizationResult(result, mapping, matches)

    @staticmethod
    def depseudonymize(text: str, mapping: dict[str, str]) -> str:
        for token, value in mapping.items():
            text = text.replace(token, value)
        return text
