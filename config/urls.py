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
    path("survey/", views.survey, name="survey"),
    path("analytics/", views.analytics, name="analytics"),
]
