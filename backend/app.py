from flask import Flask, render_template, request, jsonify, url_for, flash, session, redirect
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmanet.db")

app.secret_key = os.urandom(24) 

def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    rows = cur.fetchall()
    conn.close()
    return (rows[0] if rows else None) if one else rows

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

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/medicines")
def medicine_list():
    # Correct the query to select medicines and their compositions
    medicines = query_db(
        "SELECT m.name, c.composition, m.price, m.stock, "
        "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
        "JOIN compositions c ON m.composition_id = c.id"
    )
    return render_template("medicine_list.html", medicines=medicines, query="All Medicines", alternatives=[])

@app.route("/medicine/<name>")
def medicine_details(name):
    query = name.lower()

    # Query for the specific medicine
    medicines = query_db(
        "SELECT m.name, c.composition, m.price, m.stock, "
        "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
        "JOIN compositions c ON m.composition_id = c.id "
        "WHERE LOWER(m.name) LIKE ?",
        (f"%{query}%",)
    )

    if medicines:
        comp = medicines[0][1].lower()
        main_comp = comp.split()[0]  # Get the main composition (first word)

        # Query for alternatives, making sure alternatives without images use the placeholder
        alternatives = query_db(
            "SELECT m.name, c.composition, m.price, m.stock, "
            "IFNULL(m.image, 'meds/placeholder.jpg') FROM medicines m "
            "JOIN compositions c ON m.composition_id = c.id "
            "WHERE LOWER(c.composition) LIKE ? AND LOWER(m.name) NOT LIKE ?",
            (f"%{main_comp}%", f"%{query}%")
        )

        # If no alternatives found, check synonyms (for example: acetaminophen -> paracetamol)
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

    return render_template(
        "medicine_list.html",
        medicines=medicines,
        alternatives=alternatives,
        query=name
    )

# Route for the Create Account page
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

        # Check if email already exists
        existing_user = query_db('SELECT * FROM User WHERE Email = ?', [email], one=True)
        if existing_user:
            flash('Email already registered', 'danger')
            return redirect(url_for('create_account'))

        # Hash the password before saving
        hashed_password = generate_password_hash(password)

        # Insert new user into the database
        query_db('INSERT INTO User (FirstName, LastName, Email, Phone, Address, Password) VALUES (?, ?, ?, ?, ?, ?)',
                 [first_name, last_name, email, phone, address, hashed_password])
        flash('Account created successfully', 'success')
        return redirect(url_for('login'))

    return render_template('create_account.html')


# Route for the Login page
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        # Check if user exists
        user = query_db('SELECT * FROM User WHERE Email = ?', [email], one=True)
        if user and check_password_hash(user[6], password):  # Assuming password is the 7th column
            # Login success, store user info in session
            session['user_id'] = user[0]  # User ID (Assuming UserID is the 1st column)
            session['user_first_name'] = user[1]  # First Name (Assuming First Name is the 2nd column)
            session['user_last_name'] = user[2]  # Last Name (Assuming Last Name is the 3rd column)
            session['user_email'] = user[3]  # Email (Assuming Email is the 4th column)
            session['user_phone'] = user[4]  # Phone (Assuming Phone is the 5th column)
            session['user_address'] = user[5] # Address (Assuming Address is the 6th column)
            flash('Login successful', 'success')
            return redirect(url_for('dashboard'))  # Redirect to user dashboard or home page

        else:
            flash('Invalid email or password', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')


# Route for the dashboard page
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:  # Check if the user is logged in
        return redirect(url_for('login'))  # If not logged in, redirect to login page
    
    # If logged in, render the dashboard and pass user info from session
    return render_template('dashboard.html', 
                           first_name=session['user_first_name'],
                           last_name=session['user_last_name'],
                           email=session['user_email'],
                           phone=session['user_phone'],
                           address=session['user_address'])


if __name__ == "__main__":
    app.run(debug=True)
