-- =====================================================================
-- Procure-to-Pay Reconciliation: database schema (PostgreSQL)
-- Creates the six source tables with data types, primary keys and
-- foreign keys. Re-running it drops and recreates everything.
--
-- Run from the project root:
--   psql -d p2p -f sql/01_schema.sql
-- =====================================================================

DROP TABLE IF EXISTS bank_payments, supplier_invoices, goods_receipts,
                     purchase_orders, suppliers, fx_rates CASCADE;

-- Master data: one row per supplier
CREATE TABLE suppliers (
    supplier_id        VARCHAR(10)  PRIMARY KEY,
    supplier_name      VARCHAR(100) NOT NULL,
    country            CHAR(2)      NOT NULL,
    currency           CHAR(3)      NOT NULL CHECK (currency IN ('PLN', 'EUR')),
    payment_terms_days INTEGER      NOT NULL CHECK (payment_terms_days > 0)
);

-- Daily EUR/PLN exchange rates
CREATE TABLE fx_rates (
    rate_date DATE          PRIMARY KEY,
    eur_pln   NUMERIC(8, 4) NOT NULL CHECK (eur_pln > 0)
);

-- ERP purchasing module: what we ordered
CREATE TABLE purchase_orders (
    po_id       VARCHAR(10)    PRIMARY KEY,
    supplier_id VARCHAR(10)    NOT NULL REFERENCES suppliers (supplier_id),
    po_date     DATE           NOT NULL,
    sku         VARCHAR(10)    NOT NULL,
    qty_ordered INTEGER        NOT NULL CHECK (qty_ordered > 0),
    unit_price  NUMERIC(12, 2) NOT NULL,
    currency    CHAR(3)        NOT NULL
);

-- Warehouse (WMS): what we actually received
CREATE TABLE goods_receipts (
    grn_id       VARCHAR(10) PRIMARY KEY,
    po_id        VARCHAR(10) NOT NULL REFERENCES purchase_orders (po_id),
    receipt_date DATE        NOT NULL,
    sku          VARCHAR(10) NOT NULL,
    qty_received INTEGER     NOT NULL CHECK (qty_received >= 0)
);

-- Accounts Payable: what suppliers billed us.
-- po_id is nullable on purpose: invoices without a PO are one of the issues we look for.
-- invoice_number is NOT unique on purpose: duplicates are another issue.
CREATE TABLE supplier_invoices (
    invoice_id     VARCHAR(10)    PRIMARY KEY,
    invoice_number VARCHAR(30)    NOT NULL,
    supplier_id    VARCHAR(10)    NOT NULL REFERENCES suppliers (supplier_id),
    po_id          VARCHAR(10)    REFERENCES purchase_orders (po_id),
    invoice_date   DATE           NOT NULL,
    qty_invoiced   INTEGER,
    unit_price     NUMERIC(12, 2),
    amount         NUMERIC(14, 2) NOT NULL,
    currency       CHAR(3)        NOT NULL
);

-- Bank statement: what we actually paid.
-- No foreign key to invoices: the bank only knows the free-text transfer title.
CREATE TABLE bank_payments (
    payment_id       VARCHAR(10)    PRIMARY KEY,
    payment_date     DATE           NOT NULL,
    beneficiary_name VARCHAR(100)   NOT NULL,
    transfer_title   VARCHAR(100),
    amount_pln       NUMERIC(14, 2) NOT NULL
);

-- Indexes for the join columns used in 03_reconciliation.sql
CREATE INDEX ix_grn_po          ON goods_receipts (po_id);
CREATE INDEX ix_inv_po          ON supplier_invoices (po_id);
CREATE INDEX ix_inv_number      ON supplier_invoices (supplier_id, invoice_number);
CREATE INDEX ix_pay_title       ON bank_payments (transfer_title);
