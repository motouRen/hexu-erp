import csv
import io
import json
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from .models import Store, StoreAccess, Product, Channel, Inventory, Order, StockDocument, StockMovement, LoginAttempt
from . import services as svc
from . import stock


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReliabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser("v2-owner", password="Test-Password-123")
        cls.operator = User.objects.create_user("v2-operator", password="Test-Password-123")
        cls.reviewer = User.objects.create_user("v2-reviewer", password="Test-Password-123")
        cls.viewer = User.objects.create_user("v2-viewer", password="Test-Password-123")
        operate = Permission.objects.get(codename="operate_erp")
        approve = Permission.objects.get(codename="approve_stock")
        cls.operator.user_permissions.add(operate)
        cls.reviewer.user_permissions.add(operate, approve)
        cls.a = Store.objects.create(code="SH-01", name="授权门店")
        cls.b = Store.objects.create(code="HZ-01", name="另一门店")
        for user in (cls.operator, cls.reviewer, cls.viewer):
            StoreAccess.objects.create(user=user, store=cls.a)
        StoreAccess.objects.create(user=cls.reviewer, store=cls.b)
        cls.channel = Channel.objects.create(code="V2", name="测试渠道")
        cls.product = Product.objects.create(sku="V2-P1", name="测试商品", price="10.00")
        for store, quantity in ((cls.a, 1000), (cls.b, 100)):
            svc.receive_stock({"product_id": cls.product.id, "store_id": store.id, "quantity": quantity}, cls.owner)

    def setUp(self):
        self.client.force_login(self.operator)

    def post(self, path, data, key=None):
        return self.client.post(path, json.dumps(data), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def order(self, store=None, quantity=1, actor=None):
        return svc.create_order({"channel_id": self.channel.id, "store_id": (store or self.a).id,
            "customer": "测试客户", "items": [{"product_id": self.product.id, "quantity": quantity}]}, actor or self.owner)["id"]

    def document(self, kind="receive", quantity=5, actor=None, **extra):
        data = {"kind": kind, "store_id": self.a.id, "source": "SRC-02",
                "items": [{"product_id": self.product.id, "quantity": quantity}], **extra}
        return stock.create_document(data, actor or self.operator)["id"]

    def balance(self, store=None):
        return Inventory.objects.get(store=store or self.a, product=self.product)

    def test_dashboard_hides_unauthorized_stores_and_orders(self):
        self.order(self.a)
        self.order(self.b)
        data = self.client.get("/api/state/").json()
        self.assertEqual([s["id"] for s in data["stores"]], [self.a.id])
        self.assertEqual(data["stats"]["total_orders"], 1)
        self.assertTrue(all(o["store_id"] == self.a.id for o in data["orders"]))

    def test_new_user_without_store_assignments_has_no_business_data(self):
        user = get_user_model().objects.create_user("no-scope")
        self.client.force_login(user)
        data = self.client.get("/api/state/").json()
        self.assertEqual((data["stores"], data["orders"], data["inventory"]), ([], [], []))

    def test_dashboard_sums_the_same_product_across_multiple_authorized_stores(self):
        self.client.force_login(self.reviewer)
        data = self.client.get("/api/state/").json()
        self.assertEqual(data["stats"]["available"], 1100)
        self.assertEqual(data["products"][0]["available"], 1100)

    def test_explicit_unauthorized_store_is_denied_for_query_and_export(self):
        for path in ("/api/state/", "/api/records/orders/", "/api/export/orders/"):
            self.assertEqual(self.client.get(path, {"store": self.b.id}).status_code, 403)

    def test_export_only_contains_authorized_stores(self):
        self.order(self.a)
        self.order(self.b)
        rows = list(csv.reader(io.StringIO(self.client.get("/api/export/orders/").content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], self.a.name)

    def test_unauthorized_order_action_is_denied(self):
        order_id = self.order(self.b)
        response = self.post(f"/api/orders/{order_id}/pay/", {"reference": "P"})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(Order.objects.get(pk=order_id).paid_at)

    def test_unauthorized_receive_and_transfer_are_denied(self):
        self.assertEqual(self.post("/api/stock/receive/", {"product_id": self.product.id, "store_id": self.b.id, "quantity": 3}).status_code, 403)
        with self.assertRaises(svc.BusinessError):
            self.document("transfer", target_store_id=self.b.id)
        self.assertEqual(self.balance(self.b).on_hand, 100)

    def test_revoked_scope_also_blocks_idempotent_replay(self):
        key = str(uuid.uuid4())
        data = {"product_id": self.product.id, "store_id": self.a.id, "quantity": 1}
        self.assertEqual(self.post("/api/stock/receive/", data, key).status_code, 200)
        StoreAccess.objects.filter(user=self.operator).delete()
        self.assertEqual(self.post("/api/stock/receive/", data, key).status_code, 403)

    def test_csv_unauthorized_order_rolls_back_other_rows(self):
        data = "external_id,channel_code,store_code,customer,sku,quantity,unit_price\nA,V2,SH-01,客户,V2-P1,1,10.00\nB,V2,HZ-01,客户,V2-P1,1,10.00\n"
        self.assertEqual(self.post("/api/orders/import/", {"csv": data}).status_code, 403)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.balance().reserved, 0)

    def test_server_search_finds_order_beyond_old_200_limit(self):
        Order.objects.bulk_create([Order(number=f"PAGE-{i:04}", external_id=f"EXT-{i:04}", channel=self.channel,
            store=self.a, customer="分页客户", total="10.00", status="canceled", actor=self.owner) for i in range(235)])
        response = self.client.get("/api/records/orders/", {"q": "EXT-0000", "status": "canceled"}).json()
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["results"][0]["external_id"], "EXT-0000")
        page = self.client.get("/api/records/orders/", {"page": 2}).json()
        self.assertEqual((page["count"], page["page"], len(page["results"])), (235, 2, 20))
        rows = list(csv.reader(io.StringIO(self.client.get("/api/export/orders/?q=EXT-0000&status=canceled").content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "EXT-0000")

    def test_invalid_date_range_and_page_size_are_rejected(self):
        self.assertEqual(self.client.get("/api/records/orders/?date_from=2026-11-09&date_to=2026-11-01").status_code, 400)
        self.assertEqual(self.client.get("/api/records/orders/?date_from=not-a-date").status_code, 400)
        self.assertEqual(self.client.get("/api/records/orders/?page_size=1000").status_code, 400)

    def test_receive_document_draft_post_and_reverse(self):
        doc = self.document()
        self.assertEqual(self.balance().on_hand, 1000)
        stock.post_document(doc, {}, self.operator)
        self.assertEqual(self.balance().on_hand, 1005)
        self.assertEqual(StockDocument.objects.get(pk=doc).movements.count(), 1)
        stock.cancel_document(doc, {}, self.operator)
        self.assertEqual(self.balance().on_hand, 1000)
        self.assertEqual(StockDocument.objects.get(pk=doc).status, "canceled")
        self.assertEqual(StockDocument.objects.get(pk=doc).movements.count(), 2)

    def test_receive_cannot_reverse_after_downstream_reservation(self):
        doc = self.document()
        stock.post_document(doc, {}, self.operator)
        self.order(actor=self.operator)
        with self.assertRaises(svc.BusinessError) as error:
            stock.cancel_document(doc, {}, self.operator)
        self.assertEqual(error.exception.code, "downstream_exists")
        self.assertEqual(StockDocument.objects.get(pk=doc).status, "posted")

    def test_transfer_moves_both_stores_and_reversal_is_balanced(self):
        doc = self.document("transfer", 7, actor=self.reviewer, target_store_id=self.b.id)
        stock.post_document(doc, {}, self.reviewer)
        self.assertEqual((self.balance().on_hand, self.balance(self.b).on_hand), (993, 107))
        self.assertEqual(sum(m.quantity_delta for m in StockMovement.objects.filter(document_id=doc)), 0)
        stock.cancel_document(doc, {}, self.reviewer)
        self.assertEqual((self.balance().on_hand, self.balance(self.b).on_hand), (1000, 100))

    def test_transfer_cannot_move_reserved_stock(self):
        self.order(quantity=999)
        doc = self.document("transfer", 2, actor=self.reviewer, target_store_id=self.b.id)
        with self.assertRaises(svc.BusinessError):
            stock.post_document(doc, {}, self.reviewer)
        self.assertEqual((self.balance().on_hand, self.balance(self.b).on_hand), (1000, 100))
        self.assertFalse(StockMovement.objects.filter(document_id=doc).exists())

    def test_repeated_post_does_not_double_receive(self):
        doc = self.document()
        stock.post_document(doc, {}, self.operator)
        with self.assertRaises(svc.BusinessError):
            stock.post_document(doc, {}, self.operator)
        self.assertEqual(self.balance().on_hand, 1005)

    def test_count_requires_separate_approver_and_applies_difference(self):
        doc = self.document("count", 997)
        stock.submit_document(doc, {}, self.operator)
        with self.assertRaises(svc.BusinessError):
            stock.post_document(doc, {}, self.operator)
        stock.post_document(doc, {"note": "已复点"}, self.reviewer)
        self.assertEqual(self.balance().on_hand, 997)
        document = StockDocument.objects.get(pk=doc)
        self.assertEqual(document.approved_by_id, self.reviewer.id)
        self.assertEqual(document.movements.get().quantity_delta, -3)

    def test_even_reviewer_cannot_approve_own_count(self):
        doc = self.document("count", 1000, actor=self.reviewer)
        stock.submit_document(doc, {}, self.reviewer)
        with self.assertRaises(svc.BusinessError) as error:
            stock.post_document(doc, {}, self.reviewer)
        self.assertEqual(error.exception.code, "approval_required")

    def test_count_rejects_changed_inventory_version(self):
        doc = self.document("count", 997)
        stock.submit_document(doc, {}, self.operator)
        order = self.order(actor=self.operator)
        svc.cancel_order(order, {}, self.operator)
        with self.assertRaises(svc.BusinessError) as error:
            stock.post_document(doc, {}, self.reviewer)
        self.assertEqual(error.exception.code, "stale_count")
        self.assertEqual(self.balance().on_hand, 1000)

    def test_count_snapshot_is_checked_when_saving_the_form(self):
        version = self.balance().version
        order_id = self.order(actor=self.operator)
        svc.cancel_order(order_id, {}, self.operator)
        with self.assertRaises(svc.BusinessError) as error:
            stock.create_document({"kind": "count", "store_id": self.a.id, "items": [
                {"product_id": self.product.id, "quantity": 1000, "expected_version": version}]}, self.operator)
        self.assertEqual(error.exception.code, "stale_count")

    def test_count_cannot_reduce_stock_below_reserved_quantity(self):
        self.order(quantity=10)
        doc = self.document("count", 5)
        stock.submit_document(doc, {}, self.operator)
        with self.assertRaises(svc.BusinessError):
            stock.post_document(doc, {}, self.reviewer)
        self.assertEqual(self.balance().on_hand, 1000)

    def test_login_rate_limit_blocks_sixth_attempt(self):
        self.client.logout()
        for _ in range(5):
            self.assertEqual(self.client.post("/login/", {"username": self.operator.username, "password": "wrong"}).status_code, 200)
        self.assertEqual(self.client.post("/login/", {"username": self.operator.username, "password": "wrong"}).status_code, 429)
        self.assertFalse(LoginAttempt.objects.filter(key__contains=self.operator.username).exists())

    def test_successful_login_resets_failures(self):
        self.client.logout()
        self.client.post("/login/", {"username": self.operator.username, "password": "wrong"})
        self.assertEqual(self.client.post("/login/", {"username": self.operator.username, "password": "Test-Password-123"}).status_code, 302)
        self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_public_demo_accounts_have_no_admin_privileges(self):
        call_command("init_roles", verbosity=0)
        from .management.commands.init_demo import Command
        Command().public_accounts()
        for user in get_user_model().objects.filter(username__startswith="demo"):
            self.assertFalse(user.is_staff or user.is_superuser)
            self.assertTrue(user.check_password("HexuDemo2026!"))
        guest = get_user_model().objects.get(username="demo")
        self.assertFalse(guest.has_perm("core.operate_erp"))
        limited = get_user_model().objects.get(username="demo_store")
        self.assertEqual(list(limited.store_access.values_list("store_id", flat=True)), [self.a.id])
