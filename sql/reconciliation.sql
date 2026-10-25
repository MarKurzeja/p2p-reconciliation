-- =====================================================================
-- Procure-to-Pay Reconciliation: PO vs Goods Receipt vs Invoice vs Bank
-- Tested on SQLite 3.53. Also works on PostgreSQL / SQL Server with
-- small changes (marked with a DIALECT comment).
-- Tables: suppliers, purchase_orders, goods_receipts,
--         supplier_invoices, bank_payments, fx_rates
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. 3-WAY MATCH at purchase-order level (PO + goods receipt + invoice)
--     Every PO gets exactly one category.
--     FULL OUTER JOIN so that invoices without a PO are caught too.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_three_way;
CREATE VIEW v_three_way AS
WITH grn AS (
    SELECT po_id,
           SUM(qty_received)  AS qty_received,
           MAX(receipt_date)  AS receipt_date
    FROM goods_receipts
    GROUP BY po_id
),
inv AS (                                   -- only the first invoice with a given number;
    SELECT *                               -- duplicates are counted separately in Q2
    FROM (
        SELECT i.*,
               ROW_NUMBER() OVER (PARTITION BY supplier_id, invoice_number
                                  ORDER BY invoice_date, invoice_id) AS rn
        FROM supplier_invoices i
    ) x
    WHERE rn = 1
)
SELECT
    COALESCE(po.po_id, inv.po_id)                    AS po_id,
    inv.invoice_id,
    COALESCE(po.supplier_id, inv.supplier_id)        AS supplier_id,
    COALESCE(po.currency, inv.currency)              AS currency,
    po.po_date,
    po.qty_ordered,
    po.unit_price                                    AS po_price,
    grn.qty_received,
    grn.receipt_date,
    inv.qty_invoiced,
    inv.unit_price                                   AS invoice_price,
    inv.invoice_date,
    inv.amount                                       AS invoice_amount,
    CASE
        WHEN po.po_id IS NULL                             THEN 'INVOICE_WITHOUT_PO'
        WHEN inv.invoice_id IS NULL                       THEN 'PO_NOT_INVOICED'
        WHEN grn.po_id IS NULL                            THEN 'INVOICE_WITHOUT_RECEIPT'
        WHEN inv.qty_invoiced > grn.qty_received          THEN 'QTY_INVOICED_ABOVE_RECEIVED'
        WHEN inv.unit_price > po.unit_price * 1.01        THEN 'PRICE_ABOVE_PO'
        WHEN inv.invoice_date < grn.receipt_date          THEN 'MATCHED_TIMING'   -- invoice before receipt
        ELSE 'MATCHED'
    END AS match_status,
    -- amount (in invoice currency) billed above what was received / ordered
    CASE
        WHEN po.po_id IS NULL OR grn.po_id IS NULL THEN inv.amount
        ELSE ROUND(inv.amount - MIN(inv.qty_invoiced, grn.qty_received) * po.unit_price, 2)
             -- DIALECT: PostgreSQL -> LEAST(...)
    END AS overbilled_amount
FROM purchase_orders po
LEFT JOIN grn ON grn.po_id = po.po_id
FULL OUTER JOIN inv ON inv.po_id = po.po_id;


-- Q1 summary
SELECT match_status,
       COUNT(*)                               AS n_records,
       ROUND(SUM(COALESCE(overbilled_amount, 0)), 2) AS overbilled_in_invoice_ccy
FROM v_three_way
GROUP BY match_status
ORDER BY n_records DESC;


-- ---------------------------------------------------------------------
-- Q2. DUPLICATE INVOICES – same number from the same supplier
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_duplicate_invoices;
CREATE VIEW v_duplicate_invoices AS
SELECT *
FROM (
    SELECT i.*,
           ROW_NUMBER() OVER (PARTITION BY supplier_id, invoice_number
                              ORDER BY invoice_date, invoice_id) AS rn,
           COUNT(*)     OVER (PARTITION BY supplier_id, invoice_number) AS copies
    FROM supplier_invoices i
) x
WHERE rn > 1;

SELECT COUNT(*) AS duplicate_invoices, ROUND(SUM(amount), 2) AS duplicated_amount
FROM v_duplicate_invoices;


-- ---------------------------------------------------------------------
-- Q3. PAYMENTS vs INVOICES – stage 1: exact match on the transfer
--     title (= invoice number). The rest goes to Python (fuzzy matching).
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_payment_match_exact;
CREATE VIEW v_payment_match_exact AS
SELECT
    p.payment_id,
    p.payment_date,
    p.beneficiary_name,
    p.transfer_title,
    p.amount_pln,
    i.invoice_id,
    i.invoice_number,
    i.supplier_id,
    i.currency,
    i.amount                                         AS invoice_amount,
    -- invoice amount in PLN at the payment-date FX rate
    ROUND(CASE WHEN i.currency = 'EUR' THEN i.amount * fx.eur_pln
               ELSE i.amount END, 2)                 AS expected_pln,
    ROW_NUMBER() OVER (PARTITION BY i.invoice_number, i.supplier_id
                       ORDER BY p.payment_date)      AS pay_seq
FROM bank_payments p
JOIN supplier_invoices i
     ON i.invoice_number = p.transfer_title
    AND i.invoice_id = (SELECT MIN(i2.invoice_id) FROM supplier_invoices i2
                        WHERE i2.invoice_number = p.transfer_title)
LEFT JOIN fx_rates fx ON fx.rate_date = p.payment_date;

-- Classify matched payments
DROP VIEW IF EXISTS v_payment_status;
CREATE VIEW v_payment_status AS
SELECT *,
       ROUND(amount_pln - expected_pln, 2) AS diff_pln,
       CASE
           WHEN pay_seq > 1                                        THEN 'PAID_TWICE'
           WHEN ABS(amount_pln - expected_pln) <= 1                THEN 'PAYMENT_MATCHED'
           WHEN amount_pln < expected_pln * 0.95                   THEN 'PARTIAL_PAYMENT'
           WHEN currency = 'EUR'                                   THEN 'FX_DIFFERENCE'
           ELSE 'AMOUNT_MISMATCH'
       END AS payment_status
FROM v_payment_match_exact;

SELECT payment_status, COUNT(*) AS n, ROUND(SUM(diff_pln), 2) AS diff_pln
FROM v_payment_status
GROUP BY payment_status
ORDER BY n DESC;


-- Payments not matched by title -> input for fuzzy matching in Python
SELECT p.*
FROM bank_payments p
WHERE NOT EXISTS (
    SELECT 1 FROM supplier_invoices i WHERE i.invoice_number = p.transfer_title
);


-- Invoices without any payment (for aging analysis: unpaid, or simply not yet due?)
SELECT i.*
FROM supplier_invoices i
WHERE NOT EXISTS (
    SELECT 1 FROM bank_payments p WHERE p.transfer_title = i.invoice_number
);


-- ---------------------------------------------------------------------
-- Q4. SUMMARY REPORT – all discrepancy categories in one table
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_discrepancy_report;
CREATE VIEW v_discrepancy_report AS
SELECT 'three_way' AS source, po_id AS ref_id, supplier_id,
       match_status AS discrepancy_type, overbilled_amount AS amount, currency
FROM v_three_way
WHERE match_status NOT IN ('MATCHED', 'MATCHED_TIMING')
UNION ALL
SELECT 'duplicate', invoice_id, supplier_id, 'DUPLICATE_INVOICE', amount, currency
FROM v_duplicate_invoices
UNION ALL
SELECT 'payment', payment_id, supplier_id, payment_status, diff_pln, 'PLN'
FROM v_payment_status
WHERE payment_status NOT IN ('PAYMENT_MATCHED');

SELECT discrepancy_type, COUNT(*) AS n
FROM v_discrepancy_report
GROUP BY discrepancy_type
ORDER BY n DESC;


-- ---------------------------------------------------------------------
-- Q5. MONTHLY TREND – are discrepancies growing over time? (CTE)
-- ---------------------------------------------------------------------
WITH monthly AS (
    SELECT SUBSTR(po_date, 1, 7) AS month,          -- DIALECT: PostgreSQL -> TO_CHAR(po_date,'YYYY-MM')
           COUNT(*) AS n_po,
           SUM(CASE WHEN match_status IN ('MATCHED', 'MATCHED_TIMING') THEN 1 ELSE 0 END) AS n_matched
    FROM v_three_way
    WHERE po_date IS NOT NULL
    GROUP BY SUBSTR(po_date, 1, 7)
)
SELECT month, n_po, n_matched,
       ROUND(100.0 * n_matched / n_po, 1) AS match_rate_pct
FROM monthly
ORDER BY month;


-- ---------------------------------------------------------------------
-- Q6. SUPPLIER RANKING by number of discrepancies (RANK) – where to start
-- ---------------------------------------------------------------------
SELECT s.supplier_name,
       COUNT(*) AS n_discrepancies,
       RANK() OVER (ORDER BY COUNT(*) DESC) AS rnk
FROM v_discrepancy_report d
JOIN suppliers s ON s.supplier_id = d.supplier_id
GROUP BY s.supplier_name
ORDER BY rnk
LIMIT 10;
