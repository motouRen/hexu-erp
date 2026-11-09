from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from .models import Product, Store, Channel, Inventory, Order, OrderLine, StockMovement, Payment, ReturnLine, AuditEvent


class BusinessError(Exception):
    def __init__(self, message, code="validation_error", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def text(value, label, maximum, required=True):
    if not isinstance(value, str):
        raise BusinessError(f"{label}必须是文本")
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise BusinessError(f"{label}不能为空且不能超过 {maximum} 个字符" if required else f"{label}不能超过 {maximum} 个字符")
    return value


def integer(value, label="数量", minimum=1, maximum=1000000):
    if isinstance(value, bool) or len(str(value)) > 10 or not str(value).isascii() or not str(value).isdigit():
        raise BusinessError(f"{label}必须是整数")
    number = int(value)
    if not minimum <= number <= maximum:
        raise BusinessError(f"{label}须在 {minimum} 到 {maximum} 之间")
    return number


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
            raise BusinessError("单价超出范围")
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if rounded != amount:
            raise BusinessError("单价最多保留两位小数")
        return rounded
    except (InvalidOperation, ValueError):
        raise BusinessError("请输入有效金额")


def lookup(model, object_id):
    try:
        return model.objects.get(pk=integer(object_id, "记录编号"))
    except model.DoesNotExist:
        raise BusinessError("所选记录不存在", "not_found", 404)


def audit(actor, action, ref, detail=None):
    AuditEvent.objects.create(actor=actor, action=action, object_ref=str(ref), detail=detail or {})


def lock_inventory(store, product_id):
    Inventory.objects.get_or_create(store=store, product_id=product_id)
    return Inventory.objects.select_for_update().get(store=store, product_id=product_id)


def move(balance, actor, kind, quantity_delta, reserved_delta=0, order=None, note=""):
    balance.on_hand += quantity_delta
    balance.reserved += reserved_delta
    if balance.reserved < 0 or balance.on_hand < balance.reserved:
        raise BusinessError("可用库存不足，请减少数量或先补货", "insufficient_stock", 409)
    if balance.on_hand > 2000000000:
        raise BusinessError("库存数量超出范围")
    balance.save(update_fields=["on_hand", "reserved"])
    StockMovement.objects.create(product=balance.product, store=balance.store, actor=actor,
        kind=kind, quantity_delta=quantity_delta, reserved_delta=reserved_delta, order=order,
        note=note, on_hand_after=balance.on_hand, reserved_after=balance.reserved)


@transaction.atomic
def receive_stock(data, actor):
    product, store = lookup(Product, data.get("product_id")), lookup(Store, data.get("store_id"))
    quantity = integer(data.get("quantity"))
    note = text(data.get("note", ""), "入库备注", 240, required=False)
    balance = lock_inventory(store, product.id)
    if balance.on_hand + quantity > 2000000000:
        raise BusinessError("库存数量超出范围")
    move(balance, actor, "receive", quantity, note=note)
    audit(actor, "stock.receive", product.sku, {"store": store.name, "quantity": quantity, "note": note})
    return {"message": "补货已入库", "product_id": product.id}


@transaction.atomic
def create_order(data, actor):
    store, channel = lookup(Store, data.get("store_id")), lookup(Channel, data.get("channel_id"))
    customer = text(data.get("customer", ""), "客户名称", 80)
    external_id = text(data.get("external_id", ""), "渠道订单号", 80, required=False)
    if external_id and Order.objects.filter(channel=channel, external_id=external_id).exists():
        raise BusinessError("该渠道订单号已经存在", "duplicate_external_order", 409)
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise BusinessError("每张订单需包含 1 到 100 行商品")
    prepared, seen, total = [], set(), Decimal("0.00")
    for item in items:
        if not isinstance(item, dict):
            raise BusinessError("订单商品格式错误")
        product = lookup(Product, item.get("product_id"))
        if product.id in seen:
            raise BusinessError("同一商品请合并为一行")
        seen.add(product.id)
        quantity = integer(item.get("quantity"))
        price = money(item.get("unit_price", str(product.price)))
        total += price * quantity
        prepared.append((product, quantity, price))
    if total > Decimal("99999999999999.99"):
        raise BusinessError("订单总金额超出范围")
    balances = {p.id: lock_inventory(store, p.id) for p, _, _ in sorted(prepared, key=lambda line: line[0].id)}
    for product, quantity, _ in prepared:
        if balances[product.id].available < quantity:
            raise BusinessError(f"{product.name} 可用库存不足（当前 {balances[product.id].available}）", "insufficient_stock", 409)
    order = Order.objects.create(number="HX" + timezone.localtime().strftime("%y%m%d") + uuid4().hex[:12].upper(),
        channel=channel, store=store, customer=customer, external_id=external_id, total=total, actor=actor)
    for product, quantity, price in prepared:
        OrderLine.objects.create(order=order, product=product, product_name=product.name, sku=product.sku,
            quantity=quantity, unit_price=price)
        move(balances[product.id], actor, "reserve", 0, quantity, order=order)
    audit(actor, "order.create", order.number, {"amount": str(total), "channel": channel.name})
    return {"message": "订单已创建，库存已占用", "id": order.id, "number": order.number}


def locked_order(order_id):
    try:
        return Order.objects.select_for_update().get(pk=order_id)
    except Order.DoesNotExist:
        raise BusinessError("订单不存在", "not_found", 404)


@transaction.atomic
def pay_order(order_id, data, actor):
    order = locked_order(order_id)
    if order.paid_at or order.status != "pending":
        raise BusinessError("此订单不能再次登记收款", "invalid_state", 409)
    reference = text(data.get("reference", ""), "收款凭证", 120)
    Payment.objects.create(order=order, kind="receipt", amount=order.total, reference=reference, actor=actor)
    order.paid_at = timezone.now()
    order.save(update_fields=["paid_at"])
    audit(actor, "order.pay", order.number, {"amount": str(order.total), "reference": reference})
    return {"message": "已登记全额收款"}


@transaction.atomic
def ship_order(order_id, data, actor):
    order = locked_order(order_id)
    if order.status != "pending" or not order.paid_at:
        raise BusinessError("只有已登记收款的待发货订单才能发货", "invalid_state", 409)
    lines = list(order.lines.select_related("product").order_by("product_id"))
    for line in lines:
        balance = lock_inventory(order.store, line.product_id)
        move(balance, actor, "ship", -line.quantity, -line.quantity, order=order)
    order.status, order.shipped_at = "shipped", timezone.now()
    order.save(update_fields=["status", "shipped_at"])
    audit(actor, "order.ship", order.number)
    return {"message": "已发货，实物库存已扣减"}


@transaction.atomic
def cancel_order(order_id, data, actor):
    order = locked_order(order_id)
    if order.status != "pending" or order.paid_at:
        raise BusinessError("仅未收款待发货订单可取消；已收款请登记退款", "invalid_state", 409)
    for line in order.lines.order_by("product_id"):
        move(lock_inventory(order.store, line.product_id), actor, "release", 0, -line.quantity, order=order)
    order.status = "canceled"
    order.save(update_fields=["status"])
    audit(actor, "order.cancel", order.number)
    return {"message": "订单已取消，库存占用已释放"}


@transaction.atomic
def refund_order(order_id, data, actor):
    order = locked_order(order_id)
    if not order.paid_at or order.status not in ["pending", "shipped", "partial_return"]:
        raise BusinessError("此订单不能登记退款", "invalid_state", 409)
    reference = text(data.get("reference", ""), "退款凭证", 120)
    if order.status == "pending":
        for line in order.lines.order_by("product_id"):
            move(lock_inventory(order.store, line.product_id), actor, "release", 0, -line.quantity, order=order)
        refund_amount = order.total
        payment = Payment.objects.create(order=order, kind="refund", amount=refund_amount, reference=reference, actor=actor)
        order.status = "canceled"
    else:
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise BusinessError("请选择退货商品及数量")
        prepared, seen, refund_amount = [], set(), Decimal("0.00")
        for item in items:
            if not isinstance(item, dict):
                raise BusinessError("退货商品格式错误")
            line_id = integer(item.get("line_id"), "订单行编号")
            if line_id in seen:
                raise BusinessError("退货商品行不能重复")
            seen.add(line_id)
            try:
                line = order.lines.get(id=line_id)
            except OrderLine.DoesNotExist:
                raise BusinessError("退货商品不属于该订单")
            quantity = integer(item.get("quantity"))
            if quantity > line.quantity - line.returned_quantity:
                raise BusinessError("退货数量超过剩余可退数量", "return_exceeded", 409)
            restock = item.get("restock", True)
            if not isinstance(restock, bool):
                raise BusinessError("是否回库必须为布尔值")
            prepared.append((line, quantity, restock))
            refund_amount += quantity * line.unit_price
        payment = Payment.objects.create(order=order, kind="refund", amount=refund_amount, reference=reference, actor=actor)
        for line, quantity, restock in sorted(prepared, key=lambda item: item[0].product_id):
            if restock:
                move(lock_inventory(order.store, line.product_id), actor, "return", quantity, order=order)
            line.returned_quantity += quantity
            line.save(update_fields=["returned_quantity"])
            ReturnLine.objects.create(payment=payment, order_line=line, quantity=quantity, restock=restock)
        order.status = "returned" if all(l.returned_quantity == l.quantity for l in order.lines.all()) else "partial_return"
    order.refunded_total += refund_amount
    order.save(update_fields=["status", "refunded_total"])
    audit(actor, "order.refund", order.number, {"amount": str(refund_amount), "reference": reference, "payment_id": payment.id})
    return {"message": "退货退款已登记", "refund_amount": str(refund_amount)}
