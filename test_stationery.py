"""Tests that back every claim made about this project on the resume.

Run:  python -m unittest -v
"""
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")  # headless charts

import Stationery_Management_System as sms


def make_system():
    inv = sms.StationerySystem(":memory:")
    sup = inv.add_supplier("Acme Paper", "Ravi", "9876543210", "acme@example.com", "Varanasi")
    cust = inv.add_customer("Green Valley School", "9123456789", "gv@example.com")
    pid = inv.add_product("Notebook", "Paper", 35, 45, initial_stock=20, reorder_level=5, supplier_id=sup)
    return inv, sup, cust, pid


def stock_of(inv, pid):
    return inv.conn.execute("SELECT stock FROM Products WHERE id = ?", (pid,)).fetchone()[0]


class SchemaAndConstraints(unittest.TestCase):
    def setUp(self):
        self.inv, self.sup, self.cust, self.pid = make_system()

    def test_five_table_schema(self):
        names = {r[0] for r in self.inv.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(names, {"Products", "Suppliers", "Customers", "Purchases", "Sales"})

    def test_check_constraint_rejects_negative_stock(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.inv.conn.execute("UPDATE Products SET stock = -1 WHERE id = ?", (self.pid,))

    def test_check_constraint_rejects_zero_quantity(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.inv.conn.execute(
                "INSERT INTO Sales (product_id, quantity, unit_price, sale_date) VALUES (?, 0, 10, '2026-01-01')",
                (self.pid,))

    def test_foreign_key_rejects_unknown_supplier(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.inv.add_product("Ghost", "Paper", 1, 2, supplier_id=999)

    def test_supplier_with_products_cannot_be_deleted(self):
        ok, _ = self.inv.delete_supplier(self.sup)
        self.assertFalse(ok)

    def test_indexes_exist_and_are_used(self):
        idx = {r[1] for r in self.inv.conn.execute("PRAGMA index_list(Sales)")}
        self.assertIn("idx_sales_product", idx)
        self.assertIn("idx_sales_date", idx)
        plan = " ".join(str(r) for r in self.inv.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM Sales WHERE product_id = 1"))
        self.assertIn("idx_sales_product", plan)
        plan = " ".join(str(r) for r in self.inv.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM Sales WHERE sale_date >= '2026-01-01'"))
        self.assertIn("idx_sales_date", plan)


class AtomicTransactions(unittest.TestCase):
    def setUp(self):
        self.inv, self.sup, self.cust, self.pid = make_system()

    def test_sale_updates_stock_and_logs_record_together(self):
        ok, _ = self.inv.record_sale(self.pid, 4, self.cust)
        self.assertTrue(ok)
        self.assertEqual(stock_of(self.inv, self.pid), 16)
        self.assertEqual(len(self.inv.sales_history()), 1)

    def test_oversell_is_blocked_and_changes_nothing(self):
        ok, msg = self.inv.record_sale(self.pid, 21, self.cust)
        self.assertFalse(ok)
        self.assertIn("Insufficient stock", msg)
        self.assertEqual(stock_of(self.inv, self.pid), 20)
        self.assertTrue(self.inv.sales_history().empty)

    def test_failed_sale_rolls_back_the_stock_decrement(self):
        # The stock UPDATE succeeds first; the Sales INSERT then fails on the bad customer FK.
        ok, msg = self.inv.record_sale(self.pid, 5, customer_id=999)
        self.assertFalse(ok)
        self.assertIn("rolled back", msg)
        self.assertEqual(stock_of(self.inv, self.pid), 20)  # decrement was undone
        self.assertTrue(self.inv.sales_history().empty)

    def test_purchase_increases_stock_and_logs_record(self):
        ok, _ = self.inv.record_purchase(self.pid, 30, self.sup, unit_cost=33)
        self.assertTrue(ok)
        self.assertEqual(stock_of(self.inv, self.pid), 50)
        self.assertEqual(self.inv.purchase_history().iloc[0]["unit_cost"], 33)

    def test_failed_purchase_changes_nothing(self):
        ok, _ = self.inv.record_purchase(self.pid, 30, supplier_id=999)
        self.assertFalse(ok)
        self.assertEqual(stock_of(self.inv, self.pid), 20)
        self.assertTrue(self.inv.purchase_history().empty)

    def test_invalid_quantities_rejected(self):
        self.assertFalse(self.inv.record_sale(self.pid, 0)[0])
        self.assertFalse(self.inv.record_sale(self.pid, -3)[0])
        self.assertFalse(self.inv.record_purchase(self.pid, 0)[0])


class LowStockAlerts(unittest.TestCase):
    def test_sale_that_hits_reorder_level_warns_and_appears_in_report(self):
        inv, _, _, pid = make_system()          # stock 20, reorder level 5
        ok, msg = inv.record_sale(pid, 14)      # leaves 6 -> still fine
        self.assertNotIn("LOW STOCK", msg)
        self.assertTrue(inv.low_stock_report().empty)
        ok, msg = inv.record_sale(pid, 1)       # leaves 5 -> at reorder level
        self.assertIn("LOW STOCK", msg)
        self.assertEqual(list(inv.low_stock_report()["id"]), [pid])

    def test_catalog_flags_low_items(self):
        inv, _, _, pid = make_system()
        inv.record_sale(pid, 15)
        self.assertEqual(inv.view_all_products().iloc[0]["status"], "LOW")


class SoftDeletes(unittest.TestCase):
    def test_deactivate_hides_product_but_preserves_history_and_can_restore(self):
        inv, _, cust, pid = make_system()
        inv.record_sale(pid, 2, cust)
        self.assertTrue(inv.delete_product(pid))
        self.assertTrue(inv.view_all_products().empty)               # hidden from the catalog
        self.assertEqual(len(inv.sales_history()), 1)                # history intact
        self.assertFalse(inv.record_sale(pid, 1)[0])                 # cannot sell an inactive item
        self.assertEqual(len(inv.view_inactive_products()), 1)
        self.assertTrue(inv.restore_product(pid)[0])
        self.assertEqual(len(inv.view_all_products()), 1)


class Validation(unittest.TestCase):
    def setUp(self):
        self.inv, self.sup, self.cust, self.pid = make_system()

    def test_product_validation(self):
        with self.assertRaises(ValueError):
            self.inv.add_product("   ", "Paper", 1, 2)
        with self.assertRaises(ValueError):
            self.inv.add_product("notebook", "Paper", 1, 2)          # duplicate (case-insensitive)
        with self.assertRaises(ValueError):
            self.inv.add_product("Pen", "Writing", -1, 2)
        ok, _ = self.inv.update_product(self.pid, "selling_price", "abc")
        self.assertFalse(ok)
        ok, _ = self.inv.update_product(self.pid, "selling_price", "-5")
        self.assertFalse(ok)
        ok, _ = self.inv.update_product(self.pid, "selling_price", "50")
        self.assertTrue(ok)

    def test_contact_validation(self):
        with self.assertRaises(ValueError):
            self.inv.add_customer("Bad Email", "9123456789", "not-an-email")
        with self.assertRaises(ValueError):
            self.inv.add_supplier("Bad Phone", None, "12ab", None, None)
        self.assertFalse(self.inv.update_customer(self.cust, "email", "nope")[0])
        self.assertTrue(self.inv.update_customer(self.cust, "email", "ok@example.com")[0])
        self.assertTrue(self.inv.add_customer("Walk-in", None, None))  # blanks are allowed


class Analytics(unittest.TestCase):
    def setUp(self):
        self.inv, self.sup, self.cust, self.pid = make_system()

    def test_profit_uses_cost_at_time_of_sale(self):
        self.inv.record_sale(self.pid, 2, self.cust)                # profit 2 * (45 - 35) = 20
        self.inv.update_product(self.pid, "cost_price", "40")       # later cost change
        self.assertEqual(self.inv.generate_sales_report().iloc[0]["profit"], 20)
        self.assertEqual(self.inv.top_selling_products().iloc[0]["profit"], 20)

    def test_monthly_report_has_margin_and_growth(self):
        self.inv.record_sale(self.pid, 2, self.cust, sale_date="2026-01-10")   # revenue 90, profit 20
        self.inv.record_sale(self.pid, 4, self.cust, sale_date="2026-02-10")   # revenue 180, profit 40
        df = self.inv.monthly_performance_report()
        self.assertEqual(list(df["month"]), ["2026-01", "2026-02"])
        self.assertAlmostEqual(df.iloc[0]["margin_pct"], 22.22, places=2)
        self.assertAlmostEqual(df.iloc[1]["revenue_growth_pct"], 100.0, places=2)

    def test_inventory_valuation(self):
        row = self.inv.inventory_valuation_report().iloc[0]
        self.assertEqual(row["stock_value_at_cost"], 20 * 35)
        self.assertEqual(row["stock_value_at_retail"], 20 * 45)

    def test_charts_are_rendered_to_png(self):
        self.inv.record_sale(self.pid, 1, self.cust, sale_date="2026-01-10")
        self.inv.record_sale(self.pid, 1, self.cust, sale_date="2026-02-10")
        self.inv.record_sale(self.pid, 1, self.cust, sale_date="2026-03-10")
        with tempfile.TemporaryDirectory() as tmp:
            sms.CHARTS_DIR = tmp
            self.inv.plot_inventory_distribution()
            self.inv.plot_sales_trend()               # includes the 3-month moving average
            self.inv.plot_monthly_performance()
            self.inv.plot_category_distribution()
            self.inv.plot_top_products()
            files = sorted(os.listdir(tmp))
        self.assertEqual(files, ["category_distribution.png", "inventory_distribution.png",
                                 "monthly_performance.png", "sales_trend.png", "top_products.png"])


class MigrationAndSeed(unittest.TestCase):
    def test_old_database_is_migrated_and_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            con = sqlite3.connect(path)
            con.executescript('''
                CREATE TABLE Products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT,
                    cost_price REAL NOT NULL, selling_price REAL NOT NULL, stock INTEGER DEFAULT 0,
                    reorder_level INTEGER DEFAULT 10, supplier_id INTEGER, is_active INTEGER DEFAULT 1);
                CREATE TABLE Sales (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
                    customer_id INTEGER, quantity INTEGER NOT NULL, unit_price REAL NOT NULL, sale_date DATE NOT NULL);
                INSERT INTO Products (name, cost_price, selling_price, stock) VALUES ('Pen', 5, 10, 50);
                INSERT INTO Sales (product_id, quantity, unit_price, sale_date) VALUES (1, 3, 10, '2026-01-01');
            ''')
            con.commit()
            con.close()
            inv = sms.StationerySystem(path)
            self.assertEqual(inv.generate_sales_report().iloc[0]["profit"], 15)   # 3 * (10 - 5)
            inv.close()

    def test_demo_seed_populates_every_table(self):
        inv = sms.StationerySystem(":memory:")
        sms.seed_demo_data(inv)
        for table in ("Products", "Suppliers", "Customers", "Purchases", "Sales"):
            count = inv.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertGreater(count, 0, table)
        self.assertGreaterEqual(len(inv.monthly_performance_report()), 3)
        self.assertEqual(inv.conn.execute("SELECT COUNT(*) FROM Products WHERE stock < 0").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
