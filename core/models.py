import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F


class Product(models.Model):
    sku = models.CharField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=48, default="其他")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = models.PositiveIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(price__gte=0), name="product_price_nonnegative")]

    def __str__(self):
        return f"{self.sku} · {self.name}"


class Store(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    location = models.CharField(max_length=160, blank=True)

    def __str__(self):
        return self.name


class Channel(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=16, choices=[("online", "线上"), ("retail", "门店")], default="online")

    def __str__(self):
        return self.name


class Inventory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    on_hand = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "store"], name="inventory_product_store"),
            models.CheckConstraint(condition=Q(on_hand__gte=F("reserved")), name="inventory_reserved_within_stock")]

    @property
    def available(self):
        return self.on_hand - self.reserved


class Order(models.Model):
    number = models.CharField(max_length=40, unique=True)
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT)
    external_id = models.CharField(max_length=80, blank=True)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    customer = models.CharField(max_length=80)
    status = models.CharField(max_length=24, default="pending", choices=[
        ("pending", "待发货"), ("shipped", "已发货"), ("canceled", "已取消"),
        ("partial_return", "部分退货"), ("returned", "已退货")])
    total = models.DecimalField(max_digits=16, decimal_places=2)
    refunded_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "external_id"], condition=~Q(external_id=""), name="unique_channel_external_order"),
            models.CheckConstraint(condition=Q(total__gte=0) & Q(refunded_total__gte=0) & Q(refunded_total__lte=F("total")), name="order_amounts_valid"),
        ]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, related_name="lines", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=120)
    sku = models.CharField(max_length=48)
    quantity = models.PositiveIntegerField()
    returned_quantity = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(returned_quantity__lte=F("quantity")) & Q(unit_price__gte=0), name="order_line_valid")]


class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    store = models.ForeignKey(Store, on_delete=models.PROTECT)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("receive", "补货入库"), ("reserve", "订单占用"),
        ("ship", "发货出库"), ("release", "取消释放"), ("return", "退货入库")])
    quantity_delta = models.IntegerField()
    reserved_delta = models.IntegerField(default=0)
    on_hand_after = models.PositiveIntegerField()
    reserved_after = models.PositiveIntegerField()
    note = models.CharField(max_length=240, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Payment(models.Model):
    order = models.ForeignKey(Order, related_name="payments", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[("receipt", "收款登记"), ("refund", "退款登记")])
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0), name="payment_amount_nonnegative")]


class ReturnLine(models.Model):
    payment = models.ForeignKey(Payment, related_name="return_lines", on_delete=models.PROTECT)
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    restock = models.BooleanField(default=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=48)
    object_ref = models.CharField(max_length=80)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [("operate_erp", "操作 ERP 业务"), ("manage_catalog", "管理商品门店与渠道")]


class Mutation(models.Model):
    key = models.UUIDField(default=uuid.uuid4, unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
