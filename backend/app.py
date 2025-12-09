from flask import Flask, render_template, request, jsonify, url_for, flash, session, redirect
import sqlite3
import os
import re
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmanet.db")
app.secret_key = os.urandom(24)


# ----------------------- DB HELPER -----------------------
def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    rows = cur.fetchall()
    conn.close()
    return (rows[0] if rows else None) if one else rows


# ----------------------- PRESCRIPTION HELPERS -----------------------
def parse_course_days(course_duration: str, default_days: int = 7) -> int:
    """
    Extract number of days from free text like:
    '10 days', '5d', '2 weeks', etc. Fallback = default_days.
    """
    if not course_duration:
        return default_days

    text = course_duration.lower()

    # e.g. "10 days", "10 day", "10d"
    m = re.search(r"(\d+)\s*(day|days|d)\b", text)
    if m:
        return int(m.group(1))

    # e.g. "2 weeks", "1 week", "2w"
    m = re.search(r"(\d+)\s*(week|weeks|w)\b", text)
    if m:
        return int(m.group(1)) * 7

    # fallback: any number
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))

    return default_days


def is_prescription_active(created_at: str, course_duration: str) -> bool:
    """
    Return True if now is within [created_at, created_at + course_duration_in_days].
    """
    if not created_at:
        return False

    # Try default SQLite timestamp format first
    try:
        start_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            start_dt = datetime.fromisoformat(created_at)
        except Exception:
            return False

    days = parse_course_days(course_duration or "")
    end_dt = start_dt + timedelta(days=days)
    return datetime.now() <= end_dt


# ----------------------- SUGGESTION API -----------------------
@app.route("/suggest")
def suggest():
    query = request.args.get("query", "").lower()
    results = query_db(
        "SELECT m.name FROM medicines m "
        "JOIN compositions c ON m.composition_id = c.id "
        "WHERE LOWER(m.name) LIKE ?",
        (f"%{query}%",)
    )
    suggestions = [r[0] for r in results]
    return jsonify(suggestions)


# ----------------------- PUBLIC / USER FLOWS -----------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/medicines")
def medicine_list():
    # No interaction warning on the generic "all medicines" page
    medicines = query_db(
        "SELECT m.name, c.composition, m.price, m.stock, "
        "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
        "JOIN compositions c ON m.composition_id = c.id"
    )
    return render_template(
        "medicine_list.html",
        medicines=medicines,
        query="All Medicines",
        alternatives=[],
        interaction_warning=None,
    )


@app.route("/medicine/<name>")
def medicine_details(name):
    query = name.lower()

    # 1) Main medicine lookup (shape kept same as before for your template)
    medicines = query_db(
        "SELECT m.name, c.composition, m.price, m.stock, "
        "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
        "JOIN compositions c ON m.composition_id = c.id "
        "WHERE LOWER(m.name) LIKE ?",
        (f"%{query}%",)
    )

    # 2) Alternatives (existing composition-based logic)
    if medicines:
        comp = (medicines[0][1] or "").lower()
        main_comp = comp.split()[0] if comp else ""

        alternatives = query_db(
            "SELECT m.name, c.composition, m.price, m.stock, "
            "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
            "JOIN compositions c ON m.composition_id = c.id "
            "WHERE LOWER(c.composition) LIKE ? AND LOWER(m.name) NOT LIKE ?",
            (f"%{main_comp}%", f"%{query}%")
        )

        if not alternatives:
            synonyms = {
                "acetaminophen": "paracetamol",
                "paracetamol": "acetaminophen",
                "amoxicillin": "augmentin",
                "ibuprofen": "motrin",
            }

            for k, v in synonyms.items():
                if k in main_comp:
                    alternatives = query_db(
                        "SELECT m.name, c.composition, m.price, m.stock, "
                        "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
                        "JOIN compositions c ON m.composition_id = c.id "
                        "WHERE LOWER(c.composition) LIKE ? AND LOWER(m.name) NOT LIKE ?",
                        (f"%{v}%", f"%{query}%")
                    )
                    break
    else:
        alternatives = []

    # 3) Drug–drug interaction check (using medicine IDs, NOT compositions)
    interaction_warning = None

    # Only check if the user is logged in and we actually found a medicine
    if "user_id" in session and medicines:
        searched_name = medicines[0][0]

        # Get ID of the searched medicine
        row = query_db(
            "SELECT id FROM medicines WHERE LOWER(name) = LOWER(?)",
            (searched_name,),
            one=True
        )
        if row:
            searched_med_id = row[0]
        else:
            searched_med_id = None

        if searched_med_id is not None:
            # Get all medicines from this user's prescriptions, with dates and course_duration
            pres_rows = query_db(
                """
                SELECT
                    P.created_at,
                    P.course_duration,
                    PM.medicine_id,
                    M.name
                FROM Prescriptions P
                JOIN PrescriptionMedicines PM ON P.id = PM.prescription_id
                JOIN medicines M ON PM.medicine_id = M.id
                WHERE P.user_id = ?
                """,
                (session["user_id"],)
            )

            # For each prescription medicine, if prescription is active, check DrugInteractions table
            for created_at, course_duration, active_med_id, active_med_name in pres_rows:
                if not is_prescription_active(created_at, course_duration):
                    continue

                hits = query_db(
                    """
                    SELECT severity, warning_message
                    FROM DrugInteractions
                    WHERE
                        (medicine_id_1 = ? AND medicine_id_2 = ?)
                        OR
                        (medicine_id_1 = ? AND medicine_id_2 = ?)
                    """,
                    (searched_med_id, active_med_id, active_med_id, searched_med_id)
                )

                for severity, warning_message in hits:
                    if severity and severity.lower() == "high":
                        interaction_warning = {
                            "with_medicine": active_med_name,
                            "severity": severity,
                            "message": warning_message
                                      or "Warning: high-risk interaction with your current medication."
                        }
                        break

                if interaction_warning:
                    break

    return render_template(
        "medicine_list.html",
        medicines=medicines,
        alternatives=alternatives,
        query=name,
        interaction_warning=interaction_warning,
    )


# ----------------------- CART & ORDERS -----------------------
@app.route("/cart/add", methods=["POST"])
def add_to_cart():
    """
    Add a medicine to the logged-in user's cart.

    It tries to get the medicine key from several possible fields:
    - medicine_id   (id or name)
    - medicine_name
    - name
    - medicine
    If none of those exist, it will try to infer the medicine name
    from the referrer URL, e.g. /medicine/tylenol.
    """
    if "user_id" not in session:
        flash("Please log in to add items to your cart.", "danger")
        return redirect(url_for("login"))

    # Try multiple field names from the form
    raw_key = (
        request.form.get("medicine_id")
        or request.form.get("medicine_name")
        or request.form.get("name")
        or request.form.get("medicine")
        or ""
    ).strip()

    # If still empty, try to pull from referrer URL like /medicine/tylenol
    if not raw_key and request.referrer:
        path = urlparse(request.referrer).path  # e.g. "/medicine/tylenol"
        parts = path.rstrip("/").split("/")
        if len(parts) >= 3 and parts[-2].lower() == "medicine":
            raw_key = parts[-1]

    quantity_str = request.form.get("quantity", "1")

    if not raw_key:
        flash("Invalid medicine selection.", "danger")
        return redirect(request.referrer or url_for("medicine_list"))

    # Resolve to numeric medicine_id
    if raw_key.isdigit():
        med_row = query_db(
            "SELECT id FROM medicines WHERE id = ?",
            (raw_key,),
            one=True
        )
    else:
        # IMPORTANT CHANGE: use LIKE instead of exact match
        med_row = query_db(
            "SELECT id FROM medicines WHERE LOWER(name) LIKE ?",
            (f"%{raw_key.lower()}%",),
            one=True
        )

    if not med_row:
        flash("Selected medicine not found in database.", "danger")
        return redirect(request.referrer or url_for("medicine_list"))

    medicine_id = med_row[0]

    # Quantity validation
    try:
        quantity = int(quantity_str)
    except ValueError:
        quantity = 1
    if quantity <= 0:
        quantity = 1

    # If user already has this medicine in cart, just bump quantity
    existing = query_db(
        "SELECT id, quantity FROM CartItems WHERE user_id = ? AND medicine_id = ?",
        (session["user_id"], medicine_id),
        one=True
    )

    if existing:
        cart_id, old_qty = existing
        query_db(
            "UPDATE CartItems SET quantity = ? WHERE id = ?",
            (old_qty + quantity, cart_id)
        )
    else:
        query_db(
            "INSERT INTO CartItems (user_id, medicine_id, quantity) VALUES (?, ?, ?)",
            (session['user_id'], medicine_id, quantity)
        )

    flash("Item added to cart.", "success")
    # Stay on the page where the user clicked "Add to Cart"
    return redirect(request.referrer or url_for("medicine_list"))


@app.route("/cart")
def view_cart():
    """
    Show the current user's cart contents and total.
    """
    if "user_id" not in session:
        flash("Please log in to view your cart.", "danger")
        return redirect(url_for("login"))

    rows = query_db(
        """
        SELECT
            CI.id,
            CI.medicine_id,
            CI.quantity,
            M.name,
            M.price,
            IFNULL(M.image, 'meds/placeholder.jpg')
        FROM CartItems CI
        JOIN medicines M ON CI.medicine_id = M.id
        WHERE CI.user_id = ?
        """,
        (session["user_id"],)
    )

    cart_items = []
    total_amount = 0.0

    for row in rows:
        cart_id, med_id, qty, name, price, image = row
        line_total = (price or 0) * qty
        total_amount += line_total
        cart_items.append({
            "cart_id": cart_id,
            "medicine_id": med_id,
            "name": name,
            "quantity": qty,
            "price": price,
            "image": image,
            "line_total": line_total,
        })

    return render_template(
        "cart.html",
        cart_items=cart_items,
        total_amount=total_amount
    )


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    # Load cart items
    cart_rows = query_db(
        """
        SELECT
            CI.id,
            CI.medicine_id,
            CI.quantity,
            M.name,
            M.price
        FROM CartItems CI
        JOIN medicines M ON CI.medicine_id = M.id
        WHERE CI.user_id = ?
        """,
        (session["user_id"],)
    )

    if request.method == "GET":
        if not cart_rows:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("view_cart"))

        total_amount = 0.0
        display_items = []
        for row in cart_rows:
            _, med_id, qty, name, price = row
            line_total = (price or 0) * qty
            total_amount += line_total
            display_items.append({
                "medicine_id": med_id,
                "name": name,
                "quantity": qty,
                "price": price,
                "line_total": line_total,
            })

        return render_template(
            "checkout.html",
            items=display_items,
            total_amount=total_amount,
            address=session.get("user_address", "")
        )

    # POST -> place order
    if not cart_rows:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("view_cart"))

    delivery_type = request.form.get("delivery_type", "pickup")
    if delivery_type not in ("pickup", "delivery"):
        delivery_type = "pickup"

    delivery_address = session.get("user_address", "")

    total_amount = 0.0
    items_for_insert = []
    for row in cart_rows:
        _, med_id, qty, name, price = row
        price = price or 0
        line_total = price * qty
        total_amount += line_total
        items_for_insert.append((med_id, qty, price, line_total))

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Insert into Orders (status always "In Progress" for now)
    cur.execute(
        """
        INSERT INTO Orders (user_id, total_amount, delivery_type, delivery_address, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session["user_id"], total_amount, delivery_type, delivery_address, "In Progress")
    )
    order_id = cur.lastrowid

    # Insert line items – store quantity + unit_price only
    for med_id, qty, price, line_total in items_for_insert:
        cur.execute(
            """
            INSERT INTO OrderItems
                (order_id, medicine_id, quantity, unit_price)
            VALUES
                (?, ?, ?, ?)
            """,
            (order_id, med_id, qty, price)
        )

    # Clear cart
    cur.execute("DELETE FROM CartItems WHERE user_id = ?", (session["user_id"],))

    conn.commit()
    conn.close()

    flash(f"Order #{order_id} placed successfully! Status: In Progress.", "success")
    return redirect(url_for("dashboard"))


@app.route("/orders")
def user_orders():
    """Full order history page."""
    if "user_id" not in session:
        return redirect(url_for("login"))

    orders = query_db(
        """
        SELECT
            O.id,
            O.total_amount,
            O.delivery_type,
            O.status,
            O.created_at,
            COALESCE(SUM(OI.quantity), 0) AS total_items
        FROM Orders O
        LEFT JOIN OrderItems OI ON O.id = OI.order_id
        WHERE O.user_id = ?
        GROUP BY O.id
        ORDER BY O.created_at DESC
        """,
        (session["user_id"],)
    )

    return render_template("orders.html", orders=orders)


@app.route("/orders/<int:order_id>")
def order_details(order_id):
    """Return JSON for one order + its items (used by modal)."""
    if "user_id" not in session:
        return jsonify({"error": "not_authenticated"}), 401

    order = query_db(
        """
        SELECT id, total_amount, delivery_type, delivery_address, status, created_at
        FROM Orders
        WHERE id = ? AND user_id = ?
        """,
        (order_id, session["user_id"]),
        one=True
    )

    if not order:
        return jsonify({"error": "not_found"}), 404

    (
        oid,
        total_amount,
        delivery_type,
        delivery_address,
        status,
        created_at
    ) = order

    items_rows = query_db(
        """
        SELECT M.name, OI.quantity, OI.unit_price
        FROM OrderItems OI
        JOIN medicines M ON OI.medicine_id = M.id
        WHERE OI.order_id = ?
        """,
        (order_id,)
    )

    items = [
        {
            "name": r[0],
            "quantity": r[1],
            "unit_price": r[2],
            "line_total": (r[2] or 0) * r[1],
        }
        for r in items_rows
    ]

    return jsonify({
        "id": oid,
        "total_amount": total_amount,
        "delivery_type": delivery_type,
        "delivery_address": delivery_address,
        "status": status,
        "created_at": created_at,
        "items": items,
    })


# ----------------------- USER AUTH -----------------------
@app.route('/create-account', methods=['GET', 'POST'])
def create_account():
    if request.method == 'POST':
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        email = request.form['email']
        phone = request.form['phone']
        address = request.form['address']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('create_account'))

        existing_user = query_db(
            'SELECT * FROM User WHERE Email = ?',
            [email],
            one=True
        )
        if existing_user:
            flash('Email already registered', 'danger')
            return redirect(url_for('create_account'))

        hashed_password = generate_password_hash(password)

        query_db(
            'INSERT INTO User (FirstName, LastName, Email, Phone, Address, Password) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [first_name, last_name, email, phone, address, hashed_password]
        )
        flash('Account created successfully', 'success')
        return redirect(url_for('login'))

    return render_template('create_account.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    # USER login (patients/customers)
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = query_db(
            'SELECT * FROM User WHERE Email = ?',
            [email],
            one=True
        )

        if user and check_password_hash(user[6], password):
            session.clear()
            session['user_id'] = user[0]
            session['user_first_name'] = user[1]
            session['user_last_name'] = user[2]
            session['user_email'] = user[3]
            session['user_phone'] = user[4]
            session['user_address'] = user[5]
            flash('Login successful', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Appointments for this logged-in user
    appointments = query_db(
        """
        SELECT A.id,
               D.Name AS doctor_name,
               D.Speciality,
               A.description,
               A.status,
               A.created_at
        FROM Appointments AS A
        JOIN Doctor AS D ON A.doctor_id = D.id
        WHERE A.user_id = ?
        ORDER BY A.created_at DESC
        """,
        (session['user_id'],)
    )

    # Recent orders (latest 3)
    recent_orders = query_db(
        """
        SELECT
            O.id,
            O.total_amount,
            O.delivery_type,
            O.status,
            O.created_at,
            COALESCE(SUM(OI.quantity), 0) AS total_items
        FROM Orders O
        LEFT JOIN OrderItems OI ON O.id = OI.order_id
        WHERE O.user_id = ?
        GROUP BY O.id
        ORDER BY O.created_at DESC
        LIMIT 3
        """,
        (session["user_id"],)
    )

    return render_template(
        'dashboard.html',
        first_name=session['user_first_name'],
        last_name=session['user_last_name'],
        email=session['user_email'],
        phone=session['user_phone'],
        address=session['user_address'],
        appointments=appointments,
        recent_orders=recent_orders
    )


# ----------------------- USER: BOOK APPOINTMENT -----------------------
@app.route('/book-appointment', methods=['GET', 'POST'])
def book_appointment():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        doctor_id = request.form.get('doctor_id')
        # form field is "symptoms", DB column is "description"
        description = request.form.get('symptoms', '').strip()

        if not doctor_id or not description:
            flash("Please select a doctor and describe your symptoms.", "danger")
            return redirect(url_for('book_appointment'))

        query_db(
            """
            INSERT INTO Appointments (user_id, doctor_id, description, status)
            VALUES (?, ?, ?, ?)
            """,
            (session['user_id'], doctor_id, description, "Pending")
        )

        flash("Appointment booked successfully!", "success")
        return redirect(url_for('dashboard'))

    # GET: show doctor cards
    return render_template('book_appointment.html')


@app.route('/my-appointments')
def my_appointments():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    appointments = query_db(
        """
        SELECT A.id,
               D.Name AS doctor_name,
               D.Speciality,
               A.description,
               A.status,
               A.created_at
        FROM Appointments AS A
        JOIN Doctor AS D ON A.doctor_id = D.id
        WHERE A.user_id = ?
        ORDER BY A.created_at DESC
        """,
        (session['user_id'],)
    )

    return render_template(
        'user_appointments.html',
        appointments=appointments,
        first_name=session.get('user_first_name')
    )


# ----------------------- DOCTOR AUTH -----------------------
@app.route('/doctor-login', methods=['GET', 'POST'])
def doctor_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        # Doctor table: id, Name, Speciality, Degree, Email, Phone, Password
        doctor = query_db(
            """
            SELECT id, Name, Email, Password, Speciality, Degree
            FROM Doctor
            WHERE Email = ?
            """,
            (email,),
            one=True
        )

        valid = False
        if doctor:
            stored_password = doctor[3]  # Password column

            # Handle hashed or plain
            if isinstance(stored_password, str) and (
                stored_password.startswith("scrypt:")
                or stored_password.startswith("pbkdf2:")
            ):
                valid = check_password_hash(stored_password, password)
            else:
                valid = (stored_password == password)

        if doctor and valid:
            session.clear()
            session['doctor_id'] = doctor[0]
            session['doctor_name'] = doctor[1]
            session['doctor_email'] = doctor[2]
            session['doctor_specialization'] = doctor[4]
            session['doctor_degree'] = doctor[5]
            flash("Doctor login successful", "success")
            return redirect(url_for('doctor_dashboard'))
        else:
            flash("Invalid doctor credentials", "danger")
            return redirect(url_for('doctor_login'))

    return render_template("doctor_login.html")


# ----------------------- DOCTOR DASHBOARD -----------------------
@app.route('/doctor-dashboard')
def doctor_dashboard():
    if 'doctor_id' not in session:
        return redirect(url_for('doctor_login'))

    appointments = query_db(
        """
        SELECT A.id,
               U.FirstName || ' ' || U.LastName AS patient_name,
               U.Email,
               U.Phone,
               A.description,
               A.status,
               A.created_at
        FROM Appointments AS A
        JOIN User AS U ON A.user_id = U.id
        WHERE A.doctor_id = ?
        ORDER BY A.created_at DESC
        """,
        (session['doctor_id'],)
    )

    # list of medicines for prescription modal
    medicines = query_db(
        "SELECT id, name FROM medicines ORDER BY name"
    )

    return render_template(
        "doctor_dashboard.html",
        doctor_name=session.get('doctor_name'),
        specialization=session.get('doctor_specialization'),
        degree=session.get('doctor_degree'),
        appointments=appointments,
        medicines=medicines
    )


@app.route('/doctor/update-appointment', methods=['POST'])
def update_appointment_status():
    if 'doctor_id' not in session:
        return redirect(url_for('doctor_login'))

    appointment_id = request.form.get('appointment_id')
    new_status = request.form.get('status')

    if not appointment_id or new_status not in ('Approved', 'Rejected', 'Pending'):
        flash("Invalid appointment update request.", "danger")
        return redirect(url_for('doctor_dashboard'))

    query_db(
        """
        UPDATE Appointments
        SET status = ?
        WHERE id = ? AND doctor_id = ?
        """,
        (new_status, appointment_id, session['doctor_id'])
    )

    flash(f"Appointment #{appointment_id} marked as {new_status}.", "success")
    return redirect(url_for('doctor_dashboard'))


# ----------------------- CREATE PRESCRIPTION (DOCTOR) -----------------------
@app.route("/doctor/create-prescription", methods=["POST"])
def create_prescription():
    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    appointment_id = request.form.get("appointment_id")
    diagnosis = request.form.get("diagnosis", "").strip()
    course_duration = request.form.get("course_duration", "").strip()

    # These come from the dynamic rows in the modal form
    medicine_ids = request.form.getlist("medicine_id[]")
    dosages = request.form.getlist("dosage[]")
    frequencies = request.form.getlist("frequency[]")

    # Basic validation
    if not appointment_id or not diagnosis or not course_duration:
        flash("Please fill all required fields for the prescription.", "danger")
        return redirect(url_for("doctor_dashboard"))

    if not medicine_ids:
        flash("Please add at least one medicine.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Make sure this appointment belongs to this doctor and get the user_id
    appt = query_db(
        "SELECT user_id FROM Appointments WHERE id = ? AND doctor_id = ?",
        (appointment_id, session["doctor_id"]),
        one=True
    )
    if not appt:
        flash("Invalid appointment for this doctor.", "danger")
        return redirect(url_for("doctor_dashboard"))

    user_id = appt[0]

    # Insert prescription in one connection to get lastrowid reliably
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO Prescriptions (appointment_id, doctor_id, user_id, diagnosis, course_duration)
        VALUES (?, ?, ?, ?, ?)
        """,
        (appointment_id, session["doctor_id"], user_id, diagnosis, course_duration)
    )
    prescription_id = cur.lastrowid
    conn.commit()
    conn.close()

    if not prescription_id:
        flash("Could not create prescription record.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Insert medicines for this prescription
    for mid, dose, freq in zip(medicine_ids, dosages, frequencies):
        mid = (mid or "").strip()
        dose = (dose or "").strip()
        freq = (freq or "").strip()
        if not mid:
            continue

        query_db(
            """
            INSERT INTO PrescriptionMedicines (prescription_id, medicine_id, dosage, frequency)
            VALUES (?, ?, ?, ?)
            """,
            (prescription_id, mid, dose, freq)
        )

    flash(f"Prescription #{prescription_id} created successfully.", "success")
    return redirect(url_for("doctor_dashboard"))


# ----------------------- USER: FETCH PRESCRIPTION (AJAX) -----------------------
@app.route("/user/prescription/<int:appointment_id>")
def user_prescription(appointment_id):
    """
    Returns JSON describing the prescription for this appointment (if any),
    only for the logged-in user.
    """
    if "user_id" not in session:
        return jsonify({"found": False, "error": "not_authenticated"}), 401

    pres = query_db(
        """
        SELECT P.id,
               P.diagnosis,
               P.course_duration,
               IFNULL(P.created_at, ''),
               D.Name
        FROM Prescriptions AS P
        JOIN Doctor As D ON P.doctor_id = D.id
        WHERE P.appointment_id = ? AND P.user_id = ?
        ORDER BY P.created_at DESC
        LIMIT 1
        """,
        (appointment_id, session["user_id"]),
        one=True
    )

    if not pres:
        return jsonify({"found": False})

    prescription_id, diagnosis, course_duration, created_at, doctor_name = pres

    meds_rows = query_db(
        """
        SELECT M.name, PM.dosage, PM.frequency
        FROM PrescriptionMedicines AS PM
        JOIN medicines AS M ON PM.medicine_id = M.id
        WHERE PM.prescription_id = ?
        ORDER BY M.name
        """,
        (prescription_id,)
    )

    medicines = [
        {
            "name": r[0],
            "dosage": r[1] or "",
            "frequency": r[2] or ""
        }
        for r in meds_rows
    ]

    return jsonify({
        "found": True,
        "doctor_name": doctor_name,
        "diagnosis": diagnosis,
        "course_duration": course_duration,
        "created_at": created_at,
        "medicines": medicines
    })


# ----------------------- LOGOUTS -----------------------
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))


@app.route("/doctor-logout")
def doctor_logout():
    session.clear()
    flash("Doctor logged out.", "info")
    return redirect(url_for("doctor_login"))


# ----------------------- MAIN -----------------------
if __name__ == "__main__":
    app.run(debug=True)
