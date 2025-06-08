import sqlite3
import json
import uuid
import toml
from flask import Flask, request, jsonify, render_template_string, session
from flask_cors import CORS
from datetime import timedelta


# ==============================================================================
# ===== KONFIGURATION (BITTE SORGFÄLTIG ANPASSEN) ==============================
# ==============================================================================

# 1. Pfad zur Datenbank deines nostr-rs-relay
DATABASE_PATH = "/root/relay/data/relay/nostr.db"

# 2. Pfad zur Konfigurationsdatei deines nostr-rs-relay
CONFIG_PATH = "/root/relay/config/config.toml"

# 3. WebSocket URL deines Relays
RELAY_WEBSOCKET_URL = "wss://free.relayted.de"

# 4. Geheimer Schlüssel für die Flask-Session (weniger wichtig ohne Login)
SECRET_SESSION_KEY = 'ae89fe77832801b49ac5eac28ac2a637b323fafef2995eb9fc0ea845b9ae8a6f'

# ==============================================================================
# ===== ENDE DER KONFIGURATION =================================================
# ==============================================================================

app = Flask(__name__)
app.secret_key = SECRET_SESSION_KEY
app.permanent_session_lifetime = timedelta(hours=8)
CORS(app)

# --- Hilfsfunktionen ---
def get_db_connection():
    try:
        conn = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
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

def check_auth():
    """
    Authentifizierungsprüfung wird übersprungen.
    Jeder hat Zugriff. SICHERE DAS PANEL ÜBER DEINE FIREWALL AB!
    """
    return True

# --- API Endpunkte ---

@app.route('/api/stats')
def get_stats():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db_connection()
    banned_count = 0
    total_events = 0
    distinct_pubkeys = 0
    try:
        total_events = conn.execute('SELECT COUNT(*) FROM event').fetchone()[0]
        distinct_pubkeys = conn.execute('SELECT COUNT(DISTINCT pubkey) FROM event').fetchone()[0]
        conn_rw = get_db_connection_rw()
        banned_count = conn_rw.execute('SELECT COUNT(*) FROM banned_pubkeys').fetchone()[0]
        conn_rw.close()
    except Exception:
        # Falls die Tabellen nicht existieren etc.
        pass
    finally:
        if conn:
            conn.close()
    return jsonify({
        "total_events": total_events,
        "distinct_pubkeys": distinct_pubkeys,
        "banned_pubkeys": banned_count,
    })

@app.route('/api/events')
def get_events():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    query = request.args.get('q', '')
    limit = int(request.args.get('limit', 100))
    
    conn = get_db_connection()
    try:
        if query:
            search_query = f'%{query}%'
            cursor = conn.execute(
                'SELECT id, pubkey, kind, content, created_at, event_id FROM event WHERE pubkey LIKE ? OR event_id LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT ?',
                (search_query, search_query, search_query, limit)
            )
        else:
            cursor = conn.execute('SELECT id, pubkey, kind, content, created_at, event_id FROM event ORDER BY created_at DESC LIMIT ?', (limit,))
        events = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    return jsonify(events)

@app.route('/api/events/<event_db_id>', methods=['DELETE'])
def delete_event(event_db_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db_connection_rw()
    try:
        conn.execute('DELETE FROM event WHERE id = ?', (event_db_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success", "message": f"Event {event_db_id} deleted."})

@app.route('/api/banned', methods=['GET', 'POST'])
def handle_banned_users():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    
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
        else: # GET
            banned_cursor = conn_rw.execute('SELECT pubkey FROM banned_pubkeys ORDER BY banned_at DESC')
            return jsonify([dict(row) for row in banned_cursor.fetchall()])
    finally:
        conn_rw.close()

@app.route('/api/banned/<pubkey>', methods=['DELETE'])
def unban_user(pubkey):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db_connection_rw()
    try:
        conn.execute('DELETE FROM banned_pubkeys WHERE pubkey = ?', (pubkey,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success", "message": f"Pubkey {pubkey[:8]}... unbanned."})

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 403
    try:
        if request.method == 'POST':
            new_content = request.json.get('content')
            with open(CONFIG_PATH, 'w') as f:
                f.write(new_content)
            return jsonify({"status": "success", "message": "Configuration saved. Relay restart might be required."})
        else: # GET
            with open(CONFIG_PATH, 'r') as f:
                content = f.read()
            return jsonify({"content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Frontend Rendering ---
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
    <style>
        :root {
            --bg-color: #f8f9fa; --text-color: #212529; --primary-color: #007bff; --primary-hover: #0056b3;
            --border-color: #dee2e6; --card-bg: #ffffff; --shadow: 0 4px 6px rgba(0,0,0,0.07);
            --error: #dc3545; --success: #28a745; --warn: #ffc107;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; background-color: var(--bg-color); color: var(--text-color); }
        .container { max-width: 1400px; margin: 2rem auto; padding: 1.5rem 2rem; background-color: var(--card-bg); border-radius: 8px; box-shadow: var(--shadow); }
        header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
        header h1 { margin: 0; }
        #top-controls { display: flex; align-items: center; gap: 1rem; }
        #lang-switcher, button { padding: 8px 14px; font-size: 0.9rem; cursor: pointer; border: 1px solid var(--border-color); background-color: #fff; color: var(--text-color); border-radius: 5px; transition: all 0.2s; }
        button:hover, #lang-switcher:hover { background-color: #f1f3f5; }
        button.primary { background-color: var(--primary-color); color: white; border-color: var(--primary-color); }
        button.primary:hover { background-color: var(--primary-hover); }
        button.danger { background-color: var(--error); color: white; border-color: var(--error); }
        .view { display: none; }
        .view.active { display: block; }
        .message { padding: 1rem; border-radius: 5px; margin-top: 1rem; border: 1px solid; }
        .message.warn { color: #856404; background-color: #fff3cd; border-color: #ffeeba; }
        nav.tabs { border-bottom: 2px solid var(--border-color); margin-bottom: 1.5rem; }
        nav.tabs button { background: none; border: none; border-bottom: 3px solid transparent; padding: 1rem 1.5rem; cursor: pointer; color: #6c757d; border-radius: 0; font-size: 1rem; margin-bottom: -2px; }
        nav.tabs button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; table-layout: fixed; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border-color); word-break: break-word; vertical-align: top; }
        th { background-color: var(--bg-color); }
        /* Geändert von 'pre' zu 'div' für den Inhalt, damit HTML-Tags wie <img> gerendert werden können */
        td div.note-content { white-space: pre-wrap; margin: 0; font-family: inherit; font-size: 0.9em; max-height: 250px; overflow-y: auto; }
        td img { max-width: 100%; height: auto; max-height: 200px; border-radius: 8px; margin-top: 5px; display: block; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
        .stat-card { background: var(--bg-color); padding: 1.5rem; border-radius: 8px; text-align: center; }
        .stat-card .value { font-size: 2.5rem; font-weight: bold; color: var(--primary-color); }
        .stat-card .label { font-size: 1rem; color: #6c757d; }
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

        <div id="admin-view" class="view active">
            <nav class="tabs">
                <button class="tab-button active" data-tab="dashboard" data-i18n="tabDashboard">Dashboard</button>
                <button class="tab-button" data-tab="events" data-i18n="tabEvents">Events</button>
                <button class="tab-button" data-tab="stream" data-i18n="tabStream">Live Stream</button>
                <button class="tab-button" data-tab="banned" data-i18n="tabBanned">Banned Users</button>
                <button class="tab-button" data-tab="config" data-i18n="tabConfig">Configuration</button>
            </nav>
            <div id="dashboard-content" class="tab-content active">
                <div class="stats-grid">
                    <div class="stat-card"><div id="stats-total-events" class="value">...</div><div class="label" data-i18n="statTotalEvents">Total Events</div></div>
                    <div class="stat-card"><div id="stats-distinct-pubkeys" class="value">...</div><div class="label" data-i18n="statUniqueUsers">Unique Users</div></div>
                    <div class="stat-card"><div id="stats-banned-pubkeys" class="value">...</div><div class="label" data-i18n="statBannedUsers">Banned Users</div></div>
                </div>
            </div>
            <div id="events-content" class="tab-content">
                <input type="text" id="event-search" placeholder="Search by pubkey, event ID, or content..." data-i18n-placeholder="eventSearchPlaceholder" style="margin-bottom: 1rem;">
                <table id="events-table">
                    <thead><tr><th data-i18n="colTime">Time</th><th data-i18n="colPubkey">Pubkey</th><th data-i18n="colKind">Kind</th><th data-i18n="colContent">Content</th><th data-i18n="colActions">Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="stream-content" class="tab-content">
                <h3 data-i18n="streamTitle">Live Event Stream from {{ relay_websocket_url }}</h3>
                <table id="stream-table">
                     <thead><tr><th data-i18n="colTime">Time</th><th data-i18n="colPubkey">Pubkey</th><th data-i18n="colKind">Kind</th><th data-i18n="colContent">Content</th></tr></thead>
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
        eventSearchPlaceholder: "Search by pubkey, event ID, or content...",
        colTime: "Time", colPubkey: "Pubkey", colKind: "Kind", colContent: "Content", colActions: "Actions",
        deleteAction: "Delete", banAction: "Ban", unbanAction: "Unban",
        streamTitle: "Live Event Stream from {{ relay_websocket_url }}", bannedListTitle: "Banned Pubkeys",
        configWarning: "Warning: Editing this file is dangerous. A mistake can stop your relay. Make a backup before saving. A relay restart might be needed for changes to take effect.",
        saveConfigButton: "Save Configuration",
        confirmDelete: "Are you sure you want to delete this event?", confirmBan: "Are you sure you want to ban this pubkey?",
        confirmUnban: "Are you sure you want to unban this pubkey?", confirmConfigSave: "Are you sure you want to save the configuration? This could break your relay if incorrect.",
        error: "Error:", success: "Success",
    },
    de: {
        title: "Nostr Relay Admin-Panel", tabDashboard: "Dashboard", tabEvents: "Events", tabStream: "Live-Stream", tabBanned: "Gesperrte Nutzer", tabConfig: "Konfiguration",
        statTotalEvents: "Events gesamt", statUniqueUsers: "Eind. Nutzer", statBannedUsers: "Gesperrte Nutzer",
        eventSearchPlaceholder: "Suche nach Pubkey, Event-ID oder Inhalt...",
        colTime: "Zeit", colPubkey: "Pubkey", colKind: "Art", colContent: "Inhalt", colActions: "Aktionen",
        deleteAction: "Löschen", banAction: "Sperren", unbanAction: "Entsperren",
        streamTitle: "Live-Event-Stream von {{ relay_websocket_url }}", bannedListTitle: "Gesperrte Pubkeys",
        configWarning: "Warnung: Das Bearbeiten dieser Datei ist gefährlich. Ein Fehler kann dein Relay lahmlegen. Erstelle vor dem Speichern ein Backup. Ein Neustart des Relays könnte für die Übernahme der Änderungen nötig sein.",
        saveConfigButton: "Konfiguration speichern",
        confirmDelete: "Möchtest du dieses Event wirklich löschen?", confirmBan: "Möchtest du diesen Pubkey wirklich sperren?",
        confirmUnban: "Möchtest du diesen Pubkey wirklich entsperren?", confirmConfigSave: "Möchtest du die Konfiguration wirklich speichern? Dies kann bei Fehlern dein Relay unbrauchbar machen.",
        error: "Fehler:", success: "Erfolg",
    }
};
let currentLang = 'en';
const setLanguage = (lang) => {
    currentLang = lang;
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (el.tagName === 'INPUT' && el.placeholder) {
             el.placeholder = translations[lang][key] || el.placeholder;
        } else {
            el.innerHTML = translations[lang][key] || el.innerHTML;
        }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = translations[lang][key] || el.placeholder;
    });
};
document.addEventListener('DOMContentLoaded', () => {
    const tabs = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');
    
    document.getElementById('lang-switcher').addEventListener('click', () => setLanguage(currentLang === 'en' ? 'de' : 'en'));
    setLanguage('de');

    let liveStreamSocket;
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            tabContents.forEach(c => c.classList.remove('active'));
            document.getElementById(`${tab.dataset.tab}-content`).classList.add('active');
            if (liveStreamSocket && tab.dataset.tab !== 'stream') {
                liveStreamSocket.close();
                liveStreamSocket = null;
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
        if (!response.ok) {
            throw new Error(`API call failed: ${response.statusText}`);
        }
        return response.json();
    }
    
    function escapeHtml(text) {
        if (typeof text !== 'string') return '';
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }

    // === NEUE FUNKTION ZUM RENDERN VON NOTIZEN MIT BILDERN ===
    function renderNoteContent(content) {
        if (typeof content !== 'string') return '';

        // Regulärer Ausdruck, der nach URLs sucht, die auf Bilddateiendungen enden.
        const imageUrlRegex = /(https?:\/\/[^\s]+\.(?:jpg|jpeg|png|gif|webp|avif))/gi;
        
        // Ersetze jede gefundene Bild-URL durch ein <img>-Tag.
        // Der restliche Text wird sicher escaped.
        return escapeHtml(content).replace(imageUrlRegex, (url) => {
            // Wichtig: Der `url` Parameter ist die gefundene URL. Da der gesamte String
            // bereits escaped wurde, müssen wir die URL für das src-Attribut nicht
            // erneut escapen. Es ist bereits sicher.
            return `<br><a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="nostr image" loading="lazy"></a>`;
        });
    }

    async function loadDashboard() {
        const stats = await apiCall('/api/stats');
        document.getElementById('stats-total-events').textContent = stats.total_events || '0';
        document.getElementById('stats-distinct-pubkeys').textContent = stats.distinct_pubkeys || '0';
        document.getElementById('stats-banned-pubkeys').textContent = stats.banned_pubkeys || '0';
    }

    async function loadEvents(query = '') {
        const events = await apiCall(`/api/events?q=${encodeURIComponent(query)}`);
        const tbody = document.querySelector('#events-table tbody');
        tbody.innerHTML = '';
        events.forEach(e => {
            const row = tbody.insertRow();
            row.innerHTML = `
                <td>${new Date(e.created_at * 1000).toLocaleString()}</td>
                <td>${e.pubkey.substring(0,10)}...</td>
                <td>${e.kind}</td>
                <td><div class="note-content">${renderNoteContent(e.content)}</div></td>
                <td>
                    <button class="danger" onclick="deleteEvent(${e.id})">${translations[currentLang].deleteAction}</button>
                    <button onclick="banUser('${e.pubkey}')">${translations[currentLang].banAction}</button>
                </td>
            `;
        });
    }

    function startLiveStream() {
        const tbody = document.querySelector('#stream-table tbody');
        tbody.innerHTML = '';
        liveStreamSocket = new WebSocket("{{ relay_websocket_url }}");
        liveStreamSocket.onopen = () => {
            const subId = `admin-stream-${Math.random()}`;
            liveStreamSocket.send(JSON.stringify(["REQ", subId, {}]));
        };
        liveStreamSocket.onmessage = (msg) => {
            try {
                const [type, subId, event] = JSON.parse(msg.data);
                if (type === "EVENT" && event && event.created_at) {
                    const row = tbody.insertRow(0);
                    row.innerHTML = `
                        <td>${new Date(event.created_at * 1000).toLocaleString()}</td>
                        <td>${event.pubkey.substring(0,10)}...</td>
                        <td>${event.kind}</td>
                        <td><div class="note-content">${renderNoteContent(event.content)}</div></td>
                    `;
                    if(tbody.rows.length > 100) tbody.deleteRow(-1);
                }
            } catch(e) { console.error('Error parsing stream message:', msg.data); }
        };
        liveStreamSocket.onerror = (err) => console.error("WebSocket Error:", err);
    }
    
    async function loadBannedUsers() {
        const users = await apiCall('/api/banned');
        const list = document.getElementById('banned-list');
        list.innerHTML = '';
        users.forEach(u => {
            const li = document.createElement('li');
            li.innerHTML = `<span>${u.pubkey}</span> <button class="danger" onclick="unbanUser('${u.pubkey}')">${translations[currentLang].unbanAction}</button>`;
            list.appendChild(li);
        });
    }

    async function loadConfig() {
        const data = await apiCall('/api/config');
        document.getElementById('config-editor').value = data.content;
    }
    
    document.getElementById('save-config-btn').addEventListener('click', async () => {
        if (!confirm(translations[currentLang].confirmConfigSave)) return;
        const content = document.getElementById('config-editor').value;
        const result = await apiCall('/api/config', {
            method: 'POST', body: JSON.stringify({ content }), headers: {'Content-Type': 'application/json'}
        });
        alert(`${translations[currentLang].success} ${result.message}`);
    });

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
            loadBannedUsers();
            loadDashboard();
        } catch (e) {
            alert(`${translations[currentLang].error} ${e.message}`);
        }
    }
    
    window.unbanUser = async (pubkey) => {
        if (!confirm(translations[currentLang].confirmUnban)) return;
        const result = await apiCall(`/api/banned/${pubkey}`, { method: 'DELETE' });
        alert(result.message);
        loadBannedUsers();
        loadDashboard();
    }

    // Initialen Tab laden
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
    app.run(host='0.0.0.0', port=4001, debug=True)
