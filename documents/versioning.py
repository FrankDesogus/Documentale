"""
Utility centralizzate per la gestione degli schemi di revisione.

Supporta due schemi:
  NUMERIC    -> 00, 01, 02 ... 09, 10 ... 99, 100 (zero-padding preservato)
  ALPHABETIC -> A, B ... Z, AA, AB ... AZ, BA ...

Regole:
  - NUMERIC accetta solo /^\d+$/
  - ALPHABETIC accetta solo /^[A-Z]+$/ (normalizza lowercase -> uppercase)
  - Valori vuoti sempre rifiutati
  - Nessuna conversione automatica tra schemi
"""

import re

from django.core.exceptions import ValidationError
from django.db import models

_RE_NUMERIC = re.compile(r'^\d+$')
_RE_ALPHABETIC = re.compile(r'^[A-Za-z]+$')


class SequenceScheme(models.TextChoices):
    NUMERIC = 'numeric', 'Numerica'
    ALPHABETIC = 'alphabetic', 'Alfabetica'


def normalize_sequence_value(value: str, scheme: str) -> str:
    """
    Normalizza un valore di revisione rispetto allo schema.

    NUMERIC: strip spazi.
    ALPHABETIC: strip spazi + uppercase.
    """
    if not isinstance(value, str):
        raise ValidationError("Il valore di revisione deve essere una stringa.")
    value = value.strip()
    if scheme == SequenceScheme.ALPHABETIC:
        value = value.upper()
    return value


def validate_sequence_value(value: str, scheme: str) -> None:
    """
    Valida che il valore sia coerente con lo schema. Lancia ValidationError se non lo è.
    Presuppone che il valore sia già normalizzato.
    """
    if not value:
        raise ValidationError("Il valore di revisione non può essere vuoto.")

    if scheme == SequenceScheme.NUMERIC:
        if not _RE_NUMERIC.match(value):
            raise ValidationError(
                f"Con schema Numerico il valore '{value}' non è valido. "
                "Usa solo cifre (es. 00, 01, 10)."
            )
    elif scheme == SequenceScheme.ALPHABETIC:
        if not _RE_ALPHABETIC.match(value):
            raise ValidationError(
                f"Con schema Alfabetico il valore '{value}' non è valido. "
                "Usa solo lettere maiuscole (es. A, B, AA)."
            )
    else:
        raise ValidationError(f"Schema sconosciuto: '{scheme}'.")


def next_numeric_value(value: str) -> str:
    """
    Incrementa un valore numerico preservando lo zero-padding.

    '00' -> '01', '09' -> '10', '099' -> '100', '9' -> '10'
    """
    validate_sequence_value(value, SequenceScheme.NUMERIC)
    n = int(value) + 1
    result = str(n)
    if len(result) < len(value):
        result = result.zfill(len(value))
    return result


def next_alphabetic_value(value: str) -> str:
    """
    Incrementa un valore alfabetico (base-26, A-Z).

    'A' -> 'B', 'Z' -> 'AA', 'AZ' -> 'BA', 'ZZ' -> 'AAA'
    """
    validate_sequence_value(value, SequenceScheme.ALPHABETIC)
    chars = list(value.upper())
    carry = 1
    for i in range(len(chars) - 1, -1, -1):
        val = ord(chars[i]) - ord('A') + carry
        chars[i] = chr(ord('A') + val % 26)
        carry = val // 26
    prefix = 'A' * carry if carry else ''
    return prefix + ''.join(chars)


def next_sequence_value(value: str, scheme: str) -> str:
    """Incrementa un valore rispetto allo schema dato."""
    value = normalize_sequence_value(value, scheme)
    validate_sequence_value(value, scheme)
    if scheme == SequenceScheme.NUMERIC:
        return next_numeric_value(value)
    if scheme == SequenceScheme.ALPHABETIC:
        return next_alphabetic_value(value)
    raise ValidationError(f"Schema sconosciuto: '{scheme}'.")


def sequence_label_to_number(label: str, scheme: str) -> int:
    """
    Converte un'etichetta di revisione nel numero ordinale corrispondente.

    NUMERIC:    '00' → 0,  '01' → 1,  '10' → 10
    ALPHABETIC: 'A'  → 1,  'B'  → 2,  'Z'  → 26, 'AA' → 27, 'AB' → 28
    """
    label = normalize_sequence_value(label, scheme)
    validate_sequence_value(label, scheme)
    if scheme == SequenceScheme.NUMERIC:
        return int(label)
    # ALPHABETIC: base-26, 1-indexed (A=1, Z=26, AA=27, …)
    result = 0
    for c in label.upper():
        result = result * 26 + (ord(c) - ord('A') + 1)
    return result
