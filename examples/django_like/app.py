class Manager:
    """Tiny stand-in for a Django manager; Django is not required for this demo."""

    def filter(self, **kwargs):
        return kwargs


def visible_orders(manager, tenant_id):
    return manager.filter(tenant_id=tenant_id, status="open")

