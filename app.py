from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import escape
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

CRITERI = [
    ("descrizione", "Corrisponde alla descrizione"),
    ("accogliente", "È accogliente"),
    ("cibo", "Il cibo era buono"),
    ("servizio", "Servizio cordiale e veloce"),
    ("qualita_prezzo", "Buon rapporto qualità-prezzo"),
]

PREZZI = [
    ("0-10", "0-10 €"),
    ("10-25", "10-25 €"),
    ("25-40", "25-40 €"),
    ("40+", "Oltre 40 €"),
]

COMPAT = [
    (5, "Molto compatibile"),
    (4, "Compatibile"),
    (3, "Neutra"),
    (2, "Poco compatibile"),
    (1, "Per niente"),
]

FASCE = [
    ("mattina", "Mattina", "5:00-12:00"),
    ("pranzo", "Pranzo", "12:00-15:30"),
    ("pomeriggio", "Pomeriggio", "15:30-18:30"),
    ("sera", "Sera", "18:30-22:30"),
    ("seconda_serata", "Seconda serata", "22:30-5:00"),
]

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
    aggiungi_colonna(conn, "valutazioni_locale", "fascia_prezzo", "TEXT")

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS valutazioni_criteri (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            criterio TEXT NOT NULL,
            voto INTEGER NOT NULL,
            UNIQUE (tavolo_id, utente_id, criterio)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS presenze (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            segnalatore_id INTEGER REFERENCES utenti (id),
            utente_id INTEGER REFERENCES utenti (id),
            presente INTEGER NOT NULL,
            UNIQUE (tavolo_id, segnalatore_id, utente_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS compatibilita (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            da_utente INTEGER REFERENCES utenti (id),
            a_utente INTEGER REFERENCES utenti (id),
            punteggio INTEGER NOT NULL,
            UNIQUE (tavolo_id, da_utente, a_utente)
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

    aggiungi_colonna(conn, "utenti", "notif_tavoli", "INTEGER DEFAULT 1")
    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS preferiti_utenti (
            id {pk},
            utente_id INTEGER REFERENCES utenti (id),
            salvato_id INTEGER REFERENCES utenti (id),
            UNIQUE (utente_id, salvato_id)
        )
    """)
    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS messaggi_privati (
            id {pk},
            da_utente INTEGER REFERENCES utenti (id),
            a_utente INTEGER REFERENCES utenti (id),
            testo TEXT NOT NULL,
            creato_il TEXT,
            letto INTEGER DEFAULT 0
        )
    """)
    aggiungi_colonna(conn, "tavoli", "su_richiesta", "INTEGER DEFAULT 0")
    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS richieste (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            stato TEXT NOT NULL,
            creato_il TEXT,
            UNIQUE (tavolo_id, utente_id)
        )
    """)
    aggiungi_colonna(conn, "utenti", "premium", "INTEGER DEFAULT 0")
    aggiungi_colonna(conn, "tavoli", "esclusivo", "INTEGER DEFAULT 0")
    esegui(conn, "CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT)")
    aggiungi_colonna(conn, "tavoli", "lat", "DOUBLE PRECISION")
    aggiungi_colonna(conn, "tavoli", "lng", "DOUBLE PRECISION")

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS notifiche (
            id {pk},
            utente_id INTEGER REFERENCES utenti (id),
            tipo TEXT,
            titolo TEXT,
            testo TEXT,
            link TEXT,
            da_utente INTEGER,
            creato_il TEXT,
            letta INTEGER DEFAULT 0
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS preferiti (
            id {pk},
            tavolo_id INTEGER REFERENCES tavoli (id),
            utente_id INTEGER REFERENCES utenti (id),
            UNIQUE (tavolo_id, utente_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS locali_seguiti (
            id {pk},
            locale_id INTEGER REFERENCES locali (id),
            utente_id INTEGER REFERENCES utenti (id),
            UNIQUE (locale_id, utente_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS amici (
            id {pk},
            utente_id INTEGER REFERENCES utenti (id),
            amico_id INTEGER REFERENCES utenti (id),
            UNIQUE (utente_id, amico_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS bloccati (
            id {pk},
            utente_id INTEGER REFERENCES utenti (id),
            bloccato_id INTEGER REFERENCES utenti (id),
            UNIQUE (utente_id, bloccato_id)
        )
    """)

    esegui(conn, f"""
        CREATE TABLE IF NOT EXISTS segnalazioni (
            id {pk},
            da_utente INTEGER REFERENCES utenti (id),
            utente_id INTEGER REFERENCES utenti (id),
            motivo TEXT,
            creato_il TEXT
        )
    """)

    aggiungi_colonna(conn, "utenti", "sospeso", "INTEGER DEFAULT 0")
    aggiungi_colonna(conn, "utenti", "id_alexandria", "BIGINT")
    aggiungi_colonna(conn, "segnalazioni", "gestita", "INTEGER DEFAULT 0")

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


def affidabilita(assenze, partecipato):
    if not partecipato:
        return None
    if assenze == 0:
        return "Alta affidabilità"
    if assenze / partecipato <= 0.2:
        return "Affidabilità media"
    return "Affidabilità bassa"


def etichetta_compat(media):
    if media >= 4.5:
        return "Molto compatibile"
    if media >= 3.5:
        return "Compatibile"
    if media >= 2.5:
        return "Neutra"
    if media >= 1.5:
        return "Poco compatibile"
    return "Per niente compatibile"


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


@app.after_request
def banner_valutazione(risposta):
    try:
        if (
            "utente_id" not in session
            or risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "feedback_tavolo", "service_worker")
        ):
            return risposta

        uid = session["utente_id"]
        conn = get_db()
        righe = esegui(conn, """
            SELECT t.id, t.tema, t.data_ora
            FROM tavoli t
            JOIN iscrizioni i ON i.tavolo_id = t.id AND i.utente_id = ?
            WHERE NOT EXISTS (SELECT 1 FROM valutazioni_locale v WHERE v.tavolo_id = t.id AND v.utente_id = ?)
              AND NOT EXISTS (SELECT 1 FROM assenze a WHERE a.tavolo_id = t.id AND a.utente_id = ?)
            ORDER BY t.data_ora DESC
        """, (uid, uid, uid)).fetchall()
        conn.close()

        pendenti = [r for r in righe if e_concluso(r["data_ora"])]
        if not pendenti:
            return risposta

        primo = pendenti[0]
        altre = len(pendenti) - 1
        link = url_for("feedback_tavolo", tavolo_id=primo["id"])
        extra = f" (e altre {altre})" if altre > 0 else ""
        html = (
            '<div class="banner-feedback">'
            f'<span>📝 Com\'è andata la serata «{escape(primo["tema"])}»?{extra}</span>'
            f'<a class="btn-nuovo" href="{link}">Valuta ora</a>'
            '</div>'
        )

        testo = risposta.get_data(as_text=True)
        if "<main>" in testo:
            risposta.set_data(testo.replace("<main>", "<main>" + html, 1))
    except Exception:
        pass
    return risposta


@app.after_request
def campanella(risposta):
    try:
        if (
            "utente_id" not in session
            or risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "service_worker")
        ):
            return risposta

        conn = get_db()
        n = esegui(conn, "SELECT COUNT(*) AS n FROM notifiche WHERE utente_id = ? AND letta = 0",
                   (session["utente_id"],)).fetchone()["n"]
        conn.close()

        badge = ""
        if n:
            badge = '<span class="campanella-n">' + (str(n) if n < 100 else "99+") + "</span>"
        html = '<a class="campanella" href="' + url_for("notifiche") + '" aria-label="Notifiche">🔔' + badge + "</a>"

        testo = risposta.get_data(as_text=True)
        if "</body>" in testo:
            risposta.set_data(testo.replace("</body>", html + "</body>", 1))
    except Exception:
        pass
    return risposta


def geocodifica(locale, citta):
    import json
    import time
    import urllib.parse
    import urllib.request
    for ricerca in (f"{locale}, {citta}, Italia", f"{citta}, Italia"):
        try:
            url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=it&q="
                   + urllib.parse.quote(ricerca))
            req = urllib.request.Request(url, headers={"User-Agent": "Symposium/1.0 (social eating app)"})
            with urllib.request.urlopen(req, timeout=5) as r:
                dati = json.loads(r.read().decode())
            if dati:
                return float(dati[0]["lat"]), float(dati[0]["lon"])
        except Exception:
            pass
        time.sleep(1)
    return None, None


@app.context_processor
def preferiti_corrente():
    if "utente_id" not in session or request.endpoint != "home":
        return {"preferiti_ids": set()}
    conn = get_db()
    righe = esegui(conn, "SELECT tavolo_id FROM preferiti WHERE utente_id = ?",
                   (session["utente_id"],)).fetchall()
    conn.close()
    return {"preferiti_ids": {r["tavolo_id"] for r in righe}}


def stato_preferiti(tavolo_id, locale_id):
    uid = session.get("utente_id")
    if not uid:
        return {"preferito": False, "segue_locale": False}
    conn = get_db()
    p = esegui(conn, "SELECT 1 AS ok FROM preferiti WHERE tavolo_id = ? AND utente_id = ?",
               (tavolo_id, uid)).fetchone()
    sl = None
    if locale_id:
        sl = esegui(conn, "SELECT 1 AS ok FROM locali_seguiti WHERE locale_id = ? AND utente_id = ?",
                    (locale_id, uid)).fetchone()
    conn.close()
    return {"preferito": p is not None, "segue_locale": sl is not None}


def relazioni_bloccate(conn, uid):
    a = esegui(conn, "SELECT bloccato_id AS x FROM bloccati WHERE utente_id = ?", (uid,)).fetchall()
    b = esegui(conn, "SELECT utente_id AS x FROM bloccati WHERE bloccato_id = ?", (uid,)).fetchall()
    return {r["x"] for r in a} | {r["x"] for r in b}


def utenti_oscurati():
    uid = session.get("utente_id")
    if not uid:
        return set()
    conn = get_db()
    r = relazioni_bloccate(conn, uid)
    conn.close()
    return r


def stato_relazione(altro_id):
    uid = session.get("utente_id")
    if not uid or uid == altro_id:
        return {"e_amico": False, "e_bloccato": False}
    conn = get_db()
    a = esegui(conn, "SELECT 1 AS ok FROM amici WHERE utente_id = ? AND amico_id = ?", (uid, altro_id)).fetchone()
    b = esegui(conn, "SELECT 1 AS ok FROM bloccati WHERE utente_id = ? AND bloccato_id = ?", (uid, altro_id)).fetchone()
    conn.close()
    return {"e_amico": a is not None, "e_bloccato": b is not None}


MOTIVI_SEGNALAZIONE = ("Comportamento inappropriato", "Profilo falso", "Spam", "Altro")


def info_badge(utente_id):
    conn = get_db()
    righe = esegui(conn, """
        SELECT t.data_ora, t.organizzatore_id
        FROM iscrizioni i
        JOIN tavoli t ON t.id = i.tavolo_id
        WHERE i.utente_id = ?
          AND NOT EXISTS (SELECT 1 FROM assenze a WHERE a.tavolo_id = t.id AND a.utente_id = ?)
    """, (utente_id, utente_id)).fetchall()
    n_val = esegui(conn, "SELECT COUNT(*) AS n FROM valutazioni_locale WHERE utente_id = ?",
                   (utente_id,)).fetchone()["n"]
    conn.close()

    chiuse = [r for r in righe if e_concluso(r["data_ora"])]
    serate = len(chiuse)
    organizzate = sum(1 for r in chiuse if r["organizzatore_id"] == utente_id)
    partecipate = serate - organizzate
    punti = partecipate * 10 + organizzate * 25 + int(n_val) * 5

    badge = []
    if serate >= 5:
        badge.append({"nome": "Tabler+", "classe": "tablerplus", "icona": "🔵"})
    elif serate >= 1:
        badge.append({"nome": "Tabler", "classe": "tabler", "icona": "🟠"})
    if organizzate >= 3:
        badge.append({"nome": "Event Manager", "classe": "manager", "icona": "🟣"})

    prossimi = []
    if serate < 1:
        prossimi.append("Partecipa alla tua prima serata per ottenere il badge Tabler")
    elif serate < 5:
        prossimi.append(f"Ancora {5 - serate} serate per il badge Tabler+")
    if organizzate < 3:
        prossimi.append(f"Ancora {3 - organizzate} serate organizzate per il badge Event Manager")

    principale = badge[-1] if badge else None
    return {
        "punti": punti,
        "serate": serate,
        "badge": badge,
        "badge_principale": principale,
        "prossimi_badge": prossimi,
    }


def admin_email():
    return os.environ.get("ADMIN_EMAIL", "").strip().lower()


def pagamento_attivo(conn):
    r = esegui(conn, "SELECT valore FROM impostazioni WHERE chiave = ?", ("premium_a_pagamento",)).fetchone()
    return bool(r and r["valore"] == "1")


def e_admin(conn, uid):
    if not uid or not admin_email():
        return False
    u = esegui(conn, "SELECT email FROM utenti WHERE id = ?", (uid,)).fetchone()
    return bool(u and (u["email"] or "").strip().lower() == admin_email())


def e_premium(conn, uid):
    if not uid:
        return False
    if not pagamento_attivo(conn):
        return True
    u = esegui(conn, "SELECT premium FROM utenti WHERE id = ?", (uid,)).fetchone()
    if u and u["premium"]:
        return True
    return e_admin(conn, uid)


@app.context_processor
def premium_ctx():
    uid = session.get("utente_id")
    if not uid:
        return {"sono_premium": False, "sono_admin": False}
    conn = get_db()
    r = {"sono_premium": e_premium(conn, uid), "sono_admin": e_admin(conn, uid)}
    conn.close()
    return r


@app.before_request
def riserva_esclusivi():
    if request.endpoint not in ("dettaglio_tavolo", "iscriviti"):
        return None
    try:
        conn = get_db()
        r = esegui(conn, "SELECT esclusivo FROM tavoli WHERE id = ?",
                   ((request.view_args or {}).get("tavolo_id"),)).fetchone()
        ok = (not r) or (not r["esclusivo"]) or e_premium(conn, session.get("utente_id"))
        conn.close()
    except Exception:
        return None
    if not ok:
        flash("Questo banchetto degli dei è riservato agli utenti Premium.")
        return redirect(url_for("pagina_premium"))
    return None


def utente_per_id(conn, utente_id):
    if not utente_id:
        return None
    return esegui(conn, "SELECT * FROM utenti WHERE id = ?", (utente_id,)).fetchone()


def e_iscritto(conn, tavolo_id, utente_id):
    riga = esegui(conn,
                  "SELECT 1 AS ok FROM iscrizioni WHERE tavolo_id = ? AND utente_id = ?",
                  (tavolo_id, utente_id)).fetchone()
    return riga is not None


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
               t.min_partecipanti, t.eta_min, t.eta_max, t.organizzatore_id, t.lat, t.lng, t.esclusivo, t.su_richiesta,
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
    oscurati = utenti_oscurati()
    if oscurati:
        tavoli = [t for t in tavoli if t["organizzatore_id"] not in oscurati]
    conn_p = get_db()
    premium_ok = e_premium(conn_p, session.get("utente_id"))
    conn_p.close()
    if not premium_ok:
        tavoli = [t for t in tavoli if not t["esclusivo"]]
    if fasce_sel:
        tavoli = [t for t in tavoli if fascia_di(t["data_ora"]) in fasce_sel]

    pos = pos_da_cookie()
    if pos:
        tavoli = [dict(t) for t in tavoli]
        for t in tavoli:
            if t["lat"] and t["lng"]:
                t["km"] = round(distanza_km(pos[0], pos[1], t["lat"], t["lng"]), 1)
            else:
                t["km"] = None
        raggio = request.args.get("raggio", "")
        if raggio.isdigit() and int(raggio) in (10, 30, 50, 100):
            tavoli = [t for t in tavoli if t["km"] is not None and t["km"] <= int(raggio)]
        if request.args.get("vicini") == "1":
            tavoli.sort(key=lambda t: (t["km"] is None, t["km"] or 0))
    if request.args.get("json") == "1":
        elenco = [dict(t) for t in tavoli]
        conn2 = get_db()
        tentati = 0
        for t in elenco:
            if t["lat"] is None and tentati < 4:
                tentati += 1
                la, ln = geocodifica(t["locale_nome"] or "", t["citta"] or "")
                t["lat"], t["lng"] = (la, ln) if la is not None else (0.0, 0.0)
                esegui(conn2, "UPDATE tavoli SET lat = ?, lng = ? WHERE id = ?", (t["lat"], t["lng"], t["id"]))
        conn2.commit()
        conn2.close()
        return jsonify([{
            "id": t["id"],
            "tema": t["tema"],
            "locale": t["locale_nome"] or "",
            "citta": t["citta"] or "",
            "data": data_it(t["data_ora"]),
            "iscritti": t["iscritti"],
            "posti": t["posti_totali"],
            "confermato": bool(t["min_partecipanti"] and t["iscritti"] >= t["min_partecipanti"]),
            "lat": t["lat"],
            "lng": t["lng"],
            "url": url_for("dettaglio_tavolo", tavolo_id=t["id"]),
        } for t in elenco if t["lat"] and t["lng"]])

    return render_template("home.html", tavoli=tavoli, q=q, citta=citta,
                           solo_eta=solo_eta, solo_confermati=solo_confermati,
                           fasce=FASCE, fasce_sel=fasce_sel)


def notifica(conn, utente_id, tipo, titolo, testo, link, da_utente=None):
    esegui(conn, """
        INSERT INTO notifiche (utente_id, tipo, titolo, testo, link, da_utente, creato_il, letta)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (utente_id, tipo, titolo, testo, link, da_utente, datetime.now().isoformat()))


@app.route("/notifiche")
@login_richiesto
def notifiche():
    uid = session["utente_id"]
    conn = get_db()
    righe = esegui(conn, """
        SELECT n.id, n.titolo, n.testo, n.creato_il, n.letta, n.da_utente,
               u.nome AS da_nome, u.foto_url AS da_foto
        FROM notifiche n
        LEFT JOIN utenti u ON u.id = n.da_utente
        WHERE n.utente_id = ?
        ORDER BY n.id DESC
        LIMIT 100
    """, (uid,)).fetchall()
    io = utente_per_id(conn, uid)
    conn.close()
    attive = io["notif_tavoli"] in (None, 1)
    return render_template("notifiche.html", notifiche=righe, attive=attive)


@app.route("/notifiche/<int:notifica_id>/apri")
@login_richiesto
def apri_notifica(notifica_id):
    conn = get_db()
    n = esegui(conn, "SELECT link FROM notifiche WHERE id = ? AND utente_id = ?",
               (notifica_id, session["utente_id"])).fetchone()
    if not n:
        conn.close()
        abort(404)
    esegui(conn, "UPDATE notifiche SET letta = 1 WHERE id = ?", (notifica_id,))
    conn.commit()
    conn.close()
    return redirect(n["link"] or url_for("notifiche"))


@app.route("/notifiche/lette", methods=["POST"])
@login_richiesto
def notifiche_lette():
    conn = get_db()
    esegui(conn, "UPDATE notifiche SET letta = 1 WHERE utente_id = ?", (session["utente_id"],))
    conn.commit()
    conn.close()
    return redirect(url_for("notifiche"))


@app.route("/notifiche/preferenze", methods=["POST"])
@login_richiesto
def notifiche_preferenze():
    valore = 1 if request.form.get("notif_tavoli") == "1" else 0
    conn = get_db()
    esegui(conn, "UPDATE utenti SET notif_tavoli = ? WHERE id = ?", (valore, session["utente_id"]))
    conn.commit()
    conn.close()
    flash("Preferenze salvate.")
    return redirect(url_for("notifiche"))


@app.route("/mappa")
def mappa():
    fasce_valide = {f[0] for f in FASCE}
    return render_template(
        "mappa.html",
        q=request.args.get("q", "").strip(),
        citta=request.args.get("citta", "").strip(),
        solo_eta=request.args.get("eta") == "1",
        solo_confermati=request.args.get("confermati") == "1",
        fasce=FASCE,
        fasce_sel=[f for f in request.args.getlist("fascia") if f in fasce_valide],
    )


def pulisci_testo(valore):
    import re
    return re.sub(r"[^\w\s'’-]", "", valore or "").strip()[:60]


TIPI_LOCALE = [
    ("", "Tutti i tipi", "restaurant|pub|bar|cafe|fast_food"),
    ("ristorante", "Ristoranti e pizzerie", "restaurant|fast_food"),
    ("pub", "Pub e birrerie", "pub"),
    ("bar", "Bar e caffè", "bar|cafe"),
]


def cerca_locali(nome, citta, tipo):
    import json
    import urllib.parse
    import urllib.request
    amenity = next((t[2] for t in TIPI_LOCALE if t[0] == tipo), TIPI_LOCALE[0][2])
    filtro_nome = f'["name"~"{nome}",i]' if nome else '["name"]'
    query = (
        "[out:json][timeout:20];"
        f'area["name"~"^{citta}$",i]["boundary"="administrative"]["admin_level"="8"]->.a;'
        f'nwr["amenity"~"^({amenity})$"]{filtro_nome}(area.a);'
        "out center 80;"
    )
    try:
        dati = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter", data=dati,
            headers={"User-Agent": "Symposium/1.0 (social eating app)"})
        with urllib.request.urlopen(req, timeout=22) as r:
            risposta = json.loads(r.read().decode())
    except Exception:
        return [], "Il servizio di ricerca non risponde, riprova tra poco."

    visti = set()
    risultati = []
    for el in risposta.get("elements", []):
        tag = el.get("tags", {})
        nome_l = tag.get("name")
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        if not nome_l or lat is None or lng is None:
            continue
        indirizzo = " ".join(x for x in (tag.get("addr:street"), tag.get("addr:housenumber")) if x)
        chiave = (nome_l.lower(), indirizzo.lower())
        if chiave in visti:
            continue
        visti.add(chiave)
        risultati.append({
            "nome": nome_l,
            "indirizzo": indirizzo,
            "tipo": {"restaurant": "Ristorante", "pub": "Pub", "bar": "Bar", "cafe": "Caffè",
                     "fast_food": "Fast food"}.get(tag.get("amenity"), ""),
            "lat": round(float(lat), 6),
            "lng": round(float(lng), 6),
        })
    risultati.sort(key=lambda x: x["nome"].lower())
    if not risultati:
        return [], "Nessun locale trovato: controlla il nome della città (comune)."
    return risultati, None


@app.route("/locali")
def locali():
    nome = pulisci_testo(request.args.get("nome", ""))
    citta = pulisci_testo(request.args.get("citta", ""))
    tipo = request.args.get("tipo", "")
    risultati, errore = [], None
    if citta:
        risultati, errore = cerca_locali(nome, citta, tipo)
    elif nome:
        errore = "Indica anche la città in cui cercare."
    return render_template("locali.html", nome=nome, citta=citta, tipo=tipo,
                           tipi=TIPI_LOCALE, risultati=risultati, errore=errore)


@app.route("/tavolo/<int:tavolo_id>/preferito", methods=["POST"])
@login_richiesto
def preferito(tavolo_id):
    uid = session["utente_id"]
    conn = get_db()
    if not esegui(conn, "SELECT 1 AS ok FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone():
        conn.close()
        abort(404)
    r = esegui(conn, "SELECT id FROM preferiti WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, uid)).fetchone()
    if r:
        esegui(conn, "DELETE FROM preferiti WHERE id = ?", (r["id"],))
    else:
        esegui(conn, "INSERT INTO preferiti (tavolo_id, utente_id) VALUES (?, ?)", (tavolo_id, uid))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("dettaglio_tavolo", tavolo_id=tavolo_id))


@app.route("/locale/<int:locale_id>/segui", methods=["POST"])
@login_richiesto
def segui_locale(locale_id):
    uid = session["utente_id"]
    conn = get_db()
    if not esegui(conn, "SELECT 1 AS ok FROM locali WHERE id = ?", (locale_id,)).fetchone():
        conn.close()
        abort(404)
    r = esegui(conn, "SELECT id FROM locali_seguiti WHERE locale_id = ? AND utente_id = ?", (locale_id, uid)).fetchone()
    if r:
        esegui(conn, "DELETE FROM locali_seguiti WHERE id = ?", (r["id"],))
        flash("Non segui più questo locale.")
    else:
        esegui(conn, "INSERT INTO locali_seguiti (locale_id, utente_id) VALUES (?, ?)", (locale_id, uid))
        flash("Ora segui questo locale: ti avviseremo quando qualcuno crea un banchetto lì.")
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("home"))


@app.route("/preferiti")
@login_richiesto
def preferiti():
    uid = session["utente_id"]
    conn = get_db()
    tavoli = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.posti_totali, t.citta, l.nome AS locale_nome,
               (SELECT COUNT(*) FROM iscrizioni x WHERE x.tavolo_id = t.id) AS iscritti
        FROM tavoli t
        JOIN preferiti p ON p.tavolo_id = t.id AND p.utente_id = ?
        LEFT JOIN locali l ON l.id = t.locale_id
        ORDER BY t.data_ora ASC
    """, (uid,)).fetchall()
    seguiti = esegui(conn, """
        SELECT l.id, l.nome
        FROM locali_seguiti s
        JOIN locali l ON l.id = s.locale_id
        WHERE s.utente_id = ?
        ORDER BY l.nome ASC
    """, (uid,)).fetchall()
    conn.close()
    return render_template("preferiti.html", tavoli=tavoli, seguiti=seguiti)


@app.route("/utente/<int:altro_id>/amico", methods=["POST"])
@login_richiesto
def amico(altro_id):
    uid = session["utente_id"]
    if altro_id == uid:
        abort(400)
    conn = get_db()
    if not utente_per_id(conn, altro_id):
        conn.close()
        abort(404)
    if altro_id in relazioni_bloccate(conn, uid):
        conn.close()
        flash("Non puoi aggiungere questo utente.")
        return redirect(url_for("profilo_utente", utente_id=altro_id))
    r = esegui(conn, "SELECT id FROM amici WHERE utente_id = ? AND amico_id = ?", (uid, altro_id)).fetchone()
    if r:
        esegui(conn, "DELETE FROM amici WHERE id = ?", (r["id"],))
        flash("Utente rimosso dagli amici.")
    else:
        esegui(conn, "INSERT INTO amici (utente_id, amico_id) VALUES (?, ?)", (uid, altro_id))
        notifica(conn, altro_id, "amico", session.get("utente_nome") or "Nuovo amico",
                 "ti ha aggiunto agli amici", url_for("profilo_utente", utente_id=uid), uid)
        flash("Utente aggiunto agli amici.")
    conn.commit()
    conn.close()
    return redirect(url_for("profilo_utente", utente_id=altro_id))


@app.route("/utente/<int:altro_id>/blocca", methods=["POST"])
@login_richiesto
def blocca(altro_id):
    uid = session["utente_id"]
    if altro_id == uid:
        abort(400)
    conn = get_db()
    if not utente_per_id(conn, altro_id):
        conn.close()
        abort(404)
    r = esegui(conn, "SELECT id FROM bloccati WHERE utente_id = ? AND bloccato_id = ?", (uid, altro_id)).fetchone()
    if r:
        esegui(conn, "DELETE FROM bloccati WHERE id = ?", (r["id"],))
        flash("Utente sbloccato.")
    else:
        esegui(conn, "INSERT INTO bloccati (utente_id, bloccato_id) VALUES (?, ?)", (uid, altro_id))
        esegui(conn, "DELETE FROM amici WHERE (utente_id = ? AND amico_id = ?) OR (utente_id = ? AND amico_id = ?)",
               (uid, altro_id, altro_id, uid))
        esegui(conn, "DELETE FROM preferiti_utenti WHERE (utente_id = ? AND salvato_id = ?) OR (utente_id = ? AND salvato_id = ?)",
               (uid, altro_id, altro_id, uid))
        flash("Utente bloccato.")
    conn.commit()
    conn.close()
    return redirect(url_for("profilo_utente", utente_id=altro_id))


@app.route("/utente/<int:altro_id>/segnala", methods=["POST"])
@login_richiesto
def segnala(altro_id):
    uid = session["utente_id"]
    motivo = request.form.get("motivo", "")
    if altro_id == uid or motivo not in MOTIVI_SEGNALAZIONE:
        abort(400)
    conn = get_db()
    if not utente_per_id(conn, altro_id):
        conn.close()
        abort(404)
    esegui(conn, "INSERT INTO segnalazioni (da_utente, utente_id, motivo, creato_il) VALUES (?, ?, ?, ?)",
           (uid, altro_id, motivo, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    flash("Grazie, la segnalazione è stata inviata.")
    return redirect(url_for("profilo_utente", utente_id=altro_id))


@app.route("/amici")
@login_richiesto
def amici():
    uid = session["utente_id"]
    conn = get_db()
    lista = esegui(conn, """
        SELECT u.id, u.nome, u.foto_url, u.citta
        FROM amici a JOIN utenti u ON u.id = a.amico_id
        WHERE a.utente_id = ? ORDER BY u.nome ASC
    """, (uid,)).fetchall()
    bloccati = esegui(conn, """
        SELECT u.id, u.nome, u.foto_url, u.citta
        FROM bloccati b JOIN utenti u ON u.id = b.bloccato_id
        WHERE b.utente_id = ? ORDER BY u.nome ASC
    """, (uid,)).fetchall()
    conn.close()
    return render_template("amici.html", amici=lista, bloccati=bloccati)


@app.route("/premium")
def pagina_premium():
    conn = get_db()
    a_pagamento = pagamento_attivo(conn)
    conn.close()
    return render_template("premium.html", a_pagamento=a_pagamento)


@app.route("/admin")
@login_richiesto
def admin():
    conn = get_db()
    if not e_admin(conn, session["utente_id"]):
        conn.close()
        abort(403)
    a_pagamento = pagamento_attivo(conn)
    utenti = esegui(conn, "SELECT id, nome, email, premium FROM utenti ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return render_template("admin.html", a_pagamento=a_pagamento, utenti=utenti, stat=dati_dashboard())


@app.route("/admin/premium", methods=["POST"])
@login_richiesto
def admin_premium_toggle():
    conn = get_db()
    if not e_admin(conn, session["utente_id"]):
        conn.close()
        abort(403)
    nuovo = "0" if pagamento_attivo(conn) else "1"
    if esegui(conn, "SELECT chiave FROM impostazioni WHERE chiave = ?", ("premium_a_pagamento",)).fetchone():
        esegui(conn, "UPDATE impostazioni SET valore = ? WHERE chiave = ?", (nuovo, "premium_a_pagamento"))
    else:
        esegui(conn, "INSERT INTO impostazioni (chiave, valore) VALUES (?, ?)", ("premium_a_pagamento", nuovo))
    conn.commit()
    conn.close()
    flash("Premium ora è A PAGAMENTO." if nuovo == "1" else "Premium ora è GRATUITO per tutti.")
    return redirect(url_for("admin"))


@app.route("/admin/utente/<int:utente_target>/premium", methods=["POST"])
@login_richiesto
def admin_utente_premium(utente_target):
    conn = get_db()
    if not e_admin(conn, session["utente_id"]):
        conn.close()
        abort(403)
    r = esegui(conn, "SELECT premium FROM utenti WHERE id = ?", (utente_target,)).fetchone()
    if not r:
        conn.close()
        abort(404)
    esegui(conn, "UPDATE utenti SET premium = ? WHERE id = ?", (0 if r["premium"] else 1, utente_target))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("admin"))


def cancella_tavoli(conn, ids):
    for tid in ids:
        for tab in ("messaggi", "valutazioni_criteri", "compatibilita", "presenze",
                    "valutazioni_locale", "assenze", "preferiti", "richieste", "iscrizioni"):
            esegui(conn, f"DELETE FROM {tab} WHERE tavolo_id = ?", (tid,))
        esegui(conn, "DELETE FROM tavoli WHERE id = ?", (tid,))


@app.after_request
def piede(risposta):
    try:
        if (
            risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "service_worker")
        ):
            return risposta
        html = (
            '<footer class="piede">'
            '<a href="/privacy">Privacy</a> · <a href="/termini">Termini</a> · <a href="/faq">FAQ</a>'
            "</footer>"
        )
        testo = risposta.get_data(as_text=True)
        if "</body>" in testo:
            risposta.set_data(testo.replace("</body>", html + "</body>", 1))
    except Exception:
        pass
    return risposta


@app.route("/impostazioni")
@login_richiesto
def impostazioni():
    conn = get_db()
    u = utente_per_id(conn, session["utente_id"])
    conn.close()
    return render_template("impostazioni.html", u=u)


@app.route("/impostazioni/password", methods=["POST"])
@login_richiesto
def cambia_password():
    attuale = request.form.get("attuale", "")
    nuova = request.form.get("nuova", "")
    conn = get_db()
    u = utente_per_id(conn, session["utente_id"])
    if not u or not u["password_hash"] or not check_password_hash(u["password_hash"], attuale):
        conn.close()
        flash("La password attuale non è corretta.")
        return redirect(url_for("impostazioni"))
    if len(nuova) < 6:
        conn.close()
        flash("La nuova password deve avere almeno 6 caratteri.")
        return redirect(url_for("impostazioni"))
    esegui(conn, "UPDATE utenti SET password_hash = ? WHERE id = ?",
           (generate_password_hash(nuova), session["utente_id"]))
    conn.commit()
    conn.close()
    flash("Password aggiornata.")
    return redirect(url_for("impostazioni"))


@app.route("/impostazioni/elimina", methods=["POST"])
@login_richiesto
def elimina_account():
    uid = session["utente_id"]
    conn = get_db()
    u = utente_per_id(conn, uid)
    if not u or not u["password_hash"] or not check_password_hash(u["password_hash"], request.form.get("password", "")):
        conn.close()
        flash("Password non corretta: l'account non è stato eliminato.")
        return redirect(url_for("impostazioni"))

    ids = [r["id"] for r in esegui(conn, "SELECT id FROM tavoli WHERE organizzatore_id = ?", (uid,)).fetchall()]
    cancella_tavoli(conn, ids)

    for sql in (
        "DELETE FROM messaggi WHERE utente_id = ?",
        "DELETE FROM valutazioni_criteri WHERE utente_id = ?",
        "DELETE FROM valutazioni_locale WHERE utente_id = ?",
        "DELETE FROM presenze WHERE segnalatore_id = ? OR utente_id = ?",
        "DELETE FROM compatibilita WHERE da_utente = ? OR a_utente = ?",
        "DELETE FROM assenze WHERE utente_id = ?",
        "DELETE FROM preferiti WHERE utente_id = ?",
        "DELETE FROM locali_seguiti WHERE utente_id = ?",
        "DELETE FROM amici WHERE utente_id = ? OR amico_id = ?",
        "DELETE FROM bloccati WHERE utente_id = ? OR bloccato_id = ?",
        "DELETE FROM segnalazioni WHERE da_utente = ? OR utente_id = ?",
        "DELETE FROM notifiche WHERE utente_id = ? OR da_utente = ?",
        "DELETE FROM richieste WHERE utente_id = ?",
        "DELETE FROM preferiti_utenti WHERE utente_id = ? OR salvato_id = ?",
        "DELETE FROM messaggi_privati WHERE da_utente = ? OR a_utente = ?",
        "DELETE FROM iscrizioni WHERE utente_id = ?",
        "DELETE FROM utenti WHERE id = ?",
    ):
        esegui(conn, sql, (uid,) * sql.count("?"))
    conn.commit()
    conn.close()
    session.clear()
    flash("Il tuo account è stato eliminato.")
    return redirect(url_for("home"))


def pagina_info(nome):
    return render_template("info.html", pagina=nome, contatto=os.environ.get("CONTATTO_EMAIL", "").strip())


@app.route("/privacy")
def privacy():
    return pagina_info("privacy")


@app.route("/termini")
def termini():
    return pagina_info("termini")


@app.route("/faq")
def faq():
    return pagina_info("faq")


def promuovi_coda(conn, tavolo_id):
    t = esegui(conn, "SELECT tema, posti_totali, organizzatore_id, su_richiesta FROM tavoli WHERE id = ?",
               (tavolo_id,)).fetchone()
    if not t:
        return
    link = url_for("dettaglio_tavolo", tavolo_id=tavolo_id)
    while True:
        n = esegui(conn, "SELECT COUNT(*) AS n FROM iscrizioni WHERE tavolo_id = ?", (tavolo_id,)).fetchone()["n"]
        if n >= t["posti_totali"]:
            break
        r = esegui(conn, "SELECT id, utente_id FROM richieste WHERE tavolo_id = ? AND stato = ? ORDER BY id ASC LIMIT 1",
                   (tavolo_id, "coda")).fetchone()
        if not r:
            break
        if t["su_richiesta"]:
            esegui(conn, "UPDATE richieste SET stato = ? WHERE id = ?", ("attesa", r["id"]))
            notifica(conn, t["organizzatore_id"], "richiesta", t["tema"],
                     "Si è liberato un posto: c'è una richiesta in attesa da approvare", link, r["utente_id"])
            notifica(conn, r["utente_id"], "coda", t["tema"],
                     "Si è liberato un posto: l'organizzatore deve approvare la tua richiesta", link)
            break
        esegui(conn, "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
               (tavolo_id, r["utente_id"], datetime.now().isoformat()))
        esegui(conn, "DELETE FROM richieste WHERE id = ?", (r["id"],))
        notifica(conn, r["utente_id"], "coda", t["tema"],
                 "Si è liberato un posto: ora partecipi al banchetto!", link)


def dati_richieste(tavolo_id):
    uid = session.get("utente_id")
    conn = get_db()
    t = esegui(conn, "SELECT su_richiesta, organizzatore_id FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
    out = {"su_richiesta": bool(t and t["su_richiesta"]), "mia_richiesta": None,
           "pos_coda": 0, "richieste_attesa": []}
    if uid:
        r = esegui(conn, "SELECT id, stato FROM richieste WHERE tavolo_id = ? AND utente_id = ?",
                   (tavolo_id, uid)).fetchone()
        if r:
            out["mia_richiesta"] = r["stato"]
            if r["stato"] == "coda":
                out["pos_coda"] = esegui(
                    conn, "SELECT COUNT(*) AS n FROM richieste WHERE tavolo_id = ? AND stato = ? AND id <= ?",
                    (tavolo_id, "coda", r["id"])).fetchone()["n"]
        if t and t["organizzatore_id"] == uid:
            righe = esegui(conn, """
                SELECT u.id, u.nome, u.foto_url, u.citta, u.data_nascita
                FROM richieste r JOIN utenti u ON u.id = r.utente_id
                WHERE r.tavolo_id = ? AND r.stato = ?
                ORDER BY r.id ASC
            """, (tavolo_id, "attesa")).fetchall()
            for x in righe:
                d = dict(x)
                d["eta"] = eta_da(d["data_nascita"])
                out["richieste_attesa"].append(d)
    conn.close()
    return out


@app.route("/tavolo/<int:tavolo_id>/richiesta/<int:utente_target>/<azione>", methods=["POST"])
@login_richiesto
def gestisci_richiesta(tavolo_id, utente_target, azione):
    if azione not in ("approva", "rifiuta"):
        abort(400)
    conn = get_db()
    t = esegui(conn, "SELECT tema, organizzatore_id, posti_totali, min_partecipanti FROM tavoli WHERE id = ?",
               (tavolo_id,)).fetchone()
    if not t:
        conn.close()
        abort(404)
    if t["organizzatore_id"] != session["utente_id"]:
        conn.close()
        abort(403)
    r = esegui(conn, "SELECT id FROM richieste WHERE tavolo_id = ? AND utente_id = ? AND stato = ?",
               (tavolo_id, utente_target, "attesa")).fetchone()
    if not r:
        conn.close()
        flash("Richiesta non trovata.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))
    link = url_for("dettaglio_tavolo", tavolo_id=tavolo_id)
    if azione == "approva":
        n = esegui(conn, "SELECT COUNT(*) AS n FROM iscrizioni WHERE tavolo_id = ?", (tavolo_id,)).fetchone()["n"]
        if n >= t["posti_totali"]:
            conn.close()
            flash("Il banchetto è al completo: non puoi accettare altre richieste.")
            return redirect(link)
        esegui(conn, "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
               (tavolo_id, utente_target, datetime.now().isoformat()))
        esegui(conn, "DELETE FROM richieste WHERE id = ?", (r["id"],))
        notifica(conn, utente_target, "richiesta_ok", t["tema"], "La tua richiesta è stata accettata: ora partecipi!",
                 link, session["utente_id"])
        if t["min_partecipanti"] and n + 1 == t["min_partecipanti"]:
            for x in esegui(conn, "SELECT utente_id FROM iscrizioni WHERE tavolo_id = ?", (tavolo_id,)).fetchall():
                notifica(conn, x["utente_id"], "confermato", t["tema"],
                         "Il banchetto è confermato: è stato raggiunto il numero minimo di partecipanti", link)
        flash("Richiesta accettata.")
    else:
        esegui(conn, "DELETE FROM richieste WHERE id = ?", (r["id"],))
        notifica(conn, utente_target, "richiesta_no", t["tema"], "La tua richiesta non è stata accettata",
                 link, session["utente_id"])
        flash("Richiesta rifiutata.")
    conn.commit()
    conn.close()
    return redirect(link)


@app.route("/tavolo/<int:tavolo_id>/ritira-richiesta", methods=["POST"])
@login_richiesto
def ritira_richiesta(tavolo_id):
    conn = get_db()
    esegui(conn, "DELETE FROM richieste WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, session["utente_id"]))
    conn.commit()
    conn.close()
    flash("Richiesta ritirata.")
    return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))


def puo_scrivere(conn, uid, altro):
    if not altro or altro == uid:
        return False
    if altro in relazioni_bloccate(conn, uid):
        return False
    r = esegui(conn, "SELECT 1 AS ok FROM amici WHERE (utente_id = ? AND amico_id = ?) OR (utente_id = ? AND amico_id = ?)",
               (uid, altro, altro, uid)).fetchone()
    return r is not None or e_match(conn, uid, altro)


@app.after_request
def icona_messaggi(risposta):
    try:
        if (
            "utente_id" not in session
            or risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "service_worker")
        ):
            return risposta
        conn = get_db()
        n = esegui(conn, "SELECT COUNT(*) AS n FROM messaggi_privati WHERE a_utente = ? AND letto = 0",
                   (session["utente_id"],)).fetchone()["n"]
        conn.close()
        badge = ""
        if n:
            badge = '<span class="campanella-n">' + (str(n) if n < 100 else "99+") + "</span>"
        html = '<a class="campanella msg-icona" href="' + url_for("elenco_messaggi") + '" aria-label="Messaggi">💬' + badge + "</a>"
        testo = risposta.get_data(as_text=True)
        if "</body>" in testo:
            risposta.set_data(testo.replace("</body>", html + "</body>", 1))
    except Exception:
        pass
    return risposta


@app.route("/messaggi")
@login_richiesto
def elenco_messaggi():
    uid = session["utente_id"]
    conn = get_db()
    ids = set()
    for sql in (
        "SELECT amico_id AS x FROM amici WHERE utente_id = ?",
        "SELECT utente_id AS x FROM amici WHERE amico_id = ?",
        "SELECT DISTINCT a_utente AS x FROM messaggi_privati WHERE da_utente = ?",
        "SELECT DISTINCT da_utente AS x FROM messaggi_privati WHERE a_utente = ?",
    ):
        for r in esegui(conn, sql, (uid,)).fetchall():
            ids.add(r["x"])
    match_ids = {r["x"] for r in esegui(conn, """
        SELECT p.salvato_id AS x
        FROM preferiti_utenti p
        JOIN preferiti_utenti q ON q.utente_id = p.salvato_id AND q.salvato_id = p.utente_id
        WHERE p.utente_id = ?
    """, (uid,)).fetchall()}
    ids |= match_ids
    ids.discard(uid)
    ids -= relazioni_bloccate(conn, uid)

    conversazioni = []
    for altro in ids:
        u = utente_per_id(conn, altro)
        if not u:
            continue
        ultimo = esegui(conn, """
            SELECT id, testo, da_utente, creato_il FROM messaggi_privati
            WHERE (da_utente = ? AND a_utente = ?) OR (da_utente = ? AND a_utente = ?)
            ORDER BY id DESC LIMIT 1
        """, (uid, altro, altro, uid)).fetchone()
        non_letti = esegui(conn, "SELECT COUNT(*) AS n FROM messaggi_privati WHERE da_utente = ? AND a_utente = ? AND letto = 0",
                           (altro, uid)).fetchone()["n"]
        try:
            ora = datetime.fromisoformat(ultimo["creato_il"]).strftime("%d/%m %H:%M") if ultimo else ""
        except (ValueError, TypeError):
            ora = ""
        conversazioni.append({
            "id": altro, "nome": u["nome"], "foto_url": u["foto_url"],
            "ultimo": (("Tu: " if ultimo["da_utente"] == uid else "") + ultimo["testo"]) if ultimo else ("Hai appena ricevuto un match!" if altro in match_ids else "Nessun messaggio ancora"),
            "ora": ora, "non_letti": int(non_letti), "ordine": ultimo["id"] if ultimo else 0,
            "match": altro in match_ids,
        })
    conn.close()
    conversazioni.sort(key=lambda c: c["ordine"], reverse=True)
    return render_template("messaggi.html", conversazioni=conversazioni)


@app.route("/messaggi/<int:altro_id>")
@login_richiesto
def chat_privata(altro_id):
    uid = session["utente_id"]
    conn = get_db()
    altro = utente_per_id(conn, altro_id)
    if not altro:
        conn.close()
        abort(404)
    ha_storia = esegui(conn, """
        SELECT 1 AS ok FROM messaggi_privati
        WHERE (da_utente = ? AND a_utente = ?) OR (da_utente = ? AND a_utente = ?) LIMIT 1
    """, (uid, altro_id, altro_id, uid)).fetchone()
    bloccato = altro_id in relazioni_bloccate(conn, uid)
    consentito = puo_scrivere(conn, uid, altro_id)
    conn.close()
    if altro_id == uid or bloccato or not (consentito or ha_storia):
        flash("Puoi scrivere messaggi solo ai tuoi amici.")
        return redirect(url_for("elenco_messaggi"))
    return render_template("chat_privata.html", altro=altro, puo_inviare=consentito)


@app.route("/messaggi/<int:altro_id>/lista")
@login_richiesto
def chat_privata_lista(altro_id):
    uid = session["utente_id"]
    try:
        dopo = int(request.args.get("dopo", 0))
    except ValueError:
        dopo = 0
    conn = get_db()
    if altro_id in relazioni_bloccate(conn, uid):
        conn.close()
        abort(403)
    if dopo <= 0:
        righe = esegui(conn, """
            SELECT id, testo, da_utente, creato_il FROM messaggi_privati
            WHERE (da_utente = ? AND a_utente = ?) OR (da_utente = ? AND a_utente = ?)
            ORDER BY id DESC LIMIT 100
        """, (uid, altro_id, altro_id, uid)).fetchall()
        righe = list(reversed(righe))
    else:
        righe = esegui(conn, """
            SELECT id, testo, da_utente, creato_il FROM messaggi_privati
            WHERE ((da_utente = ? AND a_utente = ?) OR (da_utente = ? AND a_utente = ?)) AND id > ?
            ORDER BY id ASC LIMIT 200
        """, (uid, altro_id, altro_id, uid, dopo)).fetchall()
    esegui(conn, "UPDATE messaggi_privati SET letto = 1 WHERE da_utente = ? AND a_utente = ? AND letto = 0",
           (altro_id, uid))
    conn.commit()
    conn.close()
    risultato = []
    for r in righe:
        try:
            ora = datetime.fromisoformat(r["creato_il"]).strftime("%d/%m %H:%M")
        except (ValueError, TypeError):
            ora = ""
        risultato.append({"id": r["id"], "testo": r["testo"], "ora": ora, "mio": r["da_utente"] == uid})
    return jsonify(risultato)


@app.route("/messaggi/<int:altro_id>/invia", methods=["POST"])
@login_richiesto
def chat_privata_invia(altro_id):
    uid = session["utente_id"]
    testo = request.form.get("testo", "").strip()[:1000]
    conn = get_db()
    if not puo_scrivere(conn, uid, altro_id):
        conn.close()
        abort(403)
    if testo:
        esegui(conn, "INSERT INTO messaggi_privati (da_utente, a_utente, testo, creato_il, letto) VALUES (?, ?, ?, ?, 0)",
               (uid, altro_id, testo, datetime.now().isoformat()))
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


def e_match(conn, a, b):
    r = esegui(conn, """
        SELECT 1 AS ok FROM preferiti_utenti p
        JOIN preferiti_utenti q ON q.utente_id = p.salvato_id AND q.salvato_id = p.utente_id
        WHERE p.utente_id = ? AND p.salvato_id = ?
    """, (a, b)).fetchone()
    return r is not None


def stato_match(altro_id):
    uid = session.get("utente_id")
    if not uid or uid == altro_id:
        return {"salvato": False, "match": False}
    conn = get_db()
    r = esegui(conn, "SELECT 1 AS ok FROM preferiti_utenti WHERE utente_id = ? AND salvato_id = ?",
               (uid, altro_id)).fetchone()
    m = e_match(conn, uid, altro_id)
    conn.close()
    return {"salvato": r is not None, "match": m}


@app.route("/utente/<int:altro_id>/salva-profilo", methods=["POST"])
@login_richiesto
def salva_profilo(altro_id):
    uid = session["utente_id"]
    if altro_id == uid:
        abort(400)
    conn = get_db()
    altro = utente_per_id(conn, altro_id)
    if not altro:
        conn.close()
        abort(404)
    if altro_id in relazioni_bloccate(conn, uid):
        conn.close()
        flash("Non puoi salvare questo profilo.")
        return redirect(url_for("profilo_utente", utente_id=altro_id))
    r = esegui(conn, "SELECT id FROM preferiti_utenti WHERE utente_id = ? AND salvato_id = ?",
               (uid, altro_id)).fetchone()
    if r:
        esegui(conn, "DELETE FROM preferiti_utenti WHERE id = ?", (r["id"],))
        flash("Profilo rimosso dai preferiti.")
    else:
        esegui(conn, "INSERT INTO preferiti_utenti (utente_id, salvato_id) VALUES (?, ?)", (uid, altro_id))
        if e_match(conn, uid, altro_id):
            notifica(conn, altro_id, "match", session.get("utente_nome") or "Nuovo match",
                     "🤝 Hai un match! Ora potete scrivervi.",
                     url_for("chat_privata", altro_id=uid), uid)
            notifica(conn, uid, "match", altro["nome"],
                     "🤝 Hai un match! Ora potete scrivervi.",
                     url_for("chat_privata", altro_id=altro_id), altro_id)
            flash("🤝 È un match! Ora potete scrivervi.")
        else:
            flash("Profilo salvato nei preferiti.")
    conn.commit()
    conn.close()
    return redirect(url_for("profilo_utente", utente_id=altro_id))


SLOT_IMMAGINI = (
    ("img_banner_home", "Banner in cima alla home"),
    ("img_header", "Sfondo della testata (tutte le pagine)"),
)


def leggi_impostazione(conn, chiave):
    r = esegui(conn, "SELECT valore FROM impostazioni WHERE chiave = ?", (chiave,)).fetchone()
    return r["valore"] if r else ""


def scrivi_impostazione(conn, chiave, valore):
    if esegui(conn, "SELECT chiave FROM impostazioni WHERE chiave = ?", (chiave,)).fetchone():
        esegui(conn, "UPDATE impostazioni SET valore = ? WHERE chiave = ?", (valore, chiave))
    else:
        esegui(conn, "INSERT INTO impostazioni (chiave, valore) VALUES (?, ?)", (chiave, valore))


@app.after_request
def immagini_decorative(risposta):
    try:
        if (
            risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "service_worker")
        ):
            return risposta
        conn = get_db()
        banner = leggi_impostazione(conn, "img_banner_home")
        header = leggi_impostazione(conn, "img_header")
        conn.close()
        if not banner and not header:
            return risposta
        testo = risposta.get_data(as_text=True)
        if header and "</head>" in testo:
            stile = ('<style>header{background-image:linear-gradient(rgba(10,74,138,.72),rgba(10,74,138,.72)),url("'
                     + header + '");background-size:cover;background-position:center}</style>')
            testo = testo.replace("</head>", stile + "</head>", 1)
        if banner and request.endpoint == "home" and "<main>" in testo:
            testo = testo.replace("<main>", '<main><img class="banner-home" src="' + banner + '" alt="">', 1)
        risposta.set_data(testo)
    except Exception:
        pass
    return risposta


@app.route("/admin/immagini", methods=["GET", "POST"])
@login_richiesto
def immagini_sito():
    conn = get_db()
    if not e_admin(conn, session["utente_id"]):
        conn.close()
        abort(403)
    if request.method == "POST":
        slot = request.form.get("slot")
        if slot not in dict(SLOT_IMMAGINI):
            conn.close()
            abort(400)
        if request.form.get("azione") == "rimuovi":
            scrivi_impostazione(conn, slot, "")
            flash("Immagine rimossa.")
        else:
            url = salva_foto(request.files.get("foto"), "decorazioni")
            if url:
                scrivi_impostazione(conn, slot, url)
                flash("Immagine aggiornata.")
        conn.commit()
        conn.close()
        return redirect(url_for("immagini_sito"))
    valori = {k: leggi_impostazione(conn, k) for k, _ in SLOT_IMMAGINI}
    conn.close()
    return render_template("immagini.html", slot=SLOT_IMMAGINI, valori=valori)


@app.after_request
def caricamento_overlay(risposta):
    try:
        if (
            risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint in (None, "static", "service_worker")
        ):
            return risposta
        if not os.path.exists(os.path.join(app.static_folder, "img", "coppa.png")):
            return risposta
        testo = risposta.get_data(as_text=True)
        if "</body>" in testo:
            risposta.set_data(testo.replace("</body>", render_template("_caricamento.html") + "</body>", 1))
    except Exception:
        pass
    return risposta


def solo_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "utente_id" not in session:
            flash("Accedi per continuare.")
            return redirect(url_for("accedi"))
        conn = get_db()
        ok = e_admin(conn, session["utente_id"])
        conn.close()
        if not ok:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def tabella_esiste(conn, nome):
    if USA_PG:
        r = esegui(conn, "SELECT 1 AS ok FROM information_schema.tables WHERE table_name = ?", (nome,)).fetchone()
    else:
        r = esegui(conn, "SELECT 1 AS ok FROM sqlite_master WHERE type = 'table' AND name = ?", (nome,)).fetchone()
    return r is not None


def conta(conn, tabella, dove="1 = 1", params=()):
    if not tabella_esiste(conn, tabella):
        return 0
    return int(esegui(conn, f"SELECT COUNT(*) AS n FROM {tabella} WHERE {dove}", params).fetchone()["n"])


def dati_dashboard():
    conn = get_db()
    sette = (datetime.now() - timedelta(days=7)).isoformat()
    ora = adesso_it().strftime("%Y-%m-%dT%H:%M")
    tot_b = conta(conn, "tavoli")
    prog = conta(conn, "tavoli", "data_ora >= ?", (ora,))
    match = 0
    if tabella_esiste(conn, "preferiti_utenti"):
        match = int(esegui(conn, """
            SELECT COUNT(*) AS n FROM preferiti_utenti p
            JOIN preferiti_utenti q ON q.utente_id = p.salvato_id AND q.salvato_id = p.utente_id
        """).fetchone()["n"]) // 2
    d = {
        "utenti": conta(conn, "utenti"),
        "nuovi": conta(conn, "utenti", "creato_il >= ?", (sette,)),
        "premium": conta(conn, "utenti", "premium = 1"),
        "sospesi": conta(conn, "utenti", "sospeso = 1"),
        "banchetti": tot_b,
        "in_programma": prog,
        "conclusi": tot_b - prog,
        "iscrizioni": conta(conn, "iscrizioni"),
        "messaggi": conta(conn, "messaggi") + conta(conn, "messaggi_privati"),
        "match": match,
        "segnalazioni": conta(conn, "segnalazioni", "COALESCE(gestita, 0) = 0"),
    }
    conn.close()
    return d


PULIZIA_UTENTE = (
    ("messaggi", "utente_id = ?"),
    ("valutazioni_criteri", "utente_id = ?"),
    ("valutazioni_locale", "utente_id = ?"),
    ("presenze", "segnalatore_id = ? OR utente_id = ?"),
    ("compatibilita", "da_utente = ? OR a_utente = ?"),
    ("assenze", "utente_id = ?"),
    ("preferiti", "utente_id = ?"),
    ("locali_seguiti", "utente_id = ?"),
    ("richieste", "utente_id = ?"),
    ("amici", "utente_id = ? OR amico_id = ?"),
    ("bloccati", "utente_id = ? OR bloccato_id = ?"),
    ("preferiti_utenti", "utente_id = ? OR salvato_id = ?"),
    ("messaggi_privati", "da_utente = ? OR a_utente = ?"),
    ("segnalazioni", "da_utente = ? OR utente_id = ?"),
    ("notifiche", "utente_id = ? OR da_utente = ?"),
    ("iscrizioni", "utente_id = ?"),
)


def cancella_utente(conn, uid):
    ids = [r["id"] for r in esegui(conn, "SELECT id FROM tavoli WHERE organizzatore_id = ?", (uid,)).fetchall()]
    cancella_tavoli(conn, ids)
    for tab, dove in PULIZIA_UTENTE:
        if tabella_esiste(conn, tab):
            esegui(conn, f"DELETE FROM {tab} WHERE {dove}", (uid,) * dove.count("?"))
    esegui(conn, "DELETE FROM utenti WHERE id = ?", (uid,))


@app.context_processor
def admin_ctx():
    return {"immagini_ok": "immagini_sito" in app.view_functions}


@app.before_request
def controlla_sospeso():
    if "utente_id" not in session or request.endpoint in (None, "static", "service_worker"):
        return None
    try:
        conn = get_db()
        r = esegui(conn, "SELECT sospeso FROM utenti WHERE id = ?", (session["utente_id"],)).fetchone()
        conn.close()
    except Exception:
        return None
    if r and r["sospeso"]:
        session.clear()
        flash("Il tuo account è stato sospeso. Contatta il gestore di Symposium.")
        return redirect(url_for("accedi"))
    return None


@app.route("/admin/utenti")
@solo_admin
def admin_utenti():
    q = request.args.get("q", "").strip()
    conn = get_db()
    utenti = esegui(conn, """
        SELECT id, nome, email, citta, premium, sospeso, creato_il FROM utenti
        WHERE LOWER(nome) LIKE LOWER(?) OR LOWER(email) LIKE LOWER(?)
        ORDER BY id DESC LIMIT 200
    """, (f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return render_template("admin_utenti.html", utenti=utenti, q=q)


@app.route("/admin/utente/<int:bersaglio>/sospendi", methods=["POST"])
@solo_admin
def admin_utente_sospendi(bersaglio):
    conn = get_db()
    if bersaglio == session["utente_id"] or e_admin(conn, bersaglio):
        conn.close()
        flash("Non puoi sospendere un amministratore.")
        return redirect(request.referrer or url_for("admin_utenti"))
    r = esegui(conn, "SELECT sospeso FROM utenti WHERE id = ?", (bersaglio,)).fetchone()
    if not r:
        conn.close()
        abort(404)
    nuovo = 0 if r["sospeso"] else 1
    esegui(conn, "UPDATE utenti SET sospeso = ? WHERE id = ?", (nuovo, bersaglio))
    conn.commit()
    conn.close()
    flash("Utente sospeso." if nuovo else "Utente riattivato.")
    return redirect(request.referrer or url_for("admin_utenti"))


@app.route("/admin/utente/<int:bersaglio>/elimina", methods=["POST"])
@solo_admin
def admin_utente_elimina(bersaglio):
    conn = get_db()
    if bersaglio == session["utente_id"] or e_admin(conn, bersaglio):
        conn.close()
        flash("Non puoi eliminare un amministratore.")
        return redirect(request.referrer or url_for("admin_utenti"))
    if not utente_per_id(conn, bersaglio):
        conn.close()
        abort(404)
    cancella_utente(conn, bersaglio)
    conn.commit()
    conn.close()
    flash("Utente eliminato.")
    return redirect(request.referrer or url_for("admin_utenti"))


@app.route("/admin/banchetti")
@solo_admin
def admin_banchetti():
    conn = get_db()
    righe = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.citta, t.esclusivo, t.posti_totali, u.nome AS org,
               (SELECT COUNT(*) FROM iscrizioni i WHERE i.tavolo_id = t.id) AS iscritti
        FROM tavoli t LEFT JOIN utenti u ON u.id = t.organizzatore_id
        ORDER BY t.id DESC LIMIT 200
    """).fetchall()
    conn.close()
    return render_template("admin_banchetti.html", banchetti=righe)


@app.route("/admin/banchetto/<int:tavolo_id>/elimina", methods=["POST"])
@solo_admin
def admin_banchetto_elimina(tavolo_id):
    conn = get_db()
    cancella_tavoli(conn, [tavolo_id])
    conn.commit()
    conn.close()
    flash("Banchetto eliminato.")
    return redirect(url_for("admin_banchetti"))


@app.route("/admin/segnalazioni")
@solo_admin
def admin_segnalazioni():
    conn = get_db()
    righe = esegui(conn, """
        SELECT s.id, s.motivo, s.creato_il, s.gestita, s.utente_id AS contro_id,
               a.nome AS da_nome, b.nome AS contro_nome, b.sospeso AS contro_sospeso
        FROM segnalazioni s
        LEFT JOIN utenti a ON a.id = s.da_utente
        LEFT JOIN utenti b ON b.id = s.utente_id
        ORDER BY s.gestita ASC, s.id DESC LIMIT 200
    """).fetchall()
    conn.close()
    return render_template("admin_segnalazioni.html", segnalazioni=righe)


@app.route("/admin/segnalazione/<int:sid>/gestita", methods=["POST"])
@solo_admin
def admin_segnalazione_gestita(sid):
    conn = get_db()
    r = esegui(conn, "SELECT gestita FROM segnalazioni WHERE id = ?", (sid,)).fetchone()
    if not r:
        conn.close()
        abort(404)
    esegui(conn, "UPDATE segnalazioni SET gestita = ? WHERE id = ?", (0 if r["gestita"] else 1, sid))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("admin_segnalazioni"))


@app.route("/admin/annuncio", methods=["POST"])
@solo_admin
def admin_annuncio():
    testo = request.form.get("testo", "").strip()[:300]
    if not testo:
        flash("Scrivi il testo dell'annuncio.")
        return redirect(url_for("admin"))
    conn = get_db()
    ids = [r["id"] for r in esegui(conn, "SELECT id FROM utenti WHERE COALESCE(sospeso, 0) = 0").fetchall()]
    for i in ids:
        notifica(conn, i, "annuncio", "Symposium", testo, url_for("home"))
    conn.commit()
    conn.close()
    flash(f"Annuncio inviato a {len(ids)} utenti.")
    return redirect(url_for("admin"))


@app.route("/admin/sso")
def admin_sso():
    import base64
    import hashlib
    import hmac
    import json
    import time
    segreto = os.environ.get("ADMIN_SSO_SECRET", "")
    token = request.args.get("token", "")
    if not segreto or "." not in token or not admin_email():
        abort(404)
    corpo, firma = token.rsplit(".", 1)
    atteso = hmac.new(segreto.encode(), corpo.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(atteso, firma):
        abort(403)
    try:
        dati = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)).decode())
        scadenza = int(dati["exp"])
    except Exception:
        abort(400)
    if scadenza < time.time() or scadenza > time.time() + 300:
        abort(403)
    conn = get_db()
    u = esegui(conn, "SELECT id, nome FROM utenti WHERE LOWER(email) = ?", (admin_email(),)).fetchone()
    if not u:
        import secrets
        nuovo_id = inserisci(
            conn,
            "INSERT INTO utenti (nome, email, password_hash, interessi, creato_il) VALUES (?, ?, ?, ?, ?)",
            ("Amministratore Symposium", admin_email(), generate_password_hash(secrets.token_hex(32)),
             "", datetime.now().isoformat())
        )
        conn.commit()
        u = {"id": nuovo_id, "nome": "Amministratore Symposium"}
    conn.close()
    session["utente_id"] = u["id"]
    session["utente_nome"] = u["nome"]
    return redirect(url_for("admin"))


def api_alexandria(metodo, percorso, corpo=None, con_chiave=False):
    import json
    import urllib.error
    import urllib.request
    base = os.environ.get("ALEXANDRIA_API_URL", "").strip().rstrip("/")
    if not base:
        return None, None
    intestazioni = {"Content-Type": "application/json"}
    if con_chiave:
        chiave = os.environ.get("ALEXANDRIA_API_KEY", "").strip()
        if not chiave:
            return None, None
        intestazioni["X-Symposium-Key"] = chiave
    dati = json.dumps(corpo).encode() if corpo is not None else None
    richiesta = urllib.request.Request(base + percorso, data=dati, headers=intestazioni, method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None
    except Exception:
        return None, None


@app.route("/wallet")
@login_richiesto
def wallet():
    if request.args.get("pagamento") == "ok":
        flash("Pagamento ricevuto: gli ALX arrivano nel wallet entro un paio di minuti.")
    elif request.args.get("pagamento") == "annullato":
        flash("Pagamento annullato: nessun addebito.")
    conn = get_db()
    u = utente_per_id(conn, session["utente_id"])
    conn.close()
    id_al = u["id_alexandria"]
    configurato = bool(os.environ.get("ALEXANDRIA_API_URL", "").strip() and os.environ.get("ALEXANDRIA_API_KEY", "").strip())
    saldo, illimitato, errore = None, False, None
    movimenti, pacchetti = [], {}
    if id_al and configurato:
        st, d = api_alexandria("GET", f"/symposium/saldo/{id_al}", con_chiave=True)
        if st == 200 and d:
            saldo = d.get("saldo_alx")
            illimitato = bool(d.get("illimitato"))
        else:
            errore = "Non riesco a leggere il saldo da Alexandria: riprova tra qualche secondo."
        st2, m = api_alexandria("GET", f"/wallet/{id_al}/transazioni")
        if st2 == 200 and isinstance(m, list):
            movimenti = m[:10]
        st3, pk = api_alexandria("GET", "/premium/pacchetti")
        if st3 == 200 and pk and pk.get("attivo"):
            pacchetti = pk.get("pacchetti", {})
    return render_template("wallet.html", collegato=bool(id_al), configurato=configurato,
                           saldo=saldo, illimitato=illimitato, errore=errore,
                           movimenti=movimenti, pacchetti=pacchetti,
                           codice=session.get("codice_alexandria"))


@app.route("/wallet/collega", methods=["POST"])
@login_richiesto
def wallet_collega():
    st, d = api_alexandria("POST", "/auth/genera-codice")
    if st == 200 and d and d.get("codice"):
        session["codice_alexandria"] = d["codice"]
    else:
        flash("Non riesco a contattare Alexandria: riprova tra qualche secondo.")
    return redirect(url_for("wallet"))


@app.route("/wallet/collega/stato")
@login_richiesto
def wallet_collega_stato():
    codice = session.get("codice_alexandria")
    if not codice:
        return jsonify({"stato": "nessuno"})
    st, d = api_alexandria("GET", f"/auth/stato-codice/{codice}")
    if st != 200 or not d:
        return jsonify({"stato": "errore"})
    if not d.get("confermato") or not d.get("id_telegram"):
        return jsonify({"stato": "attesa"})
    id_tg = int(d["id_telegram"])
    conn = get_db()
    altro = esegui(conn, "SELECT id FROM utenti WHERE id_alexandria = ? AND id != ?",
                   (id_tg, session["utente_id"])).fetchone()
    if altro:
        conn.close()
        session.pop("codice_alexandria", None)
        return jsonify({"stato": "occupato"})
    esegui(conn, "UPDATE utenti SET id_alexandria = ? WHERE id = ?", (id_tg, session["utente_id"]))
    conn.commit()
    conn.close()
    session.pop("codice_alexandria", None)
    return jsonify({"stato": "ok"})


@app.route("/wallet/scollega", methods=["POST"])
@login_richiesto
def wallet_scollega():
    conn = get_db()
    esegui(conn, "UPDATE utenti SET id_alexandria = NULL WHERE id = ?", (session["utente_id"],))
    conn.commit()
    conn.close()
    session.pop("codice_alexandria", None)
    flash("Account Alexandria scollegato.")
    return redirect(url_for("wallet"))


@app.route("/wallet/acquista", methods=["POST"])
@login_richiesto
def wallet_acquista():
    conn = get_db()
    u = utente_per_id(conn, session["utente_id"])
    conn.close()
    if not u["id_alexandria"]:
        flash("Collega prima il tuo account Alexandria.")
        return redirect(url_for("wallet"))
    pacchetto = request.form.get("pacchetto", "")
    st, d = api_alexandria("POST", "/symposium/acquista-alx",
                           {"id_telegram": int(u["id_alexandria"]), "pacchetto": pacchetto}, con_chiave=True)
    if st == 200 and d and d.get("ok") and d.get("url_pagamento"):
        return redirect(d["url_pagamento"])
    flash((d or {}).get("messaggio") or "Acquisto non disponibile al momento: riprova più tardi.")
    return redirect(url_for("wallet"))


def pos_da_cookie():
    valore = request.cookies.get("pos", "")
    try:
        a, b = valore.split("_")
        lat, lng = float(a), float(b)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def distanza_km(lat1, lng1, lat2, lng2):
    import math
    r = 6371.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = f2 - f1
    dl = math.radians(lng2 - lng1)
    x = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


@app.context_processor
def posizione_ctx():
    return {
        "pos_attiva": pos_da_cookie() is not None,
        "raggio_sel": request.args.get("raggio", ""),
        "vicini_sel": request.args.get("vicini") == "1",
    }


@app.after_request
def popup_posizione(risposta):
    try:
        if (
            risposta.status_code != 200
            or risposta.mimetype != "text/html"
            or request.endpoint != "home"
        ):
            return risposta
        testo = risposta.get_data(as_text=True)
        if "</body>" in testo:
            risposta.set_data(testo.replace(
                "</body>",
                render_template("_posizione.html", pos_attiva=pos_da_cookie() is not None) + "</body>", 1))
    except Exception:
        pass
    return risposta


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

    stat_criteri = []
    prezzo_top = None
    if tavolo["locale_id"]:
        rr = esegui(conn, """
            SELECT c.criterio AS criterio, COUNT(*) AS n, COALESCE(SUM(c.voto), 0) AS pos
            FROM valutazioni_criteri c
            JOIN tavoli t ON t.id = c.tavolo_id
            WHERE t.locale_id = ?
            GROUP BY c.criterio
        """, (tavolo["locale_id"],)).fetchall()
        per = {r["criterio"]: (int(r["n"]), int(r["pos"])) for r in rr}
        for chiave, etichetta in CRITERI:
            if chiave in per:
                n, pos = per[chiave]
                stat_criteri.append({"etichetta": etichetta, "perc": int(round(pos * 100 / n)), "n": n})
        rp = esegui(conn, """
            SELECT fascia_prezzo, COUNT(*) AS n
            FROM valutazioni_locale
            WHERE locale_id = ? AND fascia_prezzo IS NOT NULL
            GROUP BY fascia_prezzo
            ORDER BY n DESC
            LIMIT 1
        """, (tavolo["locale_id"],)).fetchone()
        if rp:
            prezzo_top = dict(PREZZI).get(rp["fascia_prezzo"])

    io = utente_per_id(conn, uid)
    conn.close()

    miei = set_interessi(io["interessi"]) if io else set()
    partecipanti = []
    for r in righe:
        p = dict(r)
        p["in_comune"] = len(miei & set_interessi(p["interessi"]))
        p["eta"] = eta_da(p["data_nascita"])
        p["assente"] = p["id"] in assenti
        p["badge"] = info_badge(p["id"])["badge_principale"]
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
        stat_criteri=stat_criteri,
        prezzo_top=prezzo_top,
        **stato_preferiti(tavolo_id, tavolo["locale_id"]),
        **dati_richieste(tavolo_id),
    )


def altri_presenti(conn, tavolo_id, uid):
    return esegui(conn, """
        SELECT u.id, u.nome, u.foto_url
        FROM iscrizioni i
        JOIN utenti u ON u.id = i.utente_id
        WHERE i.tavolo_id = ? AND i.utente_id != ?
          AND i.utente_id NOT IN (SELECT utente_id FROM assenze WHERE tavolo_id = ?)
        ORDER BY i.id ASC
    """, (tavolo_id, uid, tavolo_id)).fetchall()


def controlla_feedback(conn, tavolo_id, uid):
    tavolo = esegui(conn, """
        SELECT t.id, t.tema, t.data_ora, t.locale_id, l.nome AS locale_nome
        FROM tavoli t LEFT JOIN locali l ON l.id = t.locale_id
        WHERE t.id = ?
    """, (tavolo_id,)).fetchone()
    if not tavolo:
        abort(404)
    if not e_iscritto(conn, tavolo_id, uid):
        abort(403)
    if not e_concluso(tavolo["data_ora"]):
        return tavolo, "Potrai valutare la serata quando sarà conclusa."
    if esegui(conn, "SELECT 1 AS ok FROM assenze WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, uid)).fetchone():
        return tavolo, "Risulti assente a questa serata: non puoi valutarla."
    if esegui(conn, "SELECT 1 AS ok FROM valutazioni_locale WHERE tavolo_id = ? AND utente_id = ?", (tavolo_id, uid)).fetchone():
        return tavolo, "Hai già valutato questa serata."
    return tavolo, None


@app.route("/tavolo/<int:tavolo_id>/feedback", methods=["GET", "POST"])
@login_richiesto
def feedback_tavolo(tavolo_id):
    uid = session["utente_id"]
    conn = get_db()
    tavolo, errore = controlla_feedback(conn, tavolo_id, uid)
    if errore:
        conn.close()
        flash(errore)
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))

    altri = [dict(r) for r in altri_presenti(conn, tavolo_id, uid)]

    if request.method == "POST":
        complessivo = request.form.get("complessivo")
        prezzo = request.form.get("prezzo")
        if complessivo not in ("0", "1") or prezzo not in dict(PREZZI):
            conn.close()
            flash("Rispondi alla valutazione complessiva del locale e alla fascia di prezzo.")
            return redirect(url_for("feedback_tavolo", tavolo_id=tavolo_id))

        try:
            esegui(conn, """
                INSERT INTO valutazioni_locale (tavolo_id, utente_id, locale_id, voto, creato_il, fascia_prezzo)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tavolo_id, uid, tavolo["locale_id"], int(complessivo), datetime.now().isoformat(), prezzo))
        except IntegrityError:
            conn.rollback()
            conn.close()
            flash("Hai già valutato questa serata.")
            return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))

        for chiave, _ in CRITERI:
            v = request.form.get("crit_" + chiave)
            if v in ("0", "1"):
                esegui(conn, "INSERT INTO valutazioni_criteri (tavolo_id, utente_id, criterio, voto) VALUES (?, ?, ?, ?)",
                       (tavolo_id, uid, chiave, int(v)))

        for a in altri:
            presente = request.form.get(f"presente_{a['id']}") == "1"
            esegui(conn, "INSERT INTO presenze (tavolo_id, segnalatore_id, utente_id, presente) VALUES (?, ?, ?, ?)",
                   (tavolo_id, uid, a["id"], 1 if presente else 0))
            if presente:
                p = request.form.get(f"compat_{a['id']}")
                if p in ("1", "2", "3", "4", "5"):
                    esegui(conn, "INSERT INTO compatibilita (tavolo_id, da_utente, a_utente, punteggio) VALUES (?, ?, ?, ?)",
                           (tavolo_id, uid, a["id"], int(p)))

        conn.commit()
        conn.close()
        flash("Grazie! La tua valutazione è stata registrata.")
        return redirect(url_for("dettaglio_tavolo", tavolo_id=tavolo_id))

    conn.close()
    return render_template("feedback.html", tavolo=tavolo, altri=altri,
                           criteri=CRITERI, prezzi=PREZZI, compat=COMPAT)


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
        flash("La chat è riservata ai partecipanti del banchetto.")
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

    rc = esegui(conn, "SELECT COUNT(*) AS n, AVG(punteggio) AS media FROM compatibilita WHERE a_utente = ?", (utente_id,)).fetchone()
    compat_label = None
    if rc and int(rc["n"]) >= 3 and rc["media"] is not None:
        compat_label = etichetta_compat(float(rc["media"]))

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
        compat_label=compat_label,
        mio=(session.get("utente_id") == utente_id),
        **stato_relazione(utente_id),
        **info_badge(utente_id),
        **stato_match(utente_id),
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
        if request.form.get("su_richiesta") == "1":
            esegui(conn, "UPDATE tavoli SET su_richiesta = 1 WHERE id = ?", (tavolo_id,))
        if request.form.get("esclusivo") == "1" and e_premium(conn, utente_id):
            esegui(conn, "UPDATE tavoli SET esclusivo = 1 WHERE id = ?", (tavolo_id,))
        try:
            lat = float(request.form.get("lat", ""))
            lng = float(request.form.get("lng", ""))
        except ValueError:
            lat, lng = geocodifica(locale_nome, citta)
        if lat is not None:
            esegui(conn, "UPDATE tavoli SET lat = ?, lng = ? WHERE id = ?", (lat, lng, tavolo_id))
        if citta:
            destinatari = esegui(conn, """
                SELECT id FROM utenti
                WHERE id != ? AND LOWER(COALESCE(citta, '')) = LOWER(?) AND COALESCE(notif_tavoli, 1) = 1
            """, (utente_id, citta)).fetchall()
            for d in destinatari:
                notifica(conn, d["id"], "nuovo_tavolo", locale_nome,
                         f"{session.get('utente_nome')} ha creato un banchetto che potrebbe interessarti",
                         url_for("dettaglio_tavolo", tavolo_id=tavolo_id), utente_id)
        avvisati = {d["id"] for d in destinatari} if citta else set()
        seguaci = esegui(conn, "SELECT utente_id FROM locali_seguiti WHERE locale_id = ? AND utente_id != ?",
                         (locale_id, utente_id)).fetchall()
        for f in seguaci:
            if f["utente_id"] not in avvisati:
                notifica(conn, f["utente_id"], "locale_seguito", locale_nome,
                         "È stato creato un banchetto in un locale che segui",
                         url_for("dettaglio_tavolo", tavolo_id=tavolo_id), utente_id)
        for f in esegui(conn, "SELECT utente_id FROM amici WHERE amico_id = ?", (utente_id,)).fetchall():
            if f["utente_id"] not in avvisati:
                notifica(conn, f["utente_id"], "amico_tavolo", locale_nome,
                         f"Il tuo amico {session.get('utente_nome')} ha creato un banchetto",
                         url_for("dettaglio_tavolo", tavolo_id=tavolo_id), utente_id)
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
    org = esegui(conn, "SELECT organizzatore_id FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
    if org and org["organizzatore_id"] in relazioni_bloccate(conn, session["utente_id"]):
        conn.close()
        flash("Non puoi iscriverti a questo banchetto.")
        return redirect(url_for("home"))
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
        flash("Questo banchetto è riservato a un'altra fascia d'età.")
        return redirect(request.referrer or url_for("home"))

    extra = esegui(conn, "SELECT su_richiesta, organizzatore_id, tema FROM tavoli WHERE id = ?",
                   (tavolo_id,)).fetchone()
    uid_r = session["utente_id"]
    if e_iscritto(conn, tavolo_id, uid_r):
        conn.close()
        return redirect(request.referrer or url_for("home"))
    gia_r = esegui(conn, "SELECT stato FROM richieste WHERE tavolo_id = ? AND utente_id = ?",
                   (tavolo_id, uid_r)).fetchone()
    if gia_r:
        conn.close()
        flash("Hai già una richiesta per questo banchetto.")
        return redirect(request.referrer or url_for("home"))
    pieno = tavolo["iscritti"] >= tavolo["posti_totali"]
    if pieno or extra["su_richiesta"]:
        stato_r = "coda" if pieno else "attesa"
        try:
            esegui(conn, "INSERT INTO richieste (tavolo_id, utente_id, stato, creato_il) VALUES (?, ?, ?, ?)",
                   (tavolo_id, uid_r, stato_r, datetime.now().isoformat()))
            if stato_r == "attesa":
                notifica(conn, extra["organizzatore_id"], "richiesta", extra["tema"],
                         f"{session.get('utente_nome')} chiede di partecipare al tuo tavolo",
                         url_for("dettaglio_tavolo", tavolo_id=tavolo_id), uid_r)
            conn.commit()
            flash("Richiesta inviata: l'organizzatore la valuterà." if stato_r == "attesa"
                  else "Il banchetto è completo: sei in lista d'attesa.")
        except IntegrityError:
            conn.rollback()
        conn.close()
        return redirect(request.referrer or url_for("home"))
    if tavolo["iscritti"] < tavolo["posti_totali"]:
        try:
            esegui(conn,
                   "INSERT INTO iscrizioni (tavolo_id, utente_id, iscritto_il) VALUES (?, ?, ?)",
                   (tavolo_id, session["utente_id"], datetime.now().isoformat()))
            info = esegui(conn, "SELECT tema, organizzatore_id, min_partecipanti FROM tavoli WHERE id = ?",
                          (tavolo_id,)).fetchone()
            link_t = url_for("dettaglio_tavolo", tavolo_id=tavolo_id)
            if info and info["organizzatore_id"] != session["utente_id"]:
                notifica(conn, info["organizzatore_id"], "iscrizione", info["tema"],
                         f"{session.get('utente_nome')} ha preso posto al tuo banchetto",
                         link_t, session["utente_id"])
            totale = esegui(conn, "SELECT COUNT(*) AS n FROM iscrizioni WHERE tavolo_id = ?",
                            (tavolo_id,)).fetchone()["n"]
            if info and info["min_partecipanti"] and totale == info["min_partecipanti"]:
                for r in esegui(conn, "SELECT utente_id FROM iscrizioni WHERE tavolo_id = ?",
                                (tavolo_id,)).fetchall():
                    notifica(conn, r["utente_id"], "confermato", info["tema"],
                             "Il banchetto è confermato: è stato raggiunto il numero minimo di partecipanti",
                             link_t)
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
        flash("L'organizzatore non può annullare l'iscrizione al proprio banchetto.")
    else:
        esegui(conn,
               "DELETE FROM iscrizioni WHERE tavolo_id = ? AND utente_id = ?",
               (tavolo_id, session["utente_id"]))
        riga_t = esegui(conn, "SELECT tema FROM tavoli WHERE id = ?", (tavolo_id,)).fetchone()
        notifica(conn, tavolo["organizzatore_id"], "annullo", riga_t["tema"],
                 f"{session.get('utente_nome')} ha annullato la partecipazione",
                 url_for("dettaglio_tavolo", tavolo_id=tavolo_id), session["utente_id"])
        promuovi_coda(conn, tavolo_id)
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

    esegui(conn, "DELETE FROM richieste WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM preferiti WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM messaggi WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM valutazioni_criteri WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM compatibilita WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM presenze WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM valutazioni_locale WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM assenze WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM iscrizioni WHERE tavolo_id = ?", (tavolo_id,))
    esegui(conn, "DELETE FROM tavoli WHERE id = ?", (tavolo_id,))
    conn.commit()
    conn.close()
    flash("Banchetto eliminato.")
    return redirect(url_for("home"))


@app.route("/sw.js")
def service_worker():
    risposta = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    risposta.headers["Service-Worker-Allowed"] = "/"
    risposta.headers["Cache-Control"] = "no-cache"
    return risposta


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
