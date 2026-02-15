from flask import Flask, render_template, request, jsonify
import sqlite3

app = Flask(__name__)
DB_PATH = '../tasks.db' 

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('dashboard.html')

# --- THE WEB API ---
@app.route('/api/data')
def get_dashboard_data():
    conn = get_db_connection()
    
    # 1. Get Logs (Last 15)
    logs = conn.execute('SELECT * FROM logs ORDER BY id DESC LIMIT 15').fetchall()
    
    # 2. Get Active To-Dos
    todos = conn.execute("SELECT * FROM todo WHERE status = 'pending'").fetchall()
    
    # 3. Get Notes (Last 5)
    notes = conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT 5").fetchall()
    
    conn.close()

    return jsonify({
        "logs": [dict(row) for row in logs][::-1], # Reverse logs for display
        "todos": [dict(row) for row in todos],
        "notes": [dict(row) for row in notes],
        "services": {
            "spotify": "Active",   # You can make this dynamic later
            "outlook": "Active",
            "database": "Connected"
        }
    })

@app.route('/api/command', methods=['POST'])
def send_command():
    data = request.json
    command_text = data.get('command')
    conn = get_db_connection()
    conn.execute("INSERT INTO web_commands (command) VALUES (?)", (command_text,))
    conn.commit()
    conn.close()
    return jsonify({"status": "sent"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)