"""Rôles et permissions. Une seule table à relire pour savoir qui peut faire quoi."""
from __future__ import annotations

ROLES = {
    "owner": "Propriétaire",
    "accountant": "Comptable",
    "sales": "Commercial",
    "cashier": "Caissier",
    "auditor": "Auditeur (lecture seule)",
}

# Achats, fournisseurs, prix d'achat et corrections de stock : réservés au propriétaire et au comptable.
_BACKOFFICE = {"bank.read", "bank.write", "accounting.read", "accounting.write", "expenses.read_all", "expenses.cancel", "cash.cancel", "budgets.set", "costs.read", "suppliers.read", "suppliers.write", "purchases.read", "purchases.create", "purchases.cancel", "supplierpay.create", "supplierpay.cancel", "stock.adjust", "recurring.manage", "payroll.read", "payroll.manage"}

PERMISSIONS: dict[str, set[str]] = {
    "owner": {"*"},
    "accountant": {"clients.read_all", "clients.write", "clients.delete", "audit.read", "reports.read", "products.read", "products.write", "stock.read",
                   "sales.read_all", "sales.create", "sales.credit", "sales.cancel", "sales.deliver", "sales.free_line", "sales.override_credit", "sales.override_price",
                   "sales.backdate", "payments.create", "payments.cancel", "creditnotes.create", "expenses.create", "cash.read", "cash.close", "cash.move", "cash.move_all"} | _BACKOFFICE,
    # L'auditeur (expert-comptable externe, commissaire aux comptes) consulte tout ce qui est comptable et le journal d'audit, sans RIEN pouvoir modifier ni créer.
    "auditor": {"clients.read_all", "audit.read", "reports.read", "products.read", "stock.read", "sales.read_all", "bank.read", "accounting.read", "expenses.read_all", "cash.read", "costs.read", "suppliers.read", "purchases.read", "payroll.read"},
    # Le commercial vend du catalogue, avec remise limitée, aux clients qui sont les siens ; il peut accorder du crédit dans la limite du client.
    "sales": {"clients.read_own", "clients.create", "clients.write_own", "products.read", "stock.read", "sales.read_own", "sales.create", "sales.credit", "sales.deliver", "payments.create"},
    # Le caissier encaisse et vend au comptant ; il ne voit ni notes ni limites de crédit, et n'accorde pas de crédit.
    "cashier": {"clients.read_all", "products.read", "stock.read", "sales.read_all", "sales.create", "sales.deliver", "sales.free_line", "payments.create",
                "expenses.create", "expenses.read_own", "cash.read", "cash.close", "cash.move"},
}

# Champs masqués selon le rôle (principe du moindre privilège)
HIDDEN_CLIENT_FIELDS: dict[str, set[str]] = {
    "cashier": {"note", "credit_limit"},
}

MAX_DISCOUNT_PCT = {"sales": 10}          # remise maximale par rôle (les autres rôles : 100)
BACKDATE_DAYS = 7                          # antidater une vente au-delà exige le droit « sales.backdate »
CASHIER_EXPENSE_LIMIT = 100_000            # un caissier ne peut pas enregistrer seul une dépense plus grosse
CASHIER_BACKDATE_DAYS = 3                  # ni antidater de plus de 3 jours
COST_ALERT_PCT = 5                         # hausse du prix d'achat signalée au-delà de ce pourcentage
METHODS = ("Espèces", "Orange Money", "MTN MoMo", "Virement bancaire", "Chèque", "Autre")


def can(role: str, perm: str) -> bool:
    perms = PERMISSIONS.get(role, set())
    return "*" in perms or perm in perms
