from .models import Store, StoreAccess


def store_ids(actor):
    if actor.is_superuser:
        return Store.objects.values_list("id", flat=True)
    return StoreAccess.objects.filter(user=actor).values_list("store_id", flat=True)


def require_store(actor, store_id):
    from .services import BusinessError
    if not actor.is_superuser and not StoreAccess.objects.filter(user=actor, store_id=store_id).exists():
        raise BusinessError("你没有此门店的访问权限", "store_forbidden", 403)


def require_operation(actor, permission="core.operate_erp"):
    from .services import BusinessError
    if not actor.has_perm(permission):
        raise BusinessError("当前账号没有此操作权限", "forbidden", 403)
