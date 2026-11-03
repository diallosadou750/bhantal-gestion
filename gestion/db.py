"""Base SQLite : schéma, connexions, journal d'audit en ajout seul (verrouillé par des déclencheurs)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from . import chiffrement, config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    must_change INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    device TEXT,
    ip TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER,
    username TEXT,
    role TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    device TEXT,
    ip TEXT,
    note TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS audit_entity ON audit_log(entity, entity_id);
CREATE INDEX IF NOT EXISTS audit_user ON audit_log(user_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log est en ajout seul : modification interdite'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log est en ajout seul : suppression interdite'); END;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta(key, value) VALUES('seq', '0');
CREATE TABLE IF NOT EXISTS clients(
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    zone TEXT NOT NULL DEFAULT '',
    owner_user_id INTEGER REFERENCES users(id),
    credit_limit INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER,
    updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS clients_seq ON clients(seq);
CREATE TABLE IF NOT EXISTS processed_ops(
    op_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    result_json TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES('epoch', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('db_id', lower(hex(randomblob(16))));
INSERT OR IGNORE INTO meta(key, value) VALUES('sale_no', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('note_no', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('product_no', '0');
CREATE TABLE IF NOT EXISTS products(
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0 CHECK(price >= 0),
    reorder_level INTEGER NOT NULL DEFAULT 0,
    track_stock INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS products_seq ON products(seq);
CREATE TABLE IF NOT EXISTS sales(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    sale_date TEXT NOT NULL,
    client_id TEXT NOT NULL REFERENCES clients(id),
    kind TEXT NOT NULL CHECK(kind IN ('cash','credit')),
    due_date TEXT,
    seller_user_id INTEGER,
    total INTEGER NOT NULL CHECK(total >= 0),
    paid_at_sale INTEGER NOT NULL DEFAULT 0 CHECK(paid_at_sale >= 0),
    pay_method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    delivery_due TEXT,
    delivered_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS sales_seq ON sales(seq);
CREATE INDEX IF NOT EXISTS sales_client ON sales(client_id);
CREATE TABLE IF NOT EXISTS sale_lines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id TEXT NOT NULL REFERENCES sales(id),
    position INTEGER NOT NULL,
    product_id TEXT,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    unit_price INTEGER NOT NULL CHECK(unit_price >= 0),
    discount_pct INTEGER NOT NULL DEFAULT 0,
    amount INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS lines_sale ON sale_lines(sale_id);
CREATE TABLE IF NOT EXISTS payments(
    id TEXT PRIMARY KEY,
    sale_id TEXT NOT NULL REFERENCES sales(id),
    client_id TEXT NOT NULL,
    pay_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    method TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS payments_seq ON payments(seq);
CREATE INDEX IF NOT EXISTS payments_sale ON payments(sale_id);
CREATE TABLE IF NOT EXISTS credit_notes(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    sale_id TEXT NOT NULL REFERENCES sales(id),
    client_id TEXT NOT NULL,
    note_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    reason TEXT NOT NULL,
    settlement TEXT NOT NULL CHECK(settlement IN ('deduct','refund')),
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS notes_seq ON credit_notes(seq);
CREATE INDEX IF NOT EXISTS notes_sale ON credit_notes(sale_id);
CREATE TABLE IF NOT EXISTS credit_note_lines(
    note_id TEXT NOT NULL REFERENCES credit_notes(id),
    position INTEGER NOT NULL,
    sale_position INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    depot_id TEXT NOT NULL DEFAULT 'main',
    lot_id TEXT,
    PRIMARY KEY(note_id, position)
);
CREATE INDEX IF NOT EXISTS cnl_product ON credit_note_lines(product_id);
INSERT OR IGNORE INTO meta(key, value) VALUES('supplier_no', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('purchase_no', '0');
CREATE TABLE IF NOT EXISTS suppliers(
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    zone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS suppliers_seq ON suppliers(seq);
CREATE TABLE IF NOT EXISTS purchases(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    purchase_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('cash','credit')),
    due_date TEXT,
    supplier_ref TEXT NOT NULL DEFAULT '',
    total INTEGER NOT NULL CHECK(total >= 0),
    paid_at_purchase INTEGER NOT NULL DEFAULT 0 CHECK(paid_at_purchase >= 0),
    pay_method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS purchases_seq ON purchases(seq);
CREATE INDEX IF NOT EXISTS purchases_supplier ON purchases(supplier_id);
CREATE TABLE IF NOT EXISTS purchase_lines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT NOT NULL REFERENCES purchases(id),
    position INTEGER NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(id),
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
    amount INTEGER NOT NULL,
    prev_cost INTEGER
);
CREATE INDEX IF NOT EXISTS plines_purchase ON purchase_lines(purchase_id);
CREATE INDEX IF NOT EXISTS plines_product ON purchase_lines(product_id);
CREATE INDEX IF NOT EXISTS slines_product ON sale_lines(product_id);
CREATE TABLE IF NOT EXISTS supplier_payments(
    id TEXT PRIMARY KEY,
    purchase_id TEXT NOT NULL REFERENCES purchases(id),
    supplier_id TEXT NOT NULL,
    pay_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    method TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS spay_seq ON supplier_payments(seq);
CREATE INDEX IF NOT EXISTS spay_purchase ON supplier_payments(purchase_id);
CREATE TABLE IF NOT EXISTS stock_adjustments(
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    adj_date TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty <> 0),
    reason TEXT NOT NULL CHECK(reason IN ('initial','return','loss','inventory','other')),
    note TEXT NOT NULL DEFAULT '',
    theoretical INTEGER,
    counted INTEGER,
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS adj_seq ON stock_adjustments(seq);
CREATE INDEX IF NOT EXISTS adj_product ON stock_adjustments(product_id);
INSERT OR IGNORE INTO meta(key, value) VALUES('expense_no', '0');
CREATE TABLE IF NOT EXISTS expenses(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    expense_date TEXT NOT NULL,
    category TEXT NOT NULL,
    payee TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL CHECK(amount > 0),
    method TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS expenses_seq ON expenses(seq);
CREATE INDEX IF NOT EXISTS expenses_date ON expenses(expense_date);
CREATE TABLE IF NOT EXISTS cash_movements(
    id TEXT PRIMARY KEY,
    move_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('bank_deposit','bank_withdrawal','owner_in','owner_out','customer_refund')),
    amount INTEGER NOT NULL CHECK(amount > 0),
    note TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT 'bank',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS moves_seq ON cash_movements(seq);
CREATE TABLE IF NOT EXISTS cash_closings(
    id TEXT PRIMARY KEY,
    close_date TEXT NOT NULL,
    opening INTEGER NOT NULL,
    cash_in INTEGER NOT NULL,
    cash_out INTEGER NOT NULL,
    expected INTEGER NOT NULL,
    counted INTEGER NOT NULL CHECK(counted >= 0),
    difference INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS closings_active_date ON cash_closings(close_date) WHERE status='active';
CREATE INDEX IF NOT EXISTS closings_seq ON cash_closings(seq);
CREATE TABLE IF NOT EXISTS bank_accounts(
    account TEXT PRIMARY KEY,
    opening_balance INTEGER NOT NULL,
    opening_date TEXT NOT NULL,
    seq INTEGER NOT NULL,
    updated_at TEXT NOT NULL, updated_by INTEGER
);
CREATE TABLE IF NOT EXISTS bank_lines(
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    line_date TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL CHECK(amount <> 0),
    reference TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','matched','flagged','void')),
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS blines_seq ON bank_lines(seq);
CREATE INDEX IF NOT EXISTS blines_account ON bank_lines(account, line_date);
CREATE TABLE IF NOT EXISTS bank_matches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id TEXT NOT NULL REFERENCES bank_lines(id),
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    UNIQUE(item_type, item_id)
);
CREATE INDEX IF NOT EXISTS bmatch_line ON bank_matches(line_id);
CREATE TABLE IF NOT EXISTS bank_reconciliations(
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    rec_date TEXT NOT NULL,
    statement_balance INTEGER NOT NULL,
    book_balance INTEGER NOT NULL,
    book_only INTEGER NOT NULL,
    bank_only INTEGER NOT NULL,
    difference INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE INDEX IF NOT EXISTS brec_seq ON bank_reconciliations(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('od_no', '0');
CREATE TABLE IF NOT EXISTS accounts(
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_entries(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    entry_date TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS entries_seq ON journal_entries(seq);
CREATE TABLE IF NOT EXISTS journal_lines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES journal_entries(id),
    position INTEGER NOT NULL,
    account TEXT NOT NULL,
    debit INTEGER NOT NULL DEFAULT 0 CHECK(debit >= 0),
    credit INTEGER NOT NULL DEFAULT 0 CHECK(credit >= 0),
    memo TEXT NOT NULL DEFAULT '',
    CHECK((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
);
CREATE INDEX IF NOT EXISTS jlines_entry ON journal_lines(entry_id);
CREATE TABLE IF NOT EXISTS fiscal_years(
    year INTEGER PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('closed')),
    result INTEGER NOT NULL,
    allocation TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    closed_at TEXT NOT NULL, closed_by INTEGER
);
CREATE TABLE IF NOT EXISTS fiscal_lines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL REFERENCES fiscal_years(year),
    entry_no INTEGER NOT NULL,
    label TEXT NOT NULL,
    account TEXT NOT NULL,
    debit INTEGER NOT NULL DEFAULT 0,
    credit INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS flines_year ON fiscal_lines(year);
INSERT OR IGNORE INTO meta(key, value) VALUES('asset_no', '0');
CREATE TABLE IF NOT EXISTS fixed_assets(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('purchase','opening')),
    acquisition_date TEXT NOT NULL,
    start_date TEXT NOT NULL,
    cost INTEGER NOT NULL CHECK(cost > 0),
    residual INTEGER NOT NULL DEFAULT 0,
    life_months INTEGER NOT NULL DEFAULT 0,
    months_already INTEGER NOT NULL DEFAULT 0,
    opening_accum INTEGER NOT NULL DEFAULT 0,
    coeff INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL CHECK(method IN ('linear','none')),
    pay_mode TEXT NOT NULL,
    asset_account TEXT NOT NULL,
    dep_account TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disposed','cancelled')),
    disposal_date TEXT,
    disposal_price INTEGER NOT NULL DEFAULT 0,
    disposal_kind TEXT,
    disposal_method TEXT NOT NULL DEFAULT '',
    cancel_reason TEXT,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS assets_seq ON fixed_assets(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('provision_no', '0');
CREATE TABLE IF NOT EXISTS provisions(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('receivable','stock','litigation','other')),
    label TEXT NOT NULL,
    sale_id TEXT REFERENCES sales(id),
    client_id TEXT REFERENCES clients(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    prov_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS provisions_seq ON provisions(seq);
CREATE TABLE IF NOT EXISTS provision_releases(
    id TEXT PRIMARY KEY,
    provision_id TEXT NOT NULL REFERENCES provisions(id),
    rel_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE INDEX IF NOT EXISTS prel_prov ON provision_releases(provision_id);
INSERT OR IGNORE INTO meta(key, value) VALUES('deferral_no', '0');
CREATE TABLE IF NOT EXISTS deferrals(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('charge','product')),
    label TEXT NOT NULL,
    account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    book_date TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    expense_id TEXT REFERENCES expenses(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS deferrals_seq ON deferrals(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('quote_no', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('po_no', '0');
CREATE TABLE IF NOT EXISTS purchase_orders(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    order_date TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    total INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','received','cancelled')),
    purchase_id TEXT REFERENCES purchases(id),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS porders_seq ON purchase_orders(seq);
CREATE TABLE IF NOT EXISTS purchase_order_lines(
    order_id TEXT NOT NULL REFERENCES purchase_orders(id),
    position INTEGER NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(id),
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
    amount INTEGER NOT NULL,
    PRIMARY KEY(order_id, position)
);
INSERT OR IGNORE INTO meta(key, value) VALUES('payslip_no', '0');
CREATE TABLE IF NOT EXISTS employees(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    base_salary INTEGER NOT NULL DEFAULT 0 CHECK(base_salary >= 0),
    hire_date TEXT,
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER
);
CREATE TABLE IF NOT EXISTS payslips(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    period TEXT NOT NULL,
    base INTEGER NOT NULL, bonus INTEGER NOT NULL, gross INTEGER NOT NULL,
    employee_bp INTEGER NOT NULL, employer_bp INTEGER NOT NULL, ceiling INTEGER NOT NULL,
    social_employee INTEGER NOT NULL, social_employer INTEGER NOT NULL, its INTEGER NOT NULL, net INTEGER NOT NULL,
    advance INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    expense_id TEXT REFERENCES expenses(id),
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS payslip_one_per_month ON payslips(employee_id, period) WHERE status='active';
INSERT OR IGNORE INTO meta(key, value) VALUES('advance_no', '0');
CREATE TABLE IF NOT EXISTS salary_advances(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    advance_date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE TABLE IF NOT EXISTS employee_leaves(
    id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    kind TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days INTEGER NOT NULL CHECK(days > 0),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE TABLE IF NOT EXISTS quotes(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    client_id TEXT NOT NULL REFERENCES clients(id),
    quote_date TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    total INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','converted','declined','cancelled')),
    sale_id TEXT REFERENCES sales(id),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS quotes_seq ON quotes(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('delivery_no', '0');
CREATE TABLE IF NOT EXISTS delivery_notes(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    sale_id TEXT NOT NULL REFERENCES sales(id),
    delivery_date TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS dnotes_seq ON delivery_notes(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('transfer_no', '0');
CREATE TABLE IF NOT EXISTS transfers(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    from_account TEXT NOT NULL,
    to_account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    transfer_date TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS transfers_seq ON transfers(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('recurring_no', '0');
CREATE TABLE IF NOT EXISTS recurring_expenses(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    method TEXT NOT NULL,
    payee TEXT NOT NULL DEFAULT '',
    day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 31),
    start_date TEXT NOT NULL,
    end_date TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_generated TEXT,
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS recurring_seq ON recurring_expenses(seq);
INSERT OR IGNORE INTO meta(key, value) VALUES('snote_no', '0');
CREATE TABLE IF NOT EXISTS supplier_notes(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    purchase_id TEXT NOT NULL REFERENCES purchases(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    note_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    reason TEXT NOT NULL,
    settlement TEXT NOT NULL CHECK(settlement IN ('deduct','refund')),
    refund_method TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS snotes_seq ON supplier_notes(seq);
CREATE TABLE IF NOT EXISTS attachments(
    id TEXT PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed')),
    created_at TEXT NOT NULL,
    uploaded_by INTEGER,
    uploaded_by_name TEXT NOT NULL DEFAULT '',
    removed_reason TEXT,
    removed_by INTEGER
);
CREATE INDEX IF NOT EXISTS att_entity ON attachments(entity, entity_id);
INSERT OR IGNORE INTO meta(key, value) VALUES('depot_no', '0');
INSERT OR IGNORE INTO meta(key, value) VALUES('stransfer_no', '0');
CREATE TABLE IF NOT EXISTS depots(
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO depots(id, code, name, active, version, seq, created_at) VALUES('main', 'D01', 'Dépôt principal', 1, 1, 0, '2026-01-01T00:00:00Z');
CREATE TABLE IF NOT EXISTS stock_transfers(
    id TEXT PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL REFERENCES products(id),
    from_depot TEXT NOT NULL REFERENCES depots(id),
    to_depot TEXT NOT NULL REFERENCES depots(id),
    qty INTEGER NOT NULL CHECK(qty > 0),
    transfer_date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
    cancel_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER
);
CREATE INDEX IF NOT EXISTS stransfers_seq ON stock_transfers(seq);
CREATE TABLE IF NOT EXISTS lots(
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    lot_no TEXT NOT NULL,
    expiry_date TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, lot_no)
);
CREATE TABLE IF NOT EXISTS sale_line_lots(
    sale_id TEXT NOT NULL REFERENCES sales(id),
    position INTEGER NOT NULL,
    lot_id TEXT NOT NULL REFERENCES lots(id),
    qty INTEGER NOT NULL CHECK(qty > 0)
);
CREATE INDEX IF NOT EXISTS slots_lot ON sale_line_lots(lot_id);
CREATE TABLE IF NOT EXISTS expense_categories(
    key TEXT PRIMARY KEY COLLATE NOCASE,
    account TEXT NOT NULL,
    nature TEXT NOT NULL CHECK(nature IN ('Fixe','Variable')),
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snotes_purchase ON supplier_notes(purchase_id);
CREATE TABLE IF NOT EXISTS supplier_note_lines(
    note_id TEXT NOT NULL REFERENCES supplier_notes(id),
    position INTEGER NOT NULL,
    product_id TEXT,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    unit_cost INTEGER NOT NULL,
    PRIMARY KEY(note_id, position)
);
INSERT OR IGNORE INTO meta(key, value) VALUES('payacct_no', '0');
CREATE TABLE IF NOT EXISTS pay_accounts(
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind TEXT NOT NULL CHECK(kind IN ('bank','mobile')),
    ledger TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by INTEGER
);
CREATE INDEX IF NOT EXISTS dnotes_sale ON delivery_notes(sale_id);
CREATE TABLE IF NOT EXISTS delivery_lines(
    note_id TEXT NOT NULL REFERENCES delivery_notes(id),
    position INTEGER NOT NULL,
    sale_position INTEGER NOT NULL,
    product_id TEXT,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    PRIMARY KEY(note_id, position)
);
CREATE TABLE IF NOT EXISTS quote_lines(
    quote_id TEXT NOT NULL REFERENCES quotes(id),
    position INTEGER NOT NULL,
    product_id TEXT,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    discount_pct INTEGER NOT NULL DEFAULT 0,
    amount INTEGER NOT NULL,
    PRIMARY KEY(quote_id, position)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes apparues après la création d'une base existante (jamais de perte de données)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    if "reorder_level" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN reorder_level INTEGER NOT NULL DEFAULT 0")
    for tbl, col, ddl in (("purchase_lines", "depot_id", "TEXT NOT NULL DEFAULT 'main'"), ("purchase_lines", "lot_id", "TEXT"), ("sale_lines", "depot_id", "TEXT NOT NULL DEFAULT 'main'"),
                          ("stock_adjustments", "depot_id", "TEXT NOT NULL DEFAULT 'main'"), ("stock_adjustments", "lot_id", "TEXT"), ("supplier_note_lines", "lot_id", "TEXT"), ("payslips", "advance", "INTEGER NOT NULL DEFAULT 0")):
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if have and col not in have: conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
    for tbl in ("products", "sale_lines", "purchase_lines", "quote_lines"):                        # TVA par ligne : NULL = taux par défaut de l'entreprise
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if have and "tax_rate" not in have: conn.execute(f"ALTER TABLE {tbl} ADD COLUMN tax_rate INTEGER")
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(cash_movements)")}
    if mcols and "account" not in mcols: conn.execute("ALTER TABLE cash_movements ADD COLUMN account TEXT NOT NULL DEFAULT 'bank'")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for col, ddl in (("totp_secret", "TEXT"), ("totp_on", "INTEGER NOT NULL DEFAULT 0"), ("totp_last", "INTEGER")):
        if col not in ucols: conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(fixed_assets)")}
    if acols and "coeff" not in acols: conn.execute("ALTER TABLE fixed_assets ADD COLUMN coeff INTEGER NOT NULL DEFAULT 0")      # amortissement dégressif
    if "track_stock" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN track_stock INTEGER NOT NULL DEFAULT 1")


def errors() -> tuple:
    """Classes d'erreur de la base en service (SQLite ou PostgreSQL)."""
    if config.db_url():
        from . import dbpg
        return (dbpg.Error,)
    return (chiffrement.sqlite().Error,)


def connect():
    if config.db_url():
        from . import dbpg
        return dbpg.connect(config.db_url(), config.db_schema())
    conn = chiffrement.ouvrir(config.db_path(), isolation_level=None, check_same_thread=False, timeout=15)      # SQLCipher : la clé est appliquée avant toute autre instruction
    conn.row_factory = chiffrement.sqlite().Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _sceller_smtp(conn) -> None:
    """Mot de passe d'envoi des e-mails encore en clair dans les réglages : chiffré au démarrage."""
    from . import notify
    r = conn.execute("SELECT value FROM meta WHERE key=?", (notify.KEY,)).fetchone()
    if r and '"password": "' in r["value"] and '"password": "gl1:' not in r["value"]: notify.save(conn, notify.load(conn))


def init_db() -> None:
    if config.db_url():
        from . import dbpg
        conn = connect()
        try:
            dbpg.create_schema(conn, SCHEMA); _migrate(conn)
            conn.execute("BEGIN IMMEDIATE")
            from . import accounting
            accounting.seed_plan(conn)
            from . import secretbox
            secretbox.sceller_existants(conn); _sceller_smtp(conn)
            conn.execute("COMMIT")
        finally:
            conn.close()
        return
    chiffrement.verifier_demarrage()                       # clé présente, base en clair migrée (l'original est conservé), sinon refus de démarrer
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        from . import accounting
        accounting.seed_plan(conn)
        from . import secretbox
        secretbox.sceller_existants(conn); _sceller_smtp(conn)


@contextmanager
def transaction():
    """Transaction exclusive : le changement et son entrée d'audit réussissent ou échouent ensemble."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except errors():
            pass
        raise
    finally:
        conn.close()


@contextmanager
def read():
    conn = connect()
    try:
        if config.db_url(): conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")      # PostgreSQL : toutes les lectures voient le même instant
        yield conn
    finally:
        conn.close()
