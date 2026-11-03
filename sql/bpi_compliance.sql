-- =====================================================================
-- BPI Challenge 2019: Purchase-to-Pay process compliance checks (SQLite dialect)
-- Run: python run.py sql sql/bpi_compliance.sql bpi_data
-- Tables from xes_to_csv.py: po_items, goods_receipts, invoices,
-- invoice_clearing, events.  Tested on SQLite.
-- DIALECT: julianday() -> PostgreSQL: EXTRACT(EPOCH FROM (b - a)) / 86400
-- =====================================================================


-- C1. How many items are in each matching category?
SELECT item_category,
       COUNT(*) AS n_items,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM po_items
GROUP BY item_category
ORDER BY n_items DESC;


-- Helper view: first timestamp of each step per item
DROP VIEW IF EXISTS v_item_steps;
CREATE VIEW v_item_steps AS
SELECT p.case_id,
       p.vendor,
       p.item_category,
       p.goods_receipt_required,
       p.po_created_at,
       p.po_value_eur,
       (SELECT MIN(timestamp) FROM goods_receipts g   WHERE g.case_id = p.case_id) AS first_gr_at,
       (SELECT MIN(timestamp) FROM invoices i         WHERE i.case_id = p.case_id) AS first_invoice_at,
       (SELECT MIN(timestamp) FROM invoice_clearing c WHERE c.case_id = p.case_id) AS first_clear_at,
       (SELECT SUM(net_worth_eur) FROM invoices i     WHERE i.case_id = p.case_id) AS invoiced_value
FROM po_items p;


-- C2. Process violation: category "invoice AFTER GR", but the invoice came BEFORE the goods receipt
SELECT case_id, vendor, first_invoice_at, first_gr_at
FROM v_item_steps
WHERE item_category = '3-way match, invoice after GR'
  AND first_invoice_at < first_gr_at
ORDER BY first_invoice_at;


-- C3. Invoice cleared (paid) although a goods receipt is required and missing
SELECT case_id, vendor, item_category, first_clear_at
FROM v_item_steps
WHERE goods_receipt_required = 1          -- 'true' in the CSV; check the type after import
  AND first_clear_at IS NOT NULL
  AND first_gr_at IS NULL;


-- C4. Cleared before goods receipt in 3-way match (paying for goods not yet received)
SELECT case_id, vendor, item_category, first_clear_at, first_gr_at
FROM v_item_steps
WHERE item_category LIKE '3-way%'
  AND first_clear_at < first_gr_at;


-- C5. Invoiced value vs PO value (difference > 1%)
--     NOTE: the attribute is called "Cumulative net worth". First check on a few
--     items whether the value grows with each invoice. If it does, replace SUM
--     with MAX in the v_item_steps view (invoiced_value column).
SELECT case_id, vendor, po_value_eur, invoiced_value,
       ROUND(invoiced_value - po_value_eur, 2) AS diff_value
FROM v_item_steps
WHERE invoiced_value IS NOT NULL
  AND ABS(invoiced_value - po_value_eur) > 0.01 * po_value_eur
ORDER BY ABS(invoiced_value - po_value_eur) DESC;


-- C6. Days from invoice to clearing, per category
SELECT item_category,
       COUNT(*) AS n,
       ROUND(AVG(julianday(first_clear_at) - julianday(first_invoice_at)), 1) AS avg_days_invoice_to_clear
FROM v_item_steps
WHERE first_clear_at IS NOT NULL AND first_invoice_at IS NOT NULL
GROUP BY item_category;


-- C7. Supplier ranking by number of violations (C2 + C3 + C4)
WITH violations AS (
    SELECT vendor FROM v_item_steps
    WHERE item_category = '3-way match, invoice after GR' AND first_invoice_at < first_gr_at
    UNION ALL
    SELECT vendor FROM v_item_steps
    WHERE goods_receipt_required = 1 AND first_clear_at IS NOT NULL AND first_gr_at IS NULL
    UNION ALL
    SELECT vendor FROM v_item_steps
    WHERE item_category LIKE '3-way%' AND first_clear_at < first_gr_at
)
SELECT vendor, COUNT(*) AS n_violations,
       RANK() OVER (ORDER BY COUNT(*) DESC) AS rnk
FROM violations
GROUP BY vendor
ORDER BY rnk
LIMIT 10;
