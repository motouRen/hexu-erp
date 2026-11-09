from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Sum
from core.models import Inventory, StockMovement, Order, OrderLine, ReturnLine


class Command(BaseCommand):
    help = "Verify inventory ledgers, reservations and order payment/return invariants in a consistent read snapshot."

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        failures = []
        for balance in Inventory.objects.all().iterator():
            hand, reserved = 0, 0
            for entry in StockMovement.objects.filter(store_id=balance.store_id, product_id=balance.product_id).order_by("id").iterator():
                hand += entry.quantity_delta
                reserved += entry.reserved_delta
                if (hand, reserved) != (entry.on_hand_after, entry.reserved_after):
                    failures.append(f"movement {entry.id}: snapshot mismatch")
            expected_reserved = OrderLine.objects.filter(order__store_id=balance.store_id, order__status="pending",
                product_id=balance.product_id).aggregate(value=Sum("quantity"))["value"] or 0
            if (hand, reserved) != (balance.on_hand, balance.reserved) or reserved != expected_reserved:
                failures.append(f"inventory {balance.id}: ledger/reservation mismatch")
        for order in Order.objects.prefetch_related("lines", "payments").iterator(chunk_size=100):
            received = sum((p.amount for p in order.payments.all() if p.kind == "receipt"), Decimal(0))
            refunded = sum((p.amount for p in order.payments.all() if p.kind == "refund"), Decimal(0))
            expected_total = sum((line.quantity*line.unit_price for line in order.lines.all()), Decimal(0))
            if expected_total != order.total or refunded != order.refunded_total or received != (order.total if order.paid_at else 0):
                failures.append(f"order {order.id}: amount mismatch")
            for line in order.lines.all():
                returned = ReturnLine.objects.filter(order_line=line).aggregate(value=Sum("quantity"))["value"] or 0
                if returned != line.returned_quantity:
                    failures.append(f"order line {line.id}: return mismatch")
        if failures:
            raise CommandError("Integrity check failed:\n" + "\n".join(failures[:30]))
        self.stdout.write(self.style.SUCCESS(f"Integrity verified: {Inventory.objects.count()} inventory balances, {Order.objects.count()} orders."))
