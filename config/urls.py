from django.contrib import admin
from django.contrib.auth import views as av
from django.urls import path
from core import views
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", av.LoginView.as_view(), name="login"),
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
    path("notifications/", views.notifications, name="notifications"),
    path("analytics/survey.csv", views.survey_csv, name="survey_csv"),
    path("survey/", views.survey, name="survey"),
    path("analytics/", views.analytics, name="analytics"),
]
