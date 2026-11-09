import csv
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from .models import Product, Store, Channel, Inventory, Order, OrderLine, StockMovement, Payment, AuditEvent
from . import services as svc
from .models import StoreAccess


class WorkflowTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("owner", password="local-test-password-123")
        self.viewer = get_user_model().objects.create_user("viewer", password="local-test-password-123")
        self.operator = get_user_model().objects.create_user("operator", password="local-test-password-123")
        self.operator.user_permissions.add(Permission.objects.get(codename="operate_erp"))
        self.store = Store.objects.create(code="A", name="一号店")
        StoreAccess.objects.create(user=self.operator, store=self.store)
        StoreAccess.objects.create(user=self.viewer, store=self.store)
        self.channel = Channel.objects.create(code="WEB", name="线上渠道")
        self.product = Product.objects.create(sku="P1", name="马克杯", price="12.50")
        svc.receive_stock({"store_id": self.store.id, "product_id": self.product.id, "quantity": 10}, self.admin)
        self.client.force_login(self.admin)

    def payload(self, quantity=3, **extra):
        return {"channel_id": self.channel.id, "store_id": self.store.id, "customer": "测试顾客",
            "items": [{"product_id": self.product.id, "quantity": quantity, "unit_price": "12.50"}], **extra}

    def create(self, quantity=3):
        return svc.create_order(self.payload(quantity), self.admin)["id"]

    def balance(self):
        return Inventory.objects.get(store=self.store, product=self.product)

    def paid_shipped(self, quantity=3):
        order_id = self.create(quantity)
        svc.pay_order(order_id, {"reference": "PAY-1"}, self.admin)
        svc.ship_order(order_id, {}, self.admin)
        return Order.objects.get(id=order_id)

    def post(self, path, payload, key=None):
        return self.client.post(path, json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def test_create_reserves_without_reducing_physical_stock(self):
        self.create()
        balance = self.balance()
        self.assertEqual((balance.on_hand, balance.reserved, balance.available), (10, 3, 7))

    def test_insufficient_stock_rolls_back_entire_order(self):
        before = StockMovement.objects.count()
        with self.assertRaises(svc.BusinessError):
            self.create(11)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self.balance().reserved, 0)

    def test_multi_line_order_is_atomic(self):
        product = Product.objects.create(sku="P2", name="缺货商品", price="1.00")
        data = self.payload()
        data["items"].append({"product_id": product.id, "quantity": 1})
        with self.assertRaises(svc.BusinessError):
            svc.create_order(data, self.admin)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.balance().available, 10)

    def test_shipping_requires_payment(self):
        order_id = self.create()
        with self.assertRaises(svc.BusinessError):
            svc.ship_order(order_id, {}, self.admin)
        self.assertEqual(self.balance().on_hand, 10)

    def test_paid_ship_and_repeat_are_safe(self):
        order = self.paid_shipped()
        self.assertEqual((self.balance().on_hand, self.balance().reserved), (7, 0))
        self.assertEqual(Payment.objects.get().amount, Decimal("37.50"))
        with self.assertRaises(svc.BusinessError):
            svc.ship_order(order.id, {}, self.admin)
        self.assertEqual(self.balance().on_hand, 7)

    def test_cancel_releases_stock(self):
        order_id = self.create()
        svc.cancel_order(order_id, {}, self.admin)
        self.assertEqual((self.balance().on_hand, self.balance().reserved), (10, 0))
        self.assertEqual(Order.objects.get(id=order_id).status, "canceled")

    def test_paid_order_cannot_be_canceled_without_refund(self):
        order_id = self.create()
        svc.pay_order(order_id, {"reference": "PAY-1"}, self.admin)
        with self.assertRaises(svc.BusinessError):
            svc.cancel_order(order_id, {}, self.admin)
        self.assertEqual(self.balance().reserved, 3)

    def test_refund_before_shipping_releases_only_reservation(self):
        order_id = self.create()
        svc.pay_order(order_id, {"reference": "PAY-1"}, self.admin)
        result = svc.refund_order(order_id, {"reference": "REF-1"}, self.admin)
        self.assertEqual(result["refund_amount"], "37.50")
        self.assertEqual((self.balance().on_hand, self.balance().reserved), (10, 0))
        self.assertEqual(Order.objects.get(pk=order_id).status, "canceled")

    def test_partial_and_full_return_at_original_sale_price(self):
        order = self.paid_shipped()
        self.product.price = "99.00"
        self.product.save()
        line = order.lines.get()
        first = {"reference": "REF-1", "items": [{"line_id": line.id, "quantity": 1, "restock": True}]}
        self.assertEqual(svc.refund_order(order.id, first, self.admin)["refund_amount"], "12.50")
        self.assertEqual(Order.objects.get(pk=order.id).status, "partial_return")
        second = {"reference": "REF-2", "items": [{"line_id": line.id, "quantity": 2, "restock": True}]}
        svc.refund_order(order.id, second, self.admin)
        order.refresh_from_db()
        self.assertEqual((order.status, order.refunded_total), ("returned", Decimal("37.50")))
        self.assertEqual(self.balance().on_hand, 10)

    def test_damaged_return_can_skip_restock(self):
        order = self.paid_shipped()
        svc.refund_order(order.id, {"reference": "REF-DAMAGED", "items": [
            {"line_id": order.lines.get().id, "quantity": 3, "restock": False}]}, self.admin)
        self.assertEqual(self.balance().on_hand, 7)
        self.assertFalse(order.payments.get(kind="refund").return_lines.get().restock)

    def test_excess_return_has_no_financial_or_stock_side_effect(self):
        order = self.paid_shipped()
        before = StockMovement.objects.count()
        with self.assertRaises(svc.BusinessError):
            svc.refund_order(order.id, {"reference": "REF-1", "items": [
                {"line_id": order.lines.get().id, "quantity": 4}]}, self.admin)
        self.assertFalse(Payment.objects.filter(kind="refund").exists())
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self.balance().on_hand, 7)

    def test_invalid_quantities_are_rejected(self):
        for value in [-1, 0, True, "1.5", "9" * 5000, None, "NaN"]:
            with self.subTest(value=str(value)[:20]), self.assertRaises(svc.BusinessError):
                self.create(value)

    def test_invalid_prices_are_rejected(self):
        for value in ["NaN", "Infinity", "-1", "0.001", "10000000000", None, ""]:
            with self.subTest(value=value), self.assertRaises(svc.BusinessError):
                svc.money(value)

    def test_product_price_update_does_not_change_order_snapshot(self):
        order_id = self.create()
        response = self.post(f"/api/products/{self.product.id}/update/", {
            "name": "新版马克杯", "category": "器皿", "price": "19.90", "reorder_level": 7})
        self.assertEqual(response.status_code, 200)
        line = OrderLine.objects.get(order_id=order_id)
        self.assertEqual((line.product_name, line.unit_price), ("马克杯", Decimal("12.50")))

    def test_api_requires_login_and_viewer_cannot_mutate(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/state/").status_code, 401)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/api/state/").status_code, 200)
        self.assertEqual(self.post("/api/orders/", self.payload()).status_code, 403)

    def test_operator_can_order_but_cannot_manage_catalog(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.post("/api/orders/", self.payload()).status_code, 200)
        self.assertEqual(self.post("/api/products/", {"sku": "P3", "name": "越权", "price": "1"}).status_code, 403)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post("/api/orders/", json.dumps(self.payload()), content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 403)

    def test_idempotency_replays_result_without_reserving_twice(self):
        key = str(uuid.uuid4())
        first = self.post("/api/orders/", self.payload(), key)
        second = self.post("/api/orders/", self.payload(), key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.balance().reserved, 3)

    def test_key_cannot_be_reused_for_different_payload(self):
        key = str(uuid.uuid4())
        self.post("/api/orders/", self.payload(), key)
        self.assertEqual(self.post("/api/orders/", self.payload(2), key).status_code, 409)
        self.assertEqual(self.balance().reserved, 3)

    def test_duplicate_external_order_is_rejected(self):
        payload = self.payload(external_id="PLATFORM-001")
        self.assertEqual(self.post("/api/orders/", payload).status_code, 200)
        self.assertEqual(self.post("/api/orders/", payload).status_code, 409)

    def test_duplicate_refund_request_is_idempotent(self):
        order = self.paid_shipped()
        payload = {"reference": "REF-1", "items": [{"line_id": order.lines.get().id, "quantity": 1}]}
        key = str(uuid.uuid4())
        self.post(f"/api/orders/{order.id}/refund/", payload, key)
        self.post(f"/api/orders/{order.id}/refund/", payload, key)
        self.assertEqual(Payment.objects.filter(kind="refund").count(), 1)
        self.assertEqual(self.balance().on_hand, 8)

    def test_csv_import_rolls_back_whole_batch_when_one_order_fails(self):
        header = "external_id,channel_code,store_code,customer,sku,quantity,unit_price\n"
        data = header + "A1,WEB,A,客户,P1,1,12.50\nA2,WEB,A,客户,P1,99,12.50\n"
        response = self.post("/api/orders/import/", {"csv": data})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.balance().reserved, 0)

    def test_csv_import_combines_lines_by_external_order(self):
        other = Product.objects.create(sku="P2", name="小碟", price="2.50")
        svc.receive_stock({"store_id": self.store.id, "product_id": other.id, "quantity": 10}, self.admin)
        data = "external_id,channel_code,store_code,customer,sku,quantity,unit_price\nA1,WEB,A,客户,P1,1,12.50\nA1,WEB,A,客户,P2,2,2.50\n"
        response = self.post("/api/orders/import/", {"csv": data})
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get()
        self.assertEqual((order.lines.count(), order.total), (2, Decimal("17.50")))

    def test_malformed_csv_returns_validation_error(self):
        data = 'external_id,channel_code,store_code,customer,sku,quantity,unit_price\n"unterminated'
        response = self.post("/api/orders/import/", {"csv": data})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_csv_export_escapes_spreadsheet_formulas(self):
        svc.create_order(self.payload(customer="=1+1"), self.admin)
        response = self.client.get("/api/export/orders/")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[1][4], "'=1+1")

    def test_stock_ledger_reconciles_to_balances(self):
        order = self.paid_shipped()
        svc.refund_order(order.id, {"reference": "REF-1", "items": [{"line_id": order.lines.get().id, "quantity": 1}]}, self.admin)
        movements = StockMovement.objects.filter(product=self.product, store=self.store)
        self.assertEqual(sum(m.quantity_delta for m in movements), self.balance().on_hand)
        self.assertEqual(sum(m.reserved_delta for m in movements), self.balance().reserved)
        self.assertTrue(AuditEvent.objects.filter(action="order.refund", actor=self.admin).exists())

    def test_database_rejects_reserved_quantity_above_stock(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inventory.objects.filter(pk=self.balance().pk).update(reserved=11)

    def test_store_filter_does_not_mix_other_store_inventory(self):
        store = Store.objects.create(code="B", name="二号店")
        svc.receive_stock({"store_id": store.id, "product_id": self.product.id, "quantity": 20}, self.admin)
        response = self.client.get(f"/api/state/?store={store.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["available"], 20)
        self.assertEqual(len(response.json()["inventory"]), 1)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL is required to verify row-lock concurrency; SQLite is demo-only.")
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def test_two_orders_competing_for_last_stock_cannot_oversell(self):
        actor = get_user_model().objects.create_superuser("pg-owner", password="test-password")
        store = Store.objects.create(code="PG", name="并发店")
        channel = Channel.objects.create(code="PG", name="并发渠道")
        product = Product.objects.create(sku="PG", name="并发商品", price="1.00")
        svc.receive_stock({"store_id": store.id, "product_id": product.id, "quantity": 10}, actor)
        barrier = Barrier(2)

        def reserve():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=actor.id)
                barrier.wait(timeout=10)
                svc.create_order({"channel_id": channel.id, "store_id": store.id, "customer": "并发顾客",
                    "items": [{"product_id": product.id, "quantity": 8}]}, user)
                return "created"
            except svc.BusinessError as error:
                return error.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
        self.assertCountEqual(results, ["created", "insufficient_stock"])
        self.assertEqual(Inventory.objects.get(product=product, store=store).reserved, 8)
        self.assertEqual(Order.objects.count(), 1)
