import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmanet.db")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "../static/meds")
PLACEHOLDER_PATH = "meds/placeholder.jpg"  # ✅ fallback for missing images


def safe_image(filename):
    """
    Ensures valid image path for each medicine.
    Adds 'meds/' prefix if missing and uses placeholder if file doesn't exist.
    """
    base = os.path.basename(filename)
    full_path = os.path.join(STATIC_DIR, base)

    # ✅ Check if the file actually exists inside static/meds
    if os.path.exists(full_path):
        return f"meds/{base}"
    else:
        return PLACEHOLDER_PATH


# ---------------------------
# 💾 Create / Reset Database
# ---------------------------
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Drop the existing tables (drop children before parents)
cur.execute("DROP TABLE IF EXISTS Appointments")
cur.execute("DROP TABLE IF EXISTS medicines")
cur.execute("DROP TABLE IF EXISTS compositions")
cur.execute("DROP TABLE IF EXISTS Doctor")
cur.execute("DROP TABLE IF EXISTS User")  # user table


# ---------------------------
# 🧑‍💻 Create User Table (for user authentication)
# ---------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS User (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    FirstName TEXT NOT NULL,
    LastName TEXT NOT NULL,
    Email TEXT NOT NULL UNIQUE,
    Phone TEXT NOT NULL,
    Address TEXT NOT NULL,
    Password TEXT NOT NULL
)
""")

# ---------------------------
# 🩺 Create Doctor Table
# ---------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS Doctor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT NOT NULL,
    Speciality TEXT NOT NULL,
    Degree TEXT,
    Email TEXT,
    Phone TEXT
)
""")

# Insert sample doctors (matching the UI names)
cur.executemany("""
INSERT INTO Doctor (Name, Speciality, Degree, Email, Phone)
VALUES (?, ?, ?, ?, ?)
""", [
    ("Dr Ben",   "Neurology",     "MBBS",                  None, None),
    ("Dr Why",   "Cardiologist",  "FFFF Sex Specialist",   None, None),
    ("Dr Paamu", "Psychiatrist",  "M.D., D.O.",            None, None),
    ("Dr Komal", "Gynecologist",  "XYZ Degree",            None, None),
])

# ---------------------------
# 📝 Create Compositions Table
# ---------------------------
cur.execute("""
CREATE TABLE compositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    composition TEXT NOT NULL UNIQUE
)
""")

# ---------------------------
# 💊 Create Medicines Table (with composition_id foreign key)
# ---------------------------
cur.execute("""
CREATE TABLE medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    stock TEXT NOT NULL,
    image TEXT,
    composition_id INTEGER,
    FOREIGN KEY (composition_id) REFERENCES compositions(id)
)
""")

# ---------------------------
# 📅 Create Appointments Table
# ---------------------------
cur.execute("""
CREATE TABLE Appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES User(id),
    FOREIGN KEY (doctor_id) REFERENCES Doctor(id)
)
""")

# ---------------------------
# 🧾 Complete Medicines Dataset
# ---------------------------
medicines = [
    # 🩹 Acetaminophen / Paracetamol (Pain & Fever)
    ("Tylenol Extra Strength Caplets 50CT", "Acetaminophen 500mg", 9.99, "Available", "tylenol1.jpg"),
    ("Tylenol PM Extra Strength Caplets 24CT", "Acetaminophen 500mg + Diphenhydramine", 8.99, "Available", "tylenol2.jpg"),
    ("Children’s Tylenol Suspension Strawberry", "Acetaminophen 160mg/5mL", 9.29, "Available", "tylenol3.jpg"),
    ("Tylenol Cold & Flu Severe Caplets", "Acetaminophen + Phenylephrine + Dextromethorphan + Guaifenesin", 10.99, "Out of Stock", "tylenol4.jpg"),
    ("CVS Health Pain Relief Caplets", "Acetaminophen 500mg", 7.99, "Available", "cvs_acetaminophen.jpg"),
    ("Equate Extra Strength Pain Reliever", "Acetaminophen 500mg", 6.49, "Available", "equate_acetaminophen.jpg"),
    ("Kirkland Extra Strength Acetaminophen", "Acetaminophen 500mg", 8.29, "Available", "kirkland_acetaminophen.jpg"),
    ("Walgreens Pain Reliever Caplets", "Acetaminophen 500mg", 7.59, "Available", "walgreens_acetaminophen.jpg"),

    # 💊 Ibuprofen (Pain & Inflammation)
    ("Ibuprofen Tablets 400mg", "Ibuprofen 400mg", 6.50, "Available", "ibuprofen.jpg"),
    ("Advil Liqui-Gels 200mg", "Ibuprofen 200mg", 8.49, "Available", "advil.jpg"),
    ("Motrin IB Tablets 200mg", "Ibuprofen 200mg", 7.99, "Available", "motrin.jpg"),
    ("Nurofen Express Liquid Capsules", "Ibuprofen 200mg", 9.49, "Available", "nurofen.jpg"),

    # 💊 Amoxicillin (Antibiotic)
    ("Amoxicillin Capsules 500mg", "Amoxicillin 500mg", 10.50, "Available", "amoxicillin.jpg"),
    ("Moxatag 500mg Tablets", "Amoxicillin 500mg", 12.50, "Available", "moxatag.jpg"),
    ("Trimox Oral Suspension", "Amoxicillin 500mg", 11.00, "Out of Stock", "trimox.jpg"),

    # 💊 Azithromycin (Antibiotic)
    ("Azithromycin Tablets 250mg", "Azithromycin 250mg", 15.99, "Available", "azithromycin.jpg"),
    ("Zithromax Z-Pak 250mg", "Azithromycin 250mg", 17.49, "Available", "zithromax.jpg"),
    ("Azee 500mg Tablets", "Azithromycin 500mg", 14.75, "Available", "azee.jpg"),

    # 💊 Cetirizine (Allergy)
    ("Cetirizine Tablets 10mg", "Cetirizine 10mg", 5.99, "Available", "cetirizine.jpg"),
    ("Zyrtec Allergy Tablets 10mg", "Cetirizine 10mg", 6.49, "Available", "zyrtec.jpg"),
    ("Aller-Tec Tablets 10mg", "Cetirizine 10mg", 6.99, "Available", "allertec.jpg")
]

# Insert unique compositions into `compositions` table
for name, composition, price, stock, image in medicines:
    safe_img = safe_image(image)
    # Insert unique compositions (avoid duplicates)
    cur.execute("INSERT OR IGNORE INTO compositions (composition) VALUES (?)", (composition,))

# Commit to insert the compositions
conn.commit()

# Now insert data into the medicines table with the corresponding composition_id
for name, composition, price, stock, image in medicines:
    # Get the composition_id from the compositions table
    cur.execute("SELECT id FROM compositions WHERE composition = ?", (composition,))
    composition_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO medicines (name, price, stock, image, composition_id) VALUES (?, ?, ?, ?, ?)",
        (name, price, stock, safe_image(image), composition_id)
    )

conn.commit()
conn.close()

print("✅ Database initialized successfully with users, doctors, medicines, and appointments tables!")

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmanet.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 👉 1) Prescriptions: one record per prescription (user + doctor)
cur.execute("""
CREATE TABLE IF NOT EXISTS Prescriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    doctor_id   INTEGER NOT NULL,
    start_date  TEXT,
    end_date    TEXT,
    notes       TEXT,
    status      TEXT NOT NULL DEFAULT 'Active',
    FOREIGN KEY (user_id)   REFERENCES User(id),
    FOREIGN KEY (doctor_id) REFERENCES Doctor(id)
);
""")

# 👉 2) PrescriptionMedicines: each medicine inside a prescription
cur.execute("""
CREATE TABLE IF NOT EXISTS PrescriptionMedicines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id  INTEGER NOT NULL,
    medicine_id      INTEGER NOT NULL,
    dosage           TEXT,
    frequency        TEXT,
    duration_days    INTEGER,
    FOREIGN KEY (prescription_id) REFERENCES Prescriptions(id),
    FOREIGN KEY (medicine_id)     REFERENCES medicines(id)
);
""")

# 👉 3) DrugInteractions: dangerous / allergy combinations
cur.execute("""
CREATE TABLE IF NOT EXISTS DrugInteractions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id_1   INTEGER NOT NULL,
    medicine_id_2   INTEGER NOT NULL,
    severity        TEXT NOT NULL,        -- e.g. 'Low' / 'Moderate' / 'High'
    warning_message TEXT NOT NULL,
    FOREIGN KEY (medicine_id_1) REFERENCES medicines(id),
    FOREIGN KEY (medicine_id_2) REFERENCES medicines(id)
);
""")

conn.commit()
conn.close()

print("✅ Tables Prescriptions, PrescriptionMedicines, and DrugInteractions are ready!")
