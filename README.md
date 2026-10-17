# Stationery Management System

A command-line inventory system for a stationery business, built with **Python, SQLite (SQL), Pandas, NumPy and Matplotlib**.
It manages **products, suppliers, customers, purchases and sales**, keeps stock accurate automatically, and turns
transaction data into reports and charts.

## Features

| Area | What it does |
|---|---|
| **Relational schema** | 5 tables (Products, Suppliers, Customers, Purchases, Sales) with foreign keys |
| **CRUD + validation** | Add / view / update / delete for every entity; typed and range-checked input, e-mail/phone validation, duplicate-product protection |
| **Soft deletes** | Deactivating a product hides it but keeps all sales/purchase history; products can be restored |
| **Atomic transactions** | Every sale/purchase changes stock **and** writes its record in one transaction - both commit or both roll back |
| **Oversell protection** | `UPDATE ... WHERE stock >= qty` checks and decrements in a single statement, so stock can never go negative |
| **Low-stock alerts** | Catalog status flag, a dedicated alert report, and an instant warning when a sale drops an item to its reorder level |
| **Data integrity** | `CHECK` constraints (no negative stock/prices, quantity > 0) and `FOREIGN KEY` enforcement at the database level |
| **Query performance** | Indexes on category, active flag, product and date columns; JOIN/GROUP BY aggregation done in SQL |
| **Pandas / NumPy reports** | Sales & profit, inventory valuation (cost vs retail), top sellers, supplier spend, monthly performance with profit margin % and month-over-month growth |
| **Matplotlib charts** | Sales trend with 3-month moving average, monthly revenue vs profit, top products, inventory distribution, category pie - saved to `charts/` as PNG |

**Accurate profit:** each sale stores the unit cost *at the time of sale*, so changing a product's cost later never rewrites past profit.
Databases created by older versions are migrated automatically.

## Run it

```bash
pip install -r requirements.txt
python Stationery_Management_System.py             # keep existing data (demo data seeded on first run)
python Stationery_Management_System.py --reset     # wipe the DB and reseed demo data
python Stationery_Management_System.py --db my.db  # use a different database file
```

## Tests

```bash
python -m unittest -v
```

23 tests cover the schema, constraints, index usage (`EXPLAIN QUERY PLAN`), atomic rollback, oversell blocking,
soft deletes, validation, profit/margin/growth maths, chart output and database migration.
