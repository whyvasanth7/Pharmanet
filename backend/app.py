from flask import Flask, render_template, request, jsonify, url_for
import sqlite3
import os

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmanet.db")

def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, args)
    rows = cur.fetchall()
    conn.close()
    return (rows[0] if rows else None) if one else rows

@app.route("/suggest")
def suggest():
    query = request.args.get("query", "").lower()
    results = query_db(
        "SELECT name FROM medicines WHERE LOWER(name) LIKE ?",
        (f"%{query}%",)
    )
    suggestions = [r[0] for r in results]
    return jsonify(suggestions)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/medicines")
def medicine_list():
    medicines = query_db("SELECT name, composition, price, stock, image FROM medicines")
    return render_template("medicine_list.html", medicines=medicines, query="All Medicines", alternatives=[])

@app.route("/medicine/<name>")
def medicine_details(name):
    query = name.lower()

    medicines = query_db(
        "SELECT name, composition, price, stock, image FROM medicines WHERE LOWER(name) LIKE ?",
        (f"%{query}%",)
    )

    if medicines:
        comp = medicines[0][1].lower()
        main_comp = comp.split()[0]  # e.g. "ibuprofen" from "ibuprofen 400mg"


        alternatives = query_db(
            "SELECT name, composition, price, stock, image FROM medicines "
            "WHERE LOWER(composition) LIKE ? AND LOWER(name) NOT LIKE ?",
            (f"%{main_comp}%", f"%{query}%")
        )

        # Additional rule: link common substitutes manually
        # e.g. paracetamol ↔ acetaminophen, amoxicillin ↔ augmentin
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
                        "SELECT name, composition, price, stock, image FROM medicines "
                        "WHERE LOWER(composition) LIKE ? AND LOWER(name) NOT LIKE ?",
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


if __name__ == "__main__":
    app.run(debug=True)
