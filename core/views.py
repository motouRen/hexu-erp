import csv
import hashlib
import io
import json
import uuid
from functools import wraps
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Sum, F
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import Product, Store, Channel, Inventory, Order, StockMovement, Payment, AuditEvent, Mutation
from . import services as svc


def api_guard(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "请先登录", "code": "unauthenticated"}, status=401)
        try:
            return view(request, *args, **kwargs)
        except svc.BusinessError as error:
            return JsonResponse({"error": str(error), "code": error.code}, status=error.status)
        except csv.Error:
            return JsonResponse({"error": "CSV 格式不正确，请检查引号和分隔符", "code": "validation_error"}, status=400)
        except IntegrityError:
            return JsonResponse({"error": "记录已存在或业务约束冲突，请刷新后重试", "code": "conflict"}, status=409)
    return wrapped


def mutation(permission="core.operate_erp"):
    def decorate(view):
        @api_guard
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != "POST":
                return JsonResponse({"error": "仅支持 POST"}, status=405)
            if not request.user.has_perm(permission):
                raise svc.BusinessError("当前账号没有此操作权限", "forbidden", 403)
            try:
                key = uuid.UUID(request.headers.get("Idempotency-Key", ""))
            except ValueError:
                raise svc.BusinessError("请求缺少有效的 Idempotency-Key")
            try:
                data = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                raise svc.BusinessError("请求须为有效 JSON")
            if not isinstance(data, dict):
                raise svc.BusinessError("请求内容须为对象")
            fingerprint = hashlib.sha256((request.path + json.dumps(data, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
            with transaction.atomic():
                record, created = Mutation.objects.get_or_create(key=key, defaults={"actor": request.user, "fingerprint": fingerprint})
                record = Mutation.objects.select_for_update().get(pk=record.pk)
                if not created:
                    if record.actor_id != request.user.id or record.fingerprint != fingerprint:
                        raise svc.BusinessError("请求编号已经用于另一项操作", "idempotency_conflict", 409)
                    return JsonResponse(record.response)
                result = view(request, data, *args, **kwargs)
                record.response = result
                record.save(update_fields=["response"])
            return JsonResponse(result)
        return wrapped
    return decorate


@login_required
def index(request):
    return render(request, "index.html", {"can_operate": request.user.has_perm("core.operate_erp"),
        "can_manage": request.user.has_perm("core.manage_catalog")})


def amount(value):
    return str(value or Decimal("0.00"))


@api_guard
@require_GET
def state(request):
    store_filter = request.GET.get("store")
    selected_store = svc.integer(store_filter, "门店编号") if store_filter else None
    inventories = Inventory.objects.select_related("product", "store")
    orders_qs = Order.objects.select_related("channel", "store").prefetch_related("lines", "payments")
    movements = StockMovement.objects.select_related("product", "store", "order", "actor")
    payments = Payment.objects.select_related("order", "actor", "order__channel")
    if selected_store:
        inventories = inventories.filter(store_id=selected_store)
        orders_qs = orders_qs.filter(store_id=selected_store)
        movements = movements.filter(store_id=selected_store)
        payments = payments.filter(order__store_id=selected_store)
    today = timezone.localdate()
    today_payments = payments.filter(created_at__date=today)
    received = today_payments.filter(kind="receipt").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    refunded = today_payments.filter(kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    trend = []
    for offset in range(6, -1, -1):
        date = today - timezone.timedelta(days=offset)
        entries = payments.filter(created_at__date=date)
        r = entries.filter(kind="receipt").aggregate(v=Sum("amount"))["v"] or Decimal("0")
        f = entries.filter(kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal("0")
        trend.append({"date": date.isoformat(), "received": amount(r), "refunded": amount(f), "net": amount(r-f)})
    channel_stats = []
    for channel in Channel.objects.all().order_by("id"):
        channel_payments = payments.filter(order__channel=channel)
        r = channel_payments.filter(kind="receipt").aggregate(v=Sum("amount"))["v"] or Decimal("0")
        f = channel_payments.filter(kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal("0")
        channel_stats.append({"id": channel.id, "code": channel.code, "name": channel.name, "kind": channel.kind,
            "orders": orders_qs.filter(channel=channel).count(), "net": amount(r-f)})
    return JsonResponse({
        "user": {"name": request.user.get_full_name() or request.user.username,
            "can_operate": request.user.has_perm("core.operate_erp"), "can_manage": request.user.has_perm("core.manage_catalog")},
        "today": today.isoformat(), "selected_store": selected_store,
        "products": list(Product.objects.order_by("sku").values("id", "sku", "name", "category", "price", "reorder_level")),
        "stores": list(Store.objects.order_by("id").values("id", "name", "code", "location")),
        "channels": channel_stats,
        "inventory": [{"id": row.id, "product_id": row.product_id, "sku": row.product.sku, "name": row.product.name,
            "category": row.product.category, "store_id": row.store_id, "store": row.store.name, "on_hand": row.on_hand,
            "reserved": row.reserved, "available": row.available, "reorder_level": row.product.reorder_level} for row in inventories.order_by("product__sku", "store_id")],
        "orders": [{"id": order.id, "number": order.number, "external_id": order.external_id,
            "customer": order.customer, "channel": order.channel.name, "channel_id": order.channel_id,
            "store": order.store.name, "store_id": order.store_id, "status": order.status, "status_label": order.get_status_display(),
            "total": amount(order.total), "refunded_total": amount(order.refunded_total), "paid": bool(order.paid_at),
            "created_at": order.created_at.isoformat(), "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
            "items": [{"id": line.id, "product_id": line.product_id, "name": line.product_name, "sku": line.sku,
                "quantity": line.quantity, "returned_quantity": line.returned_quantity, "unit_price": amount(line.unit_price)} for line in order.lines.all()],
            "payments": [{"kind": p.kind, "amount": amount(p.amount), "reference": p.reference} for p in order.payments.all()]
        } for order in orders_qs[:200]],
        "movements": [{"id": m.id, "sku": m.product.sku, "name": m.product.name, "store": m.store.name,
            "kind": m.get_kind_display(), "delta": m.quantity_delta, "reserved_delta": m.reserved_delta,
            "on_hand_after": m.on_hand_after, "reserved_after": m.reserved_after, "number": m.order.number if m.order else "—",
            "note": m.note, "actor": m.actor.username, "created_at": m.created_at.isoformat()} for m in movements[:200]],
        "payments": [{"id": p.id, "number": p.order.number, "channel": p.order.channel.name, "kind": p.kind,
            "amount": amount(p.amount), "reference": p.reference, "actor": p.actor.username, "created_at": p.created_at.isoformat()}
            for p in payments.order_by("-created_at", "-id")[:200]],
        "stats": {"today_net": amount(received-refunded), "today_received": amount(received), "today_refunded": amount(refunded),
            "pending": orders_qs.filter(status="pending").count(), "low_stock": inventories.filter(on_hand__lte=F("reserved") + F("product__reorder_level")).count(),
            "available": sum(i.available for i in inventories), "total_orders": orders_qs.count()},
        "trend": trend,
        "audit": [{"action": a.action, "object_ref": a.object_ref, "actor": a.actor.username, "detail": a.detail,
            "created_at": a.created_at.isoformat()} for a in AuditEvent.objects.select_related("actor")[:100]] if request.user.has_perm("core.manage_catalog") else [],
        "limits": {"orders": 200, "movements": 200, "payments": 200, "audit": 100},
    })


@mutation("core.manage_catalog")
def products(request, data):
    product = Product.objects.create(sku=svc.text(data.get("sku", ""), "SKU", 48),
        name=svc.text(data.get("name", ""), "商品名称", 120), category=svc.text(data.get("category", "其他"), "分类", 48),
        price=svc.money(data.get("price")), reorder_level=svc.integer(data.get("reorder_level", 10), "预警阈值", minimum=0))
    for store in Store.objects.all():
        Inventory.objects.create(product=product, store=store)
    svc.audit(request.user, "product.create", product.sku)
    return {"message": "商品已创建", "id": product.id}


@mutation("core.manage_catalog")
def update_product(request, data, product_id):
    try:
        product = Product.objects.select_for_update().get(pk=product_id)
    except Product.DoesNotExist:
        raise svc.BusinessError("商品不存在", "not_found", 404)
    product.name = svc.text(data.get("name", ""), "商品名称", 120)
    product.category = svc.text(data.get("category", "其他"), "分类", 48)
    product.price = svc.money(data.get("price"))
    product.reorder_level = svc.integer(data.get("reorder_level", 10), "预警阈值", minimum=0)
    product.save(update_fields=["name", "category", "price", "reorder_level"])
    svc.audit(request.user, "product.update", product.sku, {"price": str(product.price)})
    return {"message": "商品资料已更新，历史订单保持原始快照", "id": product.id}


@mutation("core.manage_catalog")
def stores(request, data):
    store = Store.objects.create(code=svc.text(data.get("code", ""), "门店编码", 32),
        name=svc.text(data.get("name", ""), "门店名称", 80), location=svc.text(data.get("location", ""), "门店地址", 160, required=False))
    for product in Product.objects.all():
        Inventory.objects.create(product=product, store=store)
    svc.audit(request.user, "store.create", store.code)
    return {"message": "门店已创建", "id": store.id}


@mutation("core.manage_catalog")
def channels(request, data):
    kind = data.get("kind", "online")
    if kind not in ["online", "retail"]:
        raise svc.BusinessError("渠道类型无效")
    channel = Channel.objects.create(code=svc.text(data.get("code", ""), "渠道编码", 32), name=svc.text(data.get("name", ""), "渠道名称", 80), kind=kind)
    svc.audit(request.user, "channel.create", channel.code)
    return {"message": "渠道已创建", "id": channel.id}


@mutation()
def receive(request, data):
    return svc.receive_stock(data, request.user)


@mutation()
def orders(request, data):
    return svc.create_order(data, request.user)


@mutation()
def order_action(request, data, order_id, action):
    actions = {"pay": svc.pay_order, "ship": svc.ship_order, "cancel": svc.cancel_order, "refund": svc.refund_order}
    if action not in actions:
        raise svc.BusinessError("操作不存在", "not_found", 404)
    return actions[action](order_id, data, request.user)


@mutation()
def import_orders(request, data):
    content = data.get("csv", "")
    if not isinstance(content, str) or len(content) > 500000:
        raise svc.BusinessError("CSV 内容过大或格式错误")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
    required = {"external_id", "channel_code", "store_code", "customer", "sku", "quantity", "unit_price"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise svc.BusinessError("CSV 列不完整，请下载导入模板")
    grouped = {}
    for row_index, row in enumerate(reader, start=2):
        if row_index > 501:
            raise svc.BusinessError("每次最多导入 500 行商品")
        if any(row.get(k) is None for k in required) or None in row:
            raise svc.BusinessError(f"第 {row_index} 行列数不正确")
        key = (row["channel_code"].strip(), svc.text(row["external_id"], "渠道订单号", 80))
        try:
            channel = Channel.objects.get(code=key[0])
            store = Store.objects.get(code=row["store_code"].strip())
            product = Product.objects.get(sku=row["sku"].strip())
        except (Channel.DoesNotExist, Store.DoesNotExist, Product.DoesNotExist):
            raise svc.BusinessError(f"第 {row_index} 行的渠道、门店或 SKU 不存在")
        if key not in grouped:
            grouped[key] = {"channel_id": channel.id, "store_id": store.id, "external_id": key[1], "customer": row["customer"], "items": []}
        order = grouped[key]
        if order["store_id"] != store.id or order["customer"] != row["customer"]:
            raise svc.BusinessError(f"第 {row_index} 行的门店或客户与同单其他行不一致")
        order["items"].append({"product_id": product.id, "quantity": row["quantity"], "unit_price": row["unit_price"]})
    if not grouped or len(grouped) > 100:
        raise svc.BusinessError("每次需导入 1 到 100 张订单")
    ids = [svc.create_order(order, request.user)["id"] for order in grouped.values()]
    svc.audit(request.user, "order.import", "CSV", {"count": len(ids)})
    return {"message": f"已导入 {len(ids)} 张订单并占用库存", "ids": ids}


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


@api_guard
@require_GET
def export_csv(request, resource):
    if resource == "template":
        rows = [["external_id", "channel_code", "store_code", "customer", "sku", "quantity", "unit_price"]]
    elif resource == "orders":
        rows = [["订单号", "渠道订单号", "渠道", "门店", "客户", "状态", "订单金额", "已退款", "收款状态", "创建时间"]]
        for o in Order.objects.select_related("channel", "store").iterator():
            rows.append([o.number, o.external_id, o.channel.name, o.store.name, o.customer, o.get_status_display(), o.total,
                o.refunded_total, "已收款" if o.paid_at else "未收款", timezone.localtime(o.created_at).isoformat()])
    elif resource == "inventory":
        rows = [["SKU", "商品", "门店", "实物库存", "占用", "可用"]]
        for i in Inventory.objects.select_related("product", "store").order_by("product__sku", "store_id").iterator():
            rows.append([i.product.sku, i.product.name, i.store.name, i.on_hand, i.reserved, i.available])
    elif resource == "payments":
        rows = [["订单号", "类型", "金额", "凭证", "操作人", "登记时间"]]
        for p in Payment.objects.select_related("order", "actor").order_by("-created_at").iterator():
            rows.append([p.order.number, p.get_kind_display(), p.amount, p.reference, p.actor.username, timezone.localtime(p.created_at).isoformat()])
    else:
        raise svc.BusinessError("导出类型不存在", "not_found", 404)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows([[csv_safe(cell) for cell in row] for row in rows])
    response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="hexu-{resource}.csv"'
    return response


@require_GET
def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})
