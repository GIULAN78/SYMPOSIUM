from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-di-sviluppo")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symposium.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS utenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            interessi TEXT,
            creato_il TEXT
        )
    """)

    colonne = [r["name"] for r in c.execute("PRAGMA table_info(utenti)").fetchall()]
    if "password_hash" not in colonne:
        c.execute("ALTER TABLE utenti ADD COLUMN password_hash TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS locali (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            indirizzo TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tavoli (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tema TEXT NOT NULL,
            locale_id INTEGER,
            organizzatore_id INTEGER,
            data_ora TEXT NOT NULL,
            obiettivo TEXT,
            posti_totali INTEGER DEFAULT 6,
            creato_il TEXT,
            FOREIGN KEY (locale_id) REFERENCES locali (id),
            FOREIGN KEY (organizzatore_id) REFERENCES utenti (id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS iscrizioni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tavolo_id INTEGER,
            utente_id INTEGER,
            iscritto_il TEXT,
            FOREIGN KEY (tavolo_id) REFERENCES tavoli (id),
            FOREIGN KEY (utente_id) REFERENCES utenti (id),
            UNIQUE(tavolo_id, utente_id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


def login_richiesto(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "utente_id" not in session:
            flash("Accedi per continuare.")
            return redirect(url_for("accedi"))
        return f(*args, **kwargs)
    return wrapper


@app.context_processor
def utente_corrente():
    return {"utente_nome": session.get("utente_nome")}


@app.route("/")
def home():
    conn = get_db()
    tavoli = conn.execute("""
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id) AS iscritti,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id AND i.utente_id = ?) AS gia_iscritto
        FROM tavoli t
        LEFT JOIN locali l ON t.locale_id = l.id
        LEFT JOIN utenti u ON t.organizzatore_id = u.id
        ORDER BY t.data_ora ASC
    """, (session.get("utente_id", 0),)).fetchall()
    conn.close()
    return render_template("home.html", tavoli=tavoli)


@app.route("/registrati", methods=["GET", "POST"])
def registrati():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        interessi = request.form.get("interessi", "").strip()

        if len(password) < 6:
            flash("La password deve avere almeno 6 caratteri.")
            return render_template("registrati.html")

        conn = get_db()
        try:
            c = conn.execute(
                "INSERT INTO utenti (nome, email, password_hash, interessi, creato_il) VALUES (?, ?, ?, ?, ?)",
                (nome, email, generate_password_hash(password), interessi, datetime.now().isoformat())
            )
            conn.commit()
            session["utente_id"] = c.lastrowid
            session["utente_nome"] = nome
            conn.close()
            return redirect(url_for("home"))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Esiste già un account con questa email.")
            return render_template("registrati.html")

    return render_template("registrati.html")


@app.route("/accedi", methods=["GET", "POST"])
def accedi():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        utente = conn.execute("SELECT * FROM utenti WHERE email = ?", (email,)).fetchone()
        conn.close()

        if utente and utente["password_hash"] and check_password_hash(utente["password_hash"], password):
            session["utente_id"] = utente["id"]
            session["utente_nome"] = utente["nome"]
            return redirect(url_for("home"))

        flash("Email o password non corretti.")

    return render_template("accedi.html")


@app.route("/esci")
def esci():
    session.clear()
    return redirect(url_for("home"))


@app.route("/tavolo/nuovo", methods=["GET", "POST"])
@login_richiesto
def nuovo_tavolo():
    if request.method == "POST":
        tema = request.form["tema"]
        locale_nome = request.form["locale"]
        data_ora = request.form["data_ora"]
        obiettivo = request.form.get("obiettivo", "")
        posti = request.form.get("posti_totali", 6)

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM locali WHERE nome = ?", (locale_nome,))
        row = c.fetchone()
        if row:
            locale_id = row["id"]
        else:
            c.execute("INSERT INTO locali (nome, indirizzo) VALUES (?, ?)", (locale_nome, ""))
            locale_id = c.lastrowid

        utente_id = session["utente_id"]
        c.execute("""
            INSERT INTO tavoli (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti_totali, creato_il)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tema, locale_id, utente_id, data_ora, obiettivo, posti, datetime.now().isoformat()))
        tavolo_id = c.lastrowid
        c.execute(
            "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
            (tavolo_id, utente_id, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return redirect(url_for("home"))

    return render_template("nuovo_tavolo.html")


@app.route("/tavolo/<int:tavolo_id>/iscriviti", methods=["POST"])
@login_richiesto
def iscriviti(tavolo_id):
    conn = get_db()
    tavolo = conn.execute("""
        SELECT posti_totali,
               (SELECT COUNT(*) FROM iscrizioni WHERE tavolo_id = ?) AS iscritti
        FROM tavoli WHERE id = ?
    """, (tavolo_id, tavolo_id)).fetchone()

    if tavolo and tavolo["iscritti"] < tavolo["posti_totali"]:
        try:
            conn.execute(
                "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
                (tavolo_id, session["utente_id"], datetime.now().isoformat())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass
    conn.close()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
