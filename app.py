from flask import Flask, render_template, request, redirect, jsonify, session, url_for, Response
import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

DATABASE = "studentspend.db"


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def month_label(month):
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return datetime.now().strftime("%B %Y")


def valid_month(month):
    try:
        datetime.strptime(month, "%Y-%m")
        return True
    except (TypeError, ValueError):
        return False


def current_user():
    uid = session.get("user_id")

    if not uid:
        return None

    conn = get_connection()

    user = conn.execute(
        "SELECT * FROM users WHERE id=?",
        (uid,)
    ).fetchone()

    conn.close()

    return user


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def init_db():

    conn = sqlite3.connect(DATABASE)

    # USERS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # EXPENSES
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL
        )
    """)

    # SETTINGS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            monthly_budget REAL NOT NULL
        )
    """)

    # -----------------------------------------------------
    # SAFE MIGRATION
    # -----------------------------------------------------

    columns = [
        r[1]
        for r in conn.execute(
            "PRAGMA table_info(expenses)"
        ).fetchall()
    ]

    if "user_id" not in columns:
        conn.execute(
            "ALTER TABLE expenses ADD COLUMN user_id INTEGER"
        )

    # -----------------------------------------------------
    # DEFAULT USER
    # -----------------------------------------------------

    user_count = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    if user_count == 0:

        conn.execute(
            """
            INSERT INTO users
            (name,email,password,created_at)
            VALUES(?,?,?,?)
            """,
            (
                "Himanshu",
                "demo@studentspend.local",
                generate_password_hash("student123"),
                datetime.now().isoformat()
            )
        )

    # -----------------------------------------------------
    # ASSIGN OLD EXPENSES TO FIRST USER
    # -----------------------------------------------------

    first_user = conn.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()

    if first_user:

        conn.execute(
            """
            UPDATE expenses
            SET user_id=?
            WHERE user_id IS NULL
            """,
            (first_user[0],)
        )

    # -----------------------------------------------------
    # DEFAULT BUDGET
    # -----------------------------------------------------

    budget_exists = conn.execute(
        "SELECT 1 FROM settings WHERE id=1"
    ).fetchone()

    if budget_exists is None:

        conn.execute(
            """
            INSERT INTO settings
            (id,monthly_budget)
            VALUES(1,?)
            """,
            (10000,)
        )

    conn.commit()
    conn.close()


# =========================================================
# LOGIN PROTECTION
# =========================================================

@app.before_request
def require_login():

    public = {
        "login",
        "register",
        "logout",
        "static"
    }

    if request.endpoint in public:
        return

    if request.endpoint and not session.get("user_id"):
        return redirect(url_for("login"))


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("user_id"):
        return redirect(url_for("home"))

    error = ""

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_connection()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE lower(email)=?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]

            return redirect(url_for("home"))

        error = "Invalid email or password."

    return render_template(
        "login.html",
        error=error
    )


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if session.get("user_id"):
        return redirect(url_for("home"))

    error = ""

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm = request.form.get(
            "confirm",
            ""
        )

        if not name or not email or not password:

            error = "Please fill all fields."

        elif password != confirm:

            error = "Passwords do not match."

        elif len(password) < 6:

            error = "Password must be at least 6 characters."

        else:

            conn = get_connection()

            try:

                cur = conn.execute(
                    """
                    INSERT INTO users
                    (name,email,password,created_at)
                    VALUES(?,?,?,?)
                    """,
                    (
                        name,
                        email,
                        generate_password_hash(password),
                        datetime.now().isoformat()
                    )
                )

                uid = cur.lastrowid

                conn.execute(
                    """
                    INSERT OR IGNORE INTO settings
                    (id,monthly_budget)
                    VALUES(1,?)
                    """,
                    (10000,)
                )

                conn.commit()

                session["user_id"] = uid
                session["user_name"] = name

                return redirect(url_for("home"))

            except sqlite3.IntegrityError:

                error = (
                    "An account with this email already exists."
                )

            finally:

                conn.close()

    return render_template(
        "register.html",
        error=error
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def home():

    conn = get_connection()

    user = current_user()

    selected_month = request.args.get(
        "month",
        datetime.now().strftime("%Y-%m")
    )

    if not valid_month(selected_month):

        selected_month = datetime.now().strftime(
            "%Y-%m"
        )

    # -----------------------------------------------------
    # EXPENSES
    # -----------------------------------------------------

    expenses = conn.execute(
        """
        SELECT *
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        ORDER BY date DESC,id DESC
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchall()

    # -----------------------------------------------------
    # TOTAL SPENT
    # -----------------------------------------------------

    total_spent = conn.execute(
        """
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()[0]

    # -----------------------------------------------------
    # BUDGET
    # -----------------------------------------------------

    budget = conn.execute(
        """
        SELECT monthly_budget
        FROM settings
        WHERE id=1
        """
    ).fetchone()[0]

    remaining = budget - total_spent

    budget_percentage = (
        total_spent / budget * 100
        if budget > 0
        else 0
    )

    # -----------------------------------------------------
    # CATEGORY DATA
    # -----------------------------------------------------

    category_data = conn.execute(
        """
        SELECT
            category,
            SUM(amount) AS total,
            COUNT(*) AS count
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        GROUP BY category
        ORDER BY total DESC
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchall()

    # -----------------------------------------------------
    # SELECTED MONTH DATES
    # -----------------------------------------------------

    try:

        selected_start = datetime.strptime(
            selected_month + "-01",
            "%Y-%m-%d"
        )

    except ValueError:

        selected_start = datetime.now().replace(
            day=1
        )

    next_month = (
        selected_start.replace(day=28)
        + timedelta(days=4)
    ).replace(day=1)

    last_day = next_month - timedelta(days=1)

    today = datetime.now().date()

    if selected_month == today.strftime("%Y-%m"):

        end_day = min(
            last_day.date(),
            today
        )

    else:

        end_day = last_day.date()

    start_day = max(
        selected_start.date(),
        end_day - timedelta(days=6)
    )

    # -----------------------------------------------------
    # DAILY DATA
    # -----------------------------------------------------

    daily = conn.execute(
        """
        SELECT
            date,
            SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        AND date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
        """,
        (
            user["id"],
            start_day.isoformat(),
            end_day.isoformat()
        )
    ).fetchall()

    daily_map = {
        r["date"]: r["total"]
        for r in daily
    }

    days = []

    cursor = start_day

    while cursor <= end_day:

        days.append({
            "day": cursor.strftime("%a"),
            "date": cursor.isoformat(),
            "amount": daily_map.get(
                cursor.isoformat(),
                0
            )
        })

        cursor += timedelta(days=1)

    # -----------------------------------------------------
    # AVAILABLE MONTHS
    # -----------------------------------------------------

    months = conn.execute(
        """
        SELECT DISTINCT
            substr(date,1,7) AS month
        FROM expenses
        WHERE user_id=?
        ORDER BY month DESC
        """,
        (user["id"],)
    ).fetchall()

    month_values = [
        r["month"]
        for r in months
    ]

    now_month = datetime.now().strftime(
        "%Y-%m"
    )

    if now_month not in month_values:

        month_values.insert(
            0,
            now_month
        )

    # -----------------------------------------------------
    # NOTIFICATIONS
    # -----------------------------------------------------

    notification_count = 0

    if budget > 0 and budget_percentage >= 80:

        notification_count += 1

    if expenses:

        notification_count += 1

    conn.close()

    return render_template(
        "index.html",
        user=user,
        expenses=expenses,
        total_spent=total_spent,
        budget=budget,
        remaining=remaining,
        budget_percentage=budget_percentage,
        category_data=category_data,
        days=days,
        selected_month=selected_month,
        selected_month_label=month_label(
            selected_month
        ),
        months=month_values,
        notification_count=notification_count
    )


# =========================================================
# ADD EXPENSE
# =========================================================

@app.route("/add-expense", methods=["POST"])
def add_expense():

    user = current_user()

    name = request.form.get(
        "name",
        ""
    ).strip()

    category = request.form.get(
        "category",
        ""
    ).strip()

    date = request.form.get(
        "date",
        ""
    ).strip()

    try:

        amount = float(
            request.form.get(
                "amount",
                "0"
            )
        )

    except ValueError:

        amount = 0

    if (
        not name
        or not category
        or not date
        or amount <= 0
    ):

        return redirect(
            url_for("home")
        )

    conn = get_connection()

    conn.execute(
        """
        INSERT INTO expenses
        (name,amount,category,date,user_id)
        VALUES(?,?,?,?,?)
        """,
        (
            name,
            amount,
            category,
            date,
            user["id"]
        )
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "home",
            month=date[:7]
        )
    )


# =========================================================
# EDIT EXPENSE
# =========================================================

@app.route(
    "/edit-expense/<int:id>",
    methods=["POST"]
)
def edit_expense(id):

    user = current_user()

    name = request.form.get(
        "name",
        ""
    ).strip()

    category = request.form.get(
        "category",
        ""
    ).strip()

    date = request.form.get(
        "date",
        ""
    ).strip()

    try:

        amount = float(
            request.form.get(
                "amount",
                "0"
            )
        )

    except ValueError:

        amount = 0

    if (
        name
        and category
        and date
        and amount > 0
    ):

        conn = get_connection()

        conn.execute(
            """
            UPDATE expenses
            SET
                name=?,
                amount=?,
                category=?,
                date=?
            WHERE id=?
            AND user_id=?
            """,
            (
                name,
                amount,
                category,
                date,
                id,
                user["id"]
            )
        )

        conn.commit()
        conn.close()

    return redirect(
        url_for(
            "home",
            month=date[:7]
            if date
            else None
        )
    )


# =========================================================
# DELETE EXPENSE
# =========================================================

@app.route("/delete-expense/<int:id>")
def delete_expense(id):

    user = current_user()

    conn = get_connection()

    row = conn.execute(
        """
        SELECT date
        FROM expenses
        WHERE id=?
        AND user_id=?
        """,
        (
            id,
            user["id"]
        )
    ).fetchone()

    conn.execute(
        """
        DELETE FROM expenses
        WHERE id=?
        AND user_id=?
        """,
        (
            id,
            user["id"]
        )
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "home",
            month=row["date"][:7]
            if row
            else None
        )
    )


# =========================================================
# UPDATE BUDGET
# =========================================================

@app.route(
    "/update-budget",
    methods=["POST"]
)
def update_budget():

    try:

        budget = float(
            request.form.get(
                "budget",
                "0"
            )
        )

    except ValueError:

        budget = 0

    if budget > 0:

        conn = get_connection()

        conn.execute(
            """
            UPDATE settings
            SET monthly_budget=?
            WHERE id=1
            """,
            (budget,)
        )

        conn.commit()
        conn.close()

    return redirect(
        url_for(
            "home",
            month=request.form.get(
                "month",
                datetime.now().strftime(
                    "%Y-%m"
                )
            )
        )
    )


# =========================================================
# EXPORT CSV
# =========================================================

@app.route("/export-csv")
def export_csv():

    user = current_user()

    selected_month = request.args.get(
        "month",
        datetime.now().strftime("%Y-%m")
    )

    if not valid_month(selected_month):

        selected_month = datetime.now().strftime(
            "%Y-%m"
        )

    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            name,
            amount,
            category,
            date
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        ORDER BY date DESC,id DESC
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchall()

    conn.close()

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow([
        "Expense",
        "Amount",
        "Category",
        "Date"
    ])

    for row in rows:

        writer.writerow([
            row["name"],
            row["amount"],
            row["category"],
            row["date"]
        ])

    filename = (
        f"studentspend_{selected_month}.csv"
    )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment; filename={filename}"
        }
    )


# =========================================================
# ANALYTICS
# =========================================================

@app.route("/analytics")
def analytics():

    user = current_user()

    selected_month = request.args.get(
        "month",
        datetime.now().strftime("%Y-%m")
    )

    if not valid_month(selected_month):

        selected_month = datetime.now().strftime(
            "%Y-%m"
        )

    conn = get_connection()

    # TOTAL
    total_spent = conn.execute(
        """
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()[0]

    # TRANSACTIONS
    total_transactions = conn.execute(
        """
        SELECT COUNT(*)
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()[0]

    # AVERAGE
    average_expense = (
        total_spent / total_transactions
        if total_transactions
        else 0
    )

    # CATEGORIES
    category_data = conn.execute(
        """
        SELECT
            category,
            SUM(amount) AS total,
            COUNT(*) AS count
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        GROUP BY category
        ORDER BY total DESC
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchall()

    highest_category = (
        category_data[0]
        if category_data
        else None
    )

    # BIGGEST EXPENSE
    highest_expense = conn.execute(
        """
        SELECT *
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        ORDER BY amount DESC
        LIMIT 1
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()

    # DAILY DATA
    daily_data = conn.execute(
        """
        SELECT
            date,
            SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        GROUP BY date
        ORDER BY date DESC
        LIMIT 7
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchall()

    conn.close()

    return render_template(
        "analytics.html",
        user=user,
        selected_month=selected_month,
        selected_month_label=month_label(
            selected_month
        ),
        total_spent=total_spent,
        total_transactions=total_transactions,
        average_expense=average_expense,
        category_data=category_data,
        highest_category=highest_category,
        highest_expense=highest_expense,
        daily_data=daily_data
    )


# =========================================================
# SPENDAI
# =========================================================

@app.route(
    "/ask-ai",
    methods=["POST"]
)
def ask_ai():

    user = current_user()

    data = request.get_json(
        silent=True
    ) or {}

    question = data.get(
        "question",
        ""
    ).lower().strip()

    selected_month = data.get(
        "month",
        datetime.now().strftime("%Y-%m")
    )

    if not valid_month(selected_month):

        selected_month = datetime.now().strftime(
            "%Y-%m"
        )

    conn = get_connection()

    # TOTAL
    total = conn.execute(
        """
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()[0]

    # COUNT
    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()[0]

    # BUDGET
    budget = conn.execute(
        """
        SELECT monthly_budget
        FROM settings
        WHERE id=1
        """
    ).fetchone()[0]

    remaining = budget - total

    # TOP CATEGORY
    top = conn.execute(
        """
        SELECT
            category,
            SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        GROUP BY category
        ORDER BY total DESC
        LIMIT 1
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()

    # BIGGEST EXPENSE
    biggest = conn.execute(
        """
        SELECT
            name,
            amount,
            category
        FROM expenses
        WHERE user_id=?
        AND substr(date,1,7)=?
        ORDER BY amount DESC
        LIMIT 1
        """,
        (
            user["id"],
            selected_month
        )
    ).fetchone()

    conn.close()

    average = (
        total / count
        if count
        else 0
    )

    label = month_label(
        selected_month
    )

    # -----------------------------------------------------
    # AI RESPONSES
    # -----------------------------------------------------

    if any(
        x in question
        for x in [
            "spent most",
            "highest category",
            "most spending",
            "where did i spend"
        ]
    ):

        if top:

            answer = (
                f"You spent the most on "
                f"{top['category']} — "
                f"₹{top['total']:.0f} "
                f"in {label}."
            )

        else:

            answer = (
                f"No expenses recorded "
                f"for {label}."
            )

    elif any(
        x in question
        for x in [
            "biggest expense",
            "largest expense",
            "highest expense"
        ]
    ):

        if biggest:

            answer = (
                f"Your biggest expense was "
                f"{biggest['name']} — "
                f"₹{biggest['amount']:.0f} "
                f"({biggest['category']})."
            )

        else:

            answer = (
                f"No expenses recorded "
                f"for {label}."
            )

    elif any(
        x in question
        for x in [
            "average",
            "avg"
        ]
    ):

        answer = (
            f"Your average expense is "
            f"₹{average:.0f} per transaction "
            f"in {label}."
        )

    elif any(
        x in question
        for x in [
            "how much did i spend",
            "total spent",
            "total spending",
            "how much have i spent"
        ]
    ):

        answer = (
            f"You spent ₹{total:.0f} "
            f"in {label}."
        )

    elif any(
        x in question
        for x in [
            "how much is remaining",
            "remaining budget",
            "money left",
            "budget left"
        ]
    ):

        if remaining >= 0:

            answer = (
                f"You have ₹{remaining:.0f} "
                f"remaining from your "
                f"₹{budget:.0f} monthly budget."
            )

        else:

            answer = (
                f"You are ₹{abs(remaining):.0f} "
                f"over your monthly budget."
            )

    elif any(
        x in question
        for x in [
            "save more",
            "can i save",
            "saving"
        ]
    ):

        if remaining >= 0:

            answer = (
                f"You have ₹{remaining:.0f} left. "
                f"Keeping spending below that "
                f"will keep you within budget."
            )

        else:

            answer = (
                f"You're ₹{abs(remaining):.0f} "
                f"over budget, so reducing "
                f"spending would be necessary "
                f"to get back within the limit."
            )

    elif any(
        x in question
        for x in [
            "budget",
            "monthly budget"
        ]
    ):

        answer = (
            f"Your monthly budget is "
            f"₹{budget:.0f}."
        )

    elif any(
        x in question
        for x in [
            "how many transactions",
            "number of transactions",
            "how many expenses",
            "transactions"
        ]
    ):

        answer = (
            f"You have {count} transaction(s) "
            f"in {label}."
        )

    else:

        answer = (
            "Try asking: "
            "How much did I spend? "
            "Where did I spend most? "
            "What is my biggest expense? "
            "How much is remaining? "
            "Can I save more?"
        )

    return jsonify({
        "answer": answer
    })


# =========================================================
# AUTO CREATE LOGIN / REGISTER / ANALYTICS TEMPLATES
# =========================================================

LOGIN_HTML = """
<!doctype html>
<html>
<head>

<title>StudentSpend Login</title>

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<style>

body{
    margin:0;
    font-family:Inter,Arial,sans-serif;
    background:#f5f7fb;
    display:grid;
    place-items:center;
    min-height:100vh;
    color:#172033;
}

.card{
    width:min(420px,90%);
    background:white;
    padding:32px;
    border-radius:18px;
    box-shadow:0 15px 50px #17203318;
}

h1{
    margin:0 0 8px;
}

p{
    color:#6d7485;
}

.brand{
    color:#635bff;
    font-weight:800;
    font-size:22px;
    margin-bottom:25px;
}

input{
    width:100%;
    box-sizing:border-box;
    padding:13px;
    margin:7px 0;
    border:1px solid #dfe3ec;
    border-radius:9px;
}

button{
    width:100%;
    padding:13px;
    border:0;
    border-radius:9px;
    background:#635bff;
    color:white;
    font-weight:700;
    margin-top:10px;
    cursor:pointer;
}

.error{
    color:#c62828;
    background:#fff0f0;
    padding:10px;
    border-radius:8px;
}

.link{
    text-align:center;
    margin-top:18px;
}

.link a{
    color:#635bff;
    text-decoration:none;
}

</style>

</head>

<body>

<div class="card">

<div class="brand">
StudentSpend
</div>

<h1>
Welcome back
</h1>

<p>
Sign in to manage your student finances.
</p>

{% if error %}
<div class="error">
{{ error }}
</div>
{% endif %}

<form method="post">

<input
    type="email"
    name="email"
    placeholder="Email"
    required
>

<input
    type="password"
    name="password"
    placeholder="Password"
    required
>

<button>
Login
</button>

</form>

<div class="link">
New here?
<a href="/register">
Create account
</a>
</div>

</div>

</body>
</html>
"""


REGISTER_HTML = """
<!doctype html>
<html>

<head>

<title>
StudentSpend Register
</title>

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<style>

body{
    margin:0;
    font-family:Inter,Arial,sans-serif;
    background:#f5f7fb;
    display:grid;
    place-items:center;
    min-height:100vh;
    color:#172033;
}

.card{
    width:min(420px,90%);
    background:white;
    padding:32px;
    border-radius:18px;
    box-shadow:0 15px 50px #17203318;
}

h1{
    margin:0 0 8px;
}

.brand{
    color:#635bff;
    font-weight:800;
    font-size:22px;
    margin-bottom:25px;
}

p{
    color:#6d7485;
}

input{
    width:100%;
    box-sizing:border-box;
    padding:13px;
    margin:7px 0;
    border:1px solid #dfe3ec;
    border-radius:9px;
}

button{
    width:100%;
    padding:13px;
    border:0;
    border-radius:9px;
    background:#635bff;
    color:white;
    font-weight:700;
    margin-top:10px;
    cursor:pointer;
}

.error{
    color:#c62828;
    background:#fff0f0;
    padding:10px;
    border-radius:8px;
}

.link{
    text-align:center;
    margin-top:18px;
}

.link a{
    color:#635bff;
    text-decoration:none;
}

</style>

</head>

<body>

<div class="card">

<div class="brand">
StudentSpend
</div>

<h1>
Create account
</h1>

<p>
Start tracking your student expenses.
</p>

{% if error %}
<div class="error">
{{ error }}
</div>
{% endif %}

<form method="post">

<input
    name="name"
    placeholder="Full name"
    required
>

<input
    type="email"
    name="email"
    placeholder="Email"
    required
>

<input
    type="password"
    name="password"
    placeholder="Password (6+ characters)"
    required
>

<input
    type="password"
    name="confirm"
    placeholder="Confirm password"
    required
>

<button>
Create account
</button>

</form>

<div class="link">

Already have an account?

<a href="/login">
Login
</a>

</div>

</div>

</body>
</html>
"""


ANALYTICS_HTML = """
<!doctype html>

<html>

<head>

<title>
StudentSpend Analytics
</title>

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<style>

body{
    margin:0;
    font-family:Inter,Arial,sans-serif;
    background:#f6f7fb;
    color:#172033;
}

.wrap{
    max-width:1100px;
    margin:auto;
    padding:35px;
}

.top{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
    flex-wrap:wrap;
}

.brand{
    font-size:24px;
    font-weight:800;
    color:#635bff;
}

.actions a{
    color:#635bff;
    text-decoration:none;
    margin-left:15px;
}

.filter{
    background:white;
    padding:15px;
    border-radius:12px;
    margin:22px 0;
}

.filter input,
.filter button{
    padding:10px;
    border:1px solid #ddd;
    border-radius:8px;
}

.filter button{
    background:#635bff;
    color:#fff;
    border:0;
}

.cards{
    display:grid;
    grid-template-columns:
        repeat(4,1fr);
    gap:15px;
}

.card,
.panel{
    background:white;
    border-radius:16px;
    padding:20px;
    box-shadow:
        0 5px 25px #17203308;
}

.card span{
    color:#777;
    font-size:12px;
}

.card h2{
    margin:10px 0 0;
}

.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:15px;
    margin-top:15px;
}

.row{
    display:flex;
    justify-content:space-between;
    padding:12px 0;
    border-bottom:1px solid #eee;
}

@media(max-width:800px){

    .cards{
        grid-template-columns:
            1fr 1fr;
    }

    .grid{
        grid-template-columns:1fr;
    }

}

@media(max-width:500px){

    .cards{
        grid-template-columns:1fr;
    }

}

</style>

</head>

<body>

<div class="wrap">

<div class="top">

<div>

<div class="brand">
StudentSpend
</div>

<h1>
Analytics
</h1>

<p>
{{ selected_month_label }}
</p>

</div>

<div class="actions">

<a href="/?month={{ selected_month }}">
Dashboard
</a>

<a href="/logout">
Logout
</a>

</div>

</div>


<form
    class="filter"
    method="get"
>

<input
    type="month"
    name="month"
    value="{{ selected_month }}"
>

<button>
View Month
</button>

</form>


<div class="cards">

<div class="card">

<span>
TOTAL SPENT
</span>

<h2>
₹{{ "%.0f"|format(total_spent) }}
</h2>

</div>


<div class="card">

<span>
TRANSACTIONS
</span>

<h2>
{{ total_transactions }}
</h2>

</div>


<div class="card">

<span>
AVERAGE
</span>

<h2>
₹{{ "%.0f"|format(average_expense) }}
</h2>

</div>


<div class="card">

<span>
TOP CATEGORY
</span>

<h2>
{{ highest_category["category"]
   if highest_category
   else "—" }}
</h2>

</div>

</div>


<div class="grid">

<div class="panel">

<h2>
Categories
</h2>

{% for row in category_data %}

<div class="row">

<span>
{{ row["category"] }}
</span>

<strong>
₹{{ "%.0f"|format(row["total"]) }}
</strong>

</div>

{% else %}

<p>
No spending in this month.
</p>

{% endfor %}

</div>


<div class="panel">

<h2>
Highlights
</h2>

<div class="row">

<span>
Biggest expense
</span>

<strong>
{{ highest_expense["name"]
   if highest_expense
   else "—" }}
</strong>

</div>


<div class="row">

<span>
Amount
</span>

<strong>

₹{{ "%.0f"|format(
    highest_expense["amount"]
)
if highest_expense
else "0" }}

</strong>

</div>


<h2>
Recent days
</h2>

{% for row in daily_data %}

<div class="row">

<span>
{{ row["date"] }}
</span>

<strong>
₹{{ "%.0f"|format(row["total"]) }}
</strong>

</div>

{% else %}

<p>
No data.
</p>

{% endfor %}

</div>

</div>

</div>

</body>

</html>
"""


# =========================================================
# CREATE MISSING TEMPLATES
# =========================================================

@app.before_request
def ensure_templates():

    import os

    os.makedirs(
        "templates",
        exist_ok=True
    )

    files = {

        "login.html":
            LOGIN_HTML,

        "register.html":
            REGISTER_HTML,

        "analytics.html":
            ANALYTICS_HTML

    }

    for name, content in files.items():

        path = os.path.join(
            "templates",
            name
        )

        if not os.path.exists(path):

            with open(
                path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(content)


# =========================================================
# START DATABASE
# =========================================================

init_db()


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )