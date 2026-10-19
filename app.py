from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
from datetime import datetime, date

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


def aggiungi_colonna(conn, tabella, colonna, definizione):
    if USA_PG:
        esegui(conn, f"ALTER TABLE {tabella} ADD COLUMN IF NOT EXISTS {colonna} {definizione}")
    else:
        colonne = [r["name"] for r in esegui(conn, f"PRAGMA table_info({tabella})").fetchall()]
        if colonna not in colonne:
            esegui(conn, f"ALTER TABLE {tabella} ADD COLUMN {colonna} {definizione}")


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
    aggiungi_colonna(conn, "utenti", "password_hash", "TEXT")
    aggiungi_colonna(conn, "utenti", "data_nascita", "TEXT")
    aggiungi_colonna(conn, "utenti", "citta", "TEXT")
    aggiungi_colonna(conn, "utenti", "professione", "TEXT")
    aggiungi_colonna(conn, "utenti", "formazione", "TEXT")
    aggiungi_colonna(conn, "utenti", "bio", "TEXT")

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
    aggiungi_colonna(conn, "tavoli", "citta", "TEXT")
    aggiungi_colonna(conn, "tavoli", "descrizione", "TEXT")
    aggiungi_colonna(conn, "tavoli", "min_partecipanti", "INTEGER")
    aggiungi_colonna(conn, "tavoli", "eta_min", "INTEGER")
    aggiungi_colonna(conn, "tavoli", "eta_max", "INTEGER")

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


def eta_da(data_nascita):
    try:
        n = date.fromisoformat(data_nascita)
    except (ValueError, TypeError):
        return None
    oggi = date.today()
    return oggi.year - n.year - ((oggi.month, oggi.day) < (n.month, n.day))


def set_interessi(testo):
    return {x.strip().lower() for x in (testo or "").split(",") if x.strip()}


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


@app.template_filter("eta")
def eta_filtro(valore):
    return eta_da(valore)


@app.context_processor
def utente_corrente():
    return {"utente_nome": session.get("utente_nome"), "utente_id": session.get("utente_id")}


def utente_per_id(conn, utente_id):
    if not utente_id:
        return None
    return esegui(conn, "SELECT * FROM utenti WHERE id = ?", (utente_id,)).fetchone()


@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    citta = request.args.get("citta", "").strip()
    solo_eta = request.args.get("eta") == "1"
    solo_confermati = request.args.get("confermati") == "1"

    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali, t.citta,
               t.min_partecipanti, t.eta_min, t.eta_max,
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

    mia_eta = eta_da((utente_per_id(conn, session.get("utente_id")) or {}).get("data_nascita") if session.get("utente_id") else None)
    conn.close()

    if solo_eta and mia_eta is not None:
        tavoli = [t for t in tavoli
                  if (t["eta_min"] is None or mia_eta >= t["eta_min"])
                  and (t["eta_max"] is None or mia_eta <= t["eta_max"])]
    if solo_confermati:
        tavoli = [t for t in tavoli if t["min_partecipanti"] and t["iscritti"] >= t["min_partecipanti"]]

    return render_template("home.html", tavoli=tavoli, q=q, citta=citta,
                           solo_eta=solo_eta, solo_confermati=solo_confermati)


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
               t.citta, t.descrizione, t.min_partecipanti, t.eta_min, t.eta_max,
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

    righe = esegui(conn, """
        SELECT u.id, u.nome, u.interessi, u.data_nascita, u.citta
        FROM iscrizioni i
        JOIN utenti u ON i.utente_id = u.id
        WHERE i.tavolo_id = ?
        ORDER BY i.id ASC
    """, (tavolo_id,)).fetchall()

    io = utente_per_id(conn, session.get("utente_id"))
    conn.close()

    miei = set_interessi(io["interessi"]) if io else set()
    partecipanti = []
    for r in righe:
        p = dict(r)
        p["in_comune"] = len(miei & set_interessi(p["interessi"]))
        p["eta"] = eta_da(p["data_nascita"])
        partecipanti.append(p)

    eta_list = [p["eta"] for p in partecipanti if p["eta"] is not None]
    range_eta = (min(eta_list), max(eta_list)) if eta_list else None
    eta_media = round(sum(eta_list) / len(eta_list)) if eta_list else None

    uid = session.get("utente_id")
    gia_iscritto = any(p["id"] == uid for p in partecipanti)
    sono_organizzatore = uid == tavolo["organizzatore_id"]
    completo = len(partecipanti) >= tavolo["posti_totali"]
    confermato = bool(tavolo["min_partecipanti"] and len(partecipanti) >= tavolo["min_partecipanti"])

    return render_template(
        "tavolo.html",
        tavolo=tavolo,
        partecipanti=partecipanti,
        gia_iscritto=gia_iscritto,
        sono_organizzatore=sono_organizzatore,
        completo=completo,
        confermato=confermato,
        range_eta=range_eta,
        eta_media=eta_media,
    )


@app.route("/registrati", methods=["GET", "POST"])
def registrati():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        data_nascita = request.form.get("data_nascita", "").strip()
        citta = request.form.get("citta", "").strip()
        interessi = request.form.get("interessi", "").strip()

        if len(password) < 6:
            flash("La password deve avere almeno 6 caratteri.")
            return render_template("registrati.html")

        eta = eta_da(data_nascita)
        if eta is None or eta < 18:
            flash("Devi essere maggiorenne e indicare una data di nascita valida.")
            return render_template("registrati.html")

        conn = get_db()
        try:
            nuovo_id = inserisci(
                conn,
                "INSERT INTO utenti (nome, email, password_hash, interessi, creato_il, data_nascita, citta) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nome, email, generate_password_hash(password), interessi, datetime.now().isoformat(), data_nascita, citta)
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


@app.route("/utente/<int:utente_id>")
def profilo_utente(utente_id):
    conn = get_db()
    u = utente_per_id(conn, utente_id)
    if not u:
        conn.close()
        abort(404)

    organizzati = esegui(conn, "SELECT COUNT(*) AS n FROM tavoli WHERE organizzatore_id = ?", (utente_id,)).fetchone()["n"]
    partecipato = esegui(conn, """
        SELECT COUNT(*) AS n FROM iscrizioni i
        JOIN tavoli t ON t.id = i.tavolo_id
        WHERE i.utente_id = ? AND t.organizzatore_id != ?
    """, (utente_id, utente_id)).fetchone()["n"]
    io = utente_per_id(conn, session.get("utente_id"))
    conn.close()

    suoi = [x.strip() for x in (u["interessi"] or "").split(",") if x.strip()]
    miei = set_interessi(io["interessi"]) if io else set()
    interessi = [{"nome": x, "comune": x.lower() in miei} for x in suoi]
    interessi.sort(key=lambda i: not i["comune"])

    return render_template(
        "utente.html",
        u=u,
        interessi=interessi,
        n_comune=sum(1 for i in interessi if i["comune"]),
        organizzati=organizzati,
        partecipato=partecipato,
        mio=(session.get("utente_id") == utente_id),
    )


@app.route("/profilo/modifica", methods=["GET", "POST"])
@login_richiesto
def modifica_profilo():
    conn = get_db()
    uid = session["utente_id"]

    if request.method == "POST":
        nome = request.form["nome"].strip()
        data_nascita = request.form.get("data_nascita", "").strip()
        eta = eta_da(data_nascita)
        if eta is None or eta < 18:
            conn.close()
            flash("Data di nascita non valida: serve la maggiore età.")
            return redirect(url_for("modifica_profilo"))

        esegui(conn, """
            UPDATE utenti SET nome = ?, data_nascita = ?, citta = ?, professione = ?,
                              formazione = ?, bio = ?, interessi = ?
            WHERE id = ?
        """, (nome, data_nascita, request.form.get("citta", "").strip(),
              request.form.get("professione", "").strip(), request.form.get("formazione", "").strip(),
              request.form.get("bio", "").strip(), request.form.get("interessi", "").strip(), uid))
        conn.commit()
        conn.close()
        session["utente_nome"] = nome
        flash("Profilo aggiornato.")
        return redirect(url_for("profilo_utente", utente_id=uid))

    u = utente_per_id(conn, uid)
    conn.close()
    return render_template("modifica_profilo.html", u=u)


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
        minimo = int(request.form.get("min_partecipanti", 2))
        eta_min = int(request.form.get("eta_min", 18))
        eta_max = int(request.form.get("eta_max", 65))

        if minimo > posti:
            flash("Il minimo di partecipanti non può superare i posti disponibili.")
            return render_template("nuovo_tavolo.html")
        if eta_min > eta_max:
            flash("La fascia d'età non è valida.")
            return render_template("nuovo_tavolo.html")

        conn = get_db()
        row = esegui(conn, "SELECT id FROM locali WHERE nome = ?", (locale_nome,)).fetchone()
        if row:
            locale_id = row["id"]
        else:
            locale_id = inserisci(conn, "INSERT INTO locali (nome, indirizzo) VALUES (?, ?)", (locale_nome, ""))

        utente_id = session["utente_id"]
        tavolo_id = inserisci(conn, """
            INSERT INTO tavoli (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti_totali,
                                creato_il, citta, descrizione, min_partecipanti, eta_min, eta_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (tema, locale_id, utente_id, data_ora, obiettivo, posti,
              datetime.now().isoformat(), citta, descrizione, minimo, eta_min, eta_max))
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
        SELECT posti_totali, eta_min, eta_max,
               (SELECT COUNT(*) FROM iscrizioni WHERE tavolo_id = ?) AS iscritti
        FROM tavoli WHERE id = ?
    """, (tavolo_id, tavolo_id)).fetchone()

    if not tavolo:
        conn.close()
        abort(404)

    io = utente_per_id(conn, session["utente_id"])
    mia_eta = eta_da(io["data_nascita"]) if io else None
    ha_limiti = tavolo["eta_min"] is not None or tavolo["eta_max"] is not None

    if ha_limiti and mia_eta is None:
        conn.close()
        flash("Per iscriverti completa il profilo con la data di nascita.")
        return redirect(url_for("modifica_profilo"))

    if mia_eta is not None and (
        (tavolo["eta_min"] is not None and mia_eta < tavolo["eta_min"])
        or (tavolo["eta_max"] is not None and mia_eta > tavolo["eta_max"])
    ):
        conn.close()
        flash("Questo tavolo è riservato a un'altra fascia d'età.")
        return redirect(request.referrer or url_for("home"))

    if tavolo["iscritti"] < tavolo["posti_totali"]:
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


@app.route("/sw.js")
def service_worker():
    risposta = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    risposta.headers["Service-Worker-Allowed"] = "/"
    risposta.headers["Cache-Control"] = "no-cache"
    return risposta


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
