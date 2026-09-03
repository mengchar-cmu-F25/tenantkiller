"""Business-only checks contrasted with explicit tenant-isolation assertions."""

import unittest

from app import Order, find_order, open_orders
from django.db import connection


class OrderData(unittest.TestCase):
    def setUp(self):
        with connection.schema_editor() as editor:
            editor.create_model(Order)
        Order.objects.bulk_create(
            [
                Order(pk=101, tenant_id=1, name="own-open", status="open"),
                Order(pk=102, tenant_id=1, name="own-closed", status="closed"),
                Order(pk=201, tenant_id=2, name="foreign-open", status="open"),
                Order(pk=202, tenant_id=2, name="foreign-closed", status="closed"),
                Order(pk=301, tenant_id=3, name="third-open", status="open"),
                Order(pk=302, tenant_id=3, name="deleted-open", status="open", deleted=True),
            ]
        )

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.delete_model(Order)


class WeakFilterTest(OrderData):
    def test_open_non_deleted_business_conditions(self):
        orders = list(open_orders(1))
        self.assertIn("own-open", [order.name for order in orders])
        self.assertTrue(all(order.status == "open" for order in orders))
        self.assertTrue(all(not order.deleted for order in orders))


class StrongFilterTest(WeakFilterTest):
    def test_no_other_tenants(self):
        self.assertEqual(
            set(open_orders(1).values_list("tenant_id", flat=True)),
            {1},
            "foreign tenants leaked into open orders",
        )


class WeakGetTest(OrderData):
    def test_primary_key_still_selects_the_requested_order(self):
        self.assertEqual(find_order(1, 101).pk, 101)
        self.assertEqual(find_order(1, 102).pk, 102)


class StrongGetTest(WeakGetTest):
    def test_foreign_primary_key_is_not_visible(self):
        with self.assertRaises(Order.DoesNotExist, msg="foreign order must remain invisible"):
            find_order(1, 201)
