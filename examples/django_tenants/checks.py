"""Both suites pass normally; only the strong suite catches a lost scope."""

import unittest

from app import Order, visible_orders
from django.db import connection


class WeakIsolationTest(unittest.TestCase):
    def setUp(self):
        with connection.schema_editor() as editor:
            editor.create_model(Order)
        Order.objects.bulk_create(
            [Order(tenant_id=1, name="Tenant A order"), Order(tenant_id=2, name="Tenant B order")]
        )

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.delete_model(Order)

    def test_own_order_is_visible(self):
        self.assertIn("Tenant A order", visible_orders(1).values_list("name", flat=True))


class StrongIsolationTest(WeakIsolationTest):
    def test_other_tenant_order_is_hidden(self):
        self.assertNotIn("Tenant B order", visible_orders(1).values_list("name", flat=True))
