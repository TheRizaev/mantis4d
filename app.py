import os
import base64
import sqlite3
from datetime import datetime

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename

# Если используешь официальный SDK OpenAI 1.x
from openai import OpenAI

# -----------------------------------------------------------------------------
# Flask-приложение и базовая конфигурация
# -----------------------------------------------------------------------------
app = Flask(__name__)

# Секрет берем из переменных окружения (а не хардкодим)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

# Файлы/загрузка
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

# Гарантируем существование папки загрузок
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -----------------------------------------------------------------------------
# OpenAI: ленивое создание клиента, чтобы импорт модуля не падал без ключа
# -----------------------------------------------------------------------------
def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Делай raise с понятным текстом — увидишь в логах Render
        raise RuntimeError("OPENAI_API_KEY is not set in environment")
    return OpenAI(api_key=api_key)


def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            tokens INTEGER DEFAULT 1000,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Rooms table
    cursor.execute('''
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
    ''')

    conn.commit()
    conn.close()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def load_image_base64(path):
    """Load image and convert to base64"""
    # Fix Windows path separators
    path = path.replace('/', '\\') if os.name == 'nt' else path
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']

        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()

        # Check if user exists
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            session['username'] = username
        else:
            # Create new user
            cursor.execute('INSERT INTO users (username) VALUES (?)', (username,))
            conn.commit()
            session['user_id'] = cursor.lastrowid
            session['username'] = username

        conn.close()
        return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, name, width, length, height, style_prefs, color_prefs, 
               extra_prefs, created_at, generated_image_path
        FROM rooms WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (session['user_id'],))

    rooms = []
    for row in cursor.fetchall():
        rooms.append({
            'id': row[0],
            'name': row[1],
            'width': row[2],
            'length': row[3],
            'height': row[4],
            'style_prefs': row[5],
            'color_prefs': row[6],
            'extra_prefs': row[7],
            'created_at': row[8],
            'generated_image_path': row[9]
        })

    conn.close()
    return jsonify(rooms)


@app.route('/api/rooms', methods=['POST'])
def create_room():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    name = data.get('name', f'Room {datetime.now().strftime("%Y-%m-%d %H:%M")}')

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO rooms (user_id, name)
        VALUES (?, ?)
    ''', (session['user_id'], name))

    room_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({'id': room_id, 'name': name})


@app.route('/api/rooms/<int:room_id>', methods=['PUT'])
def update_room(room_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    print(f"Updating room {room_id} with data: {data}")

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    # Check if room belongs to user
    cursor.execute('SELECT id FROM rooms WHERE id = ? AND user_id = ?', 
                   (room_id, session['user_id']))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Room not found'}), 404

    # Update room
    update_fields = []
    values = []

    for field in ['name', 'width', 'length', 'height', 'style_prefs', 'color_prefs', 'extra_prefs', 'room_image_path', 'reference_image_path']:
        if field in data:
            update_fields.append(f'{field} = ?')
            values.append(data[field])
            print(f"Updating {field} = {data[field]}")

    if update_fields:
        values.append(room_id)
        cursor.execute(f'''
            UPDATE rooms SET {', '.join(update_fields)}
            WHERE id = ?
        ''', values)

    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/rooms/<int:room_id>', methods=['DELETE'])
def delete_room(room_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    # Check if room belongs to user
    cursor.execute('SELECT id FROM rooms WHERE id = ? AND user_id = ?', 
                   (room_id, session['user_id']))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Room not found'}), 404

    cursor.execute('DELETE FROM rooms WHERE id = ?', (room_id,))
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Add timestamp and user ID to avoid conflicts
        filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Fix Windows path separators for web URLs
        web_path = filepath.replace('\\', '/')

        return jsonify({'filename': filename, 'path': web_path})

    return jsonify({'error': 'Invalid file type'}), 400


@app.route('/api/generate', methods=['POST'])
def generate_interior():
    """Generate interior redesign using OpenAI Responses API for analysis and gpt-image-1 for image generation.

    The function ensures the model pays attention to room dimensions and preserves geometry.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    room_id = data.get('room_id')

    if not room_id:
        return jsonify({'error': 'Room ID required'}), 400

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    # Get room data
    cursor.execute('''
        SELECT width, length, height, style_prefs, color_prefs, extra_prefs,
               room_image_path, reference_image_path
        FROM rooms WHERE id = ? AND user_id = ?
    ''', (room_id, session['user_id']))

    room = cursor.fetchone()
    if not room:
        conn.close()
        return jsonify({'error': 'Room not found'}), 404

    print(f"Room data: {room}")
    print(f"Room image path: {room[6]}")
    print(f"Reference image path: {room[7]}")

    # Tokens system disabled - free generation
    cursor.execute('SELECT tokens FROM users WHERE id = ?', (session['user_id'],))
    user_tokens = cursor.fetchone()[0]

    try:
        # Check if images exist
        if not room[6] or not room[7]:
            conn.close()
            return jsonify({'error': 'Room and reference images are required'}), 400

        # Check if files exist on disk
        if not os.path.exists(room[6]) or not os.path.exists(room[7]):
            conn.close()
            return jsonify({'error': f'Image files not found on disk. Room: {room[6]}, Reference: {room[7]}'}), 400

        # Load images
        room_image_b64 = load_image_base64(room[6])
        reference_image_b64 = load_image_base64(room[7])
        reference_data_url = f"data:image/jpeg;base64,{reference_image_b64}"

        # Analyze reference image using Responses API
        analyze_prompt_text = (
            "Проанализируй интерьер на этом фото и опиши:\n"
            "1. Стиль дизайна (минимализм, лофт, классика, современный и т.д.)\n"
            "2. Цветовую палитру (теплые/холодные тона, основные цвета)\n"
            "3. Материалы (дерево, металл, стекло, текстиль)\n"
            "4. Тип освещения (естественное, искусственное, акцентное)\n"
            "5. Мебель (стиль, расстановка, материалы)\n"
            "6. Декор и аксессуары\n"
            "7. Общую атмосферу помещения\n\n"
            "Опиши максимально детально для точного воспроизведения стиля."
        )

        analyze_response = client.responses.create(
            model="gpt-4o",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": analyze_prompt_text},
                        {"type": "input_image", "image_url": reference_data_url}
                    ],
                }
            ],
        )

        # Extract textual analysis robustly
        reference_description = ""
        # new SDK often provides output_text
        reference_description = getattr(analyze_response, 'output_text', '')
        if not reference_description:
            # fallback: parse `output` content
            output = getattr(analyze_response, 'output', None) or analyze_response.get('output', None) if isinstance(analyze_response, dict) else None
            if output:
                # output can be a list of items
                try:
                    for item in output:
                        for c in item.get('content', []):
                            if c.get('type') in ('output_text', 'text'):
                                reference_description += c.get('text', '')
                except Exception:
                    reference_description = str(output)

        # Create final prompt, include explicit dimensions and strict rules
        width = room[0] if room[0] is not None else 4.0
        length = room[1] if room[1] is not None else 3.5
        height = room[2] if room[2] is not None else 2.7
        style_prefs = room[3] if room[3] else "modern"
        color_prefs = room[4] if room[4] else "neutral"
        extra_prefs = room[5] if room[5] else "cozy atmosphere"

        final_prompt = f"""
Переработай интерьер исходной комнаты, сохранив её геометрию, пропорции, реальные размеры и ракурс камеры.
ВАЖНО: НЕ МЕНЯЙ расположение дверей, окон и архитектурных элементов!

Размеры комнаты:
- Площадь: {width * length:.1f} м²
- Ширина: {width} м
- Длина: {length} м
- Высота потолков: {height} м

Пожелания пользователя:
- Стиль: {style_prefs}
- Цвета: {color_prefs}
- Дополнительно: {extra_prefs}

Применяй стиль и атмосферу, описанные ниже (из анализа референса):
{reference_description}

ТРЕБОВАНИЯ:
1. Сохрани точно тот же ракурс и угол обзора.
2. НЕ перемещай и не изменяй форму дверей, окон, стен.
3. НЕ меняй архитектурную планировку.
4. Не выходи за реальные границы комнаты — соблюдай указанные размеры, не увеличивай и не уменьшай пространство.
5. Сохрани правильные пропорции мебели относительно размеров помещения и высоты потолков.
6. Изменяй только мебель, декор, цвета, освещение и материалы.
7. Результат должен выглядеть как реалистичный редизайн той же комнаты с того же ракурса.

Дополнительно: в ответе модель должна кратко подтвердить, что соблюдены размеры и пропорции (2-3 предложения).
"""

        # Generate image using gpt-image-1 and receive base64
        img_result = client.images.generate(
            model="gpt-image-1",
            prompt=final_prompt,
            size="1024x1024",
            quality="high",
            # some SDKs allow output_format or return b64_json by default
        )

        # Try to extract base64 image from several possible response shapes
        b64 = None
        try:
            # common shape: img_result.data[0].b64_json
            b64 = img_result.data[0].b64_json
        except Exception:
            try:
                # alternative: dict-like
                b64 = img_result['data'][0]['b64_json']
            except Exception:
                # last resort: if SDK returns a URL (older style), download it
                try:
                    image_url = getattr(img_result.data[0], 'url', None) or (img_result.get('data')[0].get('url') if isinstance(img_result, dict) else None)
                    if image_url:
                        dl = requests.get(image_url)
                        if dl.status_code == 200:
                            # save bytes directly
                            img_bytes = dl.content
                            generated_filename = f"generated_{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                            generated_path = os.path.join(app.config['UPLOAD_FOLDER'], generated_filename).replace('\\', '/')
                            with open(generated_path, 'wb') as f:
                                f.write(img_bytes)

                            cursor.execute('''
                                UPDATE rooms SET generated_image_path = ?
                                WHERE id = ?
                            ''', (generated_path, room_id))
                            conn.commit()
                            conn.close()

                            return jsonify({'success': True, 'generated_image_path': generated_path, 'tokens_remaining': user_tokens})
                except Exception:
                    pass

        if not b64:
            conn.close()
            return jsonify({'error': 'Could not retrieve generated image from the model response.'}), 500

        img_bytes = base64.b64decode(b64)
        generated_filename = f"generated_{session['user_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        generated_path = os.path.join(app.config['UPLOAD_FOLDER'], generated_filename).replace('\\', '/')
        with open(generated_path, 'wb') as f:
            f.write(img_bytes)

        # Update DB
        cursor.execute('''
            UPDATE rooms SET generated_image_path = ?
            WHERE id = ?
        ''', (generated_path, room_id))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'generated_image_path': generated_path,
            'tokens_remaining': user_tokens
        })

    except Exception as e:
        conn.close()
        return jsonify({'error': f'Generation failed: {str(e)}'}), 500


@app.route('/api/user/tokens')
def get_user_tokens():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    cursor.execute('SELECT tokens FROM users WHERE id = ?', (session['user_id'],))
    tokens = cursor.fetchone()[0]

    conn.close()
    return jsonify({'tokens': tokens})


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)


