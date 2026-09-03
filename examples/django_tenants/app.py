"""Standalone Django ORM demo; the database lives only in this process."""

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

    class Meta:
        app_label = "tenantkiller_demo"


def visible_orders(tenant_id):
    return Order.objects.filter(tenant_id=tenant_id)
