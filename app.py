from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")
USA_PG = bool(DATABASE_URL)

if USA_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-di-sviluppo")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symposium.db")


def get_db():
    if USA_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def esegui(conn, sql, params=()):
    if USA_PG:
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur
    return conn.execute(sql, params)


def inserisci(conn, sql, params=()):
    if USA_PG:
        cur = esegui(conn, sql + " RETURNING id", params)
        return cur.fetchone()["id"]
    return esegui(conn, sql, params).lastrowid


def init_db():
    conn = get_db()
    pk = "SERIAL PRIMARY KEY" if USA_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS utenti (
            id {pk},
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            interessi TEXT,
            creato_il TEXT
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS locali (
            id {pk},
            nome TEXT NOT NULL,
            indirizzo TEXT
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS tavoli (
            id {pk},
            tema TEXT NOT NULL,
            locale_id INTEGER REFERENCES locali (id),
            organizzatore_id INTEGER REFERENCES utenti (id),
            data_ora TEXT NOT NULL,
            obiettivo TEXT,
            posti_totali INTEGER DEFAULT 6,
            creato_il TEXT,
            citta TEXT,
            descrizione TEXT
        )
    """)

    if USA_PG:
        esegui(conn, "ALTER TABLE tavoli ADD COLUMN IF NOT EXISTS citta TEXT")
        esegui(conn, "ALTER TABLE tavoli ADD COLUMN IF NOT EXISTS descrizione TEXT")
    else:
        colonne = [r["name"] for r in esegui(conn, "PRAGMA table_info(tavoli)").fetchall()]
        if "citta" not in colonne:
            esegui(conn, "ALTER TABLE tavoli ADD COLUMN citta TEXT")
        if "descrizione" not in colonne:
            esegui(conn, "ALTER TABLE tavoli ADD COLUMN descrizione TEXT")

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS iscrizioni (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            iscritto_il TEXT,
            UNIQUE (tavolo_id, utente_id)
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


@app.template_filter("data_it")
def data_it(valore):
    try:
        return datetime.fromisoformat(valore).strftime("%d/%m/%Y alle %H:%M")
    except (ValueError, TypeError):
        return valore


@app.context_processor
def utente_corrente():
    return {"utente_nome": session.get("utente_nome")}


@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    citta = request.args.get("citta", "").strip()

    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali, t.citta,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id) AS iscritti,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id AND i.utente_id = ?) AS gia_iscritto
        FROM tavoli t
        LEFT JOIN locali l ON t.locale_id = l.id
        LEFT JOIN utenti u ON t.organizzatore_id = u.id
        WHERE (LOWER(t.tema) LIKE LOWER(?)
               OR LOWER(COALESCE(l.nome, '')) LIKE LOWER(?)
               OR LOWER(COALESCE(t.obiettivo, '')) LIKE LOWER(?)
               OR LOWER(COALESCE(t.descrizione, '')) LIKE LOWER(?))
          AND LOWER(COALESCE(t.citta, '')) LIKE LOWER(?)
        ORDER BY t.data_ora ASC
    """, (session.get("utente_id", 0), f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{citta}%")).fetchall()
    conn.close()
    return render_template("home.html", tavoli=tavoli, q=q, citta=citta)


@app.route("/miei-tavoli")
@login_richiesto
def miei_tavoli():
    utente_id = session["utente_id"]
    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.posti_totali, t.citta, t.organizzatore_id,
               l.nome AS locale_nome,
               (SELECT COUNT(*) FROM iscrizioni x WHERE x.tavolo_id = t.id) AS iscritti
        FROM tavoli t
        JOIN iscrizioni i ON i.tavolo_id = t.id AND i.utente_id = ?
        LEFT JOIN locali l ON t.locale_id = l.id
        ORDER BY t.data_ora ASC
    """, (utente_id,)).fetchall()
    conn.close()

    organizzati = [t for t in tavoli if t["organizzatore_id"] == utente_id]
    partecipo = [t for t in tavoli if t["organizzatore_id"] != utente_id]
    return render_template("miei_tavoli.html", organizzati=organizzati, partecipo=partecipo)


@app.route("/tavolo/<int:tavolo_id>")
def dettaglio_tavolo(tavolo_id):
    conn = get_db()
    tavolo = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali, t.organizzatore_id,
               t.citta, t.descrizione,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome
        FROM tavoli t
        LEFT JOIN locali l ON t.locale_id = l.id
        LEFT JOIN utenti u ON t.organizzatore_id = u.id
        WHERE t.id = ?
    """, (tavolo_id,)).fetchone()

    if not tavolo:
        conn.close()
        abort(404)

    partecipanti = esegui(conn, """
        SELECT u.id, u.nome, u.interessi
        FROM iscrizioni i
        JOIN utenti u ON i.utente_id = u.id
        WHERE i.tavolo_id = ?
        ORDER BY i.id ASC
    """, (tavolo_id,)).fetchall()
    conn.close()

    utente_id = session.get("utente_id")
    gia_iscritto = any(p["id"] == utente_id for p in partecipanti)
    sono_organizzatore = utente_id == tavolo["organizzatore_id"]
    completo = len(partecipanti) >= tavolo["posti_totali"]

    return render_template(
        "tavolo.html",
        tavolo=tavolo,
        partecipanti=partecipanti,
        gia_iscritto=gia_iscritto,
        sono_organizzatore=sono_organizzatore,
        completo=completo,
    )


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
            nuovo_id = inserisci(
                conn,
                "INSERT INTO utenti (nome, email, password_hash, interessi, creato_il) VALUES (?, ?, ?, ?, ?)",
                (nome, email, generate_password_hash(password), interessi, datetime.now().isoformat())
            )
            conn.commit()
            session["utente_id"] = nuovo_id
            session["utente_nome"] = nome
            conn.close()
            return redirect(url_for("home"))
        except IntegrityError:
            conn.rollback()
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
        utente = esegui(conn, "SELECT * FROM utenti WHERE email = ?", (email,)).fetchone()
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
        citta = request.form.get("citta", "").strip()
        data_ora = request.form["data_ora"]
        obiettivo = request.form.get("obiettivo", "")
        descrizione = request.form.get("descrizione", "").strip()
        posti = int(request.form.get("posti_totali", 6))

        conn = get_db()
        row = esegui(conn, "SELECT id FROM locali WHERE nome = ?", (locale_nome,)).fetchone()
        if row:
            locale_id = row["id"]
        else:
            locale_id = inserisci(conn, "INSERT INTO locali (nome, indirizzo) VALUES (?, ?)", (locale_nome, ""))

        utente_id = session["utente_id"]
        tavolo_id = inserisci(conn, """
            INSERT INTO tavoli (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti_totali, creato_il, citta, descrizione)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (tema, locale_id, utente_id, data_ora, obiettivo, posti, datetime.now().isoformat(), citta, descrizione))
        esegui(conn,
               "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
               (tavolo_id, utente_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))

    return render_template("nuovo_tavolo.html")


@app.route("/tavolo/<int:tavolo_id>/iscriviti", methods=["POST"])
@login_richiesto
def iscriviti(tavolo_id):
    conn = get_db()
    tavolo = esegui(conn, """
        SELECT posti_totali,
               (SELECT COUNT(*) FROM iscrizioni WHERE tavolo_id = ?) AS iscritti
        FROM tavoli WHERE id = ?
    """, (tavolo_id, tavolo_id)).fetchone()

    if tavolo and tavolo["iscritti"] < tavolo["posti_totali"]:
        try:
            esegui(conn,
                   "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
                   (tavolo_id, session["utente_id"], datetime.now().isoformat()))
            conn.commit()
        except IntegrityError:
            conn.rollback()
    conn.close()
    return redirect(request.referrer or url_for("home"))


@app.route("/tavolo/<int:tavolo_id>/annulla", methods=["POST"])
@login_richiesto
def annulla_iscrizione(tavolo_id):
    conn = get_db()
    tavolo = esegui(conn, "SELECT organizzatore_id FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()

    if not tavolo:
        conn.close()
        abort(404)

    if tavolo["organizzatore_id"] == session["utente_id"]:
        flash("L'organizzatore non può annullare l'iscrizione al proprio tavolo.")
    else:
        esegui(conn,
               "DELETE FROM iscrizioni WHERE tavolo_id = ? AND utente_id = ?",
               (tavolo_id, session["utente_id"]))
        conn.commit()
        flash("Iscrizione annullata.")
    conn.close()
    return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))


@app.route("/tavolo/<int:tavolo_id>/elimina", methods=["POST"])
@login_richiesto
def elimina_tavolo(tavolo_id):
    conn = get_db()
    tavolo = esegui(conn, "SELECT organizzatore_id FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()

    if not tavolo:
        conn.close()
        abort(404)

    if tavolo["organizzatore_id"] != session["utente_id"]:
        conn.close()
        abort(403)

    esegui(conn, "DELETE FROM iscrizioni WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM tavoli WHERE id = ?", (tavolo_id,))
    conn.commit()
    conn.close()
    flash("Tavolo eliminato.")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
