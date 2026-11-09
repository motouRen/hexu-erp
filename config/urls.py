from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="login.html")),
    path("logout/", auth_views.LogoutView.as_view()),
    path("", views.index),
    path("api/state/", views.state),
    path("api/products/", views.products),
    path("api/products/<int:product_id>/update/", views.update_product),
    path("api/stores/", views.stores),
    path("api/channels/", views.channels),
    path("api/stock/receive/", views.receive),
    path("api/orders/", views.orders),
    path("api/orders/<int:order_id>/<str:action>/", views.order_action),
    path("api/orders/import/", views.import_orders),
    path("api/export/<str:resource>/", views.export_csv),
    path("health/", views.health),
]
