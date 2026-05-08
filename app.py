import os
from dotenv import load_dotenv
import csv
import io
import threading
import requests
from flask import Flask, render_template, request, redirect, Response, flash, session, jsonify
from database import get_connection
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

load_dotenv()

app = Flask(__name__)
_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    # In development without a .env file, use a dev-only fallback
    # In production, always set SECRET_KEY as an environment variable
    import warnings
    warnings.warn("SECRET_KEY not set! Using insecure dev key. Set SECRET_KEY in your .env file.", stacklevel=2)
    _secret_key = "dev-only-insecure-key-set-SECRET_KEY-in-env"
app.secret_key = _secret_key

# ─── Indian Number formatting filter (1,00,000 format) ───
def indian_format(value, decimals=0):
    """Format a number in Indian numbering system (e.g. 1,00,000)"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    
    is_negative = value < 0
    value = abs(value)
    
    if decimals > 0:
        int_part = int(value)
        dec_part = f"{value - int_part:.{decimals}f}"[1:]  # ".XX"
    else:
        int_part = round(value)
        dec_part = ""
    
    s = str(int_part)
    if len(s) <= 3:
        result = s
    else:
        last3 = s[-3:]
        remaining = s[:-3]
        # Group remaining digits in pairs from right
        groups = []
        while remaining:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        result = ",".join(groups) + "," + last3
    
    return ("-" if is_negative else "") + result + dec_part

app.jinja_env.filters['indian'] = indian_format

# ─── AUTO MIGRATE: ensure new columns exist in any pre-existing DB ───
def _migrate_db():
    try:
        conn = get_connection()
        cur  = conn.cursor()
        existing = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
        if "email" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT NULL")
        if "email_alerts" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN email_alerts INTEGER DEFAULT 1")
            
        existing_products = [r[1] for r in cur.execute("PRAGMA table_info(products)").fetchall()]
        if "expiry_date" not in existing_products:
            cur.execute("ALTER TABLE products ADD COLUMN expiry_date DATE DEFAULT NULL")
            
        # Legacy SMTP columns (kept for safe migration of existing DBs)
        if "smtp_email" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN smtp_email TEXT DEFAULT NULL")
        if "smtp_pass" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN smtp_pass TEXT DEFAULT NULL")
        if "last_alert_sent" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN last_alert_sent TIMESTAMP DEFAULT NULL")
        # EmailJS columns
        if "emailjs_service_id" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN emailjs_service_id TEXT DEFAULT NULL")
        if "emailjs_template_id" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN emailjs_template_id TEXT DEFAULT NULL")
        if "emailjs_public_key" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN emailjs_public_key TEXT DEFAULT NULL")
        if "emailjs_private_key" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN emailjs_private_key TEXT DEFAULT NULL")
        conn.commit()
        conn.close()
    except Exception as _e:
        print(f"[migrate] {_e}")

_migrate_db()   # runs once at import / startup

# ─── HELPER: get all low/critical stock products for a user ───
def get_low_stock_products(user_id, conn):
    """Returns a list of dicts for all products at or below reorder level."""
    cur = conn.cursor()
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        SELECT p.name, p.category, p.quantity, p.reorder_level,
               COALESCE(SUM(CASE WHEN s.movement_type='OUT' AND s.timestamp > ? THEN s.quantity ELSE 0 END), 0) as out_30
        FROM products p
        LEFT JOIN stock_movements s ON p.name = s.product_name AND s.user_id = p.user_id
        WHERE p.user_id = ? AND p.quantity <= p.reorder_level
        GROUP BY p.name
    """, (thirty_days_ago, user_id))

    products = []
    for row in cur.fetchall():
        name, category, qty, reorder, out_30 = row
        avg_daily  = round(out_30 / 30, 2) if out_30 > 0 else 0
        days_rem   = round(qty / avg_daily, 1) if avg_daily > 0 else None
        status     = get_stock_status(qty, reorder)
        suggested  = max(1, int(avg_daily * 14) - qty) if avg_daily > 0 else reorder * 2
        products.append({
            'name': name, 'category': category,
            'quantity': qty, 'reorder': reorder,
            'status': status, 'days_remaining': days_rem,
            'suggested_qty': suggested
        })
    products.sort(key=lambda x: 0 if x['status'] == 'CRITICAL' else 1)
    return products


# -------- AUTHENTICATION DECORATOR --------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in first!", "error")
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

# -------- LOGIN/SIGNUP ROUTES --------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.is_json:
            data = request.get_json()
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            next_url = data.get("next", "").strip()
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            next_url  = request.form.get("next", "").strip()

        if not username or not password:
            if request.is_json:
                return jsonify({"success": False, "error": "Username and password are required!"})
            flash("Username and password are required!", "error")
            return redirect("/login" + (f"?next={next_url}" if next_url else ""))

        conn = get_connection()
        cur = conn.cursor()

        try:
            cur.execute("SELECT id, password FROM users WHERE username = ?", (username,))
            user = cur.fetchone()

            if user and check_password_hash(user[1], password):
                session['user_id'] = user[0]
                session['username'] = username
                
                if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                    redirect_url = next_url
                else:
                    redirect_url = "/dashboard"
                    
                if request.is_json:
                    return jsonify({"success": True, "redirect": redirect_url})
                    
                flash(f"Welcome back, {username}!", "success")
                return redirect(redirect_url)
            else:
                if request.is_json:
                    return jsonify({"success": False, "error": "Invalid username or password!"})
                flash("Invalid username or password!", "error")
        except Exception as e:
            if request.is_json:
                return jsonify({"success": False, "error": f"Login error: {str(e)}"})
            flash(f"Login error: {str(e)}", "error")
        finally:
            conn.close()

        if request.is_json:
            return jsonify({"success": False, "error": "Login failed"})
        return redirect("/login" + (f"?next={next_url}" if next_url else ""))

    # GET — pass `next` so template can embed it in a hidden field
    next_url = request.args.get("next", "")
    return render_template("login.html", next_url=next_url)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm", "").strip()
        
        if not username or not password or not confirm:
            flash("All fields are required!", "error")
            return redirect("/signup")
        
        if len(username) < 3:
            flash("Username must be at least 3 characters!", "error")
            return redirect("/signup")
        
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "error")
            return redirect("/signup")
        
        if " " in password:
            flash("Password cannot contain spaces!", "error")
            return redirect("/signup")
        
        if password != confirm:
            flash("Passwords do not match!", "error")
            return redirect("/signup")
        
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            if cur.fetchone() is not None:
                conn.close()
                flash("Username already exists! Please choose a different one.", "error")
                return redirect("/signup")
            
            hashed_password = generate_password_hash(password)
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            conn.commit()
            conn.close()
            
            flash("Account created successfully! Please log in.", "success")
            return redirect("/login")
        except Exception as e:
            conn.close()
            flash(f"Error creating account: {str(e)}", "error")
            return redirect("/signup")
    
    return render_template("signup.html")

@app.route("/logout")
def logout():
    username = session.get('username', 'User')
    session.clear()
    flash(f"Goodbye, {username}!", "success")
    return redirect("/login")

# -------- BUSINESS LOGIC --------
def try_send_auto_alert(user_id):
    import time
    time.sleep(1)
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "SELECT email, email_alerts, emailjs_service_id, emailjs_template_id, emailjs_public_key, emailjs_private_key, username "
            "FROM users WHERE id = ?",
            (user_id,)
        )
        user = cur.fetchone()
        
        if not user:
            return
            
        recipient_email, alerts_enabled, ejs_svc, ejs_tpl, ejs_key, ejs_private, username = user
        emailjs_ok = bool(ejs_svc and ejs_tpl and ejs_key and recipient_email)
        
        if not alerts_enabled or not emailjs_ok:
            return
            
        cur.execute("""
            SELECT name, quantity, reorder_level
            FROM products
            WHERE user_id = ? AND quantity <= reorder_level
            ORDER BY (quantity * 1.0 / NULLIF(reorder_level, 0)) ASC
        """, (user_id,))
        
        low_stock_items = cur.fetchall()
        
        if not low_stock_items:
            return
            
        critical_count = sum(1 for item in low_stock_items if item[1] <= item[2] / 2)
        
        message_lines = []
        for item in low_stock_items:
            name, qty, reorder = item
            status = get_stock_status(qty, reorder)
            prefix = "RED" if status == "CRITICAL" else "YELLOW"
            message_lines.append(f"[{prefix}] {name}: {qty} left (Reorder: {reorder})")
            
        message_text = "\n".join(message_lines)
        
        payload = {
            "service_id": ejs_svc,
            "template_id": ejs_tpl,
            "user_id": ejs_key,
            "template_params": {
                "to_email": recipient_email,
                "username": username,
                "critical_count": str(critical_count),
                "alert_count": str(len(low_stock_items)),
                "message": message_text
            }
        }
        
        if ejs_private:
            payload["accessToken"] = ejs_private
        
        resp = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload, timeout=10)
        
        if resp.status_code != 200:
            print(f"[EmailJS] Alert failed: {resp.status_code} - {resp.text}", flush=True)
        
    except Exception as e:
        print(f"[EmailJS] Error: {e}", flush=True)
    finally:
        conn.close()

def get_stock_status(quantity, reorder_level):
    if quantity <= reorder_level / 2:
        return "CRITICAL"
    elif quantity <= reorder_level:
        return "LOW"
    else:
        return "NORMAL"

def convert_to_ist(timestamp_str):
    try:
        utc_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        ist_time = utc_time + timedelta(hours=5, minutes=30)
        return ist_time.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str

# ---------------- PUBLIC LANDING PAGE ----------------
@app.route("/")
def index():
    """Public landing page - redirects to dashboard if authenticated"""
    if 'user_id' in session:
        return redirect("/dashboard")
    return render_template("index.html")

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM products WHERE user_id = ?", (user_id,))
    total_skus = cur.fetchone()[0]
    
    cur.execute("SELECT quantity, reorder_level, cost_per_unit FROM products WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    
    low = critical = 0
    total_value = 0
    for q, r, c in rows:
        total_value += q * c
        if q <= r / 2:
            critical += 1
        elif q <= r:
            low += 1
    
    today = datetime.now().date()
    cur.execute(
        "SELECT SUM(quantity) FROM stock_movements WHERE user_id = ? AND movement_type='IN' AND DATE(timestamp)=?",
        (user_id, today)
    )
    inbound_today = cur.fetchone()[0] or 0
    
    cur.execute(
        "SELECT SUM(quantity) FROM stock_movements WHERE user_id = ? AND movement_type='OUT' AND DATE(timestamp)=?",
        (user_id, today)
    )
    outbound_today = cur.fetchone()[0] or 0
    
    # 30-day trend data for line chart
    thirty_days_ago = (today - timedelta(days=29)).strftime('%Y-%m-%d')
    cur.execute("""
        SELECT DATE(timestamp) as day, movement_type, SUM(quantity) as total
        FROM stock_movements
        WHERE user_id = ? AND DATE(timestamp) >= ?
        GROUP BY DATE(timestamp), movement_type
        ORDER BY day
    """, (user_id, thirty_days_ago))
    
    trend_raw = cur.fetchall()
    # Build daily arrays for last 30 days
    trend_labels = []
    trend_in = []
    trend_out = []
    for i in range(30):
        d = today - timedelta(days=29 - i)
        ds = d.strftime('%Y-%m-%d')
        trend_labels.append(d.strftime('%d %b'))
        trend_in.append(0)
        trend_out.append(0)
    
    for day_str, mtype, total in trend_raw:
        # Find index
        try:
            d = datetime.strptime(day_str, '%Y-%m-%d').date()
            idx = (d - (today - timedelta(days=29))).days
            if 0 <= idx < 30:
                if mtype == 'IN':
                    trend_in[idx] += total
                elif mtype == 'OUT':
                    trend_out[idx] += total
        except (ValueError, IndexError):
            pass
            
    # Category Distribution
    cur.execute("""
        SELECT category, SUM(quantity * cost_per_unit) as val
        FROM products 
        WHERE user_id = ? AND quantity > 0
        GROUP BY category
        ORDER BY val DESC
        LIMIT 6
    """, (user_id,))
    
    cat_data = cur.fetchall()
    cat_labels = [row[0] for row in cat_data]
    cat_values = [row[1] for row in cat_data]
    
    # Expiring Soon
    cur.execute("""
        SELECT name, quantity, expiry_date 
        FROM products 
        WHERE user_id = ? AND expiry_date IS NOT NULL 
        ORDER BY expiry_date ASC
    """, (user_id,))
    exp_rows = cur.fetchall()
    
    expiring_soon = []
    for r in exp_rows:
        try:
            exp_date = datetime.strptime(r[2], '%Y-%m-%d').date()
            days = (exp_date - today).days
            if days <= 30:
                expiring_soon.append({
                    "name": r[0],
                    "quantity": r[1],
                    "date": r[2],
                    "days": days
                })
            elif days > 30:
                # Since it's ordered by ASC date, we can break early if we exceed 30 days
                # But we might have some invalid dates, so let's just continue
                pass
        except ValueError:
            pass
            
    conn.close()
    
    return render_template(
        "dashboard.html",
        total_skus=total_skus,
        low=low,
        critical=critical,
        inbound=inbound_today,
        outbound=outbound_today,
        total_value=total_value,
        trend_labels=trend_labels,
        trend_in=trend_in,
        trend_out=trend_out,
        cat_labels=cat_labels,
        cat_values=cat_values,
        expiring_soon=expiring_soon,
    )

# ---------------- ADD SKU ----------------
@app.route("/add", methods=["GET", "POST"])
@login_required
def add_product():
    user_id = session['user_id']
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        quantity_str = request.form.get("quantity", "0")
        reorder_str = request.form.get("reorder", "0")
        cost = float(request.form.get("cost", "0"))
        supplier_id = request.form.get("supplier_id")
        expiry_date = request.form.get("expiry_date")
        
        if not supplier_id:
            supplier_id = None
        if not expiry_date:
            expiry_date = None
        
        # Validation
        if not name or not category:
            flash("Name and Category are required!", "error")
            return redirect("/add")
            
        try:
            qty = int(quantity_str)
            reorder = int(reorder_str)
        except ValueError:
            flash("Quantity and Reorder Level must be numbers!", "error")
            return redirect("/add")
        
        if qty < 0 or reorder < 0 or cost < 0:
            flash("Quantity, Reorder Level, and Cost cannot be negative!", "error")
            return redirect("/add")
            
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            # Check if product exists
            cur.execute("SELECT id FROM products WHERE user_id = ? AND name = ?", (user_id, name))
            if cur.fetchone():
                flash("Product with this name already exists!", "error")
                return redirect("/add")
            
            cur.execute(
                "INSERT INTO products (user_id, name, category, quantity, reorder_level, cost_per_unit, supplier_id, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, category, qty, reorder, cost, supplier_id, expiry_date)
            )
            conn.commit()
            
            # Log initial stock...
            flash(f"Product '{name}' added successfully!", "success")
        except Exception as e:
            flash(f"Error adding product: {str(e)}", "error")
        finally:
            conn.close()
        
        return redirect("/products")
    
    # GET method
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM suppliers WHERE user_id = ? ORDER BY name", (user_id,))
    suppliers = cur.fetchall()
    conn.close()
    
    return render_template("add_product.html", suppliers=suppliers)

# ---------------- IMPORT CSV ----------------
@app.route("/import_csv", methods=["POST"])
@login_required
def import_csv():
    user_id = session['user_id']
    if 'file' not in request.files:
        flash("No file uploaded!", "error")
        return redirect("/products")
    
    file = request.files['file']
    if file.filename == '':
        flash("No file selected!", "error")
        return redirect("/products")
        
    if not file.filename.endswith('.csv'):
        flash("Only .csv files are supported!", "error")
        return redirect("/products")
        
    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
        
        headers = [h.strip().lower() for h in reader.fieldnames] if reader.fieldnames else []
        reader.fieldnames = headers
        
        name_key = next((h for h in headers if h in ['name', 'item name', 'sku', 'product']), None)
        if not name_key:
            flash("CSV must contain a 'Name' column.", "error")
            return redirect("/products")
            
        cat_key = next((h for h in headers if h in ['category', 'type', 'group']), 'category')
        qty_key = next((h for h in headers if h in ['quantity', 'qty', 'stock']), 'quantity')
        reorder_key = next((h for h in headers if h in ['reorder level', 'reorder', 'min stock']), 'reorder_level')
        cost_key = next((h for h in headers if h in ['cost', 'price', 'cost per unit']), 'cost_per_unit')
        
        conn = get_connection()
        cur = conn.cursor()
        
        success_count = 0
        error_count = 0
        
        for row in reader:
            name = row.get(name_key, "").strip()
            if not name:
                continue
                
            category = row.get(cat_key, "Uncategorized")
            category = category.strip() if category else "Uncategorized"
            
            try:
                qty = int(row.get(qty_key, 0) or 0)
            except ValueError:
                qty = 0
                
            try:
                reorder = int(row.get(reorder_key, 10) or 10)
            except ValueError:
                reorder = 10
                
            try:
                cost = float(row.get(cost_key, 0.0) or 0.0)
            except ValueError:
                cost = 0.0
                
            qty = max(0, qty)
            reorder = max(0, reorder)
            cost = max(0.0, cost)
            
            # Check if exists
            cur.execute("SELECT id FROM products WHERE user_id = ? AND name = ?", (user_id, name))
            if cur.fetchone():
                error_count += 1
                continue
                
            cur.execute(
                "INSERT INTO products (user_id, name, category, quantity, reorder_level, cost_per_unit) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, category, qty, reorder, cost)
            )
            success_count += 1
            
        conn.commit()
        conn.close()
        
        if success_count > 0:
            msg = f"Successfully imported {success_count} products!"
            if error_count > 0:
                msg += f" ({error_count} skipped due to duplicate names)"
            flash(msg, "success")
        elif error_count > 0:
            flash(f"Failed to import: all {error_count} products already exist in your inventory.", "error")
        else:
            flash("No valid rows found in the CSV.", "error")
            
    except Exception as e:
        flash(f"Error processing CSV: {str(e)}", "error")
        
    return redirect("/products")

# ---------------- EDIT SKU ----------------
@app.route("/edit/<product_name>", methods=["GET", "POST"])
@login_required
def edit_product(product_name):
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        reorder_str = request.form.get("reorder", "0")
        cost = float(request.form.get("cost", "0"))
        supplier_id = request.form.get("supplier_id")
        expiry_date = request.form.get("expiry_date")
        
        if not supplier_id:
            supplier_id = None
        if not expiry_date:
            expiry_date = None
        
        if not new_name or not category:
            flash("SKU Name and Category are required!", "error")
            return redirect(f"/edit/{product_name}")
        
        try:
            reorder = int(reorder_str)
        except ValueError:
            flash("Reorder Level must be a number!", "error")
            return redirect(f"/edit/{product_name}")
        
        if reorder < 0 or cost < 0:
            flash("Reorder Level and Cost cannot be negative!", "error")
            return redirect(f"/edit/{product_name}")
        
        if new_name != product_name:
            cur.execute("SELECT id FROM products WHERE user_id = ? AND name = ?", (user_id, new_name))
            if cur.fetchone() is not None:
                flash(f"SKU '{new_name}' already exists in your inventory!", "error")
                return redirect(f"/edit/{product_name}")
        
        try:
            # Update product
            cur.execute(
                "UPDATE products SET name = ?, category = ?, reorder_level = ?, cost_per_unit = ?, supplier_id = ?, expiry_date = ? WHERE user_id = ? AND name = ?",
                (new_name, category, reorder, cost, supplier_id, expiry_date, user_id, product_name)
            )
            if new_name != product_name:
                cur.execute(
                    "UPDATE stock_movements SET product_name = ? WHERE user_id = ? AND product_name = ?",
                    (new_name, user_id, product_name)
                )
            conn.commit()
            flash(f"SKU updated successfully!", "success")
            return redirect("/products")
        except Exception as e:
            flash(f"Error updating SKU: {str(e)}", "error")
            return redirect(f"/edit/{product_name}")
        finally:
            conn.close()
    
    # GET request - show form
    cur.execute(
        "SELECT name, category, quantity, reorder_level, sales_count, cost_per_unit, supplier_id, expiry_date FROM products WHERE user_id = ? AND name = ?",
        (user_id, product_name)
    )
    product = cur.fetchone()
    
    cur.execute("SELECT id, name FROM suppliers WHERE user_id = ? ORDER BY name", (user_id,))
    suppliers = cur.fetchall()
    
    conn.close()
    
    if product is None:
        flash(f"SKU '{product_name}' not found!", "error")
        return redirect("/products")
    
    product_data = {
        "name": product[0],
        "category": product[1],
        "quantity": product[2],
        "reorder": product[3],
        "sales": product[4],
        "cost": product[5],
        "supplier_id": product[6],
        "expiry_date": product[7]
    }
    
    return render_template("edit_product.html", product=product_data, suppliers=suppliers)

# ---------------- VIEW STOCK ----------------
@app.route("/products")
@login_required
def view_products():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT name, category, quantity, reorder_level, sales_count, cost_per_unit, expiry_date FROM products WHERE user_id = ?",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()
    
    products = []
    today = datetime.now().date()
    for r in rows:
        days_to_expiry = None
        expiry_status = 'OK'
        
        if r[6]:
            try:
                exp_date = datetime.strptime(r[6], '%Y-%m-%d').date()
                days_to_expiry = (exp_date - today).days
                if days_to_expiry < 0:
                    expiry_status = 'EXPIRED'
                elif days_to_expiry <= 30:
                    expiry_status = 'EXPIRING_SOON'
            except ValueError:
                pass
                
        products.append({
            "name": r[0],
            "category": r[1],
            "quantity": r[2],
            "reorder": r[3],
            "sales": r[4],
            "cost": r[5],
            "expiry_date": r[6],
            "days_to_expiry": days_to_expiry,
            "expiry_status": expiry_status,
            "status": get_stock_status(r[2], r[3])
        })
    
    return render_template("view_products.html", products=products)

# ---------------- INBOUND ----------------
@app.route("/inbound", methods=["GET", "POST"])
@login_required
def inbound():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        quantity_str = request.form.get("quantity", "0")
        
        if not name:
            conn.close()
            flash("Please select a SKU!", "error")
            return redirect("/inbound")
        
        try:
            qty = int(quantity_str)
        except ValueError:
            conn.close()
            flash("Quantity must be a valid number!", "error")
            return redirect("/inbound")
        
        if qty <= 0:
            conn.close()
            flash("Quantity must be greater than 0!", "error")
            return redirect("/inbound")
        
        try:
            cur.execute(
                "UPDATE products SET quantity = quantity + ? WHERE user_id = ? AND name = ?",
                (qty, user_id, name)
            )
            cur.execute(
                "INSERT INTO stock_movements (user_id, product_name, movement_type, quantity) VALUES (?, ?, ?, ?)",
                (user_id, name, "IN", qty)
            )
            conn.commit()
            flash(f"Successfully recorded inbound: {qty} units of '{name}'", "success")
        except Exception as e:
            flash(f"Error recording inbound: {str(e)}", "error")
        finally:
            conn.close()
        
        return redirect("/products")
    
    cur.execute("SELECT name FROM products WHERE user_id = ?", (user_id,))
    products = cur.fetchall()
    conn.close()
    
    return render_template("inbound.html", products=products)

# ---------------- OUTBOUND ----------------
@app.route("/outbound", methods=["GET", "POST"])
@login_required
def outbound():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        quantity_str = request.form.get("quantity", "0")
        
        if not name:
            conn.close()
            flash("Please select a SKU!", "error")
            return redirect("/outbound")
        
        try:
            qty = int(quantity_str)
        except ValueError:
            conn.close()
            flash("Quantity must be a valid number!", "error")
            return redirect("/outbound")
        
        if qty <= 0:
            conn.close()
            flash("Quantity must be greater than 0!", "error")
            return redirect("/outbound")
        
        cur.execute("SELECT quantity FROM products WHERE user_id = ? AND name = ?", (user_id, name))
        result = cur.fetchone()
        
        if result is None:
            conn.close()
            flash(f"Product '{name}' not found!", "error")
            return redirect("/outbound")
        
        current_qty = result[0]
        
        if qty > current_qty:
            conn.close()
            flash(f"Insufficient stock! Only {current_qty} units available.", "error")
            return redirect("/outbound")
        
        try:
            cur.execute(
                "UPDATE products SET quantity = quantity - ?, sales_count = sales_count + ? WHERE user_id = ? AND name = ?",
                (qty, qty, user_id, name)
            )
            cur.execute(
                "INSERT INTO stock_movements (user_id, product_name, movement_type, quantity) VALUES (?, ?, ?, ?)",
                (user_id, name, "OUT", qty)
            )
            conn.commit()

            # Auto email alert — runs in background thread
            threading.Thread(target=try_send_auto_alert, args=(user_id,), daemon=True).start()

            flash(f"Successfully recorded outbound: {qty} units of '{name}'", "success")
        except Exception as e:
            flash(f"Error recording outbound: {str(e)}", "error")
        finally:
            conn.close()
        
        return redirect("/products")
    
    cur.execute("SELECT name, quantity FROM products WHERE user_id = ?", (user_id,))
    products = cur.fetchall()
    
    conn.close()
    return render_template("outbound.html", products=products)

# ---------------- ADJUST STOCK ----------------
@app.route("/adjust", methods=["GET", "POST"])
@login_required
def adjust_stock():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        operation = request.form.get("operation", "subtract") # "add" or "subtract"
        reason = request.form.get("reason", "Damage")
        quantity_str = request.form.get("quantity", "0")
        
        if not name:
            conn.close()
            flash("Please select a SKU!", "error")
            return redirect("/adjust")
            
        try:
            qty = int(quantity_str)
        except ValueError:
            conn.close()
            flash("Quantity must be a valid number!", "error")
            return redirect("/adjust")
            
        if qty <= 0:
            conn.close()
            flash("Quantity must be greater than 0!", "error")
            return redirect("/adjust")
            
        cur.execute("SELECT quantity FROM products WHERE user_id = ? AND name = ?", (user_id, name))
        row = cur.fetchone()
        
        if not row:
            conn.close()
            flash("Product not found!", "error")
            return redirect("/adjust")
            
        current_qty = row[0]
        
        if operation == "subtract" and qty > current_qty:
            conn.close()
            flash(f"Cannot remove {qty} units! Only {current_qty} in stock.", "error")
            return redirect("/adjust")
            
        new_qty = current_qty + qty if operation == "add" else current_qty - qty
        
        try:
            # Update quantity WITHOUT changing sales_count
            cur.execute("UPDATE products SET quantity = ? WHERE user_id = ? AND name = ?", (new_qty, user_id, name))
            
            # Log movement as ADJ
            mtype = f"ADJ_{operation[:3].upper()}"
            signed_qty = qty if operation == "add" else -qty
            
            # We insert ADJ as movement type.
            cur.execute(
                "INSERT INTO stock_movements (user_id, product_name, movement_type, quantity) VALUES (?, ?, ?, ?)",
                (user_id, name, f"ADJ_{reason.upper()[:4]}", qty)
            )
            conn.commit()
            
            op_text = "Added" if operation == "add" else "Removed"
            flash(f"Successfully adjusted stock: {op_text} {qty} units of '{name}' ({reason}).", "success")
        except Exception as e:
            flash(f"Error adjusting stock: {str(e)}", "error")
        finally:
            conn.close()
            
        return redirect("/products")
        
    cur.execute("SELECT name, quantity FROM products WHERE user_id = ? ORDER BY name", (user_id,))
    products = cur.fetchall()
    conn.close()
    
    return render_template("adjust.html", products=products)

# ---------------- REPORTS ----------------
@app.route("/reports")
@login_required
def reports():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    # Top Movers (by sales_count)
    cur.execute("""
        SELECT name, category, sales_count 
        FROM products 
        WHERE user_id = ? 
        ORDER BY sales_count DESC 
        LIMIT 10
    """, (user_id,))
    top_movers_rows = cur.fetchall()
    
    top_movers = []
    for r in top_movers_rows:
        top_movers.append({
            "name": r[0],
            "category": r[1],
            "sales_count": r[2]
        })
        
    # Summary Stats
    cur.execute("SELECT COUNT(*) FROM products WHERE user_id = ?", (user_id,))
    total_skus = cur.fetchone()[0]
    
    cur.execute("SELECT SUM(quantity) FROM products WHERE user_id = ?", (user_id,))
    total_items = cur.fetchone()[0] or 0
    
    cur.execute("SELECT SUM(quantity * cost_per_unit) FROM products WHERE user_id = ?", (user_id,))
    total_value = cur.fetchone()[0] or 0.0

    # -------- ABC ANALYSIS --------
    # Classify products by cumulative sales contribution:
    # A = top 70% of sales volume, B = next 20%, C = remaining 10%
    cur.execute("""
        SELECT name, category, sales_count, quantity, cost_per_unit
        FROM products
        WHERE user_id = ?
        ORDER BY sales_count DESC
    """, (user_id,))
    all_rows = cur.fetchall()

    total_sales_vol = sum(r[2] for r in all_rows) or 1
    cumulative = 0
    abc_data = []
    for r in all_rows:
        cumulative += r[2]
        pct = (cumulative / total_sales_vol) * 100
        if pct <= 70:
            grade = 'A'
        elif pct <= 90:
            grade = 'B'
        else:
            grade = 'C'
        abc_data.append({
            'name': r[0],
            'category': r[1],
            'sales_count': r[2],
            'quantity': r[3],
            'value': round(r[3] * r[4], 2),
            'grade': grade
        })

    # -------- DEAD STOCK DETECTION --------
    # Products with stock > 0 but no OUT movement in last 30 days
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        SELECT p.name, p.category, p.quantity, p.cost_per_unit,
               MAX(CASE WHEN s.movement_type='OUT' THEN s.timestamp ELSE NULL END) as last_out
        FROM products p
        LEFT JOIN stock_movements s ON p.name = s.product_name AND s.user_id = p.user_id
        WHERE p.user_id = ? AND p.quantity > 0
        GROUP BY p.name
        HAVING last_out IS NULL OR last_out < ?
        ORDER BY p.quantity * p.cost_per_unit DESC
    """, (user_id, thirty_days_ago))

    dead_stock = []
    for r in cur.fetchall():
        if r[4]:
            try:
                last_dt = datetime.strptime(r[4][:19], '%Y-%m-%d %H:%M:%S')
                days_idle = (datetime.now() - last_dt).days
            except Exception:
                days_idle = None
        else:
            days_idle = None  # never sold at all
        dead_stock.append({
            'name': r[0],
            'category': r[1],
            'quantity': r[2],
            'value': round(r[2] * r[3], 2),
            'days_idle': days_idle
        })

    conn.close()
    
    return render_template(
        "reports.html",
        top_movers=top_movers,
        total_skus=total_skus,
        total_items=total_items,
        total_value=total_value,
        abc_data=abc_data,
        dead_stock=dead_stock
    )

# ---------------- SUPPLIERS ----------------
@app.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        
        if not name:
            conn.close()
            flash("Supplier Name is required!", "error")
            return redirect("/suppliers")
            
        try:
            cur.execute(
                "INSERT INTO suppliers (user_id, name, contact_name, phone, email, address) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, contact, phone, email, address)
            )
            conn.commit()
            flash(f"Supplier '{name}' added successfully!", "success")
        except Exception as e:
            flash(f"Error adding supplier: {str(e)}", "error")
        finally:
            conn.close()
        
        return redirect("/suppliers")
    
    cur.execute("SELECT id, name, contact_name, phone, email, address FROM suppliers WHERE user_id = ? ORDER BY name", (user_id,))
    suppliers_list = cur.fetchall()
    conn.close()
    
    return render_template("suppliers.html", suppliers=suppliers_list)

@app.route("/suppliers/edit/<int:supplier_id>", methods=["GET", "POST"])
@login_required
def edit_supplier(supplier_id):
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        
        if not name:
            conn.close()
            flash("Supplier Name is required!", "error")
            return redirect(f"/suppliers/edit/{supplier_id}")
            
        try:
            cur.execute(
                "UPDATE suppliers SET name = ?, contact_name = ?, phone = ?, email = ?, address = ? WHERE id = ? AND user_id = ?",
                (name, contact, phone, email, address, supplier_id, user_id)
            )
            conn.commit()
            flash(f"Supplier details updated!", "success")
            return redirect("/suppliers")
        except Exception as e:
            flash(f"Error updating supplier: {str(e)}", "error")
            return redirect(f"/suppliers/edit/{supplier_id}")
        finally:
            conn.close()
            
    cur.execute("SELECT id, name, contact_name, phone, email, address FROM suppliers WHERE id = ? AND user_id = ?", (supplier_id, user_id))
    supplier = cur.fetchone()
    conn.close()
    
    if not supplier:
        flash("Supplier not found!", "error")
        return redirect("/suppliers")
        
    return render_template("suppliers.html", edit_supplier=supplier, suppliers=[]) # Re-use template for edit mode

@app.route("/suppliers/delete/<int:supplier_id>", methods=["POST"])
@login_required
def delete_supplier(supplier_id):
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # Check if used in products
        cur.execute("SELECT COUNT(*) FROM products WHERE supplier_id = ? AND user_id = ?", (supplier_id, user_id))
        count = cur.fetchone()[0]
        
        if count > 0:
            flash(f"Cannot delete supplier! They are linked to {count} products.", "error")
        else:
            cur.execute("DELETE FROM suppliers WHERE id = ? AND user_id = ?", (supplier_id, user_id))
            conn.commit()
            flash("Supplier deleted successfully!", "success")
    except Exception as e:
        flash(f"Error deleting supplier: {str(e)}", "error")
    finally:
        conn.close()
        
    return redirect("/suppliers")

# ---------------- HISTORY ----------------
@app.route("/history")
@login_required
def history():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect("/login")
    
    days = request.args.get("days")
    conn = get_connection()
    cur = conn.cursor()
    
    if days:
        since = datetime.now() - timedelta(days=int(days))
        cur.execute(
            """
            SELECT 
                p.id,
                p.name,
                p.category,
                s.movement_type,
                s.quantity,
                s.timestamp
            FROM stock_movements s
            LEFT JOIN products p ON s.product_name = p.name AND s.user_id = p.user_id
            WHERE s.user_id = ? AND s.timestamp >= ?
            ORDER BY s.timestamp DESC
            """,
            (user_id, since),
        )
    else:
        cur.execute(
            """
            SELECT 
                p.id,
                p.name,
                p.category,
                s.movement_type,
                s.quantity,
                s.timestamp
            FROM stock_movements s
            LEFT JOIN products p ON s.product_name = p.name AND s.user_id = p.user_id
            WHERE s.user_id = ?
            ORDER BY s.timestamp DESC
            """,
            (user_id,),
        )
    
    movements = cur.fetchall()
    conn.close()
    
    # Convert timestamps to IST
    converted_movements = []
    for m in movements:
        converted_m = list(m)
        converted_m[5] = convert_to_ist(m[5])
        converted_movements.append(tuple(converted_m))
    
    return render_template("history.html", movements=converted_movements)

# ---------------- REFILL DASHBOARD ----------------
@app.route("/refill")
@login_required
def refill():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()

    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

    # Get all products below reorder level, along with 30-day outbound quantity and supplier info
    cur.execute("""
        SELECT p.name, p.quantity, p.reorder_level, p.sales_count,
               COALESCE(SUM(CASE WHEN s.movement_type='OUT' AND s.timestamp > ? THEN s.quantity ELSE 0 END), 0) as out_30,
               sup.name, sup.phone, sup.email
        FROM products p
        LEFT JOIN stock_movements s ON p.name = s.product_name AND s.user_id = p.user_id
        LEFT JOIN suppliers sup ON p.supplier_id = sup.id
        WHERE p.user_id = ? AND p.quantity <= p.reorder_level
        GROUP BY p.name
    """, (thirty_days_ago, user_id))

    refill_list = []
    for row in cur.fetchall():
        name, qty, reorder, sales, out_30, sup_name, sup_phone, sup_email = row
        avg_daily = round(out_30 / 30, 2) if out_30 > 0 else 0

        if avg_daily > 0:
            days_remaining = round(qty / avg_daily, 1)
            # Suggest enough stock for 14 days
            suggested_qty = max(1, int(avg_daily * 14) - qty)
        else:
            days_remaining = None
            # Fallback: suggest double the reorder level
            suggested_qty = reorder * 2

        refill_list.append({
            'name': name,
            'quantity': qty,
            'reorder': reorder,
            'sales': sales,
            'avg_daily': avg_daily,
            'days_remaining': days_remaining,
            'suggested_qty': suggested_qty,
            'supplier_name': sup_name,
            'supplier_phone': sup_phone,
            'supplier_email': sup_email,
        })

    conn.close()

    # Sort by urgency: items closest to 0 days first; None (no history) at the end
    refill_list.sort(key=lambda x: (
        x['days_remaining'] is None,
        x['days_remaining'] if x['days_remaining'] is not None else float('inf')
    ))

    # Fetch EmailJS credentials so the template can initialise the SDK client-side
    conn2 = get_connection()
    cur2  = conn2.cursor()
    cur2.execute(
        "SELECT email, emailjs_service_id, emailjs_template_id, emailjs_public_key, username "
        "FROM users WHERE id = ?",
        (session['user_id'],)
    )
    urow = cur2.fetchone()
    conn2.close()
    recipient_email, ejs_svc, ejs_tpl, ejs_key, uname = urow if urow else ('', '', '', '', '')
    emailjs_ok = bool(ejs_svc and ejs_tpl and ejs_key and recipient_email)

    return render_template(
        "refill.html",
        products=refill_list,
        emailjs_service_id=ejs_svc   or '',
        emailjs_template_id=ejs_tpl  or '',
        emailjs_public_key=ejs_key   or '',
        recipient_email=recipient_email or '',
        username=uname or session.get('username', 'User'),
        emailjs_ok=emailjs_ok,
    )

# ─── PROFILE / SETTINGS ───
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "update_emailjs":
            new_email      = request.form.get("email", "").strip()
            service_id     = request.form.get("emailjs_service_id", "").strip()
            template_id    = request.form.get("emailjs_template_id", "").strip()
            public_key     = request.form.get("emailjs_public_key", "").strip()
            private_key    = request.form.get("emailjs_private_key", "").strip()
            email_alerts   = 1 if request.form.get("email_alerts") else 0

            if new_email and "@" not in new_email:
                flash("Please enter a valid recipient email address.", "error")
            else:
                cur.execute(
                    "UPDATE users SET email = ?, emailjs_service_id = ?, emailjs_template_id = ?, "
                    "emailjs_public_key = ?, emailjs_private_key = ?, email_alerts = ? WHERE id = ?",
                    (
                        new_email or None,
                        service_id or None,
                        template_id or None,
                        public_key or None,
                        private_key or None,
                        email_alerts,
                        user_id
                    )
                )
                conn.commit()
                flash("✅ EmailJS settings saved! Test your alert from the Refill page.", "success")

        elif action == "change_password":
            current_pw  = request.form.get("current_password", "")
            new_pw      = request.form.get("new_password", "")
            confirm_pw  = request.form.get("confirm_password", "")
            cur.execute("SELECT password FROM users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            if not row or not check_password_hash(row[0], current_pw):
                flash("Current password is incorrect.", "error")
            elif new_pw == current_pw:
                flash("New password must be different from current password.", "error")
            elif len(new_pw) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_pw != confirm_pw:
                flash("Passwords do not match.", "error")
            else:
                cur.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (generate_password_hash(new_pw), user_id)
                )
                conn.commit()
                flash("✅ Password changed successfully!", "success")

        conn.close()
        return redirect("/profile")

    cur.execute(
        "SELECT username, email, email_alerts, emailjs_service_id, emailjs_template_id, emailjs_public_key, emailjs_private_key "
        "FROM users WHERE id = ?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    user_data = {
        'username':             row[0] if row else session.get('username', ''),
        'email':                row[1] if row else '',
        'email_alerts':         row[2] if row else 1,
        'emailjs_service_id':   row[3] if row else '',
        'emailjs_template_id':  row[4] if row else '',
        'emailjs_public_key':   row[5] if row else '',
        'emailjs_private_key':  row[6] if row else '',
    }
    return render_template("profile.html", user=user_data)

# ---------------- DELETE SKU ----------------
@app.route("/delete/<product_name>", methods=["POST"])
@login_required
def delete_sku(product_name):
    user_id = session['user_id']
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM products WHERE user_id = ? AND name = ?", (user_id, product_name))
        if cur.fetchone() is None:
            conn.close()
            flash(f"SKU '{product_name}' not found!", "error")
            return redirect("/products")
        
        cur.execute("DELETE FROM products WHERE user_id = ? AND name = ?", (user_id, product_name))
        cur.execute("DELETE FROM stock_movements WHERE user_id = ? AND product_name = ?", (user_id, product_name))
        
        conn.commit()
        conn.close()
        
        flash(f"SKU '{product_name}' has been successfully deleted!", "success")
    except Exception as e:
        flash(f"Error deleting SKU: {str(e)}", "error")
    
    return redirect("/products")

# ---------------- CSV EXPORT ----------------
@app.route("/export")
@login_required
def export_csv():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect("/login")
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 
                p.id,
                p.name,
                p.category,
                s.movement_type,
                s.quantity,
                s.timestamp
            FROM stock_movements s
            LEFT JOIN products p ON s.product_name = p.name AND s.user_id = p.user_id
            WHERE s.user_id = ?
            ORDER BY s.timestamp DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
        conn.close()
        
        if not rows:
            flash("No movement records to export!", "warning")
            return redirect("/history")
        
        def generate():
            # UTF-8 BOM for Excel to recognize encoding (₹ symbol)
            yield "\ufeff"
            yield '"SKU","SKU Name","Category","Movement","Quantity","Date & Time"\n'
            for r in rows:
                sku_id = r[0] if r[0] else "N/A"
                sku_name = (r[1] if r[1] else "Unknown").replace('"', '""')
                category = (r[2] if r[2] else "Uncategorized").replace('"', '""')
                movement = r[3]
                quantity = r[4]
                timestamp = convert_to_ist(r[5])
                yield f'"{sku_id}","{sku_name}","{category}","{movement}","{quantity}","{timestamp}"\n'
        
        return Response(
            generate(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=movement_report.csv"}
        )
    except Exception as e:
        flash(f"Error exporting CSV: {str(e)}", "error")
        return redirect("/history")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
