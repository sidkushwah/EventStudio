import os
import sqlite3
import hashlib
from datetime import datetime
from flask import Flask, render_template, request, jsonify, g

app = Flask(__name__)
DATABASE = os.path.join(app.root_path, 'omnievent_v6.db')


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # 1. Users Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')

        # 2. Events Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                owner_name TEXT NOT NULL,
                title TEXT NOT NULL,
                date_str TEXT NOT NULL,
                venue TEXT NOT NULL,
                required_info TEXT DEFAULT '',
                ticket_price REAL DEFAULT 0,
                capacity INTEGER DEFAULT 100,
                accent_color TEXT DEFAULT '#38bdf8',
                created_at TEXT NOT NULL
            )
        ''')

        # 3. Attendants Table (One registration per user per event)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attendants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                attendant_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                uid_code TEXT UNIQUE NOT NULL,
                checked_in INTEGER DEFAULT 0,
                scanned_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(event_id, user_id)
            )
        ''')

        # 4. Volunteers Table (One registration per user per event)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS volunteers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                role TEXT NOT NULL,
                expected_pay REAL DEFAULT 0,
                payment_info TEXT DEFAULT '',
                status TEXT DEFAULT 'Pending Approval',
                created_at TEXT NOT NULL,
                UNIQUE(event_id, user_id)
            )
        ''')
        db.commit()


def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()


def serialize_event(ev, db):
    eid = ev['id']
    attendants = db.execute(
        'SELECT * FROM attendants WHERE event_id = ? ORDER BY id DESC', (eid,)
    ).fetchall()
    volunteers = db.execute(
        'SELECT * FROM volunteers WHERE event_id = ? ORDER BY id DESC', (eid,)
    ).fetchall()

    return {
        "id": eid,
        "owner_id": ev["owner_id"],
        "owner_name": ev["owner_name"],
        "title": ev["title"],
        "date_str": ev["date_str"],
        "venue": ev["venue"],
        "required_info": ev["required_info"],
        "ticket_price": ev["ticket_price"],
        "capacity": ev["capacity"],
        "accent_color": ev["accent_color"],
        "attendants": [dict(a) for a in attendants],
        "volunteers": [dict(v) for v in volunteers]
    }


@app.route('/')
def index():
    return render_template('index.html')


# --- AUTHENTICATION & PROFILE MANAGEMENT ---
@app.route('/api/auth/register', methods=['POST'])
def register_user():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    phone = data.get('phone', '').strip()
    password = data.get('password', '').strip()

    if not name or not email or not phone or not password:
        return jsonify({"error": "All fields are required."}), 400

    db = get_db()
    existing = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if existing:
        return jsonify({"error": "Email already registered. Please log in."}), 409

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    cur = db.cursor()
    cur.execute('''
        INSERT INTO users (name, email, phone, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (name, email, phone, hash_pw(password), now_str))
    db.commit()

    return jsonify({
        "id": cur.lastrowid,
        "name": name,
        "email": email,
        "phone": phone
    })


@app.route('/api/auth/login', methods=['POST'])
def login_user():
    data = request.get_json(force=True, silent=True) or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE email = ? AND password_hash = ?',
        (email, hash_pw(password))
    ).fetchone()

    if not user:
        return jsonify({"error": "Invalid email or password."}), 401

    return jsonify({
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "phone": user["phone"]
    })


@app.route('/api/auth/delete/<int:user_id>', methods=['POST'])
def delete_profile(user_id):
    db = get_db()
    db.execute('DELETE FROM attendants WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM volunteers WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM events WHERE owner_id = ?', (user_id,))
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({"status": "deleted"})


# --- CREATE & EDIT EVENT APIs ---
@app.route('/api/events', methods=['GET'])
def list_events():
    db = get_db()
    rows = db.execute('SELECT * FROM events ORDER BY id DESC').fetchall()
    return jsonify([serialize_event(r, db) for r in rows])


@app.route('/api/events/create', methods=['POST'])
def create_event():
    data = request.get_json(force=True, silent=True) or {}
    owner_id = int(data.get('owner_id', 1))
    owner_name = data.get('owner_name', 'Organizer')

    db = get_db()
    # Enforce one-time Create Event per owner if they already created one
    existing_ev = db.execute('SELECT * FROM events WHERE owner_id = ?', (owner_id,)).fetchone()
    if existing_ev:
        return jsonify(serialize_event(existing_ev, db))

    title = data.get('title', '').strip()
    date_str = data.get('date_str', '').strip()
    venue = data.get('venue', '').strip()
    required_info = data.get('required_info', '').strip()
    ticket_price = max(0.0, float(data.get('ticket_price', 0)))
    capacity = max(1, int(data.get('capacity', 100)))

    if not title or not venue:
        return jsonify({"error": "Event Title and Venue are required."}), 400

    cur = db.cursor()
    cur.execute('''
        INSERT INTO events (
            owner_id, owner_name, title, date_str, venue,
            required_info, ticket_price, capacity, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        owner_id, owner_name, title, date_str, venue,
        required_info, ticket_price, capacity,
        datetime.now().strftime("%d %b %Y")
    ))
    db.commit()

    ev = db.execute('SELECT * FROM events WHERE id = ?', (cur.lastrowid,)).fetchone()
    return jsonify(serialize_event(ev, db))


@app.route('/api/events/<int:event_id>/edit', methods=['POST'])
def edit_event(event_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not ev:
        return jsonify({"error": "Event not found."}), 404

    title = data.get('title', ev['title']).strip() or ev['title']
    date_str = data.get('date_str', ev['date_str']).strip() or ev['date_str']
    venue = data.get('venue', ev['venue']).strip() or ev['venue']
    required_info = data.get('required_info', ev['required_info']).strip()
    ticket_price = max(0.0, float(data.get('ticket_price', ev['ticket_price'])))
    capacity = max(1, int(data.get('capacity', ev['capacity'])))

    db.execute('''
        UPDATE events
        SET title = ?, date_str = ?, venue = ?, required_info = ?,
            ticket_price = ?, capacity = ?
        WHERE id = ?
    ''', (title, date_str, venue, required_info, ticket_price, capacity, event_id))
    db.commit()

    updated = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    return jsonify(serialize_event(updated, db))


# --- JOIN EVENT (ATTENDANT - OPENS ONLY ONCE) ---
@app.route('/api/events/<int:event_id>/join_attendant', methods=['POST'])
def join_attendant(event_id):
    data = request.get_json(force=True, silent=True) or {}
    user_id = int(data.get('user_id', 0))
    attendant_name = data.get('attendant_name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()

    db = get_db()
    ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not ev:
        return jsonify({"error": "Event not found."}), 404

    # If user already joined this event, return existing pass immediately
    existing = db.execute(
        'SELECT * FROM attendants WHERE event_id = ? AND user_id = ?',
        (event_id, user_id)
    ).fetchone()
    if existing:
        return jsonify({
            "attendant": dict(existing),
            "event": serialize_event(ev, db)
        })

    if not attendant_name:
        return jsonify({"error": "Please enter your Attendant Name."}), 400

    current_count = db.execute(
        'SELECT COUNT(*) as cnt FROM attendants WHERE event_id = ?', (event_id,)
    ).fetchone()['cnt']
    if current_count >= ev['capacity']:
        return jsonify({"error": "Event has reached maximum capacity!"}), 400

    seed = f"{event_id}-{user_id}-{attendant_name}-{datetime.utcnow().isoformat()}"
    short_hash = hashlib.sha256(seed.encode()).hexdigest()[:6].upper()
    prefix = ''.join([c for c in ev['title'].upper() if c.isalnum()])[:3] or 'EVT'
    uid_code = f"UID-{prefix}-{short_hash}"

    now_str = datetime.now().strftime("%d %b, %I:%M %p")
    cur = db.cursor()
    cur.execute('''
        INSERT INTO attendants (
            event_id, user_id, attendant_name, email, phone, uid_code, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (event_id, user_id, attendant_name, email, phone, uid_code, now_str))
    db.commit()

    attendant = db.execute('SELECT * FROM attendants WHERE id = ?', (cur.lastrowid,)).fetchone()
    updated_ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    return jsonify({
        "attendant": dict(attendant),
        "event": serialize_event(updated_ev, db)
    })


# --- VOLUNTEER REGISTRATION (OPENS ONLY ONCE) & SCANNER ---
@app.route('/api/events/<int:event_id>/apply_volunteer', methods=['POST'])
def apply_volunteer(event_id):
    data = request.get_json(force=True, silent=True) or {}
    user_id = int(data.get('user_id', 0))
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()
    role = data.get('role', '').strip()
    expected_pay = max(0.0, float(data.get('expected_pay', 0)))
    payment_info = data.get('payment_info', '').strip()

    db = get_db()
    existing = db.execute(
        'SELECT * FROM volunteers WHERE event_id = ? AND user_id = ?',
        (event_id, user_id)
    ).fetchone()
    if existing:
        updated_ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
        return jsonify(serialize_event(updated_ev, db))

    if not name or not phone or not role:
        return jsonify({"error": "Name, Contact Phone, and Role are required."}), 400

    now_str = datetime.now().strftime("%d %b, %I:%M %p")
    db.execute('''
        INSERT INTO volunteers (
            event_id, user_id, name, email, phone, role,
            expected_pay, payment_info, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending Approval', ?)
    ''', (event_id, user_id, name, email, phone, role, expected_pay, payment_info, now_str))
    db.commit()

    updated_ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    return jsonify(serialize_event(updated_ev, db))


@app.route('/api/volunteers/<int:vol_id>/status', methods=['POST'])
def update_volunteer_status(vol_id):
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get('status', 'Approved')

    db = get_db()
    vol = db.execute('SELECT event_id FROM volunteers WHERE id = ?', (vol_id,)).fetchone()
    if not vol:
        return jsonify({"error": "Volunteer not found"}), 404

    db.execute('UPDATE volunteers SET status = ? WHERE id = ?', (new_status, vol_id))
    db.commit()

    updated_ev = db.execute('SELECT * FROM events WHERE id = ?', (vol['event_id'],)).fetchone()
    return jsonify(serialize_event(updated_ev, db))


@app.route('/api/events/<int:event_id>/scan_uid', methods=['POST'])
def scan_uid(event_id):
    data = request.get_json(force=True, silent=True) or {}
    uid_code = data.get('uid_code', '').strip().upper()

    db = get_db()
    att = db.execute(
        'SELECT * FROM attendants WHERE event_id = ? AND UPPER(uid_code) = ?',
        (event_id, uid_code)
    ).fetchone()

    if not att:
        return jsonify({
            "status": "invalid",
            "message": f"UID '{uid_code}' not found for this event."
        }), 404

    if att['checked_in'] == 1:
        return jsonify({
            "status": "duplicate",
            "message": f"ALREADY SCANNED! {att['attendant_name']} checked in at {att['scanned_at']}."
        }), 409

    now_str = datetime.now().strftime("%I:%M:%S %p")
    db.execute(
        'UPDATE attendants SET checked_in = 1, scanned_at = ? WHERE id = ?',
        (now_str, att['id'])
    )
    db.commit()

    updated_ev = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    return jsonify({
        "status": "verified",
        "message": f"Verified Attendant: {att['attendant_name']} ({uid_code})",
        "event": serialize_event(updated_ev, db)
    })


init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)