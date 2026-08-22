from src.anonymization.anonymizer import anonymize
from src.anonymization.pseudonymizer import Pseudonymizer

TEXT = (
    "Patient : Jean DUPONT, né le : 14/05/1956. "
    "Le patient Jean DUPONT est suivi par Dr Marie LAURENT, RPPS : 10001234567."
)


def test_same_pii_same_token():
    res = Pseudonymizer().pseudonymize(TEXT)
    assert res.text.count("[PATIENT_001]") == 2
    assert "Jean DUPONT" not in res.text


def test_reversibility():
    p = Pseudonymizer()
    res = p.pseudonymize(TEXT)
    assert p.depseudonymize(res.text, res.mapping) == TEXT


def test_strict_is_irreversible():
    res = anonymize(TEXT)
    assert "Jean DUPONT" not in res.text
    assert "[REDACTED:" in res.text
    assert not hasattr(res, "mapping")


def test_pii_count_positive():
    assert Pseudonymizer().pseudonymize(TEXT).pii_count >= 3


def test_repeated_value_replaced_everywhere():
    # Passe 2 : une valeur détectée PII quelque part est masquée partout,
    # même sans contexte (ex. date répétée dans un tableau).
    text = (
        "Né(e) le 24/07/1963. Tableau : séjour du 24/07/1963 au 24/07/1963.\n"
        "Autre mention : 24/07/1963 sans contexte."
    )
    res = Pseudonymizer().pseudonymize(text)
    assert "24/07/1963" not in res.text
    assert res.text.count("[DATE_NAISSANCE_001]") == 4


def test_strict_repeated_value_replaced_everywhere():
    from src.anonymization.anonymizer import anonymize

    text = "Né(e) le 24/07/1963. Tableau : 24/07/1963 sans contexte."
    res = anonymize(text)
    assert "24/07/1963" not in res.text
    assert res.text.count("[REDACTED:DATE_NAISSANCE]") == 2
