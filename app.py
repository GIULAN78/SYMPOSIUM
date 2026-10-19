from flask import Flask, render_template, request, redirect, url_for, session
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
            interessi TEXT,
            creato_il TEXT
        )
    """)

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


@app.route("/")
def home():
    conn = get_db()
    tavoli = conn.execute("""
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id) AS iscritti
        FROM tavoli t
        LEFT JOIN locali l ON t.locale_id = l.id
        LEFT JOIN utenti u ON t.organizzatore_id = u.id
        ORDER BY t.data_ora ASC
    """).fetchall()
    conn.close()
    return render_template("home.html", tavoli=tavoli)


@app.route("/tavolo/nuovo", methods=["GET", "POST"])
def nuovo_tavolo():
    conn = get_db()

    if request.method == "POST":
        tema = request.form["tema"]
        locale_nome = request.form["locale"]
        data_ora = request.form["data_ora"]
        obiettivo = request.form.get("obiettivo", "")
        posti = request.form.get("posti_totali", 6)

        c = conn.cursor()
        c.execute("SELECT id FROM locali WHERE nome = ?", (locale_nome,))
        row = c.fetchone()
        if row:
            locale_id = row["id"]
        else:
            c.execute("INSERT INTO locali (nome, indirizzo) VALUES (?, ?)", (locale_nome, ""))
            locale_id = c.lastrowid

        organizzatore_id = session.get("utente_id", 1)

        c.execute("""
            INSERT INTO tavoli (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti_totali, creato_il)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return redirect(url_for("home"))

    conn.close()
    return render_template("nuovo_tavolo.html")


@app.route("/tavolo/<int:tavolo_id>/iscriviti", methods=["POST"])
def iscriviti(tavolo_id):
    utente_id = session.get("utente_id", 1)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
            (tavolo_id, utente_id, datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
