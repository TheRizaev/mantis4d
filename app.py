import os
import base64
import sqlite3
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, send_from_directory
)
from werkzeug.utils import secure_filename

import requests
from openai import OpenAI

# -----------------------------------------------------------------------------
# Flask app & config
# -----------------------------------------------------------------------------
app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -----------------------------------------------------------------------------
# OpenAI client (lazy)
# -----------------------------------------------------------------------------
def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment")
    return OpenAI(api_key=api_key)

# -----------------------------------------------------------------------------
# DB
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            tokens INTEGER DEFAULT 1000,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            width REAL,
            length REAL,
            height REAL,
            style_prefs TEXT,
            color_prefs TEXT,
            extra_prefs TEXT,
            room_image_path TEXT,
            reference_image_path TEXT,
            generated_image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    conn.commit()
    conn.close()

# В Flask 3 нет before_first_request — просто инициализируем БД при импорте
init_db()

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def load_image_base64(path: str) -> str:
    if os.name == "nt":
        path = path.replace("/", "\\")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# -----------------------------------------------------------------------------
# Routes: auth + pages
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    # если нет шаблонов, можно вернуть простую строку:
    try:
        return render_template("index.html")
    except Exception:
        return "App is running. Implement index.html template.", 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()

        conn = sqlite3.connect("app.db")
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()

        if user:
            session["user_id"] = user[0]
            session["username"] = username
        else:
            cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
            conn.commit()
            session["user_id"] = cursor.lastrowid
            session["username"] = username

        conn.close()
        return redirect(url_for("index"))

    try:
        return render_template("login.html")
    except Exception:
        return """
        <form method="post">
          <input name="username" placeholder="Username"/>
          <button type="submit">Login</button>
        </form>
        """, 200

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -----------------------------------------------------------------------------
# Rooms API
# -----------------------------------------------------------------------------
@app.get("/api/rooms")
def get_rooms():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, width, length, height, style_prefs, color_prefs,
               extra_prefs, created_at, generated_image_path
        FROM rooms WHERE user_id = ?
        ORDER BY created_at DESC
    """, (session["user_id"],))

    rooms = []
    for row in cursor.fetchall():
        rooms.append({
            "id": row[0],
            "name": row[1],
            "width": row[2],
            "length": row[3],
            "height": row[4],
            "style_prefs": row[5],
            "color_prefs": row[6],
            "extra_prefs": row[7],
            "created_at": row[8],
            "generated_image_path": row[9]
        })

    conn.close()
    return jsonify(rooms)

@app.post("/api/rooms")
def create_room():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", f'Room {datetime.now().strftime("%Y-%m-%d %H:%M")}')

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO rooms (user_id, name) VALUES (?, ?)
    """, (session["user_id"], name))

    room_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({"id": room_id, "name": name})

@app.put("/api/rooms/<int:room_id>")
def update_room(room_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json(force=True, silent=True) or {}

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM rooms WHERE id = ? AND user_id = ?", (room_id, session["user_id"]))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Room not found"}), 404

    update_fields = []
    values = []
    for field in [
        "name", "width", "length", "height",
        "style_prefs", "color_prefs", "extra_prefs",
        "room_image_path", "reference_image_path"
    ]:
        if field in data:
            update_fields.append(f"{field} = ?")
            values.append(data[field])

    if update_fields:
        values.append(room_id)
        cursor.execute(f"UPDATE rooms SET {', '.join(update_fields)} WHERE id = ?", values)

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.delete("/api/rooms/<int:room_id>")
def delete_room(room_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM rooms WHERE id = ? AND user_id = ?", (room_id, session["user_id"]))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Room not found"}), 404

    cursor.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

# -----------------------------------------------------------------------------
# Uploads
# -----------------------------------------------------------------------------
@app.post("/api/upload")
def upload_file():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        web_path = filepath.replace("\\", "/")
        return jsonify({"filename": filename, "path": web_path})

    return jsonify({"error": "Invalid file type"}), 400

@app.get("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# -----------------------------------------------------------------------------
# Generation (OpenAI)
# -----------------------------------------------------------------------------
@app.post("/api/generate")
def generate_interior():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    client = get_openai_client()

    data = request.get_json(force=True, silent=True) or {}
    room_id = data.get("room_id")
    if not room_id:
        return jsonify({"error": "Room ID required"}), 400

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT width, length, height, style_prefs, color_prefs, extra_prefs,
               room_image_path, reference_image_path
        FROM rooms WHERE id = ? AND user_id = ?
    """, (room_id, session["user_id"]))
    room = cursor.fetchone()

    if not room:
        conn.close()
        return jsonify({"error": "Room not found"}), 404

    cursor.execute("SELECT tokens FROM users WHERE id = ?", (session["user_id"],))
    user_tokens = cursor.fetchone()[0]

    try:
        if not room[6] or not room[7]:
            conn.close()
            return jsonify({"error": "Room and reference images are required"}), 400

        if not os.path.exists(room[6]) or not os.path.exists(room[7]):
            conn.close()
            return jsonify({
                "error": f"Image files not found on disk. Room: {room[6]}, Reference: {room[7]}"
            }), 400

        room_image_b64 = load_image_base64(room[6])
        reference_image_b64 = load_image_base64(room[7])
        reference_data_url = f"data:image/jpeg;base64,{reference_image_b64}"

        analyze_prompt_text = (
            "Проанализируй интерьер на этом фото и опиши:\n"
            "1. Стиль дизайна\n"
            "2. Цветовую палитру\n"
            "3. Материалы\n"
            "4. Тип освещения\n"
            "5. Мебель\n"
            "6. Декор и аксессуары\n"
            "7. Общую атмосферу помещения\n\n"
            "Опиши максимально детально для точного воспроизведения стиля."
        )

        # Анализ референса (оставлен твой формат, SDK 1.x)
        analyze_response = client.responses.create(
            model="gpt-4o",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": analyze_prompt_text},
                    {"type": "input_image", "image_url": reference_data_url}
                ],
            }],
        )

        reference_description = getattr(analyze_response, "output_text", "")
        if not reference_description:
            output = getattr(analyze_response, "output", None)
            if isinstance(output, list):
                try:
                    for item in output:
                        for c in item.get("content", []):
                            if c.get("type") in ("output_text", "text"):
                                reference_description += c.get("text", "")
                except Exception:
                    reference_description = str(output) if output else ""

        width = room[0] if room[0] is not None else 4.0
        length = room[1] if room[1] is not None else 3.5
        height = room[2] if room[2] is not None else 2.7
        style_prefs = room[3] if room[3] else "modern"
        color_prefs = room[4] if room[4] else "neutral"
        extra_prefs = room[5] if room[5] else "cozy atmosphere"

        final_prompt = f"""
Переработай интерьер исходной комнаты, сохранив её геометрию, пропорции, реальные размеры и ракурс камеры.
НЕ МЕНЯЙ расположение дверей, окон и архитектурных элементов!

Размеры:
- Площадь: {width * length:.1f} м²
- Ширина: {width} м
- Длина: {length} м
- Высота: {height} м

Пожелания:
- Стиль: {style_prefs}
- Цвета: {color_prefs}
- Дополнительно: {extra_prefs}

Опираться на анализ референса:
{reference_description}

Требования:
1) Сохранить ракурс, пропорции и планировку.
2) Не менять архитектуру.
3) Масштаб мебели соотнести с размерами помещения.
4) Изменять только мебель/декор/цвета/свет/материалы.
5) В ответе кратко подтвердить соблюдение размеров (2–3 предложения).
""".strip()

        img_result = client.images.generate(
            model="gpt-image-1",
            prompt=final_prompt,
            size="1024x1024",
            quality="high",
        )

        b64 = None
        try:
            b64 = img_result.data[0].b64_json
        except Exception:
            try:
                b64 = img_result["data"][0]["b64_json"]  # на всякий случай
            except Exception:
                try:
                    image_url = getattr(img_result.data[0], "url", None) or (
                        img_result.get("data")[0].get("url") if isinstance(img_result, dict) else None
                    )
                    if image_url:
                        dl = requests.get(image_url)
                        if dl.status_code == 200:
                            img_bytes = dl.content
                            generated_filename = f"generated_{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                            generated_path = os.path.join(app.config["UPLOAD_FOLDER"], generated_filename).replace("\\", "/")
                            with open(generated_path, "wb") as f:
                                f.write(img_bytes)

                            cursor.execute("UPDATE rooms SET generated_image_path = ? WHERE id = ?", (generated_path, room_id))
                            conn.commit()
                            conn.close()
                            return jsonify({"success": True, "generated_image_path": generated_path, "tokens_remaining": user_tokens})
                except Exception:
                    pass

        if not b64:
            conn.close()
            return jsonify({"error": "Could not retrieve generated image from the model response."}), 500

        img_bytes = base64.b64decode(b64)
        generated_filename = f"generated_{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        generated_path = os.path.join(app.config["UPLOAD_FOLDER"], generated_filename).replace("\\", "/")
        with open(generated_path, "wb") as f:
            f.write(img_bytes)

        cursor.execute("UPDATE rooms SET generated_image_path = ? WHERE id = ?", (generated_path, room_id))
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "generated_image_path": generated_path,
            "tokens_remaining": user_tokens
        })

    except Exception as e:
        conn.close()
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500

# -----------------------------------------------------------------------------
# Tokens
# -----------------------------------------------------------------------------
@app.get("/api/user/tokens")
def get_user_tokens():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT tokens FROM users WHERE id = ?", (session["user_id"],))
    tokens = cursor.fetchone()[0]
    conn.close()

    return jsonify({"tokens": tokens})

# -----------------------------------------------------------------------------
# Dev entrypoint (локально)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # локально можно так
    app.run(debug=True, host="0.0.0.0", port=5000)
