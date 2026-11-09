from django.contrib import admin
from .models import Product, Store, Channel, Inventory, Order, OrderLine, StockMovement, Payment, ReturnLine, AuditEvent, Mutation


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [Product, Store, Channel, Inventory, Order, OrderLine, StockMovement, Payment, ReturnLine, AuditEvent, Mutation]:
    admin.site.register(model, ReadOnlyAdmin)
admin.site.site_header = "禾序 ERP · 用户与审计"
