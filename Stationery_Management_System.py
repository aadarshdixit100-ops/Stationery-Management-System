'''
STATIONERY MANAGEMENT SYSTEM

A command-line inventory system built with Python, SQLite, Pandas, NumPy
and Matplotlib.

Highlights:
    * 5-table relational schema with CHECK + FOREIGN KEY constraints and indexes
    * Atomic transactions: stock change + sale/purchase record commit or roll back together
    * Oversell protection, low-stock alerts, soft deletes (history is never lost)
    * Pandas/NumPy reports (profit margin, growth, valuation) and Matplotlib charts

Usage:
    python Stationery_Management_System.py             # keep existing data
    python Stationery_Management_System.py --reset     # wipe DB and reseed demo data
    python Stationery_Management_System.py --db my.db  # use a different database file
'''

import argparse
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

pd.set_option('display.max_rows', None)
pd.set_option('display.width', 1000)
pd.set_option('display.colheader_justify', 'center')

CURRENCY = "₹"
CHARTS_DIR = "charts"


def clear_screen():
    """Clears the terminal screen for a cleaner user experience."""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(title, icon="🏢"):
    width = 64
    print("=" * width)
    print(f" {icon}  {title}".center(width))
    print("=" * width)


def print_df(df, title=None):
    """Pretty-prints a DataFrame using tabulate if available, else pandas."""
    if title:
        print(f"\n--- {title} ---")
    if df.empty:
        print("(No records found)\n")
        return
    if HAS_TABULATE:
        shown = df.astype(object).where(df.notna(), None)  # NaN -> "-" in the table
        print(tabulate(shown, headers="keys", tablefmt="fancy_grid", showindex=False, missingval="-"))
    else:
        print(df.to_string(index=False, na_rep="-"))
    print()


def money(value):
    return f"{CURRENCY}{value:,.2f}"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def validate_contact(phone=None, email=None):
    """Raises ValueError if a phone number or e-mail address is malformed (blank is allowed)."""
    if phone and not PHONE_RE.match(re.sub(r"[\s\-()]", "", phone)):
        raise ValueError("Phone must contain 7-15 digits (an optional leading + is allowed).")
    if email and not EMAIL_RE.match(email):
        raise ValueError("Email address is not valid.")


def finish_plot(filename):
    """Saves the current figure as a PNG in CHARTS_DIR, then shows it (unless running headless)."""
    plt.tight_layout()
    os.makedirs(CHARTS_DIR, exist_ok=True)
    path = os.path.join(CHARTS_DIR, filename)
    plt.savefig(path, dpi=150)
    print(f"💾 Chart saved to {path}")
    if "agg" not in matplotlib.get_backend().lower():
        plt.show()
    plt.close()


# ==========================================================================
# CORE SYSTEM CLASS
# ==========================================================================
class StationerySystem:
    # Column name -> type used to validate values typed in the "update" menus
    PRODUCT_FIELD_TYPES = {
        "name": str, "category": str,
        "cost_price": float, "selling_price": float,
        "reorder_level": int, "supplier_id": int,
    }

    def __init__(self, db_name="stationery.db"):
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        self.setup_database()

    # ---------------------------------------------------------------- #
    # SCHEMA SETUP
    # ---------------------------------------------------------------- #
    def setup_database(self):
        # CHECK constraints make the database itself reject bad data
        # (negative stock, zero/negative quantities, negative prices).
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS Suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                address TEXT
            );

            CREATE TABLE IF NOT EXISTS Customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT
            );

            CREATE TABLE IF NOT EXISTS Products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT,
                cost_price REAL NOT NULL CHECK (cost_price >= 0),
                selling_price REAL NOT NULL CHECK (selling_price >= 0),
                stock INTEGER DEFAULT 0 CHECK (stock >= 0),
                reorder_level INTEGER DEFAULT 10 CHECK (reorder_level >= 0),
                supplier_id INTEGER,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (supplier_id) REFERENCES Suppliers(id)
            );

            CREATE TABLE IF NOT EXISTS Sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                customer_id INTEGER,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price REAL NOT NULL,
                unit_cost REAL NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
                sale_date DATE NOT NULL,
                FOREIGN KEY (product_id) REFERENCES Products(id),
                FOREIGN KEY (customer_id) REFERENCES Customers(id)
            );

            CREATE TABLE IF NOT EXISTS Purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                supplier_id INTEGER,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_cost REAL NOT NULL CHECK (unit_cost >= 0),
                purchase_date DATE NOT NULL,
                FOREIGN KEY (product_id) REFERENCES Products(id),
                FOREIGN KEY (supplier_id) REFERENCES Suppliers(id)
            );

            -- Indexes to speed up the most common lookups/joins
            CREATE INDEX IF NOT EXISTS idx_products_category ON Products(category);
            CREATE INDEX IF NOT EXISTS idx_products_active ON Products(is_active);
            CREATE INDEX IF NOT EXISTS idx_sales_product ON Sales(product_id);
            CREATE INDEX IF NOT EXISTS idx_sales_date ON Sales(sale_date);
            CREATE INDEX IF NOT EXISTS idx_purchases_product ON Purchases(product_id);
            CREATE INDEX IF NOT EXISTS idx_purchases_date ON Purchases(purchase_date);
        ''')
        self._migrate_sales_unit_cost()
        self.conn.commit()

    def _migrate_sales_unit_cost(self):
        """Older databases have no Sales.unit_cost. Add it and backfill from the product's
        current cost so existing data keeps working; new sales record the cost at sale time."""
        cols = [row[1] for row in self.conn.execute("PRAGMA table_info(Sales)")]
        if "unit_cost" not in cols:
            self.conn.execute("ALTER TABLE Sales ADD COLUMN unit_cost REAL NOT NULL DEFAULT 0")
            self.conn.execute(
                "UPDATE Sales SET unit_cost = (SELECT cost_price FROM Products WHERE Products.id = Sales.product_id)")

    def is_empty(self):
        return self.conn.execute("SELECT COUNT(*) FROM Products").fetchone()[0] == 0

    # ---------------------------------------------------------------- #
    # SUPPLIER CRUD
    # ---------------------------------------------------------------- #
    def add_supplier(self, name, contact_person, phone, email, address):
        """Raises ValueError for a blank name or malformed phone/email."""
        if not (name or "").strip():
            raise ValueError("Supplier name cannot be blank.")
        validate_contact(phone, email)
        with self.conn:
            cur = self.conn.execute(
                'INSERT INTO Suppliers (name, contact_person, phone, email, address) VALUES (?, ?, ?, ?, ?)',
                (name.strip(), contact_person, phone, email, address)
            )
        return cur.lastrowid

    def view_suppliers(self):
        return pd.read_sql_query("SELECT * FROM Suppliers ORDER BY name", self.conn)

    def update_supplier(self, supplier_id, field, value):
        """Returns (ok, message)."""
        allowed = {"name", "contact_person", "phone", "email", "address"}
        if field not in allowed:
            return False, "Invalid field."
        if field == "name" and not (value or "").strip():
            return False, "Name cannot be blank."
        try:
            validate_contact(phone=value if field == "phone" else None,
                             email=value if field == "email" else None)
        except ValueError as e:
            return False, str(e)
        with self.conn:
            cur = self.conn.execute(f'UPDATE Suppliers SET {field} = ? WHERE id = ?', (value, supplier_id))
        return (True, "Updated.") if cur.rowcount > 0 else (False, "Supplier not found.")

    def delete_supplier(self, supplier_id):
        try:
            with self.conn:
                cur = self.conn.execute('DELETE FROM Suppliers WHERE id = ?', (supplier_id,))
            if cur.rowcount == 0:
                return False, "Supplier not found."
            return True, "Supplier deleted."
        except sqlite3.IntegrityError:
            return False, "Cannot delete: supplier is linked to existing products/purchases."

    # ---------------------------------------------------------------- #
    # CUSTOMER CRUD
    # ---------------------------------------------------------------- #
    def add_customer(self, name, phone, email):
        """Raises ValueError for a blank name or malformed phone/email."""
        if not (name or "").strip():
            raise ValueError("Customer name cannot be blank.")
        validate_contact(phone, email)
        with self.conn:
            cur = self.conn.execute(
                'INSERT INTO Customers (name, phone, email) VALUES (?, ?, ?)',
                (name.strip(), phone, email)
            )
        return cur.lastrowid

    def view_customers(self):
        return pd.read_sql_query("SELECT * FROM Customers ORDER BY name", self.conn)

    def update_customer(self, customer_id, field, value):
        """Returns (ok, message)."""
        allowed = {"name", "phone", "email"}
        if field not in allowed:
            return False, "Invalid field."
        if field == "name" and not (value or "").strip():
            return False, "Name cannot be blank."
        try:
            validate_contact(phone=value if field == "phone" else None,
                             email=value if field == "email" else None)
        except ValueError as e:
            return False, str(e)
        with self.conn:
            cur = self.conn.execute(f'UPDATE Customers SET {field} = ? WHERE id = ?', (value, customer_id))
        return (True, "Updated.") if cur.rowcount > 0 else (False, "Customer not found.")

    def delete_customer(self, customer_id):
        try:
            with self.conn:
                cur = self.conn.execute('DELETE FROM Customers WHERE id = ?', (customer_id,))
            if cur.rowcount == 0:
                return False, "Customer not found."
            return True, "Customer deleted."
        except sqlite3.IntegrityError:
            return False, "Cannot delete: customer has recorded sales history."

    # ---------------------------------------------------------------- #
    # PRODUCT CRUD
    # ---------------------------------------------------------------- #
    def add_product(self, name, category, cost_price, selling_price, initial_stock=0,
                    reorder_level=10, supplier_id=None):
        """Raises ValueError for a blank/duplicate name or negative numbers, and
        sqlite3.IntegrityError for a bad supplier_id."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Product name cannot be blank.")
        if self._active_name_exists(name):
            raise ValueError(f"An active product named '{name}' already exists.")
        if min(cost_price, selling_price, initial_stock, reorder_level) < 0:
            raise ValueError("Prices, stock and reorder level cannot be negative.")
        with self.conn:
            cur = self.conn.execute(
                '''INSERT INTO Products (name, category, cost_price, selling_price, stock, reorder_level, supplier_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (name, category, cost_price, selling_price, initial_stock, reorder_level, supplier_id)
            )
        return cur.lastrowid

    def _active_name_exists(self, name, exclude_id=None):
        row = self.conn.execute(
            "SELECT 1 FROM Products WHERE LOWER(name) = LOWER(?) AND is_active = 1 AND id != ?",
            (name, exclude_id if exclude_id is not None else -1)).fetchone()
        return row is not None

    def view_all_products(self, active_only=True):
        query = '''
            SELECT p.id, p.name, p.category, p.cost_price, p.selling_price,
                   p.stock, p.reorder_level, COALESCE(s.name, '—') AS supplier
            FROM Products p
            LEFT JOIN Suppliers s ON p.supplier_id = s.id
        '''
        if active_only:
            query += " WHERE p.is_active = 1"
        query += " ORDER BY p.category, p.name"
        df = pd.read_sql_query(query, self.conn)
        # Vectorised stock-status flag (NumPy) so low items stand out in the catalog
        df['status'] = np.where(df['stock'] <= df['reorder_level'], 'LOW', 'OK')
        return df

    def search_product(self, keyword):
        query = '''
            SELECT p.id, p.name, p.category, p.stock, p.selling_price, COALESCE(s.name, '—') AS supplier
            FROM Products p
            LEFT JOIN Suppliers s ON p.supplier_id = s.id
            WHERE p.name LIKE ? AND p.is_active = 1
            ORDER BY p.name
        '''
        return pd.read_sql_query(query, self.conn, params=(f"%{keyword or ''}%",))

    def update_product(self, product_id, field, value):
        """Validates and converts the value to the right type. Returns (ok, message)."""
        caster = self.PRODUCT_FIELD_TYPES.get(field)
        if caster is None:
            return False, "Invalid field."
        try:
            value = caster(value)
        except (TypeError, ValueError):
            return False, f"'{value}' is not a valid value for {field}."
        if caster in (int, float) and value < 0:
            return False, f"{field} cannot be negative."
        if field == "name":
            value = value.strip()
            if not value:
                return False, "Name cannot be blank."
            if self._active_name_exists(value, exclude_id=product_id):
                return False, f"An active product named '{value}' already exists."
        try:
            with self.conn:
                cur = self.conn.execute(f'UPDATE Products SET {field} = ? WHERE id = ?', (value, product_id))
        except sqlite3.IntegrityError:
            return False, "Update rejected (for example, that supplier ID does not exist)."
        if cur.rowcount == 0:
            return False, "Product not found."
        return True, "Updated."

    def delete_product(self, product_id):
        """Soft-delete: deactivates the product but preserves historical sales/purchase records."""
        with self.conn:
            cur = self.conn.execute('UPDATE Products SET is_active = 0 WHERE id = ?', (product_id,))
        return cur.rowcount > 0

    def view_inactive_products(self):
        return pd.read_sql_query(
            "SELECT id, name, category, stock FROM Products WHERE is_active = 0 ORDER BY name", self.conn)

    def restore_product(self, product_id):
        """Reactivates a soft-deleted product. Returns (ok, message)."""
        row = self.conn.execute("SELECT name, is_active FROM Products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return False, "Product not found."
        name, is_active = row
        if is_active:
            return False, "Product is already active."
        if self._active_name_exists(name, exclude_id=product_id):
            return False, f"Cannot restore: another active product is already named '{name}'."
        with self.conn:
            self.conn.execute("UPDATE Products SET is_active = 1 WHERE id = ?", (product_id,))
        return True, f"'{name}' restored."

    def low_stock_report(self):
        query = '''
            SELECT id, name, category, stock, reorder_level
            FROM Products
            WHERE stock <= reorder_level AND is_active = 1
            ORDER BY stock ASC
        '''
        return pd.read_sql_query(query, self.conn)

    # ---------------------------------------------------------------- #
    # TRANSACTIONS (automated stock updates)
    # ---------------------------------------------------------------- #
    # Each method below runs inside `with self.conn:`, which makes the stock
    # change and the sales/purchase record ONE atomic transaction: it commits
    # if everything succeeds and rolls back automatically if anything fails.
    def record_sale(self, product_id, quantity, customer_id=None, sale_date=None):
        if not isinstance(quantity, int) or quantity <= 0:
            return False, "Quantity must be a positive whole number."
        sale_date = sale_date or datetime.now().strftime("%Y-%m-%d")

        try:
            with self.conn:
                row = self.conn.execute(
                    'SELECT name, selling_price, cost_price, stock, reorder_level '
                    'FROM Products WHERE id = ? AND is_active = 1',
                    (product_id,)
                ).fetchone()
                if not row:
                    return False, "Product ID not found or inactive."
                product_name, selling_price, cost_price, current_stock, reorder_level = row

                # Decrement only if enough stock remains (check + update in one statement)
                cur = self.conn.execute(
                    'UPDATE Products SET stock = stock - ? WHERE id = ? AND stock >= ?',
                    (quantity, product_id, quantity)
                )
                if cur.rowcount == 0:
                    return False, f"Insufficient stock for '{product_name}'. Only {current_stock} left."

                self.conn.execute(
                    'INSERT INTO Sales (product_id, customer_id, quantity, unit_price, unit_cost, sale_date) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (product_id, customer_id, quantity, selling_price, cost_price, sale_date)
                )
        except sqlite3.IntegrityError:
            return False, "Sale rejected and rolled back (check that the customer ID exists)."
        except sqlite3.Error as e:
            return False, f"Database error, transaction rolled back: {e}"

        remaining = current_stock - quantity
        msg = (f"Sold {quantity}x '{product_name}'. Remaining stock: {remaining}. "
               f"Revenue: {money(quantity * selling_price)}")
        if remaining <= reorder_level:
            msg += f"\n⚠️  LOW STOCK: '{product_name}' is at or below its reorder level ({reorder_level}). Restock soon."
        return True, msg

    def record_purchase(self, product_id, quantity, supplier_id=None, purchase_date=None, unit_cost=None):
        """unit_cost is the price actually paid; defaults to the product's listed cost price."""
        if not isinstance(quantity, int) or quantity <= 0:
            return False, "Quantity must be a positive whole number."
        if unit_cost is not None and unit_cost < 0:
            return False, "Unit cost cannot be negative."
        purchase_date = purchase_date or datetime.now().strftime("%Y-%m-%d")

        try:
            with self.conn:
                row = self.conn.execute(
                    'SELECT name, stock, cost_price FROM Products WHERE id = ? AND is_active = 1',
                    (product_id,)
                ).fetchone()
                if not row:
                    return False, "Product ID not found or inactive."
                product_name, current_stock, cost_price = row
                paid = cost_price if unit_cost is None else unit_cost

                self.conn.execute(
                    'INSERT INTO Purchases (product_id, supplier_id, quantity, unit_cost, purchase_date) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (product_id, supplier_id, quantity, paid, purchase_date)
                )
                self.conn.execute('UPDATE Products SET stock = stock + ? WHERE id = ?', (quantity, product_id))
        except sqlite3.IntegrityError:
            return False, "Purchase rejected and rolled back (check that the supplier ID exists)."
        except sqlite3.Error as e:
            return False, f"Database error, transaction rolled back: {e}"

        return True, (f"Restocked {quantity}x '{product_name}'. New stock: {current_stock + quantity}. "
                      f"Cost: {money(quantity * paid)}")

    def sales_history(self, limit=50):
        query = '''
            SELECT s.id, s.sale_date, p.name AS product, COALESCE(c.name, 'Walk-in') AS customer,
                   s.quantity, s.unit_price, (s.quantity * s.unit_price) AS total
            FROM Sales s
            JOIN Products p ON s.product_id = p.id
            LEFT JOIN Customers c ON s.customer_id = c.id
            ORDER BY s.sale_date DESC, s.id DESC
            LIMIT ?
        '''
        return pd.read_sql_query(query, self.conn, params=(limit,))

    def purchase_history(self, limit=50):
        query = '''
            SELECT pu.id, pu.purchase_date, p.name AS product, COALESCE(s.name, 'Unknown') AS supplier,
                   pu.quantity, pu.unit_cost, (pu.quantity * pu.unit_cost) AS total
            FROM Purchases pu
            JOIN Products p ON pu.product_id = p.id
            LEFT JOIN Suppliers s ON pu.supplier_id = s.id
            ORDER BY pu.purchase_date DESC, pu.id DESC
            LIMIT ?
        '''
        return pd.read_sql_query(query, self.conn, params=(limit,))

    # ---------------------------------------------------------------- #
    # REPORTS (Pandas / NumPy-powered)
    # ---------------------------------------------------------------- #
    def generate_sales_report(self):
        query = '''
            SELECT s.sale_date, p.name AS product_name, p.category, s.quantity,
                   (s.quantity * s.unit_price) AS revenue,
                   (s.quantity * (s.unit_price - s.unit_cost)) AS profit
            FROM Sales s
            JOIN Products p ON s.product_id = p.id
            ORDER BY s.sale_date DESC
        '''
        return pd.read_sql_query(query, self.conn)

    def inventory_valuation_report(self):
        query = '''
            SELECT category,
                   COUNT(*) AS product_count,
                   SUM(stock) AS total_units,
                   SUM(stock * cost_price) AS stock_value_at_cost,
                   SUM(stock * selling_price) AS stock_value_at_retail
            FROM Products
            WHERE is_active = 1
            GROUP BY category
            ORDER BY stock_value_at_retail DESC
        '''
        return pd.read_sql_query(query, self.conn)

    def top_selling_products(self, top_n=10):
        query = '''
            SELECT p.name, p.category, SUM(s.quantity) AS units_sold,
                   SUM(s.quantity * s.unit_price) AS revenue,
                   SUM(s.quantity * (s.unit_price - s.unit_cost)) AS profit
            FROM Sales s
            JOIN Products p ON s.product_id = p.id
            GROUP BY p.id
            ORDER BY revenue DESC
            LIMIT ?
        '''
        return pd.read_sql_query(query, self.conn, params=(top_n,))

    def monthly_performance_report(self):
        """SQL aggregates sales per month; Pandas/NumPy then add profit margin and month-over-month growth."""
        query = '''
            SELECT strftime('%Y-%m', s.sale_date) AS month,
                   SUM(s.quantity) AS units_sold,
                   SUM(s.quantity * s.unit_price) AS revenue,
                   SUM(s.quantity * (s.unit_price - s.unit_cost)) AS profit
            FROM Sales s
            JOIN Products p ON s.product_id = p.id
            GROUP BY month
            ORDER BY month
        '''
        df = pd.read_sql_query(query, self.conn)
        if df.empty:
            return df
        df['margin_pct'] = np.where(df['revenue'] > 0, df['profit'] / df['revenue'] * 100, 0.0).round(2)
        df['revenue_growth_pct'] = (df['revenue'].pct_change() * 100).round(2)
        return df

    def supplier_purchase_report(self):
        query = '''
            SELECT COALESCE(s.name, 'Unknown') AS supplier,
                   COUNT(pu.id) AS orders_placed,
                   SUM(pu.quantity) AS units_purchased,
                   SUM(pu.quantity * pu.unit_cost) AS total_spend
            FROM Purchases pu
            LEFT JOIN Suppliers s ON pu.supplier_id = s.id
            GROUP BY supplier
            ORDER BY total_spend DESC
        '''
        return pd.read_sql_query(query, self.conn)

    # ---------------------------------------------------------------- #
    # CHARTS (Matplotlib)
    # ---------------------------------------------------------------- #
    def plot_inventory_distribution(self):
        df = pd.read_sql_query(
            "SELECT name, stock FROM Products WHERE is_active = 1 ORDER BY stock DESC LIMIT 15", self.conn)
        if df.empty:
            print("Not enough data to plot.")
            return
        plt.figure(figsize=(12, 6))
        plt.bar(df['name'], df['stock'], color='#2ca02c', edgecolor='black')
        plt.title('Top 15 Products by Stock Quantity', fontsize=16, fontweight='bold')
        plt.xlabel('Product')
        plt.ylabel('Units in Stock')
        plt.xticks(rotation=45, ha='right')
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        finish_plot('inventory_distribution.png')

    def plot_sales_trend(self):
        df = self.monthly_performance_report()
        if df.empty:
            print("Not enough sales data to plot.")
            return
        plt.figure(figsize=(11, 6))
        plt.plot(df['month'], df['revenue'], marker='o', linewidth=2, label='Revenue', color='#1f77b4')
        plt.plot(df['month'], df['profit'], marker='s', linewidth=2, label='Profit', color='#ff7f0e')

        # 3-month moving average of revenue, computed with NumPy
        window = 3
        if len(df) >= window:
            moving_avg = np.convolve(df['revenue'].to_numpy(), np.ones(window) / window, mode='valid')
            plt.plot(df['month'].iloc[window - 1:], moving_avg, linestyle='--', linewidth=2,
                     color='gray', label=f'{window}-month avg revenue')

        plt.title('Sales Trend Over Time', fontsize=16, fontweight='bold')
        plt.xlabel('Month')
        plt.ylabel(f'Amount ({CURRENCY})')
        plt.legend()
        plt.grid(linestyle='--', alpha=0.6)
        plt.xticks(rotation=45, ha='right')
        finish_plot('sales_trend.png')

    def plot_monthly_performance(self):
        df = self.monthly_performance_report()
        if df.empty:
            print("Not enough sales data to plot.")
            return
        x = np.arange(len(df))
        width = 0.35
        plt.figure(figsize=(11, 6))
        plt.bar(x - width / 2, df['revenue'], width=width, label='Revenue', color='#4c72b0')
        plt.bar(x + width / 2, df['profit'], width=width, label='Profit', color='#55a868')
        plt.xticks(x, df['month'], rotation=45, ha='right')
        plt.title('Monthly Revenue vs. Profit', fontsize=16, fontweight='bold')
        plt.ylabel(f'Amount ({CURRENCY})')
        plt.legend()
        plt.grid(axis='y', linestyle='--', alpha=0.6)
        finish_plot('monthly_performance.png')

    def plot_category_distribution(self):
        df = pd.read_sql_query(
            "SELECT category, SUM(stock) AS total_stock FROM Products WHERE is_active = 1 GROUP BY category",
            self.conn)
        if df.empty or df['total_stock'].sum() == 0:
            print("Not enough data to plot.")
            return
        plt.figure(figsize=(8, 8))
        plt.pie(df['total_stock'], labels=df['category'], autopct='%1.1f%%', startangle=140)
        plt.title('Category-wise Stock Distribution', fontsize=16, fontweight='bold')
        finish_plot('category_distribution.png')

    def plot_top_products(self):
        df = self.top_selling_products(10)
        if df.empty:
            print("Not enough sales data to plot.")
            return
        plt.figure(figsize=(11, 6))
        plt.barh(df['name'][::-1], df['revenue'][::-1], color='#c44e52')
        plt.title('Top 10 Products by Revenue', fontsize=16, fontweight='bold')
        plt.xlabel(f'Revenue ({CURRENCY})')
        plt.grid(axis='x', linestyle='--', alpha=0.6)
        finish_plot('top_products.png')

    # ---------------------------------------------------------------- #
    # UTILITIES
    # ---------------------------------------------------------------- #
    def clear_database(self):
        self.cursor.executescript('''
            DELETE FROM Sales; DELETE FROM Purchases; DELETE FROM Products;
            DELETE FROM Suppliers; DELETE FROM Customers;
        ''')
        for table in ("Products", "Sales", "Purchases", "Suppliers", "Customers"):
            self.cursor.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        self.conn.commit()

    def close(self):
        self.conn.close()


# ==========================================================================
# CLI INPUT HELPERS
# ==========================================================================
def get_valid_integer(prompt, allow_blank=False, min_value=None):
    while True:
        raw = input(prompt).strip()
        if allow_blank and raw == "":
            return None
        try:
            value = int(raw)
        except ValueError:
            print("❌ Invalid input! Please enter a whole number.")
            continue
        if min_value is not None and value < min_value:
            print(f"❌ Value must be at least {min_value}.")
            continue
        return value


def get_valid_float(prompt, allow_blank=False, min_value=None):
    while True:
        raw = input(prompt).strip()
        if allow_blank and raw == "":
            return None
        try:
            value = float(raw)
        except ValueError:
            print("❌ Invalid input! Please enter a number/decimal.")
            continue
        if min_value is not None and value < min_value:
            print(f"❌ Value must be at least {min_value}.")
            continue
        return value


def get_text(prompt, allow_blank=True):
    while True:
        val = input(prompt).strip()
        if val or allow_blank:
            return val if val else None
        print("❌ This field cannot be blank.")


def pause():
    input("\nPress ENTER to continue...")


# ==========================================================================
# SUBMENUS
# ==========================================================================
def product_menu(inv):
    while True:
        clear_screen()
        print_header("PRODUCT MANAGEMENT", "📦")
        print("1. View All Products")
        print("2. Search Product")
        print("3. Add New Product")
        print("4. Update Product")
        print("5. Deactivate (Delete) Product")
        print("6. Low Stock Alert")
        print("7. Restore Deactivated Product")
        print("8. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-8): ").strip()
        clear_screen()

        if choice == '1':
            print_df(inv.view_all_products(), "FULL PRODUCT CATALOG")
        elif choice == '2':
            kw = get_text("Enter part of the product name: ")
            print_df(inv.search_product(kw), f"Search results for '{kw}'")
        elif choice == '3':
            print_header("ADD NEW PRODUCT", "➕")
            name = get_text("Product Name: ", allow_blank=False)
            category = get_text("Category (e.g. Writing, Paper, Office, Art): ")
            cost = get_valid_float("Cost Price (₹): ", min_value=0)
            sell = get_valid_float("Selling Price (₹): ", min_value=0)
            stock = get_valid_integer("Initial Stock Quantity: ", min_value=0)
            reorder = get_valid_integer("Reorder Level (min stock before alert) [10]: ",
                                        allow_blank=True, min_value=0)
            reorder = 10 if reorder is None else reorder
            print_df(inv.view_suppliers(), "Available Suppliers")
            sup_id = get_valid_integer("Supplier ID (blank if none): ", allow_blank=True)
            try:
                pid = inv.add_product(name, category, cost, sell, stock, reorder, sup_id)
                print(f"✅ '{name}' added with Product ID {pid}.")
            except (ValueError, sqlite3.IntegrityError) as e:
                print(f"❌ Could not add product: {e}")
        elif choice == '4':
            print_df(inv.view_all_products(), "Products")
            pid = get_valid_integer("Enter Product ID to update: ")
            field = get_text("Field to update (name/category/cost_price/selling_price/reorder_level/supplier_id): ",
                             allow_blank=False)
            value = get_text("New value: ", allow_blank=False)
            ok, msg = inv.update_product(pid, field, value)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '5':
            print_df(inv.view_all_products(), "Products")
            pid = get_valid_integer("Enter Product ID to deactivate: ")
            ok = inv.delete_product(pid)
            print("✅ Product deactivated (history preserved)." if ok else "❌ Product not found.")
        elif choice == '6':
            print_df(inv.low_stock_report(), "⚠️  LOW STOCK ALERT")
        elif choice == '7':
            inactive = inv.view_inactive_products()
            print_df(inactive, "DEACTIVATED PRODUCTS")
            if not inactive.empty:
                pid = get_valid_integer("Enter Product ID to restore: ")
                ok, msg = inv.restore_product(pid)
                print(("✅ " if ok else "❌ ") + msg)
        elif choice == '8':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def supplier_menu(inv):
    while True:
        clear_screen()
        print_header("SUPPLIER MANAGEMENT", "🚚")
        print("1. View All Suppliers")
        print("2. Add New Supplier")
        print("3. Update Supplier")
        print("4. Delete Supplier")
        print("5. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-5): ").strip()
        clear_screen()

        if choice == '1':
            print_df(inv.view_suppliers(), "ALL SUPPLIERS")
        elif choice == '2':
            print_header("ADD NEW SUPPLIER", "➕")
            name = get_text("Supplier / Company Name: ", allow_blank=False)
            contact = get_text("Contact Person: ")
            phone = get_text("Phone: ")
            email = get_text("Email: ")
            address = get_text("Address: ")
            try:
                sid = inv.add_supplier(name, contact, phone, email, address)
                print(f"✅ Supplier '{name}' added with ID {sid}.")
            except ValueError as e:
                print(f"❌ Could not add supplier: {e}")
        elif choice == '3':
            print_df(inv.view_suppliers(), "Suppliers")
            sid = get_valid_integer("Enter Supplier ID to update: ")
            field = get_text("Field to update (name/contact_person/phone/email/address): ", allow_blank=False)
            value = get_text("New value: ", allow_blank=False)
            ok, msg = inv.update_supplier(sid, field, value)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '4':
            print_df(inv.view_suppliers(), "Suppliers")
            sid = get_valid_integer("Enter Supplier ID to delete: ")
            ok, msg = inv.delete_supplier(sid)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '5':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def customer_menu(inv):
    while True:
        clear_screen()
        print_header("CUSTOMER MANAGEMENT", "🧑‍💼")
        print("1. View All Customers")
        print("2. Add New Customer")
        print("3. Update Customer")
        print("4. Delete Customer")
        print("5. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-5): ").strip()
        clear_screen()

        if choice == '1':
            print_df(inv.view_customers(), "ALL CUSTOMERS")
        elif choice == '2':
            print_header("ADD NEW CUSTOMER", "➕")
            name = get_text("Customer Name: ", allow_blank=False)
            phone = get_text("Phone: ")
            email = get_text("Email: ")
            try:
                cid = inv.add_customer(name, phone, email)
                print(f"✅ Customer '{name}' added with ID {cid}.")
            except ValueError as e:
                print(f"❌ Could not add customer: {e}")
        elif choice == '3':
            print_df(inv.view_customers(), "Customers")
            cid = get_valid_integer("Enter Customer ID to update: ")
            field = get_text("Field to update (name/phone/email): ", allow_blank=False)
            value = get_text("New value: ", allow_blank=False)
            ok, msg = inv.update_customer(cid, field, value)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '4':
            print_df(inv.view_customers(), "Customers")
            cid = get_valid_integer("Enter Customer ID to delete: ")
            ok, msg = inv.delete_customer(cid)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '5':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def sales_menu(inv):
    while True:
        clear_screen()
        print_header("SALES / POINT OF SALE", "🛒")
        print("1. Record a Sale")
        print("2. View Recent Sales History")
        print("3. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-3): ").strip()
        clear_screen()

        if choice == '1':
            print_df(inv.view_all_products(), "Products")
            pid = get_valid_integer("Enter Product ID: ")
            qty = get_valid_integer("Enter Quantity Sold: ", min_value=1)
            print_df(inv.view_customers(), "Customers")
            cid = get_valid_integer("Customer ID (blank for walk-in): ", allow_blank=True)
            ok, msg = inv.record_sale(pid, qty, cid)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '2':
            print_df(inv.sales_history(), "RECENT SALES")
        elif choice == '3':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def purchase_menu(inv):
    while True:
        clear_screen()
        print_header("PURCHASES / RESTOCK", "📥")
        print("1. Record a Purchase (Restock)")
        print("2. View Recent Purchase History")
        print("3. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-3): ").strip()
        clear_screen()

        if choice == '1':
            print_df(inv.view_all_products(), "Products")
            pid = get_valid_integer("Enter Product ID: ")
            qty = get_valid_integer("Enter Quantity Purchased: ", min_value=1)
            cost = get_valid_float("Actual unit cost paid (₹) (blank = use listed cost price): ",
                                   allow_blank=True, min_value=0)
            print_df(inv.view_suppliers(), "Suppliers")
            sid = get_valid_integer("Supplier ID (blank if unknown): ", allow_blank=True)
            ok, msg = inv.record_purchase(pid, qty, sid, unit_cost=cost)
            print(("✅ " if ok else "❌ ") + msg)
        elif choice == '2':
            print_df(inv.purchase_history(), "RECENT PURCHASES")
        elif choice == '3':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def reports_menu(inv):
    while True:
        clear_screen()
        print_header("REPORTS & ANALYTICS", "📊")
        print("1. Sales & Profit Report")
        print("2. Inventory Valuation Report (by category)")
        print("3. Top Selling Products")
        print("4. Monthly Performance Summary")
        print("5. Supplier-wise Purchase Report")
        print("6. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-6): ").strip()
        clear_screen()

        if choice == '1':
            df = inv.generate_sales_report()
            print_df(df, "SALES & PROFIT REPORT")
            if not df.empty:
                print(f"💰 Total Revenue: {money(df['revenue'].sum())}")
                print(f"📈 Total Profit:  {money(df['profit'].sum())}")
        elif choice == '2':
            df = inv.inventory_valuation_report()
            print_df(df, "INVENTORY VALUATION BY CATEGORY")
            if not df.empty:
                print(f"🏷️  Total Stock Value (Cost):   {money(df['stock_value_at_cost'].sum())}")
                print(f"🏷️  Total Stock Value (Retail): {money(df['stock_value_at_retail'].sum())}")
        elif choice == '3':
            n = get_valid_integer("How many top products to show? [10]: ", allow_blank=True, min_value=1) or 10
            print_df(inv.top_selling_products(n), f"TOP {n} SELLING PRODUCTS")
        elif choice == '4':
            print_df(inv.monthly_performance_report(), "MONTHLY PERFORMANCE SUMMARY")
        elif choice == '5':
            print_df(inv.supplier_purchase_report(), "SUPPLIER-WISE PURCHASE REPORT")
        elif choice == '6':
            return
        else:
            print("❌ Invalid choice.")
        pause()


def charts_menu(inv):
    while True:
        clear_screen()
        print_header("VISUAL CHARTS (Matplotlib)", "📉")
        print("1. Inventory Distribution (Bar)")
        print("2. Sales Trend Over Time (Line)")
        print("3. Monthly Revenue vs. Profit (Bar)")
        print("4. Category-wise Stock Distribution (Pie)")
        print("5. Top 10 Products by Revenue (Horizontal Bar)")
        print("6. ⬅  Back to Main Menu")
        choice = input("\nEnter your choice (1-6): ").strip()
        clear_screen()

        if choice == '6':
            return
        if choice not in {'1', '2', '3', '4', '5'}:
            print("❌ Invalid choice.")
            pause()
            continue

        print("Generating chart... close the chart window to return to the menu.\n")
        if choice == '1':
            inv.plot_inventory_distribution()
        elif choice == '2':
            inv.plot_sales_trend()
        elif choice == '3':
            inv.plot_monthly_performance()
        elif choice == '4':
            inv.plot_category_distribution()
        elif choice == '5':
            inv.plot_top_products()


def run_menu(inv):
    while True:
        clear_screen()
        print_header("PRO STATIONERY MANAGEMENT SYSTEM")
        print("1. 📦 Product Management")
        print("2. 🚚 Supplier Management")
        print("3. 🧑‍💼 Customer Management")
        print("4. 🛒 Sales / Point of Sale")
        print("5. 📥 Purchases / Restock")
        print("6. 📊 Reports & Analytics")
        print("7. 📉 Visual Charts")
        print("8. ❌ Exit System")
        print("=" * 64)

        choice = input("Enter your choice (1-8): ").strip()

        if choice == '1':
            product_menu(inv)
        elif choice == '2':
            supplier_menu(inv)
        elif choice == '3':
            customer_menu(inv)
        elif choice == '4':
            sales_menu(inv)
        elif choice == '5':
            purchase_menu(inv)
        elif choice == '6':
            reports_menu(inv)
        elif choice == '7':
            charts_menu(inv)
        elif choice == '8':
            clear_screen()
            print("Exiting System. Have a great day! 👋")
            break
        else:
            print("❌ Invalid choice. Please enter a number between 1 and 8.")
            pause()


# ==========================================================================
# SEED DATA
# ==========================================================================
SUPPLIERS_SEED = [
    ("Ganesh Paper & Stationery Distributors", "Ramesh Gupta", "9876543210", "ganesh.dist@example.com", "Varanasi, UP"),
    ("Om Traders (Writing Instruments)", "Suresh Yadav", "9876501234", "omtraders@example.com", "Kanpur, UP"),
    ("Sharma Office Supplies", "Anita Sharma", "9812345678", "sharma.office@example.com", "Lucknow, UP"),
    ("Creative Art Wholesalers", "Vikram Singh", "9900112233", "creativeart@example.com", "Delhi"),
]

CUSTOMERS_SEED = [
    ("Green Valley Public School", "9123456789", "gvps@example.com"),
    ("Radiant Coaching Institute", "9988776655", "radiant.coaching@example.com"),
    ("City Corporate Office Supplies Dept.", "9871234560", "procurement@cityoffice.example.com"),
    ("Walk-in Customer", None, None),
]

# (name, category, cost_price, selling_price, reorder_level, supplier_index)
PRODUCTS_SEED = [
    ("Ball Pen (Cello/Reynolds)", "Writing", 5, 10, 30, 1),
    ("Gel Pen", "Writing", 8, 15, 30, 1),
    ("Pencil (Nataraj/Apsara)", "Writing", 3, 5, 40, 1),
    ("Eraser", "Accessories", 3, 5, 40, 0),
    ("Sharpener", "Accessories", 4, 8, 30, 0),
    ("Scale (15 cm)", "Accessories", 8, 15, 20, 0),
    ("Scale (30 cm)", "Accessories", 12, 20, 20, 0),
    ("Notebook (100 pages)", "Paper", 35, 45, 25, 0),
    ("Notebook (200 pages)", "Paper", 60, 75, 25, 0),
    ("Long Register", "Paper", 80, 100, 15, 0),
    ("Drawing Book", "Art", 35, 50, 15, 3),
    ("Chart Paper", "Paper", 8, 15, 25, 0),
    ("A4 Paper (500 sheets)", "Paper", 230, 280, 10, 0),
    ("File Folder", "Office", 18, 30, 20, 2),
    ("Plastic File", "Office", 12, 20, 20, 2),
    ("Stapler", "Office", 45, 70, 10, 2),
    ("Staples Pin Box", "Office", 10, 20, 20, 2),
    ("Paper Clips (Box)", "Office", 15, 25, 20, 2),
    ("Binder Clips (Pack)", "Office", 20, 35, 20, 2),
    ("Glue Stick", "Office", 18, 30, 20, 2),
    ("Fevicol (100 g)", "Office", 35, 50, 15, 2),
    ("Transparent Tape", "Office", 15, 25, 20, 2),
    ("Double-Sided Tape", "Office", 30, 45, 15, 2),
    ("Highlighter", "Writing", 20, 35, 20, 1),
    ("Permanent Marker", "Writing", 20, 35, 20, 1),
    ("Whiteboard Marker", "Writing", 22, 40, 20, 1),
    ("Whiteboard Duster", "Accessories", 25, 40, 10, 0),
    ("Geometry Box", "Accessories", 80, 120, 10, 0),
    ("Compass Box", "Accessories", 100, 150, 10, 0),
    ("Calculator (Basic)", "Electronics", 180, 250, 8, 2),
    ("Clipboard", "Office", 60, 90, 10, 2),
    ("Sticky Notes", "Paper", 25, 40, 15, 0),
    ("Sticky Flags", "Paper", 20, 35, 15, 0),
    ("Envelope", "Paper", 3, 5, 40, 0),
    ("Brown Cover", "Paper", 8, 15, 25, 0),
    ("Book Cover Roll", "Paper", 30, 45, 15, 0),
    ("Crayons (12 Colors)", "Art", 40, 60, 15, 3),
    ("Color Pencils (12 Colors)", "Art", 60, 90, 15, 3),
    ("Sketch Pens (12 Colors)", "Art", 70, 100, 15, 3),
    ("Watercolor Set", "Art", 80, 120, 10, 3),
    ("Paint Brush Set", "Art", 50, 80, 10, 3),
    ("Scissors", "Office", 40, 60, 15, 2),
]


def seed_transaction_history(inv, product_ids, customer_ids, supplier_ids):
    """Generates a few months of realistic sales/purchase history so
    reports and charts have meaningful data to show immediately.
    Dates are capped at today so no transaction is ever in the future."""
    today = datetime.now()

    def first_of_month(months_ago):
        year, month = today.year, today.month - months_ago
        while month <= 0:
            month += 12
            year -= 1
        return datetime(year, month, 1)

    def random_date(month_start):
        d = month_start + timedelta(days=random.randint(0, 27))
        return min(d, today).strftime("%Y-%m-%d")

    for months_ago in range(3, -1, -1):
        month_start = first_of_month(months_ago)
        # A handful of purchases (restocking) for the month
        for _ in range(6):
            inv.record_purchase(random.choice(product_ids), random.randint(20, 60),
                                random.choice(supplier_ids), purchase_date=random_date(month_start))
        # A handful of sales for the month
        for _ in range(15):
            inv.record_sale(random.choice(product_ids), random.randint(1, 8),
                            random.choice(customer_ids), sale_date=random_date(month_start))


def seed_demo_data(inv):
    random.seed(42)  # reproducible demo history
    supplier_ids = [inv.add_supplier(*s) for s in SUPPLIERS_SEED]
    customer_ids = [inv.add_customer(*c) for c in CUSTOMERS_SEED]
    product_ids = []
    for name, category, cost, sell, reorder, sup_idx in PRODUCTS_SEED:
        product_ids.append(inv.add_product(name, category, cost, sell, initial_stock=50,
                                           reorder_level=reorder, supplier_id=supplier_ids[sup_idx]))
    seed_transaction_history(inv, product_ids, customer_ids, supplier_ids)


# ==========================================================================
# ENTRY POINT
# ==========================================================================
def main():
    parser = argparse.ArgumentParser(description="Stationery Management System")
    parser.add_argument("--db", default="stationery.db", help="SQLite database file (default: stationery.db)")
    parser.add_argument("--reset", action="store_true", help="Wipe all data and reseed demo data")
    args = parser.parse_args()

    inv = StationerySystem(args.db)
    try:
        if args.reset:
            inv.clear_database()
        if inv.is_empty():  # first run (or after --reset): load demo data
            seed_demo_data(inv)
        run_menu(inv)
    finally:
        inv.close()


if __name__ == "__main__":
    main()
