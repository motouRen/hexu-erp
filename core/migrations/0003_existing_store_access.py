from django.db import migrations


def assign_existing(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Store = apps.get_model("core", "Store")
    Access = apps.get_model("core", "StoreAccess")
    # Preserve the v0.1 access scope for existing active users. New users are deny-by-default.
    for user in User.objects.filter(is_active=True, is_superuser=False):
        for store in Store.objects.all():
            Access.objects.get_or_create(user=user, store=store)


class Migration(migrations.Migration):
    dependencies = [("core", "0002_loginattempt_alter_auditevent_options_and_more")]
    operations = [migrations.RunPython(assign_existing, migrations.RunPython.noop)]
