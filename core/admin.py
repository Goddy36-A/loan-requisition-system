from django.contrib import admin
from . import models as m
for model in (m.Branch, m.Profile, m.LoanProduct, m.LoanApplication, m.Document, m.Assessment,
              m.Notification, m.SurveyQuestion, m.SurveyAnswer):
    admin.site.register(model)
@admin.register(m.AuditLog)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "application")
    readonly_fields = [f.name for f in m.AuditLog._meta.fields]
