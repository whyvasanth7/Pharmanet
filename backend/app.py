from flask import Flask, render_template, request, jsonify, url_for, flash, session, redirect
import sqlite3
import os
import re
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

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

    # Load appointments for this logged-in user
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
        'dashboard.html',
        first_name=session['user_first_name'],
        last_name=session['user_last_name'],
        email=session['user_email'],
        phone=session['user_phone'],
        address=session['user_address'],
        appointments=appointments
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
        JOIN Doctor AS D ON P.doctor_id = D.id
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
