from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from .models import Store, Product, StockDocument, StockDocumentLine, StockMovement
from .access import require_operation, require_store
from . import services as svc


@transaction.atomic
def create_document(data, actor):
    require_operation(actor)
    kind = data.get("kind")
    if kind not in ("receive", "transfer", "count"):
        raise svc.BusinessError("库存单据类型无效")
    store = svc.lookup(Store, data.get("store_id"))
    require_store(actor, store.id)
    target = None
    if kind == "transfer":
        target = svc.lookup(Store, data.get("target_store_id"))
        require_store(actor, target.id)
        if target.id == store.id:
            raise svc.BusinessError("调出和调入门店不能相同")
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise svc.BusinessError("单据需包含 1 到 100 行商品")
    prepared, seen, versions = [], set(), {}
    for item in items:
        if not isinstance(item, dict):
            raise svc.BusinessError("商品行格式错误")
        product = svc.lookup(Product, item.get("product_id"))
        if product.id in seen:
            raise svc.BusinessError("同一商品请合并为一行")
        seen.add(product.id)
        quantity = svc.integer(item.get("quantity"), minimum=0 if kind == "count" else 1)
        if kind == "count" and "expected_version" in item:
            versions[product.id] = svc.integer(item["expected_version"], "库存版本", minimum=0, maximum=2000000000)
        prepared.append((product, quantity))
    balances = {p.id: svc.lock_inventory(store, p.id) for p, _ in sorted(prepared, key=lambda v: v[0].id)}
    for product_id, expected in versions.items():
        if balances[product_id].version != expected:
            raise svc.BusinessError("清点期间库存发生变化，请刷新并重新盘点", "stale_count", 409)
    doc = StockDocument.objects.create(number="ST" + timezone.localtime().strftime("%y%m%d") + uuid4().hex[:10].upper(),
        kind=kind, store=store, target_store=target, created_by=actor,
        source=svc.text(data.get("source", ""), "来源单号", 120, required=False),
        note=svc.text(data.get("note", ""), "备注", 240, required=False))
    for product, quantity in prepared:
        balance = balances[product.id]
        StockDocumentLine.objects.create(document=doc, product=product, product_name=product.name, sku=product.sku,
            quantity=quantity, snapshot_quantity=balance.on_hand, snapshot_reserved=balance.reserved, inventory_version=balance.version)
    svc.audit(actor, "stock.document.create", doc.number, {"kind": kind, "store": store.name})
    return {"message": "库存单据草稿已创建", "id": doc.id, "number": doc.number}


def locked_document(document_id, actor):
    require_operation(actor)
    try:
        doc = StockDocument.objects.select_for_update().get(pk=document_id)
    except StockDocument.DoesNotExist:
        raise svc.BusinessError("库存单据不存在", "not_found", 404)
    require_store(actor, doc.store_id)
    if doc.target_store_id:
        require_store(actor, doc.target_store_id)
    return doc


def balances_for(doc):
    pairs = [(doc.store_id, line.product_id) for line in doc.lines.all()]
    if doc.target_store_id:
        pairs += [(doc.target_store_id, line.product_id) for line in doc.lines.all()]
    return {(store_id, product_id): svc.lock_inventory(Store.objects.get(pk=store_id), product_id)
            for store_id, product_id in sorted(pairs)}


@transaction.atomic
def submit_document(document_id, data, actor):
    doc = locked_document(document_id, actor)
    if doc.kind != "count" or doc.status != "draft":
        raise svc.BusinessError("只有盘点草稿可以提交审批", "invalid_state", 409)
    balances = balances_for(doc)
    for line in doc.lines.all():
        if balances[(doc.store_id, line.product_id)].version != line.inventory_version:
            raise svc.BusinessError("盘点期间库存已变化，请撤销并重新盘点", "stale_count", 409)
    doc.status = "submitted"
    doc.save(update_fields=["status"])
    svc.audit(actor, "stock.document.submit", doc.number)
    return {"message": "盘点单已提交，等待其他审批人确认差异"}


@transaction.atomic
def post_document(document_id, data, actor):
    doc = locked_document(document_id, actor)
    if doc.kind == "count":
        require_operation(actor, "core.approve_stock")
        if doc.status != "submitted" or doc.created_by_id == actor.id:
            raise svc.BusinessError("盘点需提交后由另一名审批人确认", "approval_required", 409)
    elif doc.status != "draft":
        raise svc.BusinessError("只有草稿可以过账", "invalid_state", 409)
    balances = balances_for(doc)
    for line in doc.lines.order_by("product_id"):
        balance = balances[(doc.store_id, line.product_id)]
        if doc.kind == "count":
            if balance.version != line.inventory_version:
                raise svc.BusinessError("库存已发生变化，请重新盘点，不能覆盖最新库存", "stale_count", 409)
            svc.move(balance, actor, "count", line.quantity - balance.on_hand, document=doc, note=doc.note)
        elif doc.kind == "receive":
            svc.move(balance, actor, "receive", line.quantity, document=doc, note=doc.source)
        else:
            if balance.available < line.quantity:
                raise svc.BusinessError("调出门店可用库存不足", "insufficient_stock", 409)
            svc.move(balance, actor, "transfer_out", -line.quantity, document=doc)
            svc.move(balances[(doc.target_store_id, line.product_id)], actor, "transfer_in", line.quantity, document=doc)
    doc.status, doc.posted_at = "posted", timezone.now()
    if doc.kind == "count":
        doc.approved_by = actor
        doc.approval_note = svc.text(data.get("note", ""), "审批说明", 240, required=False)
    doc.save(update_fields=["status", "posted_at", "approved_by", "approval_note"])
    svc.audit(actor, "stock.document.post", doc.number, {"kind": doc.kind})
    return {"message": "库存单据已过账，库存和流水已同步更新"}


@transaction.atomic
def cancel_document(document_id, data, actor):
    doc = locked_document(document_id, actor)
    if doc.status == "canceled":
        raise svc.BusinessError("单据已撤销", "invalid_state", 409)
    if doc.status == "posted":
        if doc.kind == "count":
            raise svc.BusinessError("已审批盘点需新建盘点单修正", "invalid_state", 409)
        balances = balances_for(doc)
        entries = list(doc.movements.order_by("id"))
        for entry in entries:
            latest = StockMovement.objects.filter(store_id=entry.store_id, product_id=entry.product_id).order_by("-id").first()
            if not latest or latest.id != entry.id:
                raise svc.BusinessError("该商品库存已有后续业务，不能撤销此单", "downstream_exists", 409)
        for entry in entries:
            svc.move(balances[(entry.store_id, entry.product_id)], actor, "reversal", -entry.quantity_delta,
                     -entry.reserved_delta, document=doc, note="撤销 " + doc.number)
    doc.status, doc.canceled_at = "canceled", timezone.now()
    doc.save(update_fields=["status", "canceled_at"])
    svc.audit(actor, "stock.document.cancel", doc.number)
    return {"message": "单据已撤销，原记录和冲销流水均保留"}
