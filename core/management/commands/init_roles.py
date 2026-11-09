from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create ERP operator and viewer groups without changing existing users."

    def handle(self, *args, **options):
        operator, _ = Group.objects.get_or_create(name="业务操作员")
        permission = Permission.objects.get(content_type__app_label="core", codename="operate_erp")
        operator.permissions.add(permission)
        reviewer, _ = Group.objects.get_or_create(name="门店主管")
        reviewer.permissions.add(permission, Permission.objects.get(content_type__app_label="core", codename="approve_stock"))
        Group.objects.get_or_create(name="只读成员")
        self.stdout.write(self.style.SUCCESS("ERP roles are ready."))
