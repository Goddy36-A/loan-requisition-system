from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()

_STATUS_CLASS = {"DRAFT": "st-draft", "SUBMITTED": "st-review", "DOCUMENT_REVIEW": "st-review", "CREDIT_ASSESSMENT": "st-review",
    "MANAGER_REVIEW": "st-review", "INFORMATION_REQUIRED": "st-info", "APPROVED": "st-ok", "DISBURSED": "st-active",
    "COMPLETED": "st-ok", "REJECTED": "st-bad", "WITHDRAWN": "st-draft"}


@register.filter
def money(value):
    """1234567.5 -> 1,234,567.50"""
    try: return f"{Decimal(value):,.2f}"
    except (InvalidOperation, TypeError, ValueError): return value


@register.filter
def status_class(status):
    return _STATUS_CLASS.get(str(status), "st-draft")


@register.filter
def status_label(status):
    """DOCUMENT_REVIEW -> Document review"""
    return str(status).replace("_", " ").capitalize()
