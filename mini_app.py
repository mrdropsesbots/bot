import os
import sqlite3
import json
import hmac
import hashlib
from flask import Flask, request, jsonify, render_template_string
from urllib.parse import unquote

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/data/places.db")
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []

# ------------------ Проверка подписи Telegram ------------------
def verify_telegram_data(init_data_str):
    d = {}
    for item in init_data_str.split('&'):
        k, v = item.split('=', 1)
        d[k] = unquote(v)
    received_hash = d.pop('hash', None)
    if not received_hash:
        return False, None
    data_check_arr = [f"{k}={d[k]}" for k in sorted(d.keys())]
    data_check_string = "\n".join(data_check_arr)
    secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if computed_hash != received_hash:
        return False, None
    user = json.loads(d.get('user', '{}'))
    return True, user

def check_admin(init_data_str):
    if not init_data_str:
        return False
    valid, user = verify_telegram_data(init_data_str)
    if not valid or not user:
        return False
    return user.get('id') in ADMIN_IDS

# ------------------ HTML-интерфейс ------------------
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Админ-панель</title>
    <style>
        body { font-family: -apple-system, sans-serif; padding: 16px; background: var(--tg-theme-bg-color); color: var(--tg-theme-text-color); }
        button, .btn { padding: 10px 16px; margin: 5px; background: var(--tg-theme-button-color); color: var(--tg-theme-button-text-color); border: none; border-radius: 8px; cursor: pointer; }
        button.danger { background: #e74c3c; }
        input, textarea, select { width: calc(100% - 20px); padding: 8px; margin: 5px 0; border-radius: 6px; border: 1px solid #ccc; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 8px; border-bottom: 1px solid #ddd; text-align: left; }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div id="loading">Проверка доступа...</div>
    <div id="app" class="hidden">
        <h2>🏠 Заведения</h2>
        <button onclick="showAddVenue()">➕ Добавить</button>
        <button onclick="loadVenues()">🔄 Обновить</button>
        <div id="addVenueForm" class="hidden">
            <input type="text" id="v_name" placeholder="Название">
            <input type="text" id="v_category" placeholder="Категория">
            <input type="text" id="v_address" placeholder="Адрес">
            <input type="number" id="v_lat" placeholder="Широта" step="any">
            <input type="number" id="v_lon" placeholder="Долгота" step="any">
            <input type="text" id="v_desc" placeholder="Описание">
            <input type="text" id="v_phone" placeholder="Телефон">
            <button onclick="addVenue()">Сохранить</button>
        </div>
        <div id="venuesList"></div>

        <hr>
        <h2>🍽️ Массовое добавление меню</h2>
        <textarea id="menuCsv" rows="5" placeholder="Заведение, Блюдо, Цена, Категория (одна строка — одна позиция)"></textarea>
        <button onclick="bulkAddMenu()">⚡ Добавить всё</button>
        <div id="bulkResult"></div>
    </div>

    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script>
        const tg = window.Telegram.WebApp;
        const initData = tg.initData || '';

        async function init() {
            const resp = await fetch('/check_admin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({initData})
            });
            if (resp.ok) {
                document.getElementById('loading').style.display = 'none';
                document.getElementById('app').classList.remove('hidden');
                loadVenues();
            } else {
                document.getElementById('loading').innerText = '⛔ Доступ запрещён.';
            }
        }

        async function api(url, method = 'GET', body = null) {
            const opts = {method, headers: {'Content-Type': 'application/json'}};
            if (body) opts.body = JSON.stringify({...body, initData});
            const resp = await fetch(url, opts);
            return resp.json();
        }

        async function loadVenues() {
            const data = await api('/api/venues');
            if (data.venues) {
                let html = '<table><tr><th>Название</th><th>Адрес</th><th></th></tr>';
                data.venues.forEach(v => {
                    html += `<tr><td>${v.name}</td><td>${v.address||''}</td>
                             <td><button class="danger" onclick="deleteVenue(${v.id})">🗑️</button></td></tr>`;
                });
                html += '</table>';
                document.getElementById('venuesList').innerHTML = html;
            }
        }

        function showAddVenue() {
            document.getElementById('addVenueForm').classList.toggle('hidden');
        }

        async function addVenue() {
            const name = document.getElementById('v_name').value;
            const category = document.getElementById('v_category').value;
            const address = document.getElementById('v_address').value;
            const lat = document.getElementById('v_lat').value;
            const lon = document.getElementById('v_lon').value;
            const desc = document.getElementById('v_desc').value;
            const phone = document.getElementById('v_phone').value;
            await api('/api/venues', 'POST', {name, category, address, latitude: lat, longitude: lon, description: desc, phone});
            loadVenues();
            document.getElementById('addVenueForm').classList.add('hidden');
        }

        async function deleteVenue(id) {
            if (confirm('Удалить заведение и всё меню?')) {
                await api(`/api/venues/${id}`, 'DELETE');
                loadVenues();
            }
        }

        async function bulkAddMenu() {
            const csv = document.getElementById('menuCsv').value;
            const resp = await api('/api/menu/bulk', 'POST', {csv});
            document.getElementById('bulkResult').innerText = `✅ Добавлено: ${resp.added}. Ошибки: ${resp.errors?.join(', ') || 'нет'}`;
        }

        init();
    </script>
</body>
</html>
"""

# ------------------ Роуты ------------------
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/check_admin", methods=["POST"])
def check_admin_route():
    data = request.get_json()
    if check_admin(data.get("initData", "")):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 403

@app.route("/api/venues", methods=["GET"])
def api_venues():
    if not check_admin(request.args.get("initData", "")):
        return jsonify({"error": "Forbidden"}), 403
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT v.id, v.name, c.name as category, v.address FROM venues v JOIN categories c ON v.category_id=c.id ORDER BY v.name")
    venues = [{"id": r[0], "name": r[1], "category": r[2], "address": r[3]} for r in cur.fetchall()]
    conn.close()
    return jsonify({"venues": venues})

@app.route("/api/venues", methods=["POST"])
def api_add_venue():
    if not check_admin(request.args.get("initData", "")):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cat_name = data.get("category", "Без категории")
    cur.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO categories (name) VALUES (?)", (cat_name,))
        cat_id = cur.lastrowid
    else:
        cat_id = row[0]
    cur.execute("INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) VALUES (?,?,?,?,?,?,?)",
                (data["name"], cat_id, data.get("address",""), float(data.get("latitude",0)), float(data.get("longitude",0)), data.get("description",""), data.get("phone","")))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/venues/<int:vid>", methods=["DELETE"])
def api_delete_venue(vid):
    if not check_admin(request.args.get("initData", "")):
        return jsonify({"error": "Forbidden"}), 403
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM venues WHERE id=?", (vid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/menu/bulk", methods=["POST"])
def api_bulk_menu():
    if not check_admin(request.args.get("initData", "")):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    csv_text = data.get("csv", "")
    lines = [line.strip() for line in csv_text.split('\n') if line.strip()]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    added = 0
    errors = []
    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 3:
            errors.append(f"Строка {i+1}: неверный формат")
            continue
        venue_name, item_name, price_str = parts[0], parts[1], parts[2]
        category = parts[3] if len(parts) > 3 else "Без категории"
        try:
            price = float(price_str)
        except ValueError:
            errors.append(f"Строка {i+1}: цена не число")
            continue
        cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
        v = cur.fetchone()
        if not v:
            errors.append(f"Строка {i+1}: заведение '{venue_name}' не найдено")
            continue
        cur.execute("INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
                    (v[0], item_name, price, category))
        added += 1
    conn.commit()
    conn.close()
    return jsonify({"added": added, "errors": errors})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
