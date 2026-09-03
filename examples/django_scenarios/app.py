"""Synthetic three-tenant data model backed only by in-memory SQLite."""

import django
from django.conf import settings
from django.db import models

settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    INSTALLED_APPS=[],
)
django.setup()


class Order(models.Model):
    tenant_id = models.IntegerField()
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=20)
    deleted = models.BooleanField(default=False)

    class Meta:
        app_label = "tenantkiller_scenarios"


def open_orders(tenant_id):
    return Order.objects.filter(tenant_id=tenant_id, status="open", deleted=False)


def find_order(tenant_id, order_id):
    return Order.objects.get(tenant_id=tenant_id, pk=order_id)
