from datetime import date, timedelta
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q, F, Sum
from django.utils import timezone
from .models import Product, Store, Channel, Inventory, Order, Payment, StockMovement, StockDocument, AuditEvent
from .access import store_ids, require_store
from . import services as svc


def amount(value):
    return format(value or Decimal("0.00"), ".2f")


def filtered(actor, resource, params):
    allowed = list(store_ids(actor))
    if resource == "orders":
        qs = Order.objects.filter(store_id__in=allowed).select_related("channel", "store").prefetch_related("lines", "payments")
        search = ["number", "external_id", "customer", "channel__name", "lines__sku", "lines__product_name"]
    elif resource == "inventory":
        qs = Inventory.objects.filter(store_id__in=allowed).select_related("product", "store").order_by("product__sku", "store_id")
        search = ["product__sku", "product__name", "store__name"]
    elif resource == "payments":
        qs = Payment.objects.filter(order__store_id__in=allowed).select_related("order", "order__channel", "actor").order_by("-created_at", "-id")
        search = ["order__number", "reference", "order__channel__name"]
    elif resource == "movements":
        qs = StockMovement.objects.filter(store_id__in=allowed).select_related("product", "store", "order", "document", "actor")
        search = ["product__sku", "product__name", "store__name", "order__number", "document__number", "note"]
    elif resource == "documents":
        qs = StockDocument.objects.filter(store_id__in=allowed).filter(Q(target_store__isnull=True) | Q(target_store_id__in=allowed))
        qs = qs.select_related("store", "target_store", "created_by", "approved_by").prefetch_related("lines")
        search = ["number", "source", "note", "lines__sku", "lines__product_name"]
    elif resource == "audit" and actor.is_superuser:
        qs, search = AuditEvent.objects.select_related("actor"), ["action", "object_ref", "actor__username"]
    else:
        raise svc.BusinessError("列表类型不存在或没有权限", "forbidden", 403)
    store = params.get("store")
    if store:
        selected = svc.integer(store, "门店编号")
        require_store(actor, selected)
        if resource == "payments":
            qs = qs.filter(order__store_id=selected)
        elif resource == "documents":
            qs = qs.filter(Q(store_id=selected) | Q(target_store_id=selected))
        elif resource != "audit":
            qs = qs.filter(store_id=selected)
    query = str(params.get("q", "")).strip()[:120]
    if query:
        conditions = Q()
        for field in search:
            conditions |= Q(**{field + "__icontains": query})
        qs = qs.filter(conditions).distinct()
    status = params.get("status", "all")
    if status and status != "all":
        if resource in ("orders", "documents"):
            qs = qs.filter(status=status)
        elif resource == "inventory" and status == "low":
            qs = qs.filter(on_hand__lte=F("reserved") + F("product__reorder_level"))
        elif resource == "payments":
            qs = qs.filter(kind=status)
    kind = params.get("kind")
    if kind and resource == "documents":
        qs = qs.filter(kind=kind)
    channel = params.get("channel")
    if channel and resource in ("orders", "payments"):
        qs = qs.filter(**{("channel_id" if resource == "orders" else "order__channel_id"): svc.integer(channel, "渠道编号")})
    dates = {}
    for key in ("date_from", "date_to"):
        if params.get(key):
            try:
                dates[key] = date.fromisoformat(params[key])
            except (ValueError, TypeError):
                raise svc.BusinessError("日期格式须为 YYYY-MM-DD")
    if len(dates) == 2 and dates["date_from"] > dates["date_to"]:
        raise svc.BusinessError("开始日期不能晚于结束日期")
    if resource != "inventory":
        if "date_from" in dates:
            qs = qs.filter(created_at__date__gte=dates["date_from"])
        if "date_to" in dates:
            qs = qs.filter(created_at__date__lte=dates["date_to"])
    return qs


def serialize(resource, obj):
    if resource == "orders":
        return {"id": obj.id, "number": obj.number, "external_id": obj.external_id, "customer": obj.customer,
            "channel": obj.channel.name, "channel_id": obj.channel_id, "store": obj.store.name, "store_id": obj.store_id,
            "status": obj.status, "status_label": obj.get_status_display(), "total": amount(obj.total),
            "refunded_total": amount(obj.refunded_total), "paid": bool(obj.paid_at), "created_at": obj.created_at.isoformat(),
            "shipped_at": obj.shipped_at.isoformat() if obj.shipped_at else None,
            "items": [{"id": l.id, "product_id": l.product_id, "name": l.product_name, "sku": l.sku,
                "quantity": l.quantity, "returned_quantity": l.returned_quantity, "unit_price": amount(l.unit_price)} for l in obj.lines.all()],
            "payments": [{"kind": p.kind, "amount": amount(p.amount), "reference": p.reference} for p in obj.payments.all()]}
    if resource == "inventory":
        return {"id": obj.id, "product_id": obj.product_id, "sku": obj.product.sku, "name": obj.product.name,
            "category": obj.product.category, "store_id": obj.store_id, "store": obj.store.name, "on_hand": obj.on_hand,
            "reserved": obj.reserved, "available": obj.available, "reorder_level": obj.product.reorder_level, "version": obj.version}
    if resource == "payments":
        return {"id": obj.id, "number": obj.order.number, "channel": obj.order.channel.name, "kind": obj.kind,
            "amount": amount(obj.amount), "reference": obj.reference, "actor": obj.actor.username, "created_at": obj.created_at.isoformat()}
    if resource == "movements":
        return {"id": obj.id, "sku": obj.product.sku, "name": obj.product.name, "store": obj.store.name,
            "kind": obj.get_kind_display(), "delta": obj.quantity_delta, "reserved_delta": obj.reserved_delta,
            "on_hand_after": obj.on_hand_after, "reserved_after": obj.reserved_after,
            "number": obj.order.number if obj.order else obj.document.number if obj.document else "历史记录",
            "note": obj.note, "actor": obj.actor.username, "created_at": obj.created_at.isoformat()}
    if resource == "documents":
        return {"id": obj.id, "number": obj.number, "kind": obj.kind, "kind_label": obj.get_kind_display(),
            "status": obj.status, "status_label": obj.get_status_display(), "store_id": obj.store_id, "store": obj.store.name,
            "target_store_id": obj.target_store_id, "target_store": obj.target_store.name if obj.target_store else "",
            "source": obj.source, "note": obj.note, "created_by": obj.created_by.username, "created_by_id": obj.created_by_id,
            "approved_by": obj.approved_by.username if obj.approved_by else "", "approval_note": obj.approval_note,
            "created_at": obj.created_at.isoformat(), "posted_at": obj.posted_at.isoformat() if obj.posted_at else None,
            "items": [{"id": l.id, "product_id": l.product_id, "name": l.product_name, "sku": l.sku,
                       "quantity": l.quantity, "snapshot_quantity": l.snapshot_quantity, "snapshot_reserved": l.snapshot_reserved,
                       "difference": l.quantity-l.snapshot_quantity} for l in obj.lines.all()]}
    return {"action": obj.action, "object_ref": obj.object_ref, "actor": obj.actor.username,
            "detail": obj.detail, "created_at": obj.created_at.isoformat()}


def listing(actor, resource, params):
    qs = filtered(actor, resource, params)
    page_size = svc.integer(params.get("page_size", 20), "每页条数", maximum=100)
    page_number = svc.integer(params.get("page", 1), "页码", maximum=100000000)
    paginator = Paginator(qs, page_size)
    page = paginator.get_page(page_number)
    return {"results": [serialize(resource, row) for row in page.object_list], "page": page.number,
            "page_size": page_size, "pages": paginator.num_pages, "count": paginator.count}


def dashboard(actor, params):
    inventories = filtered(actor, "inventory", params)
    orders = filtered(actor, "orders", params)
    payments = filtered(actor, "payments", params)
    today = timezone.localdate()

    def totals(qs):
        received = qs.filter(kind="receipt").aggregate(v=Sum("amount"))["v"] or Decimal(0)
        refunded = qs.filter(kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal(0)
        return received, refunded

    received, refunded = totals(payments.filter(created_at__date=today))
    trend = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        r, f = totals(payments.filter(created_at__date=day))
        trend.append({"date": day.isoformat(), "received": amount(r), "refunded": amount(f), "net": amount(r-f)})
    channels = []
    for channel in Channel.objects.all().order_by("id"):
        r, f = totals(payments.filter(order__channel=channel))
        channels.append({"id": channel.id, "code": channel.code, "name": channel.name, "kind": channel.kind,
            "orders": orders.filter(channel=channel).count(), "net": amount(r-f)})
    availability = dict(inventories.order_by().values("product_id").annotate(v=Sum(F("on_hand")-F("reserved"))).values_list("product_id", "v"))
    products = [{**p, "available": availability.get(p["id"], 0)} for p in Product.objects.order_by("sku").values("id", "sku", "name", "category", "price", "reorder_level")]
    return {"user": {"id": actor.id, "name": actor.get_full_name() or actor.username, "username": actor.username,
        "can_operate": actor.has_perm("core.operate_erp"), "can_manage": actor.has_perm("core.manage_catalog"),
        "can_approve": actor.has_perm("core.approve_stock"), "is_admin": actor.is_superuser},
        "version": "0.2.0", "today": today.isoformat(), "selected_store": params.get("store") or None,
        "products": products, "stores": list(Store.objects.filter(id__in=store_ids(actor)).order_by("id").values("id", "name", "code", "location")),
        "channels": channels, "trend": trend,
        "stats": {"today_net": amount(received-refunded), "today_received": amount(received), "today_refunded": amount(refunded),
                  "pending": orders.filter(status="pending").count(), "total_orders": orders.count(),
                  "low_stock": inventories.filter(on_hand__lte=F("reserved")+F("product__reorder_level")).count(),
                  "available": sum(availability.values()),
                  "awaiting_approval": filtered(actor, "documents", params).filter(status="submitted").count()},
        **{resource: [serialize(resource, row) for row in filtered(actor, resource, params)[:20]]
           for resource in ("inventory", "orders", "payments", "movements", "documents")},
        "audit": [serialize("audit", row) for row in filtered(actor, "audit", params)[:20]] if actor.is_superuser else [],
        "limits": {"orders": 20, "movements": 20, "payments": 20, "audit": 20}}


def export_rows(actor, resource, params):
    qs = filtered(actor, resource, params)
    if resource == "orders":
        yield ["订单号", "渠道订单号", "渠道", "门店", "客户", "状态", "订单金额", "已退款", "收款状态", "创建时间"]
        for o in qs:
            yield [o.number, o.external_id, o.channel.name, o.store.name, o.customer, o.get_status_display(), o.total,
                   o.refunded_total, "已收款" if o.paid_at else "未收款", timezone.localtime(o.created_at).isoformat()]
    elif resource == "inventory":
        yield ["SKU", "商品", "门店", "实物库存", "占用", "可用"]
        for i in qs:
            yield [i.product.sku, i.product.name, i.store.name, i.on_hand, i.reserved, i.available]
    elif resource == "payments":
        yield ["订单号", "类型", "金额", "凭证", "操作人", "登记时间"]
        for p in qs:
            yield [p.order.number, p.get_kind_display(), p.amount, p.reference, p.actor.username, timezone.localtime(p.created_at).isoformat()]
    elif resource == "documents":
        yield ["库存单号", "类型", "状态", "门店", "调入门店", "来源单号", "创建人", "审批人"]
        for d in qs:
            yield [d.number, d.get_kind_display(), d.get_status_display(), d.store.name,
                   d.target_store.name if d.target_store else "", d.source, d.created_by.username, d.approved_by.username if d.approved_by else ""]
    else:
        raise svc.BusinessError("不支持此导出类型")
