from decimal import Decimal, InvalidOperation
import re
from functools import lru_cache
from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static as _static
from django.utils.safestring import mark_safe

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


# --- Offline styling: embed CSS/JS directly in the page so nothing depends on a separate file being served with the right type ---
@lru_cache(maxsize=None)
def _read_static(path):
    found = finders.find(path)
    if not found: return ""
    with open(found, encoding="utf-8") as f: return f.read()


@register.simple_tag
def inline_css(*paths):
    """<style> with the given static CSS files embedded. Relative url(../x) references are pointed at /static/<app>/x."""
    out = []
    for path in paths:
        css = _read_static(path)
        if not css: continue
        base = _static(path.rsplit("/", 2)[0] + "/") if path.count("/") >= 2 else _static("")
        css = re.sub(r"url\((['\"]?)\.\./", lambda m: "url(" + m.group(1) + base, css)
        out.append(css.replace("</style", "<\\/style"))
    return mark_safe("<style>" + "\n".join(out) + "</style>")


@register.simple_tag
def inline_js(path):
    js = _read_static(path)
    return mark_safe("<script>" + js.replace("</script", "<\\/script") + "</script>") if js else ""
