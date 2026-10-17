"""Shared input cleaning for phone numbers, NINs and bank account numbers."""
import re
from django.core.exceptions import ValidationError


def normalise_phone(raw):
    """Tidy a phone number: strip separators and turn Ugandan local formats into +256... (other +numbers are kept)."""
    s = re.sub(r"[\s\-().]", "", (raw or "").strip())
    if s.startswith("00"): s = "+" + s[2:]
    if s.startswith("0") and len(s) == 10: s = "+256" + s[1:]
    elif s.startswith("256") and len(s) == 12: s = "+" + s
    return s


def clean_phone(raw):
    s = normalise_phone(raw)
    if not re.fullmatch(r"\+\d{9,15}", s):
        raise ValidationError("Enter a valid phone number, e.g. 0700 000 001 or +256700000001.")
    return s


def clean_nin(raw):
    """Blank is allowed (callers decide whether it is required); otherwise 14 letters/digits."""
    s = (raw or "").strip().upper()
    if s and (len(s) != 14 or not s.isalnum()):
        raise ValidationError("NIN must be 14 letters/digits.")
    return s


def clean_account_number(raw):
    s = re.sub(r"[\s\-]", "", (raw or "").strip())
    if not re.fullmatch(r"\d{6,20}", s):
        raise ValidationError("Account number must be 6 to 20 digits.")
    return s
