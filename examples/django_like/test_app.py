import unittest

from app import Manager, visible_orders


class OrderIsolationTest(unittest.TestCase):
    def test_visible_orders_keep_tenant_scope(self):
        self.assertEqual(
            visible_orders(Manager(), 42),
            {"tenant_id": 42, "status": "open"},
        )


if __name__ == "__main__":
    unittest.main()

