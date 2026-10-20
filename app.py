from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from PIL import Image, ImageOps, UnidentifiedImageError
from zoneinfo import ZoneInfo
import sqlite3
import os
import io
import uuid
from datetime import datetime, date, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")
USA_PG = bool(DATABASE_URL)

if USA_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")
R2_OK = all([R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_URL])
_s3 = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-di-sviluppo")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symposium.db")


def get_s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
    return _s3


def salva_foto(file, cartella):
    if not file or not file.filename:
        return None
    if not R2_OK:
        flash("Il caricamento delle foto non è configurato.")
        return None
    try:
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1200, 1200))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        buf.seek(0)
    except (UnidentifiedImageError, OSError):
        flash("Il file scelto non è un'immagine valida.")
        return None
    chiave = f"{cartella}/{uuid.uuid4().hex}.jpg"
    try:
        get_s3().upload_fileobj(
            buf, R2_BUCKET, chiave,
            ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "public, max-age=31536000"},
        )
    except Exception:
        flash("Errore nel caricamento della foto, riprova.")
        return None
    return f"{R2_PUBLIC_URL.rstrip('/')}/{chiave}"


@app.errorhandler(413)
def file_troppo_grande(e):
    flash("La foto è troppo pesante (massimo 12 MB).")
    return redirect(request.referrer or url_for("home"))


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
    aggiungi_colonna(conn, "utenti", "foto_url", "TEXT")

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
    aggiungi_colonna(conn, "tavoli", "foto_url", "TEXT")

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS iscrizioni (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            iscritto_il TEXT,
            UNIQUE (tavolo_id, utente_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS messaggi (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            testo TEXT NOT NULL,
            creato_il TEXT
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS valutazioni_locale (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            locale_id INTEGER REFERENCES locali (id),
            voto INTEGER NOT NULL,
            creato_il TEXT,
            UNIQUE (tavolo_id, utente_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS assenze (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            creato_il TEXT,
            UNIQUE (tavolo_id, utente_id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


def adesso_it():
    return datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)


def e_concluso(data_ora):
    try:
        return datetime.fromisoformat(data_ora) + timedelta(hours=2) < adesso_it()
    except (ValueError, TypeError):
        return False


def affidabilita(assenze, partecipato):
    if not partecipato:
        return None
    if assenze == 0:
        return "Alta affidabilità"
    if assenze / partecipato <= 0.2:
        return "Affidabilità media"
    return "Affidabilità bassa"


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


@app.template_filter("percento")
def percento(positivi, totale):
    try:
        return int(round(positivi * 100 / totale))
    except (TypeError, ZeroDivisionError):
        return 0


@app.context_processor
def utente_corrente():
    return {"utente_nome": session.get("utente_nome"), "utente_id": session.get("utente_id")}


def utente_per_id(conn, utente_id):
    if not utente_id:
        return None
    return esegui(conn, "SELECT * FROM utenti WHERE id = ?", (utente_id,)).fetchone()


FASCE = [
    ("mattina", "Mattina", "5:00-12:00"),
    ("pranzo", "Pranzo", "12:00-15:30"),
    ("pomeriggio", "Pomeriggio", "15:30-18:30"),
    ("sera", "Sera", "18:30-22:30"),
    ("seconda_serata", "Seconda serata", "22:30-5:00"),
]


def fascia_di(data_ora):
    try:
        d = datetime.fromisoformat(data_ora)
    except (ValueError, TypeError):
        return None
    m = d.hour * 60 + d.minute
    if 5 * 60 <= m < 12 * 60:
        return "mattina"
    if 12 * 60 <= m < 15 * 60 + 30:
        return "pranzo"
    if 15 * 60 + 30 <= m < 18 * 60 + 30:
        return "pomeriggio"
    if 18 * 60 + 30 <= m < 22 * 60 + 30:
        return "sera"
    return "seconda_serata"


@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    citta = request.args.get("citta", "").strip()
    solo_eta = request.args.get("eta") == "1"
    solo_confermati = request.args.get("confermati") == "1"
    fasce_valide = {f[0] for f in FASCE}
    fasce_sel = [f for f in request.args.getlist("fascia") if f in fasce_valide]

    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.obiettivo, t.posti_totali, t.citta, t.foto_url,
               t.min_partecipanti, t.eta_min, t.eta_max, t.organizzatore_id,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome,
               u.foto_url AS organizzatore_foto,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id) AS iscritti,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id AND i.utente_id = ?) AS gia_iscritto,
               (SELECT COUNT(*) FROM valutazioni_locale v WHERE v.locale_id = t.locale_id) AS voti_locale,
               (SELECT COALESCE(SUM(v.voto), 0) FROM valutazioni_locale v WHERE v.locale_id = t.locale_id) AS voti_pos
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

    io = utente_per_id(conn, session.get("utente_id"))
    mia_eta = eta_da(io["data_nascita"]) if io else None
    conn.close()

    if solo_eta and mia_eta is not None:
        tavoli = [t for t in tavoli
                  if (t["eta_min"] is None or mia_eta >= t["eta_min"])
                  and (t["eta_max"] is None or mia_eta <= t["eta_max"])]
    if solo_confermati:
        tavoli = [t for t in tavoli if t["min_partecipanti"] and t["iscritti"] >= t["min_partecipanti"]]
    if fasce_sel:
        tavoli = [t for t in tavoli if fascia_di(t["data_ora"]) in fasce_sel]

    return render_template("home.html", tavoli=tavoli, q=q, citta=citta,
                           solo_eta=solo_eta, solo_confermati=solo_confermati,
                           fasce=FASCE, fasce_sel=fasce_sel)


@app.route("/miei-tavoli")
@login_richiesto
def miei_tavoli():
    utente_id = session["utente_id"]
    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.posti_totali, t.citta, t.organizzatore_id, t.foto_url,
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
               t.citta, t.descrizione, t.min_partecipanti, t.eta_min, t.eta_max, t.foto_url,
               t.locale_id,
               l.nome AS locale_nome,
               u.nome AS organizzatore_nome,
               u.foto_url AS organizzatore_foto,
               (SELECT COUNT(*) FROM valutazioni_locale v WHERE v.locale_id = t.locale_id) AS voti_locale,
               (SELECT COALESCE(SUM(v.voto), 0) FROM valutazioni_locale v WHERE v.locale_id = t.locale_id) AS voti_pos
        FROM tavoli t
        LEFT JOIN locali l ON t.locale_id = l.id
        LEFT JOIN utenti u ON t.organizzatore_id = u.id
        WHERE t.id = ?
    """, (tavolo_id,)).fetchone()

    if not tavolo:
        conn.close()
        abort(404)

    righe = esegui(conn, """
        SELECT u.id, u.nome, u.interessi, u.data_nascita, u.citta, u.foto_url
        FROM iscrizioni i
        JOIN utenti u ON i.utente_id = u.id
        WHERE i.tavolo_id = ?
        ORDER BY i.id ASC
    """, (tavolo_id,)).fetchall()

    assenti = {r["utente_id"] for r in esegui(conn, "SELECT utente_id FROM assenze WHERE tavolo_id = ?", (tavolo_id,)).fetchall()}

    uid = session.get("utente_id")
    mio_voto = None
    if uid:
        r = esegui(conn, "SELECT voto FROM valutazioni_locale WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, uid)).fetchone()
        mio_voto = r["voto"] if r else None

    io = utente_per_id(conn, uid)
    conn.close()

    miei = set_interessi(io["interessi"]) if io else set()
    partecipanti = []
    for r in righe:
        p = dict(r)
        p["in_comune"] = len(miei & set_interessi(p["interessi"]))
        p["eta"] = eta_da(p["data_nascita"])
        p["assente"] = p["id"] in assenti
        partecipanti.append(p)

    eta_list = [p["eta"] for p in partecipanti if p["eta"] is not None]
    range_eta = (min(eta_list), max(eta_list)) if eta_list else None
    eta_media = round(sum(eta_list) / len(eta_list)) if eta_list else None

    gia_iscritto = any(p["id"] == uid for p in partecipanti)
    sono_organizzatore = uid == tavolo["organizzatore_id"]
    completo = len(partecipanti) >= tavolo["posti_totali"]
    confermato = bool(tavolo["min_partecipanti"] and len(partecipanti) >= tavolo["min_partecipanti"])
    concluso = e_concluso(tavolo["data_ora"])
    sono_assente = uid in assenti
    puo_valutare = bool(concluso and gia_iscritto and not sono_assente and mio_voto is None)

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
        concluso=concluso,
        puo_valutare=puo_valutare,
        mio_voto=mio_voto,
    )


@app.route("/tavolo/<int:tavolo_id>/valuta-locale", methods=["POST"])
@login_richiesto
def valuta_locale(tavolo_id):
    voto = 1 if request.form.get("voto") == "1" else 0
    uid = session["utente_id"]
    conn = get_db()
    tavolo = esegui(conn, "SELECT data_ora, locale_id FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
    if not tavolo:
        conn.close()
        abort(404)

    if not e_concluso(tavolo["data_ora"]):
        conn.close()
        flash("Potrai valutare il locale dopo la serata.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))
    if not e_iscritto(conn, tavolo_id, uid):
        conn.close()
        abort(403)
    assente = esegui(conn, "SELECT 1 AS ok FROM assenze WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, uid)).fetchone()
    if assente:
        conn.close()
        flash("Non puoi valutare un locale se risulti assente.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))

    try:
        esegui(conn,
               "INSERT INTO valutazioni_locale (tavolo_id, utente_id, locale_id, voto, creato_il) VALUES (?, ?, ?, ?, ?)",
               (tavolo_id, uid, tavolo["locale_id"], voto, datetime.now().isoformat()))
        conn.commit()
        flash("Grazie per la valutazione!")
    except IntegrityError:
        conn.rollback()
        flash("Hai già valutato questo locale.")
    conn.close()
    return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))


@app.route("/tavolo/<int:tavolo_id>/assenza/<int:utente_target>", methods=["POST"])
@login_richiesto
def segna_assenza(tavolo_id, utente_target):
    conn = get_db()
    tavolo = esegui(conn, "SELECT organizzatore_id, data_ora FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
    if not tavolo:
        conn.close()
        abort(404)
    if tavolo["organizzatore_id"] != session["utente_id"]:
        conn.close()
        abort(403)
    if not e_concluso(tavolo["data_ora"]):
        conn.close()
        flash("Le assenze si segnano dopo la serata.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))
    if utente_target == tavolo["organizzatore_id"] or not e_iscritto(conn, tavolo_id, utente_target):
        conn.close()
        abort(400)

    esistente = esegui(conn, "SELECT id FROM assenze WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, utente_target)).fetchone()
    if esistente:
        esegui(conn, "DELETE FROM assenze WHERE id = ?", (esistente["id"],))
        flash("Assenza rimossa.")
    else:
        esegui(conn, "INSERT INTO assenze (tavolo_id, utente_id, creato_il) VALUES (?, ?, ?)",
               (tavolo_id, utente_target, datetime.now().isoformat()))
        flash("Assenza segnata.")
    conn.commit()
    conn.close()
    return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))


def e_iscritto(conn, tavolo_id, utente_id):
    riga = esegui(conn,
                  "SELECT 1 AS ok FROM iscrizioni WHERE tavolo_id = ? AND utente_id = ?",
                  (tavolo_id, utente_id)).fetchone()
    return riga is not None


def leggi_messaggi(conn, tavolo_id, dopo):
    if dopo <= 0:
        righe = esegui(conn, """
            SELECT m.id, m.testo, m.creato_il, m.utente_id, u.nome, u.foto_url
            FROM messaggi m JOIN utenti u ON u.id = m.utente_id
            WHERE m.tavolo_id = ?
            ORDER BY m.id DESC LIMIT 100
        """, (tavolo_id,)).fetchall()
        righe = list(reversed(righe))
    else:
        righe = esegui(conn, """
            SELECT m.id, m.testo, m.creato_il, m.utente_id, u.nome, u.foto_url
            FROM messaggi m JOIN utenti u ON u.id = m.utente_id
            WHERE m.tavolo_id = ? AND m.id > ?
            ORDER BY m.id ASC LIMIT 200
        """, (tavolo_id, dopo)).fetchall()

    uid = session.get("utente_id")
    risultato = []
    for r in righe:
        try:
            ora = datetime.fromisoformat(r["creato_il"]).strftime("%d/%m %H:%M")
        except (ValueError, TypeError):
            ora = ""
        risultato.append({
            "id": r["id"],
            "testo": r["testo"],
            "ora": ora,
            "nome": r["nome"],
            "foto_url": r["foto_url"],
            "utente_id": r["utente_id"],
            "mio": r["utente_id"] == uid,
        })
    return risultato


@app.route("/tavolo/<int:tavolo_id>/chat")
@login_richiesto
def chat_tavolo(tavolo_id):
    conn = get_db()
    tavolo = esegui(conn, "SELECT id, tema FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
    if not tavolo:
        conn.close()
        abort(404)
    if not e_iscritto(conn, tavolo_id, session["utente_id"]):
        conn.close()
        flash("La chat è riservata ai partecipanti del tavolo.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))
    conn.close()
    return render_template("chat.html", tavolo=tavolo)


@app.route("/tavolo/<int:tavolo_id>/chat/messaggi")
@login_richiesto
def chat_messaggi(tavolo_id):
    try:
        dopo = int(request.args.get("dopo", 0))
    except ValueError:
        dopo = 0
    conn = get_db()
    if not e_iscritto(conn, tavolo_id, session["utente_id"]):
        conn.close()
        abort(403)
    messaggi = leggi_messaggi(conn, tavolo_id, dopo)
    conn.close()
    return jsonify(messaggi)


@app.route("/tavolo/<int:tavolo_id>/chat/invia", methods=["POST"])
@login_richiesto
def chat_invia(tavolo_id):
    testo = request.form.get("testo", "").strip()[:1000]
    conn = get_db()
    if not e_iscritto(conn, tavolo_id, session["utente_id"]):
        conn.close()
        abort(403)
    if testo:
        esegui(conn,
               "INSERT INTO messaggi (tavolo_id, utente_id, testo, creato_il) VALUES (?, ?, ?, ?)",
               (tavolo_id, session["utente_id"], testo, datetime.now().isoformat()))
        conn.commit()
    conn.close()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(url_for("chat_tavolo", tavolo_id=tavolo_id))


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
            flash("Benvenuto! Aggiungi una foto dal tuo profilo.")
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
    assenze = esegui(conn, "SELECT COUNT(*) AS n FROM assenze WHERE utente_id = ?", (utente_id,)).fetchone()["n"]
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
        assenze=assenze,
        affidabilita=affidabilita(assenze, partecipato),
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

        nuova_foto = salva_foto(request.files.get("foto"), "profili")
        if nuova_foto:
            esegui(conn, "UPDATE utenti SET foto_url = ? WHERE id = ?", (nuova_foto, uid))

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

        foto_url = salva_foto(request.files.get("foto"), "tavoli")

        conn = get_db()
        row = esegui(conn, "SELECT id FROM locali WHERE nome = ?", (locale_nome,)).fetchone()
        if row:
            locale_id = row["id"]
        else:
            locale_id = inserisci(conn, "INSERT INTO locali (nome, indirizzo) VALUES (?, ?)", (locale_nome, ""))

        utente_id = session["utente_id"]
        tavolo_id = inserisci(conn, """
            INSERT INTO tavoli (tema, locale_id, organizzatore_id, data_ora, obiettivo, posti_totali,
                                creato_il, citta, descrizione, min_partecipanti, eta_min, eta_max, foto_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (tema, locale_id, utente_id, data_ora, obiettivo, posti,
              datetime.now().isoformat(), citta, descrizione, minimo, eta_min, eta_max, foto_url))
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

    esegui(conn, "DELETE FROM messaggi WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM valutazioni_locale WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM assenze WHERE tavolo_id = ?", (tavolo_id,))
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
