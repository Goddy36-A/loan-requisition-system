from django.contrib import admin
from django.contrib.auth import views as av
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import path, reverse_lazy
from core import views
from django.conf import settings
from core.forms import StyledAuthForm, StyledPasswordChangeForm


class SignIn(av.LoginView):
    authentication_form = StyledAuthForm


class PasswordChange(SuccessMessageMixin, av.PasswordChangeView):
    form_class = StyledPasswordChangeForm
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("account")
    success_message = "Your password was changed."

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", SignIn.as_view(), name="login"),
    path("logout/", av.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("", views.dashboard, name="dashboard"),
    path("products/", views.products, name="products"),
    path("apply/<int:product_id>/", views.apply, name="apply"),
    path("applications/<int:pk>/", views.detail, name="detail"),
    path("applications/<int:pk>/transition/", views.do_transition, name="transition"),
    path("applications/<int:pk>/upload/", views.upload, name="upload"),
    path("applications/<int:pk>/payments/", views.record_payment, name="record_payment"),
    path("overdue/", views.overdue, name="overdue"),
    path("documents/<int:doc_id>/", views.download, name="download"),
    path("documents/<int:doc_id>/review/", views.review_doc, name="review_doc"),
    path("profile/", views.profile, name="profile"),
    path("account/", views.account, name="account"),
    path("account/password/", PasswordChange.as_view(), name="password_change"),
    path("account/bank/add/", views.bank_add, name="bank_add"),
    path("account/bank/<int:pk>/primary/", views.bank_primary, name="bank_primary"),
    path("account/bank/<int:pk>/remove/", views.bank_remove, name="bank_remove"),
    path("account/kin/add/", views.kin_add, name="kin_add"),
    path("account/kin/<int:pk>/remove/", views.kin_remove, name="kin_remove"),
    path("applications/<int:pk>/witnesses/add/", views.witness_add, name="witness_add"),
    path("witnesses/<int:pk>/remove/", views.witness_remove, name="witness_remove"),
    path("applications/<int:pk>/payout-account/", views.choose_account, name="choose_account"),
    path("notifications/", views.notifications, name="notifications"),
    path("analytics/survey.csv", views.survey_csv, name="survey_csv"),
    path("survey/", views.survey, name="survey"),
    path("analytics/", views.analytics, name="analytics"),
]
