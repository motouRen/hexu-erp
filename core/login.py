import hashlib
from datetime import timedelta
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from .models import LoginAttempt
from django.conf import settings

WINDOW = timedelta(minutes=10)
LIMIT = 5


class ProtectedLoginView(LoginView):
    template_name = "login.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "demo_mode": settings.DEMO_MODE}

    def keys(self):
        # Never trust client-supplied X-Forwarded-For. REMOTE_ADDR comes from the configured server.
        address = self.request.META.get("REMOTE_ADDR", "unknown")
        username = self.request.POST.get("username", "").strip().casefold()[:150]
        return [hashlib.sha256(value.encode()).hexdigest() for value in ("ip:"+address, "account:"+username)]

    def post(self, request, *args, **kwargs):
        now = timezone.now()
        if LoginAttempt.objects.filter(key__in=self.keys(), window_started_at__gt=now-WINDOW, failures__gte=LIMIT).exists():
            response = HttpResponse("登录尝试过于频繁，请在 10 分钟后重试。", status=429, content_type="text/plain; charset=utf-8")
            response["Retry-After"] = "600"
            return response
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        with transaction.atomic():
            for key in sorted(self.keys()):
                record, _ = LoginAttempt.objects.get_or_create(key=key, defaults={"window_started_at": timezone.now()})
                record = LoginAttempt.objects.select_for_update().get(pk=record.pk)
                if record.window_started_at <= timezone.now()-WINDOW:
                    record.window_started_at, record.failures = timezone.now(), 0
                record.failures += 1
                record.save(update_fields=["window_started_at", "failures"])
        return super().form_invalid(form)

    def form_valid(self, form):
        LoginAttempt.objects.filter(key__in=self.keys()).delete()
        return super().form_valid(form)
