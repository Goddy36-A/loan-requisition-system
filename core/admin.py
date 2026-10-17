from django.contrib import admin
from . import models as m
for model in (m.Branch, m.Profile, m.LoanProduct, m.LoanApplication, m.Document, m.Assessment,
              m.Notification, m.SurveyQuestion, m.SurveyAnswer):
    admin.site.register(model)
@admin.register(m.AuditLog)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "application")
    readonly_fields = [f.name for f in m.AuditLog._meta.fields]
admin.site.register(m.RepaymentInstallment)
admin.site.register(m.SmsLog)
@admin.register(m.Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("paid_on", "application", "amount", "method", "recorded_by")
    readonly_fields = [f.name for f in m.Payment._meta.fields]  # payments go through the app so installments stay in sync
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
