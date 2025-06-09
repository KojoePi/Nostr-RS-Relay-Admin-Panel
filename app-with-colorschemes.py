import sqlite3
import json
import toml
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS


# ==============================================================================
# ===== KONFIGURATION (BITTE SORGFÄLTIG ANPASSEN) ==============================
# ==============================================================================

# 1. Pfad zur Datenbank deines nostr-rs-relay (absoluter Pfad empfohlen)
DATABASE_PATH = "/path/to/nostr.db"

# 2. Pfad zur Konfigurationsdatei deines nostr-rs-relay (absoluter Pfad empfohlen)
CONFIG_PATH = "/path/to/config.toml"

# 3. WebSocket URL deines Relays
RELAY_WEBSOCKET_URL = "wss://your.relay.here"

# 4. Geheimer Schlüssel für die Flask-Session
SECRET_SESSION_KEY = 'mit "openssl rand -hex 32" im Terninal generieren'

# ==============================================================================
# ===== ENDE DER KONFIGURATION =================================================
# ==============================================================================

app = Flask(__name__)
app.secret_key = SECRET_SESSION_KEY
app.permanent_session_lifetime = timedelta(hours=8)
CORS(app)

# --- Hilfsfunktionen ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_db_connection_rw():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    conn = get_db_connection_rw()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS banned_pubkeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pubkey TEXT NOT NULL UNIQUE,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def format_db_size(size_bytes):
    if not isinstance(size_bytes, (int, float)) or size_bytes < 0: return "N/A"
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < 1024**2: return f"{round(size_bytes / 1024, 2)} KB"
    elif size_bytes < 1024**3: return f"{round(size_bytes / (1024**2), 2)} MB"
    else: return f"{round(size_bytes / (1024**3), 2)} GB"

# --- API Endpunkte ---

@app.route('/api/stats')
def get_stats():
    conn, conn_rw = None, None
    stats = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now_ts = int(datetime.now().timestamp())
        ts_24h_ago = now_ts - (24 * 3600)
        ts_1h_ago = now_ts - 3600

        stats['total_events'] = cursor.execute('SELECT COUNT(*) FROM event').fetchone()[0] or 0
        stats['distinct_pubkeys'] = cursor.execute('SELECT COUNT(DISTINCT author) FROM event').fetchone()[0] or 0
        stats['events_24h'] = cursor.execute('SELECT COUNT(*) FROM event WHERE created_at > ?', (ts_24h_ago,)).fetchone()[0] or 0
        stats['events_1h'] = cursor.execute('SELECT COUNT(*) FROM event WHERE created_at > ?', (ts_1h_ago,)).fetchone()[0] or 0
        stats['new_users_24h'] = cursor.execute("SELECT COUNT(*) FROM (SELECT MIN(created_at) as first_seen FROM event GROUP BY author) WHERE first_seen > ?", (ts_24h_ago,)).fetchone()[0] or 0
        stats['top_kinds'] = [dict(row) for row in cursor.execute("SELECT kind, COUNT(*) as count FROM event GROUP BY kind ORDER BY count DESC LIMIT 5").fetchall()]
        dm_count = cursor.execute("SELECT COUNT(*) FROM event WHERE kind = 4").fetchone()[0] or 0
        stats['dm_percentage'] = round((dm_count / stats['total_events']) * 100, 2) if stats['total_events'] > 0 else 0
        top_users_query = cursor.execute("SELECT lower(hex(author)) as pubkey, COUNT(*) as count FROM event GROUP BY author ORDER BY count DESC LIMIT 5").fetchall()
        stats['top_users'] = [dict(row) for row in top_users_query]
        oldest_event_ts = cursor.execute("SELECT MIN(created_at) FROM event").fetchone()[0]
        stats['oldest_event_date'] = datetime.fromtimestamp(oldest_event_ts).strftime('%d. %b %Y') if oldest_event_ts else "N/A"
        conn_rw = get_db_connection_rw()
        stats['banned_pubkeys'] = conn_rw.execute('SELECT COUNT(*) FROM banned_pubkeys').fetchone()[0] or 0
        stats['db_size'] = format_db_size(os.path.getsize(DATABASE_PATH)) if os.path.exists(DATABASE_PATH) else "N/A"
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()
        if conn_rw: conn_rw.close()
    return jsonify(stats)

@app.route('/api/events')
def get_events():
    query = request.args.get('q', '')
    limit = int(request.args.get('limit', 100))
    conn = get_db_connection()
    try:
        select_clause = 'SELECT id, lower(hex(author)) as pubkey, kind, content, created_at, lower(hex(event_hash)) as event_id FROM event'
        if query:
            search_query_hex = f'%{query}%'
            cursor = conn.execute(
                f'{select_clause} WHERE pubkey LIKE ? OR event_id LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT ?',
                (search_query_hex, search_query_hex, query, limit)
            )
        else:
            cursor = conn.execute(f'{select_clause} ORDER BY created_at DESC LIMIT ?', (limit,))
        events = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()
    return jsonify(events)

@app.route('/api/events/<int:event_db_id>', methods=['DELETE'])
def delete_event(event_db_id):
    conn = get_db_connection_rw()
    try:
        conn.execute('DELETE FROM event WHERE id = ?', (event_db_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success"})

@app.route('/api/banned', methods=['GET', 'POST'])
def handle_banned_users():
    conn_rw = get_db_connection_rw()
    try:
        if request.method == 'POST':
            pubkey = request.json.get('pubkey')
            if pubkey and len(pubkey) == 64:
                conn_rw.execute('INSERT OR IGNORE INTO banned_pubkeys (pubkey) VALUES (?)', (pubkey,))
                conn_rw.commit()
                return jsonify({"status": "success", "message": f"Pubkey {pubkey[:8]}... banned."})
            return jsonify({"status": "error", "message": "Invalid pubkey."}), 400
        else:
            cursor = conn_rw.execute('SELECT pubkey FROM banned_pubkeys ORDER BY banned_at DESC')
            return jsonify([row['pubkey'] for row in cursor.fetchall()])
    finally:
        conn_rw.close()

@app.route('/api/banned/<pubkey>', methods=['DELETE'])
def unban_user(pubkey):
    conn = get_db_connection_rw()
    try:
        conn.execute('DELETE FROM banned_pubkeys WHERE pubkey = ?', (pubkey,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success"})

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    try:
        if request.method == 'POST':
            with open(CONFIG_PATH, 'w') as f:
                f.write(request.json.get('content', ''))
            return jsonify({"status": "success"})
        else:
            with open(CONFIG_PATH, 'r') as f:
                return jsonify({"content": f.read()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, relay_websocket_url=RELAY_WEBSOCKET_URL)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nostr Relay Admin by relayted.de</title>
    <script src="https://unpkg.com/nostr-tools@2/lib/nostr.js"></script>
    <style>
        body[data-theme-color="blue"] { --primary: #007bff; }
        body[data-theme-color="teal"] { --primary: #17a2b8; }
        body[data-theme-color="lilac"] { --primary: #c8a2c8; }

        body[data-theme-mode="light"] {
            --bg: #f8f9fa; --text: #212529; --card-bg: #ffffff;
            --border: #dee2e6; --shadow: rgba(0,0,0,0.07);
        }
        body[data-theme-mode="dark"] {
            --bg: #121212; --text: #e0e0e0; --card-bg: #1e1e1e;
            --border: #3e3e3e; --shadow: rgba(0,0,0,0.2);
        }
        :root {
             --secondary: #6c757d; --danger: #dc3545;
        }

        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,"Helvetica Neue",Arial,sans-serif; margin: 0; background-color: var(--bg); color: var(--text); transition: background-color 0.2s, color 0.2s; }
        .container { max-width: 1400px; margin: 0 auto; padding: 2rem 1rem 4rem; }
        h1,h2,h3 { color: var(--primary); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; padding: 1rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }
        .theme-switcher { display: flex; align-items: center; gap: 1rem; }
        .color-options { display: flex; gap: 10px; }
        .color-box { width: 24px; height: 24px; border-radius: 50%; cursor: pointer; border: 2px solid var(--border); transition: transform 0.2s; }
        .color-box.active { border-color: var(--primary); transform: scale(1.15); box-shadow: 0 0 5px var(--primary); }
        #theme-toggle-btn { background: none; border: none; cursor: pointer; padding: 5px; display:flex; align-items:center; }
        #theme-toggle-btn svg { width: 22px; height: 22px; fill: var(--text); }
        body[data-theme-mode="light"] .theme-icon-moon { display: inline; }
        body[data-theme-mode="light"] .theme-icon-sun { display: none; }
        body[data-theme-mode="dark"] .theme-icon-moon { display: none; }
        body[data-theme-mode="dark"] .theme-icon-sun { display: inline; }

        nav.tabs { border-bottom: 2px solid var(--border); margin-bottom: 2rem; }
        nav.tabs button { background: none; border: none; border-bottom: 3px solid transparent; padding: 1rem 1.5rem; cursor: pointer; color: var(--secondary); font-size: 1rem; margin-bottom: -2px; }
        nav.tabs button.active { color: var(--primary); border-bottom-color: var(--primary); }
        
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1.5rem; }
        .stat-card { background: var(--card-bg); border-radius: 8px; padding: 1.5rem; text-align: center; box-shadow: 0 2px 4px var(--shadow); }
        .stat-card .value { font-size: 2rem; font-weight: bold; color: var(--primary); }
        .stat-card .label { font-size: 0.9rem; color: var(--secondary); margin-top: 0.5rem; }
        .dashboard-section { margin-top: 2.5rem; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; table-layout: fixed; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); word-break: break-word; vertical-align: top; }
        th { background-color: var(--bg); }
        .actions-cell { display: flex; flex-direction: column; gap: 5px; width: 160px; }
        td div.note-content { white-space: pre-wrap; font-size: 0.9em; max-height: 250px; overflow-y: auto; }
        td img { max-width: 100%; height: auto; max-height: 200px; border-radius: 8px; margin-top: 5px; }
        
        #banned-list { list-style: none; padding: 0; }
        #banned-list li { display: flex; justify-content: space-between; align-items: center; padding: 8px; background: var(--card-bg); border: 1px solid var(--border); border-radius: 4px; margin-bottom: 5px; font-family: monospace; }
        
        button { padding: 8px 14px; font-size: 0.9rem; cursor: pointer; border: 1px solid var(--border); background-color: var(--card-bg); color: var(--text); border-radius: 5px; }
        button:not([disabled]):hover { background-color: rgba(var(--primary-rgb), 0.1); }
        .danger { background-color: var(--danger); color: white; border-color: var(--danger); }
        .danger:hover { background-color: var(--danger); opacity: 0.85; }
        .secondary { background-color: var(--secondary); color: white; border-color: var(--secondary); }
        .secondary:hover { background-color: var(--secondary); opacity: 0.85; }
        
        #config-editor { width: 100%; min-height: 50vh; box-sizing: border-box; }
        footer { padding: 1rem; text-align: center; border-top: 1px solid var(--border); margin-top: 2rem; font-size: 0.9em; color: var(--secondary); }
        footer a { color: var(--primary); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1 data-i18n="title"></h1>
            <div class="theme-switcher">
                <button id="theme-toggle-btn" title="Toggle dark mode">
                    <svg class="theme-icon-moon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 11.807A9.002 9.002 0 0 1 10.049 2a9.942 9.942 0 0 0-4.01 3.997A9.993 9.993 0 0 0 12 22a9.94 9.94 0 0 0 5.963-1.986A9.002 9.002 0 0 1 12 11.807Z"></path></svg>
                    <svg class="theme-icon-sun" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-2a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z M12 3a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Zm0 14a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0v-2a1 1 0 0 1 1-1ZM20 11a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0v-2a1 1 0 0 1 1-1ZM4 11a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0v-2a1 1 0 0 1 1-1ZM18.364 7.05a1 1 0 0 1 .707-.293A1 1 0 0 1 20.485 8.172l-1.414 1.414a1 1 0 1 1-1.414-1.414l1.414-1.414.001-.001ZM6.343 17.657a1 1 0 0 1 .707-.293 1 1 0 0 1 .707 1.707l-1.414 1.414a1 1 0 1 1-1.414-1.414l1.414-1.414Zm12.121 0a1 1 0 0 1 1.414 1.414l-1.414 1.414a1 1 0 0 1-1.414-1.414l1.414-1.414.004-.002ZM7.05 5.636a1 1 0 0 1 1.414 0L9.88 7.05a1 1 0 0 1-1.414 1.414L7.05 7.05a1 1 0 0 1 0-1.414Z"></path></svg>
                </button>
                <div class="color-options">
                    <div class="color-box" data-color="blue" style="background: #007bff;" title="Blue"></div>
                    <div class="color-box" data-color="teal" style="background: #17a2b8;" title="Teal"></div>
                    <div class="color-box" data-color="lilac" style="background: #c8a2c8;" title="Lilac"></div>
                </div>
            </div>
        </header>

        <nav class="tabs">
            <button class="tab-button active" data-tab="dashboard" data-i18n="tabDashboard"></button>
            <button class="tab-button" data-tab="events" data-i18n="tabEvents"></button>
            <button class="tab-button" data-tab="stream" data-i18n="tabStream"></button>
            <button class="tab-button" data-tab="banned" data-i18n="tabBanned"></button>
            <button class="tab-button" data-tab="config" data-i18n="tabConfig"></button>
        </nav>
        
        <div id="dashboard-content" class="tab-content active">
            <div class="stats-grid">
                <div class="stat-card"><div id="stats-total-events" class="value">...</div><div class="label" data-i18n="statTotalEvents"></div></div>
                <div class="stat-card"><div id="stats-distinct-pubkeys" class="value">...</div><div class="label" data-i18n="statUniqueUsers"></div></div>
                <div class="stat-card"><div id="stats-banned-pubkeys" class="value">...</div><div class="label" data-i18n="statBannedUsers"></div></div>
                <div class="stat-card"><div id="stats-events-24h" class="value">...</div><div class="label" data-i18n="statEvents24h"></div></div>
                <div class="stat-card"><div id="stats-events-1h" class="value">...</div><div class="label" data-i18n="statEvents1h"></div></div>
                <div class="stat-card"><div id="stats-new-users-24h" class="value">...</div><div class="label" data-i18n="statNewUsers24h"></div></div>
                <div class="stat-card"><div id="stats-dm-percentage" class="value">...</div><div class="label" data-i18n="statDmPercentage"></div></div>
                <div class="stat-card"><div id="stats-db-size" class="value">...</div><div class="label" data-i18n="statDbSize"></div></div>
                <div class="stat-card"><div id="stats-oldest-event" class="value">...</div><div class="label" data-i18n="statOldestEvent"></div></div>
            </div>
            <div class="stats-grid dashboard-section" style="grid-template-columns: 1fr 1fr; gap: 2rem;">
                <div><h3 data-i18n="titleTopKinds"></h3><table id="top-kinds-table"></table></div>
                <div><h3 data-i18n="titleTopUsers"></h3><table id="top-users-table"></table></div>
            </div>
        </div>
        
        <div id="events-content" class="tab-content">
             <input type="text" id="event-search" data-i18n-placeholder="eventSearchPlaceholder" style="margin-bottom: 1rem; width: 100%; box-sizing: border-box; padding: 10px;">
            <table id="events-table"></table>
        </div>
        <div id="stream-content" class="tab-content"><h3 data-i18n="streamTitle"></h3><table id="stream-table"></table></div>
        <div id="banned-content" class="tab-content"><h3 data-i18n="bannedListTitle"></h3><ul id="banned-list"></ul></div>
        <div id="config-content" class="tab-content"><h3 data-i18n="relayConfig"></h3><textarea id="config-editor" style="width: 100%; min-height: 50vh;"></textarea><br><button id="save-config-btn" style="margin-top:10px;" data-i18n="saveConfigButton"></button></div>
    </div>
    
    <footer>
        <p>Made with ❤️ by <a href="https://relayted.de" target="_blank" rel="noopener noreferrer">relayted.de</a></p>
    </footer>
<script>
    const translations = {
        de: {
            title:"Nostr Relay Admin-Panel by relayted.de", tabDashboard:"Dashboard", tabEvents:"Events", tabStream:"Live Stream", tabBanned:"Gesperrte", tabConfig:"Konfiguration",
            statTotalEvents:"Events Gesamt", statUniqueUsers:"Eind. Nutzer", statBannedUsers: "Gesperrte Nutzer", statEvents24h:"Events (24h)", statEvents1h:"Events (1h)",
            statNewUsers24h:"Neue Nutzer (24h)", statDmPercentage:"Verschl. DMs", statDbSize:"DB Größe", statOldestEvent:"Ältestes Event",
            titleTopKinds:"Top 5 Event-Arten", titleTopUsers:"Top 5 Aktivste Nutzer",
            colKind:"Art", colCount:"Anzahl", colPubkey:"Pubkey", colTime: "Zeit", colContent:"Inhalt", colActions:"Aktionen", colActionsLive:"Aktionen",
            actionCopy:"Pubkey Kopieren", actionView:"Profil ansehen", deleteAction:"Löschen", banAction:"Sperren", unbanAction:"Entsperren",
            copied:"Kopiert!", error:"Fehler", success:"Erfolg", streamTitle:"Live Event Stream", bannedListTitle:"Gesperrte Nutzer",
            relayConfig: "Relay Konfiguration", saveConfigButton: "Speichern", eventSearchPlaceholder: "Suche...",
            confirmDelete:"Event löschen?", confirmBan:"Nutzer sperren?", confirmUnban:"Nutzer entsperren?", confirmConfigSave:"Konfiguration speichern?"
        },
        en: {
            title:"Nostr Relay Admin Panel by relayted.de", tabDashboard:"Dashboard", tabEvents:"Events", tabStream:"Live Stream", tabBanned:"Banned", tabConfig:"Configuration",
            statTotalEvents:"Total Events", statUniqueUsers:"Unique Users", statBannedUsers: "Banned Users", statEvents24h:"Events (24h)", statEvents1h:"Events (1h)",
            statNewUsers24h:"New Users (24h)", statDmPercentage:"Encrypted DMs", statDbSize:"DB Size", statOldestEvent:"Oldest Event",
            titleTopKinds:"Top 5 Event Kinds", titleTopUsers:"Top 5 Busiest Users",
            colKind:"Kind", colCount:"Count", colPubkey:"Pubkey", colTime: "Time", colContent:"Content", colActions:"Actions", colActionsLive:"Actions",
            actionCopy:"Copy Pubkey", actionView:"View Profile", deleteAction:"Delete", banAction:"Ban", unbanAction:"Unban",
            copied:"Copied!", error:"Error", success:"Success", streamTitle:"Live Event Stream", bannedListTitle:"Banned Users",
            relayConfig: "Relay Configuration", saveConfigButton: "Save", eventSearchPlaceholder: "Search...",
            confirmDelete:"Delete event?", confirmBan:"Ban user?", confirmUnban:"Unban user?", confirmConfigSave:"Save configuration?"
        }
    };
    let currentLang = 'de';
    
    document.addEventListener('DOMContentLoaded', () => {
        const setLanguage = (lang) => {
            currentLang = lang;
            document.documentElement.lang = lang;
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (translations[lang]?.[key]) el.textContent = translations[lang][key];
            });
            document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
                const key = el.getAttribute('data-i18n-placeholder');
                if (translations[lang]?.[key]) el.placeholder = translations[lang][key];
            });
            document.title = translations[lang].title || "Nostr Admin";
        };
        
        const apiCall = async (endpoint, options = {}) => {
            const response = await fetch(endpoint, options);
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            return response.json();
        };
        const escapeHtml = (text) => (typeof text=='string' ? text.replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'})[m]) : '');
        const renderNoteContent = (content) => (typeof content=='string' ? escapeHtml(content).replace(/(https?:\/\/[^\\s]+\\.(?:jpg|jpeg|png|gif|webp|avif))/gi, url => `<br><a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" loading="lazy"></a>`) : '');
        
        async function loadDashboard() {
            try {
                const stats = await apiCall('/api/stats');
                document.getElementById('stats-total-events').textContent = stats.total_events?.toLocaleString() || '0';
                document.getElementById('stats-distinct-pubkeys').textContent = stats.distinct_pubkeys?.toLocaleString() || '0';
                document.getElementById('stats-banned-pubkeys').textContent = stats.banned_pubkeys?.toLocaleString() || '0';
                document.getElementById('stats-events-24h').textContent = stats.events_24h?.toLocaleString() || '0';
                document.getElementById('stats-events-1h').textContent = stats.events_1h?.toLocaleString() || '0';
                document.getElementById('stats-new-users-24h').textContent = stats.new_users_24h?.toLocaleString() || '0';
                document.getElementById('stats-dm-percentage').textContent = `${stats.dm_percentage || 0}%`;
                document.getElementById('stats-db-size').textContent = stats.db_size;
                document.getElementById('stats-oldest-event').textContent = stats.oldest_event_date;
                
                const kindsTable = document.querySelector('#top-kinds-table');
                kindsTable.innerHTML = `<thead><tr><th data-i18n="colKind"></th><th data-i18n="colCount"></th></tr></thead><tbody>` + stats.top_kinds.map(k => `<tr><td>Kind ${k.kind}</td><td>${k.count.toLocaleString()}</td></tr>`).join('') + `</tbody>`;
                const usersTable = document.querySelector('#top-users-table');
                usersTable.innerHTML = `<thead><tr><th data-i18n="colPubkey"></th><th data-i18n="colCount"></th></tr></thead><tbody>` + stats.top_users.map(u => `<tr><td><a href="#" onclick="viewProfile('${u.pubkey}'); return false;">${u.pubkey.substring(0,15)}...</a></td><td>${u.count.toLocaleString()}</td></tr>`).join('') + `</tbody>`;
                setLanguage(currentLang);
            } catch(e) { console.error("Dashboard Error:", e); }
        }

        async function loadEvents(query = '') {
            const table = document.querySelector('#events-table');
            table.innerHTML = `<tbody><tr><td colspan="5" style="text-align:center;">Loading...</td></tr></tbody>`;
            try {
                const data = await apiCall(`/api/events?q=${encodeURIComponent(query)}`);
                if (data.error) throw new Error(data.error);
                let tableHTML = `<thead><tr><th data-i18n="colTime"></th><th data-i18n="colPubkey"></th><th data-i18n="colKind"></th><th data-i18n="colContent"></th><th data-i18n="colActions"></th></tr></thead><tbody>`;
                tableHTML += data.map(e => `
                    <tr>
                        <td>${new Date(e.created_at * 1000).toLocaleString()}</td>
                        <td>${e.pubkey.substring(0,10)}...</td>
                        <td>${e.kind}</td>
                        <td><div class="note-content">${renderNoteContent(e.content)}</div></td>
                        <td class="actions-cell">
                            <button onclick="copyPubkey(this, '${e.pubkey}')" data-i18n="actionCopy"></button>
                            <button class="secondary" onclick="viewProfile('${e.pubkey}')" data-i18n="actionView"></button>
                            <button class="danger" onclick="deleteEvent(${e.id})" data-i18n="deleteAction"></button>
                            <button class="danger" onclick="banUser('${e.pubkey}')" data-i18n="banAction"></button>
                        </td>
                    </tr>`).join('');
                table.innerHTML = tableHTML + `</tbody>`;
                 setLanguage(currentLang);
            } catch (e) {
                 table.innerHTML = `<tbody><tr><td colspan="5" style="text-align:center;">${translations[currentLang].error}</td></tr></tbody>`;
                 setLanguage(currentLang);
            }
        }
        
        let liveStreamSocket;
        function startLiveStream() {
            const table = document.querySelector('#stream-table');
            table.innerHTML = `<thead><tr><th data-i18n="colTime"></th><th data-i18n="colPubkey"></th><th data-i18n="colKind"></th><th data-i18n="colContent"></th><th data-i18n="colActionsLive"></th></tr></thead><tbody></tbody>`;
            const tbody = table.querySelector('tbody');
            setLanguage(currentLang);
            
            liveStreamSocket = new WebSocket("{{ relay_websocket_url }}");
            liveStreamSocket.onopen = () => liveStreamSocket.send(JSON.stringify(["REQ", `admin-stream-${Math.random()}`, {}]));
            liveStreamSocket.onmessage = (msg) => {
                try {
                    const [type, , event] = JSON.parse(msg.data);
                    if (type === "EVENT" && event && event.pubkey) {
                        const row = tbody.insertRow(0);
                        row.innerHTML = `
                            <td>${new Date(event.created_at * 1000).toLocaleString()}</td>
                            <td>${event.pubkey.substring(0,10)}...</td>
                            <td>${event.kind}</td>
                            <td><div class="note-content">${renderNoteContent(event.content)}</div></td>
                            <td class="actions-cell">
                                <button onclick="copyPubkey(this, '${event.pubkey}')">${translations[currentLang].actionCopy}</button>
                                <button class="secondary" onclick="viewProfile('${event.pubkey}')">${translations[currentLang].actionView}</button>
                                <button class="danger" onclick="banUser('${event.pubkey}')">${translations[currentLang].banAction}</button>
                            </td>`;
                        if(tbody.rows.length > 200) tbody.deleteRow(-1);
                    }
                } catch(e) { /* ignore */ }
            };
        }
    
        async function loadBannedUsers() {
            const list = document.getElementById('banned-list');
            try {
                const users = await apiCall('/api/banned');
                list.innerHTML = '';
                users.forEach(u => {
                    const li = document.createElement('li');
                    li.innerHTML = `<span>${u}</span> <button class="danger" onclick="unbanUser('${u}')" data-i18n="unbanAction"></button>`;
                    list.appendChild(li);
                });
            } catch(e) { list.innerHTML = translations[currentLang].error; }
            setLanguage(currentLang);
        }

        async function loadConfig() {
            try {
                const data = await apiCall('/api/config');
                document.getElementById('config-editor').value = data.content;
            } catch (e) {
                document.getElementById('config-editor').value = `Error: ${e.message}`;
            }
        }
        
        document.getElementById('save-config-btn').addEventListener('click', async () => {
            if (confirm(translations[currentLang].confirmConfigSave)) {
                await apiCall('/api/config', { method: 'POST', body: JSON.stringify({ content: document.getElementById('config-editor').value }), headers: {'Content-Type': 'application/json'} });
            }
        });

        window.copyPubkey = (btn, pubkey) => {
            if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(pubkey).then(() => {
                    const originalText = btn.textContent;
                    btn.textContent = translations[currentLang].copied;
                    setTimeout(() => { btn.textContent = originalText; }, 1500);
                });
            } else {
                prompt("Copy manually:", pubkey);
            }
        };

        window.viewProfile = (pubkey) => {
            if (typeof pubkey === 'string' && pubkey.length === 64) {
                const npub = NostrTools.nip19.npubEncode(pubkey);
                window.open(`https://nosta.me/${npub}`, '_blank');
            }
        };

        window.deleteEvent = async (id) => {
            if (confirm(translations[currentLang].confirmDelete)) {
                await apiCall(`/api/events/${id}`, { method: 'DELETE' });
                loadEvents(document.getElementById('event-search').value);
            }
        };

        window.banUser = async (pubkey) => {
            if(confirm(translations[currentLang].confirmBan)) {
                await apiCall('/api/banned', { method: 'POST', body: JSON.stringify({pubkey}), headers: {'Content-Type': 'application/json'} });
                if(document.getElementById('banned-content').classList.contains('active')) loadBannedUsers();
                loadDashboard();
            }
        };
    
        window.unbanUser = async (pubkey) => {
            if (confirm(translations[currentLang].confirmUnban)) {
                await apiCall(`/api/banned/${pubkey}`, { method: 'DELETE' });
                loadBannedUsers(); loadDashboard();
            }
        };

        const docBody = document.body;
        const colorBoxes = document.querySelectorAll('.color-box');
        const themeToggleButton = document.getElementById('theme-toggle-btn');
        const tabs = document.querySelectorAll('.tab-button');
        const tabContents = document.querySelectorAll('.tab-content');

        const applyTheme = (mode, color) => {
            docBody.dataset.themeMode = mode;
            docBody.dataset.themeColor = color;
            localStorage.setItem('themeMode', mode);
            localStorage.setItem('colorTheme', color);
            colorBoxes.forEach(b => b.classList.remove('active'));
            document.querySelector(`.color-box[data-color="${color}"]`)?.classList.add('active');
        };
        
        const savedMode = localStorage.getItem('themeMode') || 'light';
        const savedColor = localStorage.getItem('colorTheme') || 'blue';
        
        themeToggleButton.addEventListener('click', () => {
             const newMode = docBody.dataset.themeMode === 'light' ? 'dark' : 'light';
             applyTheme(newMode, docBody.dataset.themeColor);
        });
        
        colorBoxes.forEach(box => {
            box.addEventListener('click', () => applyTheme(docBody.dataset.themeMode, box.dataset.color));
        });
        
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetId = tab.getAttribute('data-tab');
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                tabContents.forEach(c => c.classList.remove('active'));
                document.getElementById(`${targetId}-content`).classList.add('active');
                
                if (liveStreamSocket && targetId !== 'stream') {
                    liveStreamSocket.close(); liveStreamSocket = null;
                }
                switch(targetId) {
                    case 'dashboard': loadDashboard(); break;
                    case 'events': loadEvents(); break;
                    case 'stream': startLiveStream(); break;
                    case 'banned': loadBannedUsers(); break;
                    case 'config': loadConfig(); break;
                }
                setLanguage(currentLang);
            });
        });

        document.getElementById('event-search').addEventListener('input', (e) => loadEvents(e.target.value));

        applyTheme(savedMode, savedColor);
        setLanguage('de');
        loadDashboard();
    });
</script>
</body>
</html>
"""

# --- Hauptausführung ---
if __name__ == '__main__':
    setup_database()
    app.run(host='0.0.0.0', port=5111, debug=True)
