"""ETL Sport Data Solution, calcul des avantages et file de notifications."""
import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import hmac
import json
from math import asin, cos, radians, sin, sqrt
import os
from pathlib import Path
import random
import re
import secrets
import sqlite3
from time import sleep
from unicodedata import normalize
from uuid import uuid4

from openpyxl import load_workbook
import requests


ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.getenv("P12_RUNTIME", ROOT / "local"))
WORK = "1362 avenue des Platanes, 34970 Lattes, France"
MODES = {"marche/running": 15000, "velo/trottinette/autres": 25000}
DISTANCE_SPORTS = {"course a pied", "randonnee", "velo", "natation", "triathlon", "voile", "equitation"}
DEMO_DAY = date(2026, 11, 7)


def clean(value):
    return "".join(c for c in normalize("NFD", str(value or "").strip().lower()) if ord(c) < 768 or ord(c) > 879)


def connect(folder=RUNTIME):
    folder.mkdir(exist_ok=True, parents=True)
    db = sqlite3.connect(folder / "sports.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


@contextmanager
def session(folder=RUNTIME):
    with closing(connect(folder)) as db, db:
        yield db


def schema(db):
    db.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS employee(
        id TEXT PRIMARY KEY, salary_cents INTEGER NOT NULL CHECK(salary_cents > 0),
        mode TEXT NOT NULL, sport TEXT NOT NULL, route_m INTEGER CHECK(route_m >= 0), route_method TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS activity(
        id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employee(id), sport TEXT NOT NULL,
        begins TEXT NOT NULL, ends TEXT NOT NULL CHECK(ends > begins),
        distance_m INTEGER CHECK(distance_m >= 0), comment TEXT NOT NULL, source TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS outbox(
        activity_id TEXT PRIMARY KEY REFERENCES activity(id), text TEXT NOT NULL, created TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS delivery(
        activity_id TEXT PRIMARY KEY REFERENCES outbox(activity_id), status TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), rate TEXT, threshold INTEGER, days INTEGER, anchor TEXT, demo_employee TEXT);
      CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, time TEXT, operation TEXT, status TEXT, rows INTEGER, detail TEXT);
      CREATE TABLE IF NOT EXISTS config_history(id INTEGER PRIMARY KEY, time TEXT, rate TEXT, threshold INTEGER, days INTEGER, anchor TEXT);
      CREATE TABLE IF NOT EXISTS processed(id TEXT PRIMARY KEY, digest TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS activity_employee_date ON activity(employee_id,begins);
    """)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(db, op, status, rows, detail):
    db.execute("INSERT INTO runs(time,operation,status,rows,detail) VALUES (?,?,?,?,?)", (now(),op,status,rows,detail))


def year_ago(anchor):
    try:
        return anchor.replace(year=anchor.year-1)
    except ValueError:
        return anchor.replace(year=anchor.year-1, day=28)


def workbook_rows(file, required):
    workbook = load_workbook(file, read_only=True, data_only=True)
    try:
        rows = iter(workbook.active.values)
        header = next(rows)
        if any(header.count(key) != 1 for key in required):
            raise ValueError("En-tête source invalide")
        positions = [header.index(key) for key in required]
        values = [tuple(row[i] for i in positions) for row in rows if any(x is not None for x in row)]
    finally:
        workbook.close()
    if not values or any(row[0] is None for row in values) or len({str(row[0]) for row in values}) != len(values):
        raise ValueError("Clé salarié vide ou dupliquée")
    return values


def load_communes(folder):
    return [row for path in sorted(folder.glob("communes_*.json"))
            for row in json.loads(path.read_text(encoding="utf-8")) if row.get("centre")]


def municipal_distance(address, communes):
    match = re.search(r"\b(\d{5})\s+(.+)$", str(address or "").strip())
    if not match:
        return None
    zipcode, city = match.groups()
    city = re.sub(r"[^a-z0-9]", "", clean(city).removesuffix("france"))
    possible = [r for r in communes if zipcode in r.get("codesPostaux", [])]
    exact = [r for r in possible if re.sub(r"[^a-z0-9]", "", clean(r["nom"])) == city]
    origin = exact[0] if len(exact) == 1 else possible[0] if len(possible) == 1 else None
    target = next((r for r in communes if r["nom"] == "Lattes" and "34970" in r["codesPostaux"]), None)
    if origin is None or target is None:
        return None
    x1,y1 = map(radians, origin["centre"]["coordinates"])
    x2,y2 = map(radians, target["centre"]["coordinates"])
    a = sin((y2-y1)/2)**2 + cos(y1)*cos(y2)*sin((x2-x1)/2)**2
    return round(12_742_000*asin(sqrt(min(1,a))))


def google_distance(address, mode, key):
    """Mode facultatif. Envoie une adresse au fournisseur, à activer après autorisation."""
    try:
        response = requests.post("https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.distanceMeters"},
            json={"origin": {"address": address}, "destination": {"address": WORK},
                  "travelMode": "WALK" if mode == "marche/running" else "BICYCLE"},
            timeout=20)
        if response.status_code != 200:
            return None
        routes = response.json().get("routes", [])
        return int(routes[0]["distanceMeters"]) if routes else None
    except (requests.RequestException, ValueError, KeyError):
        return None


def prepare(rh, sport, geo, folder=RUNTIME, google=False):
    """Read only required columns. Keep salaries and pseudonyms in the local database."""
    folder.mkdir(parents=True,exist_ok=True)
    secret = folder / "pseudonym.key"
    try:
        with secret.open("xb") as file:
            file.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    key = secret.read_bytes()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if google and not api_key:
        raise ValueError("Variable GOOGLE_MAPS_API_KEY requise")
    communes = load_communes(geo)
    practices = dict(workbook_rows(sport, ["ID salarié", "Pratique d'un sport"]))
    source = workbook_rows(rh, ["ID salarié","Salaire brut","Adresse du domicile","Moyen de déplacement"])
    if {str(k) for k in practices} - {str(row[0]) for row in source}:
        raise ValueError("Déclaration sportive sans salarié RH")
    employees = []
    for identifier,salary,address,declared in source:
        cents = int((Decimal(str(salary))*100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        mode = clean(declared)
        if cents <= 0 or mode not in (*MODES, "vehicule thermique/electrique", "transports en commun"):
            raise ValueError("Salaire ou mode invalide")
        distance, method = None,"sans_objet"
        if mode in MODES:
            distance = google_distance(address, mode, api_key) if google and address else municipal_distance(address, communes) if not google else None
            method = ("google" if google else "centre_commune") if distance is not None else "a_verifier"
        pseudonym = hmac.new(key,str(identifier).encode(),"sha256").hexdigest()[:16]
        employees.append((pseudonym,cents,mode,clean(practices.get(identifier)).replace("runing","course a pied"),distance,method))
    return employees


def record(row, anchor, employees):
    required = ("id","employee_id","sport","begins","ends","distance_m","comment")
    if any(key not in row for key in required):
        raise ValueError("Schéma activité incomplet")
    identifier, employee, sport = row["id"],row["employee_id"],clean(row["sport"])
    if not isinstance(identifier,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",identifier) or employee not in employees or not sport:
        raise ValueError("Identifiant, salarié ou sport invalide")
    start, end = (datetime.fromisoformat(row[key]) for key in ("begins","ends"))
    if start.tzinfo is not None or end.tzinfo is not None or not year_ago(anchor) < start.date() <= anchor or not start < end <= start+timedelta(hours=24):
        raise ValueError("Dates ou durée hors règles")
    distance = row["distance_m"]
    if distance is not None and (type(distance) is not int or distance < 0 or distance > 500000):
        raise ValueError("Distance invalide")
    if sport in DISTANCE_SPORTS and distance is None:
        raise ValueError("Distance manquante pour ce sport")
    if not isinstance(row["comment"],str) or len(row["comment"])>500:
        raise ValueError("Commentaire invalide")
    return (identifier,employee,sport,start.isoformat(timespec="seconds"),end.isoformat(timespec="seconds"),distance,row["comment"])


def generate(employees, anchor, demo):
    rng = random.Random(1207)
    first = year_ago(anchor)+timedelta(days=1)
    span = (anchor-first).days
    for identifier,_,_,sport,_,_ in employees:
        if not sport:
            continue
        amount = 14 if identifier == demo else rng.randint(20,68)
        for index in range(amount):
            start = datetime.combine(first+timedelta(days=rng.randrange(span+1)),datetime.min.time()).replace(hour=rng.randint(7,19),minute=rng.choice([0,15,30,45]))
            duration = rng.randint(25,105)
            distance = None
            if sport in DISTANCE_SPORTS:
                speed = 3 if sport=="natation" else 6 if sport in {"randonnee","equitation"} else 10
                distance = round(duration/60*speed*rng.uniform(.75,1.25)*1000)
            yield dict(id=f"hist_{identifier}_{index}",employee_id=identifier,sport=sport,begins=start.isoformat(),
                       ends=(start+timedelta(minutes=duration)).isoformat(),distance_m=distance,comment="")


def initialize(employees, anchor, folder=RUNTIME):
    db = connect(folder)
    schema(db)
    ids = {row[0] for row in employees}
    existing = db.execute("SELECT * FROM settings").fetchone()
    if existing and existing["anchor"] != str(anchor):
        raise ValueError("La fenêtre initiale est fixée pour cette base. Utiliser une autre base pour changer la date.")
    if existing:
        missing = {row[0] for row in db.execute("SELECT id FROM employee")} - ids
        if missing:
            raise ValueError("Salariés retirés : prévoir une politique d’historisation avant recharge.")
    demo = existing["demo_employee"] if existing else next(r[0] for r in employees if r[3]=="course a pied")
    history = [(*record(row,anchor,ids),"history") for row in generate(employees,anchor,demo)]
    with db:
        db.executemany("""INSERT INTO employee VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            salary_cents=excluded.salary_cents,mode=excluded.mode,sport=excluded.sport,route_m=excluded.route_m,
            route_method=excluded.route_method""",employees)
        db.execute("DELETE FROM activity WHERE source='history'")
        db.executemany("INSERT INTO activity VALUES (?,?,?,?,?,?,?,?)",history)
        if not existing:
            db.execute("INSERT INTO settings VALUES (1,'5',15,5,?,?)",(str(anchor),demo))
            db.execute("INSERT INTO config_history(time,rate,threshold,days,anchor) VALUES (?,'5',15,5,?)",(now(),str(anchor)))
        log(db,"chargement", "ok",len(history),f"{len(ids)} salariés, simulation historique")
    db.close()


def enqueue(row, folder=RUNTIME):
    inbox = folder/"inbox"
    inbox.mkdir(parents=True,exist_ok=True)
    name = uuid4().hex
    temp = inbox/f"{name}.tmp"
    temp.write_text(json.dumps(row,ensure_ascii=False),encoding="utf-8")
    temp.replace(inbox/f"{name}.json")


def consume(folder=RUNTIME):
    inbox = folder/"inbox"
    inbox.mkdir(parents=True,exist_ok=True)
    with session(folder) as db:
        settings = db.execute("SELECT * FROM settings").fetchone()
        ids = {row[0] for row in db.execute("SELECT id FROM employee")}
        for path in sorted(inbox.glob("*.json")):
            status = "ok"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                values = record(payload,date.fromisoformat(settings["anchor"]),ids)
                digest = sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()
                db.execute("BEGIN IMMEDIATE")
                past = db.execute("SELECT digest FROM processed WHERE id=?",(values[0],)).fetchone()
                if past and past[0] != digest:
                    raise ValueError("Identifiant déjà utilisé pour un autre contenu")
                if not past:
                    db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?,'live')",values)
                    distance = f" · {values[5]/1000:.1f} km" if values[5] is not None else ""
                    db.execute("INSERT INTO outbox VALUES (?,?,?)",
                        (values[0],f"Bravo, sportif {values[1][:6]} ! Nouvelle activité : {values[2]}{distance}.",now()))
                    db.execute("INSERT INTO processed VALUES (?,?)",(values[0],digest))
                status = "rejeu" if past else "ok"
                log(db,"activité",status,int(not past),values[0])
                db.commit()
            except (ValueError,TypeError,KeyError,sqlite3.IntegrityError) as error:
                db.rollback()
                status = "rejet"
                log(db,"activité",status,0,type(error).__name__+" · données non intégrées")
                db.commit()
            target = folder/status
            target.mkdir(exist_ok=True)
            path.replace(target/path.name)
        heartbeat = (datetime.now(timezone.utc)-timedelta(seconds=25)).isoformat(timespec="seconds")
        if not db.execute("SELECT 1 FROM runs WHERE operation='surveillance' AND time >= ?",(heartbeat,)).fetchone():
            log(db,"surveillance","ok",0,"inbox contrôlée")


def change_config(rate, threshold=15, days=5, folder=RUNTIME):
    rate = Decimal(str(rate))
    if not rate.is_finite() or not 0 <= rate <= 100 or not 1 <= threshold <= 365 or not 0 <= days <= 30:
        raise ValueError("Paramètres invalides")
    with session(folder) as db:
        db.execute("UPDATE settings SET rate=?,threshold=?,days=? WHERE id=1",(str(rate),threshold,days))
        db.execute("""INSERT INTO config_history(time,rate,threshold,days,anchor)
                   SELECT ?,rate,threshold,days,anchor FROM settings""",(now(),))
        log(db,"paramètre","ok",0,f"taux={rate}%")


def deliver_slack(identifier, folder=RUNTIME):
    """Send one explicitly selected test message. Ambiguous delivery requires manual review."""
    url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not re.fullmatch(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+", url):
        raise ValueError("Webhook Slack absent ou invalide")
    with session(folder) as db:
        message = db.execute("SELECT text FROM outbox WHERE activity_id=?",(identifier,)).fetchone()
        if not message:
            raise ValueError("Message inexistant")
        claim = db.execute("INSERT OR IGNORE INTO delivery VALUES (?,'en_cours')",(identifier,))
        if claim.rowcount != 1:
            raise ValueError("Publication déjà tentée. Vérifier son statut avant toute reprise.")
    state = "a_verifier"
    try:
        response = requests.post(url,json={"text":message[0]},timeout=12,allow_redirects=False)
        if response.status_code == 200 and response.text.strip()=="ok":
            state = "livre"
    except requests.RequestException:
        pass
    with session(folder) as db:
        db.execute("UPDATE delivery SET status=? WHERE activity_id=?",(state,identifier))
        log(db,"Slack",state,int(state=="livre"),identifier)
    return state


def metrics(folder=RUNTIME):
    with session(folder) as db:
        s = dict(db.execute("SELECT * FROM settings").fetchone())
        rows = db.execute("""SELECT e.*, count(a.id) AS n FROM employee e LEFT JOIN activity a
                          ON e.id=a.employee_id AND a.begins>=? AND a.begins<?
                          GROUP BY e.id""",
                          ((year_ago(date.fromisoformat(s["anchor"]))+timedelta(days=1)).isoformat(),
                           (date.fromisoformat(s["anchor"])+timedelta(days=1)).isoformat())).fetchall()
        kpi = dict(employees=len(rows),activities=sum(r["n"] for r in rows),potential=0,confirmed=0,review=0,
                   cost_cents=0,declared_cost_cents=0,confirmed_cost_cents=0,
                   wellness=sum(r["n"]>=s["threshold"] for r in rows),practitioners=sum(bool(r["sport"]) for r in rows))
        kpi["days"]=kpi["wellness"]*s["days"]
        kpi["days_value_cents"]=sum(int((Decimal(r["salary_cents"])*s["days"]/218).quantize(
            Decimal("1"),rounding=ROUND_HALF_UP)) for r in rows if r["n"]>=s["threshold"])
        kpi["demo_count"]=next(r["n"] for r in rows if r["id"]==s["demo_employee"])
        for row in rows:
            if row["mode"] not in MODES:
                continue
            amount=int((Decimal(row["salary_cents"])*Decimal(s["rate"])/100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
            kpi["declared_cost_cents"]+=amount
            if row["route_m"] is None or row["route_m"] > MODES[row["mode"]]:
                kpi["review"]+=1
                continue
            kpi["potential"]+=1
            kpi["cost_cents"]+=amount
            if row["route_method"]=="google":
                kpi["confirmed"]+=1
                kpi["confirmed_cost_cents"]+=amount
        recent=[dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 15")]
        outbox=[dict(r) for r in db.execute("SELECT * FROM outbox ORDER BY created DESC LIMIT 20")]
        trend=[dict(r) for r in db.execute("SELECT substr(begins,1,7) AS mois,count(*) AS activités FROM activity GROUP BY 1 ORDER BY 1")]
    return {"kpi":kpi,"settings":s,"runs":recent,"outbox":outbox,"trend":trend}


def synthetic():
    return [(f"demo{i:03}",(30000+i*900)*100,"marche/running" if i%2 else "velo/trottinette/autres",
             "course a pied" if i%3 else "escalade", i*1400,"centre_commune") for i in range(1,61)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("init","demo","watch","once","metrics","slack"))
    parser.add_argument("--rh",type=Path)
    parser.add_argument("--sport",type=Path)
    parser.add_argument("--geo",type=Path,default=ROOT)
    parser.add_argument("--date",type=date.fromisoformat,default=DEMO_DAY)
    parser.add_argument("--google",action="store_true",help="Requiert autorisation de transmettre les adresses et une clé.")
    parser.add_argument("--message",help="Identifiant exact du message Slack à transmettre.")
    args=parser.parse_args()
    if args.action in ("init","demo"):
        data=synthetic() if args.action=="demo" else prepare(args.rh,args.sport,args.geo,google=args.google)
        initialize(data,args.date)
        print(json.dumps(metrics()["kpi"],ensure_ascii=False))
    elif args.action=="metrics":
        print(json.dumps(metrics(),ensure_ascii=False))
    elif args.action=="slack":
        print(deliver_slack(args.message))
    else:
        while True:
            consume()
            if args.action=="once":
                break
            sleep(3)


if __name__=="__main__":
    main()
