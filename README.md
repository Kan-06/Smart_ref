# 📦 SmartRefill — Warehouse Inventory Management System

## 🔗 Live Demo
https://smart-ref.onrender.com
 
A full-featured, multi-tenant warehouse management web application built with **Python Flask** and **SQLite**. SmartRefill helps teams track stock levels, manage inbound/outbound movements, monitor suppliers, generate reports, and receive low-stock email alerts — all through a clean, modern dark-themed UI.

---
## 🚀 Features

### 🔐 Authentication & User Management

- Secure signup and login with **hashed passwords** (`werkzeug.security`)
- Per-user data isolation (multi-tenant — each user sees only their own inventory)
- Session-based authentication with `login_required` decorator
- Password change from Profile Settings

### 📊 Dashboard

- At-a-glance KPIs: Total SKUs, Inventory Value, Critical & Low stock counts
- Today's inbound and outbound units
- Direct navigation to all major sections

### 📦 Warehouse Stock (Products)

- Add, edit, and delete SKUs (Stock Keeping Units)
- Fields: Name, Category, Quantity, Reorder Level, Cost per Unit, Supplier
- Real-time stock status badges: `NORMAL` / `LOW` / `CRITICAL`
- Search and filter products by name or category
- Export stock movement history to **CSV**

### 🟢 Inbound / 🔴 Outbound Movements

- Record incoming stock (Inbound)
- Record outgoing/sold stock (Outbound) with stock-level validation
- Automatic **email alert** triggered when outbound causes stock to drop below reorder level

### 🔔 Refill Dashboard

- Lists all products currently at or below reorder level
- Smart urgency sorting (most critical items first)
- Shows: Current Qty, Reorder Level, Average Daily Usage, Estimated Days Remaining, Suggested Order Qty
- **Manual "Send Alert Email"** button to trigger an immediate low-stock report

### 📧 Email Alerts

- Rich HTML email with product-level details (status, stock %, days remaining, suggested order)
- Auto-triggered on outbound when stock becomes CRITICAL or LOW
- Manually triggered from the Refill page
- Configurable via environment variables (Gmail/SMTP)
- Toggle alerts on/off from Profile Settings

### 🏢 Suppliers

- Add, edit, and delete supplier records (Name, Contact, Phone, Email, Address)
- Link suppliers to products
- Safe-delete: prevents deletion if supplier is linked to active products

### 📈 Reports

- **Top Movers**: Top 10 most-sold products
- **ABC Analysis**: Classifies products into A (top 70%), B (next 20%), C (bottom 10%) by sales volume
- **Dead Stock Detection**: Products with stock > 0 but no outbound movement in the last 30 days
- Summary stats: Total SKUs, Total Items in Stock, Total Inventory Value

### 📋 History & CSV Export

- Full stock movement log (Inbound/Outbound) with timestamps in **IST**
- Filter by time period (last 7 / 30 / 90 days)
- One-click **Export CSV** download

### 👤 Profile Settings

- Update email address for alert notifications
- Toggle email alerts on/off
- Change password securely

### 🎨 UI & Design

- Dark-themed, responsive interface
- Light/Dark theme toggle (`dark-theme.css`)
- Smooth animations via `enhancements.js`
- Mobile-friendly layout

---

## 🗂️ Project Structure

```
SMART_REFILL/
├── app.py                    # Main Flask application (all routes & logic)
├── database.py               # DB connection & schema initialization
├── inventory.db              # SQLite database (auto-created)
├── requirements.txt          # Python dependencies
├── .gitignore
│
├── templates/                # Jinja2 HTML templates
│   ├── index.html            # Public landing page
│   ├── login.html            # Login page
│   ├── signup.html           # Signup page
│   ├── dashboard.html        # Main dashboard
│   ├── view_products.html    # Warehouse stock list
│   ├── add_product.html      # Add new SKU
│   ├── edit_product.html     # Edit existing SKU
│   ├── inbound.html          # Record inbound stock
│   ├── outbound.html         # Record outbound stock
│   ├── refill.html           # Refill / low-stock dashboard
│   ├── reports.html          # Analytics & reports
│   ├── suppliers.html        # Supplier management
│   ├── history.html          # Movement history
│   └── profile.html          # User profile & settings
│
├── static/
│   ├── style.css             # Main stylesheet
│   ├── dark-theme.css        # Dark theme overrides
│   ├── enhancements.js       # UI animations & interactivity
│   └── dashboard.js          # Dashboard chart logic
│
└── scripts/                  # One-time DB migration scripts
    ├── upgrade_db.py         # General DB upgrade script
    ├── add_cost_column.py    # Adds cost_per_unit column
    ├── add_suppliers_table.py# Creates suppliers table
    └── README.md             # Scripts usage guide
```

---

## 🗄️ Database Schema

**`users`**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| username | TEXT UNIQUE | Login username |
| password | TEXT | Hashed password |
| email | TEXT | Email for alerts |
| email_alerts | INTEGER | 1 = enabled, 0 = disabled |
| created_at | TIMESTAMP | Account creation time |

**`products`**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| user_id | INTEGER FK | Owner user |
| name | TEXT | SKU name (unique per user) |
| category | TEXT | Product category |
| quantity | INTEGER | Current stock |
| reorder_level | INTEGER | Alert threshold |
| cost_per_unit | REAL | Unit cost |
| sales_count | INTEGER | Total units sold |
| supplier_id | INTEGER FK | Linked supplier |
| created_at | TIMESTAMP | Creation time |

**`stock_movements`**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| user_id | INTEGER FK | Owner user |
| product_name | TEXT | SKU name |
| movement_type | TEXT | `IN` or `OUT` |
| quantity | INTEGER | Units moved |
| timestamp | TIMESTAMP | Movement time |

**`suppliers`**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| user_id | INTEGER FK | Owner user |
| name | TEXT | Supplier name |
| contact_name | TEXT | Contact person |
| phone | TEXT | Phone number |
| email | TEXT | Email address |
| address | TEXT | Physical address |

---

## ⚙️ Setup & Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Kan-06/Smart_ref.git
cd Smart_ref
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Initialize the Database

```bash
python database.py
```

### 5. (Optional) Configure Email Alerts

Set the following environment variables to enable email alerts:

```bash
# Windows (PowerShell)
$env:MAIL_USERNAME = "your_gmail@gmail.com"
$env:MAIL_PASSWORD = "your_gmail_app_password"
$env:MAIL_FROM     = "your_gmail@gmail.com"

# macOS/Linux
export MAIL_USERNAME="your_gmail@gmail.com"
export MAIL_PASSWORD="your_gmail_app_password"
export MAIL_FROM="your_gmail@gmail.com"
```

> **Note:** For Gmail, use an [App Password](https://myaccount.google.com/apppasswords), not your account password. 2-Step Verification must be enabled.

### 6. Run the Application

```bash
python app.py
```

Open your browser at: **http://localhost:5000**

---

## 🌐 Application Routes

| Method   | Route                    | Description                  |
| -------- | ------------------------ | ---------------------------- |
| GET      | `/`                      | Public landing page          |
| GET/POST | `/login`                 | User login                   |
| GET/POST | `/signup`                | New user registration        |
| GET      | `/logout`                | Logout & clear session       |
| GET      | `/dashboard`             | Main dashboard               |
| GET      | `/products`              | View all stock / SKUs        |
| GET/POST | `/add`                   | Add new SKU                  |
| GET/POST | `/edit/<name>`           | Edit existing SKU            |
| POST     | `/delete/<name>`         | Delete SKU                   |
| GET/POST | `/inbound`               | Record inbound stock         |
| GET/POST | `/outbound`              | Record outbound stock        |
| GET      | `/refill`                | Refill / low-stock dashboard |
| POST     | `/send-alert`            | Send low-stock email alert   |
| GET      | `/reports`               | Analytics reports            |
| GET/POST | `/suppliers`             | Supplier list & add          |
| GET/POST | `/suppliers/edit/<id>`   | Edit supplier                |
| POST     | `/suppliers/delete/<id>` | Delete supplier              |
| GET      | `/history`               | Stock movement history       |
| GET      | `/export`                | Download CSV export          |
| GET/POST | `/profile`               | User profile & settings      |

---

## 🔧 Tech Stack

| Layer      | Technology                    |
| ---------- | ----------------------------- |
| Backend    | Python 3, Flask 3.0           |
| Database   | SQLite (via `sqlite3`)        |
| Auth       | Werkzeug password hashing     |
| Email      | Python `smtplib` + Gmail SMTP |
| Frontend   | HTML5, CSS3, Vanilla JS       |
| Deployment | Gunicorn (Render / Linux)     |

---

## 🚢 Deployment (Render)

1. Push your code to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Set **Build Command**: `pip install -r requirements.txt`
4. Set **Start Command**: `gunicorn app:app`
5. Add environment variables: `SECRET_KEY`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`

---

## 📌 Stock Status Logic

| Status      | Condition                       |
| ----------- | ------------------------------- |
| 🟢 NORMAL   | `quantity > reorder_level`      |
| 🟡 LOW      | `quantity <= reorder_level`     |
| 🔴 CRITICAL | `quantity <= reorder_level / 2` |

---

## 🛡️ Security Notes

- All passwords are securely hashed using Werkzeug security utilities.
- Session secrets are set via environment variables in production
- User data is fully isolated — no cross-user data leakage
- Only internal redirect paths are allowed after login (open-redirect prevention)

---

## License

Licensed under the Apache License 2.0.

© 2026 Kanishk Hegde. Please do not redistribute or present this project as your own work without attribution.

---

_SmartRefill — Built with Flask & SQLite | February 2026_
