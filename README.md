# Stationery Management System

A command-line inventory system for a stationery business, built with **Python, SQLite, Pandas, NumPy, and Matplotlib**. It manages products, suppliers, customers, purchases, and sales, keeps stock accurate, and generates reports and charts from transaction data.

## Tech Stack

- **Language:** Python
- **Database:** SQLite
- **Data Analysis:** Pandas, NumPy
- **Visualisation:** Matplotlib
- **Testing:** Python `unittest`

## Features

| **Area** | **What it does** |
|---|---|
| **Relational schema** | Uses five tables: Products, Suppliers, Customers, Purchases, and Sales, with foreign-key relationships. |
| **CRUD and validation** | Supports adding, viewing, updating, and deleting records, with input validation, email/phone checks, and duplicate-product protection. |
| **Soft deletes** | Deactivates products without removing their past sales and purchase history; products can be restored. |
| **Atomic transactions** | Updates stock and records each sale or purchase in one transaction, so both changes succeed or roll back together. |
| **Oversell protection** | Checks available stock when processing a sale to prevent stock from becoming negative. |
| **Low-stock alerts** | Flags products with low stock and provides a report, with a warning when a sale reaches the reorder level. |
| **Data integrity** | Uses database constraints to prevent invalid values and foreign-key enforcement to protect relationships. |
| **Query performance** | Uses indexes for common lookups and performs grouped reporting queries in SQL. |
| **Pandas and NumPy reports** | Calculates sales and profit, inventory value, top-selling products, supplier spending, and monthly performance. |
| **Matplotlib charts** | Creates charts for sales trends, monthly revenue and profit, top products, inventory distribution, and categories. Charts are saved as PNG files in `charts/`. |

**Accurate profit:** Each sale stores the unit cost at the time of sale, so changing a product's cost later does not change the profit recorded for earlier sales. Databases created by older versions are migrated automatically.

## Run It

Install the required packages:

```bash
pip install -r requirements.txt
```

Run the application while keeping existing data:

```bash
python Stationery_Management_System.py
```

Demo data is seeded on the first run.

Reset the database and reseed demo data:

```bash
python Stationery_Management_System.py --reset
```

Use a different database file:

```bash
python Stationery_Management_System.py --db my.db
```

## Tests

Run the tests with:

```bash
python -m unittest -v
```

The project includes 23 tests covering the database schema and constraints, index usage, transaction rollback, oversell prevention, soft deletes, validation, profit and margin calculations, growth calculations, chart output, and database migration.

## Project Structure

```text
Stationery-Management-System/
├── Stationery_Management_System.py
├── test_stationery.py
├── requirements.txt
├── README.md
├── .gitignore
└── charts/
    ├── category_distribution.png
    ├── inventory_distribution.png
    ├── monthly_performance.png
    ├── sales_trend.png
    └── top_products.png
```

## What I Learned

Through this project, I practised building a command-line application, working with relational databases and SQL transactions, validating user input, writing automated tests, analysing data with Pandas and NumPy, and creating charts with Matplotlib.

## Author

**Aadarsh Dixit**  
[GitHub Profile](https://github.com/aadarshdixit100-ops)
