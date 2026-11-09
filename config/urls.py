from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from core import views
from core.login import ProtectedLoginView
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm

admin.site.login = ProtectedLoginView.as_view(authentication_form=AdminAuthenticationForm, template_name="admin/login.html")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", ProtectedLoginView.as_view()),
    path("logout/", auth_views.LogoutView.as_view()),
    path("", views.index),
    path("api/state/", views.state),
    path("api/records/<str:resource>/", views.records),
    path("api/stock/documents/", views.stock_documents),
    path("api/stock/documents/<int:document_id>/<str:action>/", views.stock_document_action),
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
if settings.DEMO_MODE:
    urlpatterns = [item for item in urlpatterns if str(item.pattern) != "admin/"]
