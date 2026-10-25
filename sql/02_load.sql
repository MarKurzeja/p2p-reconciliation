-- =====================================================================
-- Load the generated CSV files into PostgreSQL.
-- Run from the project root (paths are relative to where you run psql):
--   psql -d p2p -f sql/02_load.sql
-- Order matters: parent tables first, because of the foreign keys.
-- =====================================================================

TRUNCATE bank_payments, supplier_invoices, goods_receipts,
         purchase_orders, suppliers, fx_rates;

\copy suppliers         FROM 'data/suppliers.csv'         WITH (FORMAT csv, HEADER true)
\copy fx_rates          FROM 'data/fx_rates.csv'          WITH (FORMAT csv, HEADER true)
\copy purchase_orders   FROM 'data/purchase_orders.csv'   WITH (FORMAT csv, HEADER true)
\copy goods_receipts    FROM 'data/goods_receipts.csv'    WITH (FORMAT csv, HEADER true)
\copy supplier_invoices FROM 'data/supplier_invoices.csv' WITH (FORMAT csv, HEADER true)
\copy bank_payments     FROM 'data/bank_payments.csv'     WITH (FORMAT csv, HEADER true)

-- Row counts after loading
SELECT 'suppliers' AS table_name, COUNT(*) AS row_count FROM suppliers
UNION ALL SELECT 'fx_rates',          COUNT(*) FROM fx_rates
UNION ALL SELECT 'purchase_orders',   COUNT(*) FROM purchase_orders
UNION ALL SELECT 'goods_receipts',    COUNT(*) FROM goods_receipts
UNION ALL SELECT 'supplier_invoices', COUNT(*) FROM supplier_invoices
UNION ALL SELECT 'bank_payments',     COUNT(*) FROM bank_payments;
