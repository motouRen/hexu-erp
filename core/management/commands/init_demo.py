import os
import random
import secrets
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from core.models import Product, Store, Channel, Order, Payment, StockMovement, AuditEvent
from core import services
from django.contrib.auth.models import Group
from core.models import StoreAccess


class Command(BaseCommand):
    help = "Initialize a local demo once; never resets existing business data or passwords."

    def handle(self, *args, **options):
        if not settings.DEBUG and not settings.DEMO_MODE:
            raise CommandError("Demo seeding is disabled in production. Use createsuperuser instead.")
        call_command("init_roles")
        password = None
        with transaction.atomic():
            if settings.DEMO_MODE and "demo" not in str(settings.DATABASES["default"]["NAME"]).lower():
                raise CommandError("Public demo must use a separate database whose name includes demo.")
            username = "_demo_seed" if settings.DEMO_MODE else os.environ.get("ERP_ADMIN_USERNAME", "admin")
            user = get_user_model().objects.filter(username=username).first()
            if user and not user.is_superuser:
                raise CommandError("The requested username already belongs to a non-admin account.")
            if not user:
                password = None if settings.DEMO_MODE else os.environ.get("ERP_ADMIN_PASSWORD") or secrets.token_urlsafe(16)
                if password and len(password) < 12:
                    raise CommandError("ERP_ADMIN_PASSWORD must contain at least 12 characters.")
                user = get_user_model().objects.create_superuser(username=username, password=password, first_name="演示初始化")
            if AuditEvent.objects.filter(action="demo.initialize").exists():
                self.stdout.write("Demo already exists; existing data and passwords are unchanged.")
            elif Product.objects.exists() or Store.objects.exists() or Channel.objects.exists() or Order.objects.exists():
                if settings.DEMO_MODE:
                    raise CommandError("Existing business data cannot be converted into a public demo.")
                self.stdout.write("Business records already exist; demo data was not added.")
            else:
                self.seed(user)
            if settings.DEMO_MODE:
                self.public_accounts()
                self.seed_v02()
                user.is_active = False
                user.save(update_fields=["is_active"])
        if password:
            access_file = Path(settings.BASE_DIR) / "LOCAL_ACCESS.txt"
            access_file.write_text("禾序 ERP 本地演示账号\nURL: http://127.0.0.1:8765/\n账号: " + username +
                "\n密码: " + password + "\n\n此文件已被 Git 忽略。请勿上传或分享；可在后台更改密码。\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS("Admin account created. Credentials: LOCAL_ACCESS.txt"))
        self.stdout.write(self.style.SUCCESS("Hexu ERP demo is ready."))

    def public_accounts(self):
        for username, group, name, codes in [("demo", "只读成员", "演示访客", ["SH-01", "HZ-01"]),
            ("demo_operator", "业务操作员", "演示操作员", ["SH-01", "HZ-01"]),
            ("demo_reviewer", "门店主管", "演示审批员", ["SH-01", "HZ-01"]),
            ("demo_store", "业务操作员", "静安店成员", ["SH-01"])]:
            member, created = get_user_model().objects.get_or_create(username=username, defaults={"first_name": name})
            if not created and (member.is_staff or member.is_superuser):
                raise CommandError("A demo username belongs to a privileged account; refusing to continue.")
            if created:
                member.set_password("HexuDemo2026!")
                member.save(update_fields=["password"])
                member.groups.add(Group.objects.get(name=group))
                for store in Store.objects.filter(code__in=codes):
                    StoreAccess.objects.get_or_create(user=member, store=store)
        self.stdout.write("Public demo users: demo / demo_operator / demo_reviewer / demo_store; password: HexuDemo2026!")

    def seed_v02(self):
        if AuditEvent.objects.filter(action="demo.v02").exists():
            return
        from core.stock import create_document, post_document, submit_document
        from core.models import Inventory
        operator = get_user_model().objects.get(username="demo_operator")
        reviewer = get_user_model().objects.get(username="demo_reviewer")
        source, target = Store.objects.get(code="SH-01"), Store.objects.get(code="HZ-01")
        product = Product.objects.get(sku="HX-005")
        transfer = create_document({"kind": "transfer", "store_id": source.id, "target_store_id": target.id,
            "source": "演示门店补货计划", "items": [{"product_id": product.id, "quantity": 3}]}, operator)
        post_document(transfer["id"], {}, operator)
        for sku, store, approve in [("HX-006", target, True), ("HX-002", source, False)]:
            product = Product.objects.get(sku=sku)
            balance = Inventory.objects.get(product=product, store=store)
            doc = create_document({"kind": "count", "store_id": store.id, "note": "演示货架复点",
                "items": [{"product_id": product.id, "quantity": balance.on_hand + 1}]}, operator)
            submit_document(doc["id"], {}, operator)
            if approve:
                post_document(doc["id"], {"note": "已由门店主管复核"}, reviewer)
        services.audit(operator, "demo.v02", "0.2.0")

    def seed(self, user):
        stores = [Store.objects.create(code="SH-01", name="上海静安店", location="上海市静安区 · 演示门店"),
                  Store.objects.create(code="HZ-01", name="杭州滨江店", location="杭州市滨江区 · 演示门店")]
        channels = [Channel.objects.create(code="TMALL", name="天猫旗舰店", kind="online"),
                    Channel.objects.create(code="DOUYIN", name="抖音小店", kind="online"),
                    Channel.objects.create(code="POS", name="线下零售", kind="retail")]
        specs = [("HX-001", "原色帆布托特包", "出行配件", "89.00", 10),
                 ("HX-002", "日常陶瓷马克杯", "杯壶器皿", "68.00", 10),
                 ("HX-003", "轻量随行保温杯", "杯壶器皿", "159.00", 12),
                 ("HX-004", "柔棉方巾两件装", "家居织物", "39.00", 15),
                 ("HX-005", "木质调香氛蜡烛", "居家香氛", "128.00", 8),
                 ("HX-006", "折叠布艺收纳篮", "收纳用品", "79.00", 10),
                 ("HX-007", "圆角榉木托盘", "厨房餐具", "119.00", 8),
                 ("HX-008", "轻便晴雨两用伞", "出行配件", "99.00", 10)]
        products = [Product.objects.create(sku=sku, name=name, category=category, price=price, reorder_level=level)
                    for sku, name, category, price, level in specs]
        for store in stores:
            for index, product in enumerate(products):
                services.receive_stock({"product_id": product.id, "store_id": store.id,
                    "quantity": [84, 56, 62, 95, 21, 68, 16, 47][index], "note": "演示期初入库 · 虚构商品与库存"}, user)
        rng = random.Random(17)
        now = timezone.now()
        StockMovement.objects.filter(kind="receive").update(created_at=now - timedelta(days=7))
        AuditEvent.objects.filter(action="stock.receive").update(created_at=now - timedelta(days=7))
        for day in range(6, -1, -1):
            for sequence in range(3):
                product = products[rng.randrange(0, 8)]
                quantity = rng.randint(1, 3)
                store, channel = stores[sequence % 2], channels[sequence]
                result = services.create_order({"channel_id": channel.id, "store_id": store.id,
                    "customer": ["演示客户 · 林女士", "演示客户 · 周先生", "演示零售顾客"][sequence],
                    "external_id": f"DEMO-{day}-{sequence}",
                    "items": [{"product_id": product.id, "quantity": quantity, "unit_price": str(product.price)}]}, user)
                order_id = result["id"]
                is_paid = not (day == 0 and sequence == 2)
                if is_paid:
                    services.pay_order(order_id, {"reference": f"DEMO-PAY-{day}-{sequence}"}, user)
                if day > 0 or sequence == 0:
                    services.ship_order(order_id, {}, user)
                if (day, sequence) == (2, 1):
                    line = Order.objects.get(pk=order_id).lines.first()
                    services.refund_order(order_id, {"reference": "DEMO-REFUND", "items": [
                        {"line_id": line.id, "quantity": 1, "restock": True}]}, user)
                timestamp = now - timedelta(days=day, minutes=(2 - sequence) * 43)
                Order.objects.filter(pk=order_id).update(created_at=timestamp,
                    paid_at=timestamp if is_paid else None,
                    shipped_at=timestamp if day > 0 or sequence == 0 else None)
                Payment.objects.filter(order_id=order_id).update(created_at=timestamp)
                StockMovement.objects.filter(order_id=order_id).update(created_at=timestamp)
                AuditEvent.objects.filter(object_ref=result["number"]).update(created_at=timestamp)
        # Give one unambiguous low-stock example using the same audited stock flow.
        result = services.create_order({"channel_id": channels[0].id, "store_id": stores[1].id,
            "customer": "演示客户 · 陈女士", "external_id": "DEMO-LOW-STOCK",
            "items": [{"product_id": products[6].id, "quantity": 9, "unit_price": str(products[6].price)}]}, user)
        services.audit(user, "demo.initialize", "v0.1", {"notice": "All seeded business data is fictional."})
