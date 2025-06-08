# Nostr-RS-Relay-Admin-Panel
EasyBreezy Admin Panel for Nostr-RS-Relay

✨ Features
Single-File Deployment: The entire application (backend, frontend, styles) is contained in a single Python file for maximum simplicity.
Web-Based UI: Clean, responsive, and easy-to-use interface with a light theme.
Bilingual: Switch between German (DE) and English (EN) on the fly.
Live Dashboard: View real-time statistics of your relay, including total events, unique users, and banned users.
Event Management:
View the latest events on your relay.
Search events by pubkey, event ID, or content.
Delete individual events directly from the UI.
Live Event Stream: Watch a real-time feed of all events as they arrive at your relay via a direct WebSocket connection.
User Management:
Ban misbehaving pubkeys.
View and manage the list of banned users.
Unban users.
Direct Configuration Editor: View and edit your relay's config.toml file directly from the admin panel. (Use with caution!)
📋 Requirements
A running instance of nostr-rs-relay.
Python 3.7+ and pip.
File system access to the relay's SQLite database (nostr.db) and its configuration file (config.toml).
🚀 Installation & Setup
Setting up the admin panel is designed to be as straightforward as possible.

1. Download the Script
Clone this repository or download the admin_panel.py script to your server.

bash
Code kopieren

git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name/
2. Install Dependencies
It's highly recommended to use a Python virtual environment.

bash
Code kopieren

# Create and activate a virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`

# Install required packages
pip install Flask flask-cors nostr-sdk toml
3. Configure the Panel
Open admin_panel.py in a text editor and modify the configuration section at the top of the file.

python
Code kopieren

# ==============================================================================
# ===== KONFIGURATION (BITTE SORGFÄLTIG ANPASSEN) ==============================
# ==============================================================================

# 1. Admin Pubkey (Hex-Format)
# Convert your `npub` to its hex format using a tool like nostr.band.
# This is the ONLY user who can log in.
ADMIN_HEX_PUBKEY = "YOUR_64_CHAR_ADMIN_HEX_PUBKEY_HERE" 

# 2. Path to your nostr-rs-relay database
# An absolute path is recommended.
DATABASE_PATH = "/path/to/your/nostr.db"

# 3. Path to your nostr-rs-relay config file
# Required for the configuration editor feature.
CONFIG_PATH = "/path/to/your/config.toml"

# 4. WebSocket URL of your Relay
# Used by the frontend for the live event stream.
RELAY_WEBSOCKET_URL = "wss://your.relay.url"

# 5. Flask Session Secret Key
# Change this to a long, random string!
SECRET_SESSION_KEY = 'change-this-to-a-very-long-random-string'

# ==============================================================================
This step is crucial for security and functionality!

▶️ Running the Admin Panel
Navigate to the directory containing admin_panel.py.

Run the script:

bash
Code kopieren

python admin_panel.py
The admin panel is now accessible in your web browser at http://<your-server-ip>:5001.

For a production environment, it is recommended to run the Flask application using a proper WSGI server like Gunicorn or uWSGI, preferably behind a reverse proxy like Nginx.

⚠️ Security Warning
This tool provides powerful administrative capabilities. Please be aware of the following:

nsec Login: While the private key is not sent to the server, entering it into any web page is inherently risky. Always prefer the NIP-07 browser extension login method. Use the nsec option only if you fully trust the machine hosting this admin panel.
Configuration Editor: Editing your config.toml directly can be dangerous. A syntax error could prevent your relay from starting. Always make a backup of your config.toml before saving changes through the web UI.
Firewall: Ensure that port 5001 (or whichever port you use) is properly firewalled and only accessible to you. Running this behind a reverse proxy with TLS and HTTP basic authentication can provide an extra layer of security.
🔧 How It Works
The script uses the Flask micro-framework to run a small web server. It serves a single HTML page that contains all the necessary JavaScript and CSS.

Backend (Python/Flask): Provides a REST API for authentication, fetching data from the SQLite database, and performing administrative actions (deleting, banning, saving config).
Frontend (HTML/JS/CSS): A vanilla JavaScript single-page application that communicates with the backend API. It handles user login (including NIP-07 signing) and dynamically renders all the views and data.
📝 TODO & Future Ideas
 Add a dark mode theme.
 Implement charts for visualizing relay statistics over time.
 Create a Dockerfile for easy, containerized deployment.
 Add pagination for the events list for very large relays.
 Integrate more directly with nostr-rs-relay's internal mechanisms if they become exposed (e.g., a built-in ban list).
🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check the issues page.
