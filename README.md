# Nostr-RS-Relay-Admin-Panel by relayted.de
EasyBreezy Admin Panel for Nostr-RS-Relays

A simple, all-in-one, web-based admin panel for operators of `nostr-rs-relay`. This single-file Python script provides a user-friendly interface for moderation, configuration, and live monitoring of your Nostr relay, with no complex setup required.

## ✨ Features

*   **Single-File Deployment:** The entire application (backend & frontend) is contained in a single Python file for maximum simplicity.
*   **Direct Access, No Login:** No authentication is built-in. The panel is instantly accessible, making it ideal for use within a secure network or behind a reverse proxy.
*   **Bilingual Interface:** Switch between German (DE) and English (EN) on the fly.
*   **Advanced Dashboard:** Get an at-a-glance overview of your relay's health and activity with key statistics:
    *   **Live Activity:** Events in the last hour and last 24 hours.
    *   **Growth Metrics:** Total events, unique users, and new users in the last 24 hours.
    *   **Content Insights:** Top 5 most used event kinds and the percentage of encrypted DMs.
    *   **System Health:** Database size and the date of the oldest stored event.
    *   **Top Lists:** See the Top 5 most active users and most common event kinds.
*   **Event Management:**
    *   View a paginated list of the latest events.
    *   Search events by pubkey, event ID, or content.
    *   Delete individual events directly from the UI.
*   **Live Event Stream:** Watch a real-time feed of all events as they arrive at your relay, with actions to copy a pubkey, view a profile, or ban a user instantly.
*   **User Moderation:**
    *   Ban misbehaving pubkeys.
    *   View and manage the list of all banned users.
    *   Unban users.
*   **Direct Configuration Editor:** View and edit your relay's `config.toml` file directly from the web interface.

## 📋 Requirements

*   A running instance of [nostr-rs-relay](https://git.sr.ht/~gheartsfield/nostr-rs-relay).
*   Python 3.8+ and `pip`.
*   File system access to the relay's SQLite database (`nostr.db`) and its configuration file (`config.toml`).

## 🚀 Installation & Setup

Setting up the admin panel is designed to be as straightforward as possible.

### 1. Download the Script

Clone this repository or download the `app.py` script to your server.

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name/
```

### 2. Install Dependencies

It's highly recommended to use a Python virtual environment.

```bash
# Create and activate a virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`

# Install required packages
pip install Flask flask-cors toml
```

### 3. Configure the Panel

Open `admin-panel.py` in a text editor and modify the configuration section at the top of the file. **This step is crucial for the panel to function.**

```python
# ==============================================================================
# ===== KONFIGURATION (BITTE SORGFÄLTIG ANPASSEN) ==============================
# ==============================================================================

# 1. Path to your nostr-rs-relay database (absolute path recommended)
DATABASE_PATH = "/path/to/nostr.db"

# 2. Path to your nostr-rs-relay config file (absolute path recommended)
CONFIG_PATH = "/path/to/config.toml"

# 3. WebSocket URL of your Relay
RELAY_WEBSOCKET_URL = "wss://your.relay.here"

# 4. Flask Session Secret Key (generate with `openssl rand -hex 32` in your terminal)
SECRET_SESSION_KEY = 'a_secure_random_key_here'

# ==============================================================================
```

## ▶️ Running the Admin Panel

1.  Navigate to the directory containing `admin-panel.py`.
2.  Ensure your virtual environment is activated (`source venv/bin/activate`).
3.  Run the script:

    ```bash
    python admin-panel.py
    ```
4.  The admin panel is now accessible in your web browser at **`http://<your-server-ip>:5001`**.

For a production environment, it is recommended to run the Flask application using a production-ready WSGI server like Gunicorn or uWSGI, placed behind a reverse proxy like Nginx.

## ⚠️ **CRITICAL SECURITY WARNING** ⚠️

This application has **NO BUILT-IN LOGIN OR AUTHENTICATION**. By design, anyone who can access the URL can perform all administrative actions, including deleting events and banning users.

**You MUST secure this panel yourself.** Do not expose it directly to the public internet. Recommended methods include:

*   **Firewall Rules:** Use `ufw` or your cloud provider's firewall to only allow access from your specific IP address.
*   **Reverse Proxy with Authentication:** Place the app behind Nginx or Caddy and configure HTTP Basic Authentication (`htpasswd`).
*   **VPN or SSH Tunnel:** Access the panel only through a private network or an SSH tunnel.

## 🔧 How It Works

The script uses the **Flask** micro-framework to run a small web server. It serves a single HTML page that contains all the necessary JavaScript and CSS.

*   **Backend (Python/Flask):** Provides a REST API for fetching data from the SQLite database and performing administrative actions (deleting, banning, saving config).
*   **Frontend (Vanilla JS):** A single-page application that communicates with the backend API to dynamically render all views and data.
