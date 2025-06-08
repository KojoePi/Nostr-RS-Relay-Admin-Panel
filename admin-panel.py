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
RELAY_WEBSOCKET_URL = "wss://your-relay.here"

# 4. Geheimer Schlüssel für die Flask-Session (Im Terminal mit openssl rand -hex 32)
SECRET_SESSION_KEY = 'replacewithsecureone'

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
    """Formatiert Bytes in ein lesbares Format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{round(size_bytes / 1024, 2)} KB"
    elif size_bytes < 1024**3:
        return f"{round(size_bytes / (1024**2), 2)} MB"
    else:
        return f"{round(size_bytes / (1024**3), 2)} GB"

# --- API Endpunkte ---

@app.route('/api/stats')
def get_stats():
    conn, conn_rw = None, None
    stats = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Zeitstempel für Abfragen
        now_ts = int(datetime.now().timestamp())
        ts_24h_ago = now_ts - (24 * 3600)
        ts_1h_ago = now_ts - 3600

        # Basis-Statistiken
        stats['total_events'] = cursor.execute('SELECT COUNT(*) FROM event').fetchone()[0] or 0
        stats['distinct_pubkeys'] = cursor.execute('SELECT COUNT(DISTINCT author) FROM event').fetchone()[0] or 0
        
        # Aktivitäts-Statistiken
        stats['events_24h'] = cursor.execute('SELECT COUNT(*) FROM event WHERE created_at > ?', (ts_24h_ago,)).fetchone()[0] or 0
        stats['events_1h'] = cursor.execute('SELECT COUNT(*) FROM event WHERE created_at > ?', (ts_1h_ago,)).fetchone()[0] or 0
        stats['new_users_24h'] = cursor.execute("SELECT COUNT(*) FROM (SELECT MIN(created_at) as first_seen FROM event GROUP BY author) WHERE first_seen > ?", (ts_24h_ago,)).fetchone()[0] or 0
        
        # Inhalts-Statistiken
        stats['top_kinds'] = [dict(row) for row in cursor.execute("SELECT kind, COUNT(*) as count FROM event GROUP BY kind ORDER BY count DESC LIMIT 5").fetchall()]
        dm_count = cursor.execute("SELECT COUNT(*) FROM event WHERE kind = 4").fetchone()[0] or 0
        stats['dm_percentage'] = round((dm_count / stats['total_events']) * 100, 2) if stats['total_events'] > 0 else 0

        # Nutzer-Statistiken
        top_users_query = cursor.execute("SELECT lower(hex(author)) as pubkey, COUNT(*) as count FROM event GROUP BY author ORDER BY count DESC LIMIT 5").fetchall()
        stats['top_users'] = [dict(row) for row in top_users_query]

        # System-Statistiken
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
        print(f"Database error in get_events: {e}")
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
    return jsonify({"status": "success", "message": f"Event {event_db_id} deleted."})

@app.route('/api/banned', methods=['GET', 'POST'])
def handle_banned_users():
    conn_rw = get_db_connection_rw()
    try:
        if request.method == 'POST':
            pubkey_to_ban = request.json.get('pubkey')
            if pubkey_to_ban and len(pubkey_to_ban) == 64:
                try:
                    conn_rw.execute('INSERT INTO banned_pubkeys (pubkey) VALUES (?)', (pubkey_to_ban,))
                    conn_rw.commit()
                except sqlite3.IntegrityError:
                   return jsonify({"status": "error", "message": "Pubkey is already banned."}), 400
                return jsonify({"status": "success", "message": f"Pubkey {pubkey_to_ban[:8]}... banned."})
            return jsonify({"status": "error", "message": "Invalid pubkey."}), 400
        else:
            banned_cursor = conn_rw.execute('SELECT pubkey FROM banned_pubkeys ORDER BY banned_at DESC')
            return jsonify([dict(row) for row in banned_cursor.fetchall()])
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
    return jsonify({"status": "success", "message": f"Pubkey {pubkey[:8]}... unbanned."})

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    try:
        if request.method == 'POST':
            new_content = request.json.get('content')
            with open(CONFIG_PATH, 'w') as f:
                f.write(new_content)
            return jsonify({"status": "success", "message": "Configuration saved. Relay restart might be required."})
        else:
            with open(CONFIG_PATH, 'r') as f:
                content = f.read()
            return jsonify({"content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, relay_websocket_url=RELAY_WEBSOCKET_URL)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nostr Relay Admin</title>
    <script src="https://unpkg.com/nostr-tools@2/lib/nostr.js"></script>
    <style>
        :root {
            --bg-color: #f8f9fa; --text-color: #212529; --primary-color: #007bff; --primary-hover: #0056b3;
            --border-color: #dee2e6; --card-bg: #ffffff; --shadow: 0 4px 6px rgba(0,0,0,0.07);
            --error: #dc3545; --success: #28a745; --warn: #ffc107; --secondary: #6c757d;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; background-color: var(--bg-color); color: var(--text-color); }
        .container { max-width: 1400px; margin: 2rem auto; padding: 1.5rem 2rem; background-color: var(--card-bg); border-radius: 8px; box-shadow: var(--shadow); }
        header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
        header h1 { margin: 0; }
        #top-controls { display: flex; align-items: center; gap: 1rem; }
        button { padding: 8px 14px; font-size: 0.9rem; cursor: pointer; border: 1px solid var(--border-color); background-color: #fff; color: var(--text-color); border-radius: 5px; transition: all 0.2s; }
        button:not([disabled]):hover { background-color: #f1f3f5; }
        button.primary { background-color: var(--primary-color); color: white; border-color: var(--primary-color); }
        button.primary:not([disabled]):hover { background-color: var(--primary-hover); }
        button.danger { background-color: var(--error); color: white; border-color: var(--error); }
        button.secondary { background-color: var(--secondary); color: white; border-color: var(--secondary); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .message { padding: 1rem; border-radius: 5px; margin-top: 1rem; border: 1px solid; }
        .message.warn { color: #856404; background-color: #fff3cd; border-color: #ffeeba; }
        nav.tabs { border-bottom: 2px solid var(--border-color); margin-bottom: 1.5rem; }
        nav.tabs button { background: none; border: none; border-bottom: 3px solid transparent; padding: 1rem 1.5rem; cursor: pointer; color: #6c757d; border-radius: 0; font-size: 1rem; margin-bottom: -2px; }
        nav.tabs button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; table-layout: fixed; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border-color); word-break: break-word; vertical-align: top; }
        th { background-color: var(--bg-color); }
        td div.note-content { white-space: pre-wrap; margin: 0; font-family: inherit; font-size: 0.9em; max-height: 250px; overflow-y: auto; }
        td img { max-width: 100%; height: auto; max-height: 200px; border-radius: 8px; margin-top: 5px; display: block; }
        .actions-cell { display: flex; flex-direction: column; gap: 5px; width: 160px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
        .stat-card { background: var(--bg-color); padding: 1.5rem; border-radius: 8px; text-align: center; }
        .stat-card .value { font-size: 2.5rem; font-weight: bold; color: var(--primary-color); }
        .stat-card .label { font-size: 1rem; color: #6c757d; }
        .dashboard-section { margin-top: 2rem; }
        #config-editor { width: 100%; min-height: 60vh; font-family: monospace; font-size: 14px; line-height: 1.5; }
        #banned-list { list-style: none; padding: 0; }
        #banned-list li { display: flex; justify-content: space-between; align-items: center; padding: 8px; background: #f1f3f5; border-radius: 4px; margin-bottom: 5px; font-family: monospace; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1 data-i18n="title">Nostr Relay Admin</h1>
            <div id="top-controls">
                <button id="lang-switcher">DE/EN</button>
            </div>
        </header>

        <div id="admin-view">
            <nav class="tabs">
                <button class="tab-button active" data-tab="dashboard" data-i18n="tabDashboard">Dashboard</button>
                <button class="tab-button" data-tab="events" data-i18n="tabEvents">Events</button>
                <button class="tab-button" data-tab="stream" data-i18n="tabStream">Live Stream</button>
                <button class="tab-button" data-tab="banned" data-i18n="tabBanned">Banned Users</button>
                <button class="tab-button" data-tab="config" data-i18n="tabConfig">Configuration</button>
            </nav>
            <!-- ########## START DASHBOARD CONTENT ########## -->
            <div id="dashboard-content" class="tab-content active">
                <div class="stats-grid">
                    <div class="stat-card"><div id="stats-total-events" class="value">...</div><div class="label" data-i18n="statTotalEvents">Total Events</div></div>
                    <div class="stat-card"><div id="stats-distinct-pubkeys" class="value">...</div><div class="label" data-i18n="statUniqueUsers">Unique Users</div></div>
                    <div class="stat-card"><div id="stats-events-24h" class="value">...</div><div class="label" data-i18n="statEvents24h">Events (24h)</div></div>
                    <div class="stat-card"><div id="stats-events-1h" class="value">...</div><div class="label" data-i18n="statEvents1h">Events (1h)</div></div>
                    <div class="stat-card"><div id="stats-new-users-24h" class="value">...</div><div class="label" data-i18n="statNewUsers24h">New Users (24h)</div></div>
                    <div class="stat-card"><div id="stats-dm-percentage" class="value">...</div><div class="label" data-i18n="statDmPercentage">Encrypted DMs</div></div>
                    <div class="stat-card"><div id="stats-db-size" class="value">...</div><div class="label" data-i18n="statDbSize">DB Size</div></div>
                    <div class="stat-card"><div id="stats-oldest-event" class="value">...</div><div class="label" data-i18n="statOldestEvent">Oldest Event</div></div>
                </div>

                <div class="stats-grid dashboard-section" style="grid-template-columns: 1fr 1fr; gap: 2rem;">
                    <div>
                        <h3 data-i18n="titleTopKinds">Top 5 Event Kinds</h3>
                        <table id="top-kinds-table">
                            <thead><tr><th data-i18n="colKind">Kind</th><th data-i18n="colCount">Count</th></tr></thead>
                            <tbody></tbody>
                        </table>
                    </div>
                    <div>
                        <h3 data-i18n="titleTopUsers">Top 5 Busiest Users</h3>
                        <table id="top-users-table">
                             <thead><tr><th data-i18n="colPubkey">Pubkey</th><th data-i18n="colCount">Event Count</th></tr></thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>
            <!-- ########## END DASHBOARD CONTENT ########## -->

            <div id="events-content" class="tab-content">
                <input type="text" id="event-search" placeholder="Search" data-i18n-placeholder="eventSearchPlaceholder" style="margin-bottom: 1rem;">
                <table id="events-table">
                    <thead><tr><th data-i18n="colTime">Time</th><th data-i18n="colPubkey">Pubkey</th><th data-i18n="colKind">Kind</th><th data-i18n="colContent">Content</th><th data-i18n="colActions">Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="stream-content" class="tab-content">
                <h3 data-i18n="streamTitle">Live Event Stream from {{ relay_websocket_url }}</h3>
                <table id="stream-table">
                     <thead><tr><th data-i18n="colTime">Time</th><th data-i18n="colPubkey">Pubkey</th><th data-i18n="colKind">Kind</th><th data-i18n="colContent">Content</th><th data-i18n="colActionsLive">Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="banned-content" class="tab-content">
                <h3 data-i18n="bannedListTitle">Banned Pubkeys</h3>
                <ul id="banned-list"></ul>
            </div>
            <div id="config-content" class="tab-content">
                <div class="message warn" data-i18n="configWarning">Warning: Editing this file is dangerous. A mistake can stop your relay. Make a backup before saving. A relay restart might be needed for changes to take effect.</div>
                <textarea id="config-editor"></textarea>
                <button id="save-config-btn" class="primary" style="margin-top: 1rem;" data-i18n="saveConfigButton">Save Configuration</button>
            </div>
        </div>
    </div>
<script>
const translations = {
    en: {
        title: "Nostr Relay Admin", tabDashboard: "Dashboard", tabEvents: "Events", tabStream: "Live Stream", tabBanned: "Banned Users", tabConfig: "Configuration",
        statTotalEvents: "Total Events", statUniqueUsers: "Unique Users", statBannedUsers: "Banned Users",
        statEvents24h: "Events (24h)", statEvents1h: "Events (1h)", statNewUsers24h: "New Users (24h)",
        statDmPercentage: "Encrypted DMs", statDbSize: "DB Size", statOldestEvent: "Oldest Event",
        titleTopKinds: "Top 5 Event Kinds", titleTopUsers: "Top 5 Busiest Users",
        colCount: "Count",
        eventSearchPlaceholder: "Search by pubkey, event ID, or content...",
        colTime: "Time", colPubkey: "Pubkey", colKind: "Kind", colContent: "Content", colActions: "Actions", colActionsLive: "Actions",
        deleteAction: "Delete", banAction: "Ban", unbanAction: "Unban", actionCopy: "Copy Pubkey", actionView: "View Profile",
        streamTitle: "Live Event Stream from {{ relay_websocket_url }}", bannedListTitle: "Banned Pubkeys",
        configWarning: "Warning: Editing this file is dangerous. A mistake can stop your relay. Make a backup before saving. A relay restart might be needed for changes to take effect.",
        saveConfigButton: "Save Configuration",
        error: "Error:", success: "Success", copied: "Copied!",
        confirmDelete: "Are you sure you want to delete this event?", confirmBan: "Are you sure you want to ban this pubkey?",
        confirmUnban: "Are you sure you want to unban this pubkey?", confirmConfigSave: "Are you sure you want to save the configuration? This could break your relay if incorrect."
    },
    de: {
        title: "Nostr Relay Admin-Panel", tabDashboard: "Dashboard", tabEvents: "Events", tabStream: "Live-Stream", tabBanned: "Gesperrte Nutzer", tabConfig: "Konfiguration",
        statTotalEvents: "Events gesamt", statUniqueUsers: "Eind. Nutzer", statBannedUsers: "Gesperrte Nutzer",
        statEvents24h: "Events (24h)", statEvents1h: "Events (1h)", statNewUsers24h: "Neue Nutzer (24h)",
        statDmPercentage: "Verschl. DMs", statDbSize: "DB Größe", statOldestEvent: "Ältestes Event",
        titleTopKinds: "Top 5 Event-Arten", titleTopUsers: "Top 5 Aktivste Nutzer",
        colCount: "Anzahl",
        eventSearchPlaceholder: "Suche nach Pubkey, Event-ID oder Inhalt...",
        colTime: "Zeit", colPubkey: "Pubkey", colKind: "Art", colContent: "Inhalt", colActions: "Aktionen", colActionsLive: "Aktionen",
        deleteAction: "Löschen", banAction: "Sperren", unbanAction: "Entsperren", actionCopy: "Pubkey kopieren", actionView: "Profil ansehen",
        streamTitle: "Live-Event-Stream von {{ relay_websocket_url }}", bannedListTitle: "Gesperrte Pubkeys",
        configWarning: "Warnung: Das Bearbeiten dieser Datei ist gefährlich. Ein Fehler kann dein Relay lahmlegen. Erstelle vor dem Speichern ein Backup. Ein Neustart des Relays könnte für die Übernahme der Änderungen nötig sein.",
        saveConfigButton: "Konfiguration speichern",
        error: "Fehler:", success: "Erfolg", copied: "Kopiert!",
        confirmDelete: "Möchtest du dieses Event wirklich löschen?", confirmBan: "Möchtest du diesen Pubkey wirklich sperren?",
        confirmUnban: "Möchtest du diesen Pubkey wirklich entsperren?", confirmConfigSave: "Möchtest du die Konfiguration wirklich speichern? Dies kann bei Fehlern dein Relay unbrauchbar machen."
    }
};
let currentLang = 'de';
const setLanguage = (lang) => {
    currentLang = lang;
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (translations[lang] && translations[lang][key]) {
            el.innerHTML = translations[lang][key];
        }
    });
};
document.addEventListener('DOMContentLoaded', () => {
    const tabs = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');
    document.getElementById('lang-switcher').addEventListener('click', () => setLanguage(currentLang === 'en' ? 'de' : 'en'));
    
    let liveStreamSocket;
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            tabContents.forEach(c => c.classList.remove('active'));
            document.getElementById(`${tab.dataset.tab}-content`).classList.add('active');
            if (liveStreamSocket && tab.dataset.tab !== 'stream') {
                liveStreamSocket.close(); liveStreamSocket = null;
            }
            switch(tab.dataset.tab) {
                case 'dashboard': loadDashboard(); break;
                case 'events': loadEvents(); break;
                case 'stream': startLiveStream(); break;
                case 'banned': loadBannedUsers(); break;
                case 'config': loadConfig(); break;
            }
        });
    });

    const apiCall = async (endpoint, options = {}) => {
        const response = await fetch(endpoint, options);
        if (!response.ok) throw new Error(`API call to ${endpoint} failed: ${response.statusText}`);
        return response.json();
    };
    
    const escapeHtml = (text) => (typeof text === 'string' ? text.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'})[m]) : '');
    const renderNoteContent = (content) => (typeof content === 'string' ? escapeHtml(content).replace(/(https?:\/\/[^\s]+\.(?:jpg|jpeg|png|gif|webp|avif))/gi, url => `<br><a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="nostr image" loading="lazy"></a>`) : '');

    async function loadDashboard() {
        try {
            const stats = await apiCall('/api/stats');
            if (stats.error) throw new Error(stats.error);
            // Populate stat cards
            document.getElementById('stats-total-events').textContent = stats.total_events || '0';
            document.getElementById('stats-distinct-pubkeys').textContent = stats.distinct_pubkeys || '0';
            document.getElementById('stats-events-24h').textContent = stats.events_24h || '0';
            document.getElementById('stats-events-1h').textContent = stats.events_1h || '0';
            document.getElementById('stats-new-users-24h').textContent = stats.new_users_24h || '0';
            document.getElementById('stats-dm-percentage').textContent = `${stats.dm_percentage}%`;
            document.getElementById('stats-db-size').textContent = stats.db_size || 'N/A';
            document.getElementById('stats-oldest-event').textContent = stats.oldest_event_date || 'N/A';

            // Populate top kinds table
            const kindsTbody = document.querySelector('#top-kinds-table tbody');
            kindsTbody.innerHTML = '';
            stats.top_kinds.forEach(k => {
                const row = kindsTbody.insertRow();
                row.innerHTML = `<td>${k.kind}</td><td>${k.count}</td>`;
            });

            // Populate top users table
            const usersTbody = document.querySelector('#top-users-table tbody');
            usersTbody.innerHTML = '';
            stats.top_users.forEach(u => {
                const row = usersTbody.insertRow();
                const pubkeyCell = row.insertCell();
                pubkeyCell.innerHTML = `<a href="javascript:viewProfile('${u.pubkey}')">${u.pubkey.substring(0,10)}...</a>`;
                const countCell = row.insertCell();
                countCell.textContent = u.count;
            });

        } catch (e) { console.error("Failed to load dashboard stats:", e); }
    }

    async function loadEvents(query = '') {
        const tbody = document.querySelector('#events-table tbody');
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;">Loading...</td></tr>`;
        try {
            const data = await apiCall(`/api/events?q=${encodeURIComponent(query)}`);
            if (data.error) throw new Error(data.error);
            tbody.innerHTML = '';
            for (const e of data) {
                const row = tbody.insertRow();
                row.innerHTML = `
                    <td>${new Date(e.created_at * 1000).toLocaleString()}</td>
                    <td>${e.pubkey.substring(0,10)}...</td>
                    <td>${e.kind}</td>
                    <td><div class="note-content">${renderNoteContent(e.content)}</div></td>
                    <td class="actions-cell">
                        <button onclick="copyPubkey(this, '${e.pubkey}')">${translations[currentLang].actionCopy}</button>
                        <button class="secondary" onclick="viewProfile('${e.pubkey}')">${translations[currentLang].actionView}</button>
                        <button class="danger" onclick="deleteEvent(${e.id})">${translations[currentLang].deleteAction}</button>
                        <button class="danger" onclick="banUser('${e.pubkey}')">${translations[currentLang].banAction}</button>
                    </td>
                `;
            }
        } catch (e) {
             tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;" class="error">${translations[currentLang].error} Daten konnten nicht geladen werden.</td></tr>`;
        }
    }

    function startLiveStream() {
        const tbody = document.querySelector('#stream-table tbody');
        tbody.innerHTML = '';
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
                        </td>
                    `;
                    if(tbody.rows.length > 200) tbody.deleteRow(-1);
                }
            } catch(e) { console.error('Error parsing stream message:', msg.data); }
        };
        liveStreamSocket.onerror = (err) => console.error("WebSocket Error:", err);
    }
    
    async function loadBannedUsers() {
        try {
            const users = await apiCall('/api/banned');
            const list = document.getElementById('banned-list');
            list.innerHTML = '';
            users.forEach(u => {
                const li = document.createElement('li');
                li.innerHTML = `<span>${u.pubkey}</span> <button class="danger" onclick="unbanUser('${u.pubkey}')">${translations[currentLang].unbanAction}</button>`;
                list.appendChild(li);
            });
        } catch(e) { console.error("Failed to load banned users:", e); }
    }

    async function loadConfig() {
        try {
            const data = await apiCall('/api/config');
            document.getElementById('config-editor').value = data.content;
        } catch (e) {
            document.getElementById('config-editor').value = `Error loading config: ${e.message}\\n\\nPlease check the CONFIG_PATH and file permissions.`;
        }
    }
    
    document.getElementById('save-config-btn').addEventListener('click', async () => {
        if (!confirm(translations[currentLang].confirmConfigSave)) return;
        const result = await apiCall('/api/config', { method: 'POST', body: JSON.stringify({ content: document.getElementById('config-editor').value }), headers: {'Content-Type': 'application/json'} });
        alert(`${translations[currentLang].success}: ${result.message}`);
    });

    window.copyPubkey = (btn, pubkey) => {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(pubkey).then(() => {
                const originalText = btn.textContent;
                btn.textContent = translations[currentLang].copied;
                btn.disabled = true;
                setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
            });
        } else {
            prompt("Copy manually (Ctrl+C):", pubkey);
        }
    };

    window.viewProfile = (pubkey) => {
        if (typeof pubkey !== 'string' || pubkey.length !== 64) {
            console.error("Invalid pubkey for viewProfile:", pubkey); return;
        }
        try {
            const npub = NostrTools.nip19.npubEncode(pubkey);
            window.open(`https://nosta.me/${npub}`, '_blank');
        } catch(e) { window.open(`https://nosta.me/${pubkey}`, '_blank'); }
    };

    window.deleteEvent = async (id) => {
        if (!confirm(translations[currentLang].confirmDelete)) return;
        await apiCall(`/api/events/${id}`, { method: 'DELETE' });
        loadEvents(document.getElementById('event-search').value);
    }

    window.banUser = async (pubkey) => {
        if (!confirm(translations[currentLang].confirmBan)) return;
        try {
            const result = await apiCall('/api/banned', { method: 'POST', body: JSON.stringify({pubkey}), headers: {'Content-Type': 'application/json'} });
             alert(result.message);
            if (document.getElementById('banned-content').classList.contains('active')) loadBannedUsers();
            loadDashboard();
        } catch (e) { alert(`${translations[currentLang].error}: ${e.message}`); }
    }
    
    window.unbanUser = async (pubkey) => {
        if (!confirm(translations[currentLang].confirmUnban)) return;
        const result = await apiCall(`/api/banned/${pubkey}`, { method: 'DELETE' });
        alert(result.message);
        loadBannedUsers();
        loadDashboard();
    }

    setLanguage('de');
    loadDashboard();
    document.getElementById('event-search').addEventListener('input', (e) => loadEvents(e.target.value));
});
</script>
</body>
</html>
"""

# --- Hauptausführung ---
if __name__ == '__main__':
    setup_database()
    app.run(host='0.0.0.0', port=5111, debug=True)
