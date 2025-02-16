from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, session
import os
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
import spill_vector
import uuid
import sqlite3
import secrets
import logging

secret_key = secrets.token_hex(16)
app = Flask(__name__)
app.secret_key = secret_key
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['AVATAR_FOLDER'] = 'static/avatars'
app.config['PREDICTIONS_FOLDER'] = 'static/predictions'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}
db_file = 'users.db'
socketio = SocketIO(app, cors_allowed_origins="*")

def init_db():
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE,
                        email TEXT UNIQUE,
                        password TEXT,
                        avatar TEXT,
                        bio TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        content TEXT,
                        image_path TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS likes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        post_id INTEGER,
                        FOREIGN KEY(user_id) REFERENCES users(id),
                        FOREIGN KEY(post_id) REFERENCES posts(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS followers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        follower_id INTEGER,
                        followed_id INTEGER,
                        FOREIGN KEY(follower_id) REFERENCES users(id),
                        FOREIGN KEY(followed_id) REFERENCES users(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sender TEXT NOT NULL,
                        recipient TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        conn.commit()

def get_logged_in_username():
    if "user_id" not in session:  
        return None  
    user_id = session["user_id"]
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        return user[0] if user else None

@app.route("/get_logged_in_user")
def get_logged_in_user():
    username = get_logged_in_username()
    if username:
        return jsonify({"username": username})
    else:
        return jsonify({"error": "User not logged in"}), 401

@app.route('/api/spills')
def get_spills():
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT posts.*, users.username 
            FROM posts 
            JOIN users ON posts.user_id = users.id 
            WHERE image_path IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 50
        ''')
        spills = c.fetchall()
    return jsonify([dict(spill) for spill in spills])

@app.route('/api/recommendations')
def get_recommendations():
    if 'user_id' not in session:
        return jsonify([])
    user_id = session['user_id']
    recommendations = get_recommendations(user_id)
    return jsonify([dict(rec) for rec in recommendations])

def get_recommendations(user_id):
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT users.* FROM users
            WHERE users.id IN (
                SELECT followed_id FROM followers
                WHERE follower_id IN (
                    SELECT followed_id FROM followers
                    WHERE follower_id = ?
                )
            )
            AND users.id NOT IN (
                SELECT followed_id FROM followers
                WHERE follower_id = ?
            )
            AND users.id != ?
            LIMIT 5
        ''', (user_id, user_id, user_id))
        return c.fetchall()

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('auth'))
    
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row 
        c = conn.cursor()
        c.execute("SELECT username, email, avatar, bio FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()

    if user:
        return render_template('prfle.html', users=user)
    else:
        return redirect(url_for('auth'))
    
@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('auth'))
    
    user_id = session['user_id']
    
    if request.method == 'POST':
        bio = request.form.get('bio')
        avatar = request.files.get('avatar')
        avatar_path = None
        
        if avatar and allowed_file(avatar.filename):
            filename = secure_filename(avatar.filename)
            avatar_path = os.path.join(app.config['AVATAR_FOLDER'], filename)
            avatar.save(avatar_path)
        
        with sqlite3.connect(db_file) as conn:
            c = conn.cursor()
            if avatar_path:
                c.execute("UPDATE users SET bio = ?, avatar = ? WHERE id = ?", (bio, avatar_path, user_id))
            else:
                c.execute("UPDATE users SET bio = ? WHERE id = ?", (bio, user_id))
            conn.commit()
        
        return redirect(url_for('profile'))
    
    # Fetch user data for the GET request
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT username, email, avatar, bio FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
    
    if user:
        return render_template('edit_profile.html', users=user)
    else:
        return redirect(url_for('auth'))


@app.route('/follow/<int:user_id>', methods=['POST'])
def follow(user_id):
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    
    follower_id = session['user_id']

    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM followers WHERE follower_id = ? AND followed_id = ?", (follower_id, user_id))
        existing_follow = c.fetchone()

        if existing_follow:
            return jsonify(success=False, message="You are already following this user."), 400

        c.execute("INSERT INTO followers (follower_id, followed_id) VALUES (?, ?)", (follower_id, user_id))
        conn.commit()

    return jsonify(success=True, message="You are now following the user.")


@app.route('/get_follow_counts/<int:user_id>', methods=['GET'])
def get_follow_counts(user_id):
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM followers WHERE followed_id = ?", (user_id,))
        followers_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM followers WHERE follower_id = ?", (user_id,))
        followed_count = c.fetchone()[0]
    
    return jsonify({
        'followers_count': followers_count,
        'followed_count': followed_count
    })


@app.route('/unfollow/<int:user_id>', methods=['POST'])
def unfollow(user_id):
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    
    follower_id = session['user_id']

    if follower_id == user_id:
        return jsonify(success=False, message="You cannot unfollow yourself."), 400

    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM followers WHERE follower_id = ? AND followed_id = ?", (follower_id, user_id))
        existing_follow = c.fetchone()

        if not existing_follow:
            return jsonify(success=False, message="You are not following this user."), 400

        c.execute("DELETE FROM followers WHERE follower_id = ? AND followed_id = ?", (follower_id, user_id))
        conn.commit()

    return jsonify(success=True, message="You have unfollowed the user.")

@app.route('/like/<int:post_id>', methods=['POST'])
def like(post_id):
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", (user_id, post_id))
        conn.commit()
    return jsonify(success=True)

@app.route('/unlike/<int:post_id>', methods=['POST'])
def unlike(post_id):
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id))
        conn.commit()
    return jsonify(success=True)


@app.route('/create_post', methods=['POST'])
def create_post():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401

    content = request.json.get('content')

    if not content:
        return jsonify(success=False, message="Content is required"), 400 

    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO posts (user_id, content) VALUES (?, ?)", (session['user_id'], content))
        conn.commit()

    return jsonify(success=True, message="Post created successfully")

@app.route('/followers/<int:user_id>')
def get_followers(user_id):
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not c.fetchone():
            return jsonify({"error": "User not found"}), 404
        c.execute("""
            SELECT u.id, u.username 
            FROM followers f
            JOIN users u ON f.follower_id = u.id
            WHERE f.followed_id = ?
        """, (user_id,))
        
        followers = [{"id": row[0], "username": row[1]} for row in c.fetchall()]
    
    return jsonify(followers)

@app.route('/followed/<int:user_id>')
def get_followed(user_id):
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not c.fetchone():
            return jsonify({"error": "User not found"}), 404
        c.execute("""
            SELECT u.id, u.username 
            FROM followers f
            JOIN users u ON f.followed_id = u.id
            WHERE f.follower_id = ?
        """, (user_id,))
        
        followed_users = [{"id": row[0], "username": row[1]} for row in c.fetchall()]
    
    return jsonify(followed_users)

@app.route('/delete_post/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    if 'user_id' not in session:
        return jsonify(success=False), 401 

    user_id = session['user_id']

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row  
        c = conn.cursor()

        # Fetch post owner
        c.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,))
        post = c.fetchone()

        if post is None:
            return jsonify(success=False, error="Post not found"), 404

        if post["user_id"] != user_id: 
            return jsonify(success=False, error="Permission denied"), 403

        # Delete the post
        c.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

        return jsonify(success=True, message="Post deleted successfully"), 200


@app.route('/get_posts')
def get_posts():
    if 'user_id' not in session:
        return jsonify(success=False), 401
        
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''SELECT posts.*, users.username, users.avatar 
                   FROM posts 
                   JOIN users ON posts.user_id = users.id 
                   ORDER BY timestamp DESC''')
        posts = c.fetchall()
    return jsonify([dict(post) for post in posts])

@app.route('/get_users', methods=['GET'])
def get_users():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401

    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    offset = (page - 1) * per_page

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, username, avatar FROM users WHERE id != ? AND username LIKE ? LIMIT ? OFFSET ?", 
                  (session['user_id'], f'%{search_query}%', per_page, offset))
        users = c.fetchall()

    users_list = [dict(user) for user in users]
    return jsonify(users_list)


@app.route('/get_user_info/<int:user_id>')
def get_user_info(user_id):
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''SELECT users.*, 
                   (SELECT COUNT(*) FROM followers WHERE followed_id = users.id) AS followers,
                   (SELECT COUNT(*) FROM followers WHERE follower_id = users.id) AS following
                   FROM users WHERE id = ?''', (user_id,))
        user = c.fetchone()
    return jsonify(dict(user)) if user else jsonify({}), 404
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/get_user_posts_for_single_user')
def get_user_posts_for_single_user():
    if 'user_id' not in session:
        return jsonify(success=False), 401
        
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''SELECT posts.*, users.username, users.avatar 
                    FROM posts 
                    JOIN users ON posts.user_id = users.id 
                    WHERE posts.user_id = ?
                    ORDER BY timestamp DESC''', (user_id,))
        posts = c.fetchall()
    
    return jsonify([dict(post) for post in posts])

@app.route('/get_user_posts/<int:user_id>')
def get_user_posts(user_id):
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''SELECT posts.*, users.username, users.avatar 
                    FROM posts 
                    JOIN users ON posts.user_id = users.id 
                    WHERE posts.user_id = ?
                    ORDER BY timestamp DESC''', (user_id,))
        posts = c.fetchall()
    
    return jsonify([dict(post) for post in posts])


@app.route('/')
def home():
    return redirect(url_for('auth'))

@app.route('/prfle.html')
def profile_html():
    return redirect(url_for('profile'))

@app.route('/about')
def about():
    return render_template('about.html')
@app.route('/about.html')
def about_html():
    return redirect(url_for('about'))

@app.route('/base')
def base():
    if 'user_id' not in session:
        return redirect(url_for('auth'))
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        db = conn.cursor()  
        cur = db.execute("SELECT id, username, bio, avatar FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
    if user:
        return render_template('base.html', user=user)
    else:
        return "User not found", 404
@app.route('/profile_selected/base.html')
def base_back():
    return redirect(url_for('base'))
@app.route('/base.html')
def base_html():
    return redirect(url_for('base'))

@app.route('/chat.html')
def chat_html():
    return redirect(url_for('chat'))

@app.route('/posts')
def posts():
    return render_template('posts.html')
@app.route('/posts.html')
def posts_html():
    return redirect(url_for('posts'))

@app.route('/problem')
def problem():
    return render_template('problem.html')
@app.route('/problem.html')
def problem_html():
    return redirect(url_for('problem'))

@app.route('/solutions')
def solutions():
    return render_template('solutions.html')
@app.route('/solutions.html')
def solutions_html():
    return redirect(url_for('solutions'))

@app.route('/gemini')
def gemini():
    return render_template('gemini.html')
@app.route('/gemini.html')
def gemini_html():
    return redirect(url_for('gemini'))

@app.route('/formpage')
def formpage():
    return render_template('formpage.html')
@app.route('/formpage.html')
def formpage_html():
    return redirect(url_for('formpage'))

@app.route('/visual')
def visual():
    image_path = request.args.get('image_path')
    if image_path is None:
        return "Image path is missing", 400
    return render_template('visual.html', image_path=image_path)

@app.route('/visual.html')
def visual_html():
    return redirect(url_for('visual'))

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '').strip()
    
    if not query:
        return jsonify([])

    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT id, username FROM users WHERE username LIKE ?", (f"%{query}%",))
        users = [{"id": row[0], "username": row[1]} for row in c.fetchall()]

    return jsonify(users)

@app.route('/profile_selected/<int:user_id>', methods=['GET'])
def profile_selected(user_id):
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        db = conn.cursor()  
        cur = db.execute("SELECT id, username, bio, avatar FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
    if user:
        return render_template('selected_profile.html', user=user)
    else:
        return "User not found", 404

@app.route('/subscribe/<int:user_id>', methods=['POST'])
def subscribe(user_id):
    if 'user_id' in session and session['user_id'] != user_id:
        with sqlite3.connect(db_file) as db:
            db.row_factory = sqlite3.Row
            db = db.cursor()
            db.execute("INSERT INTO subscriptions (follower_id, following_id) VALUES (?, ?)", (session['user_id'], user_id))
            db.commit()
    return redirect(url_for('profile', user_id=user_id))

def get_db_connection():
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/chat/<string:sender>/<string:recipient>')
def chat(sender, recipient):
    logging.debug(f"Sender: {sender}, Recipient: {recipient}")
    
    conn = get_db_connection()
    recipient_data = conn.execute('SELECT email, avatar, bio FROM users WHERE email = ?', (recipient,)).fetchone()
    conn.close()

    if not recipient_data:
        logging.error(f"Recipient {recipient} not found")
        return "Recipient not found", 404

    return render_template('chat.html', sender=sender, recipient=recipient, recipient_data=dict(recipient_data))

@app.route('/get_messages/<string:sender>/<string:recipient>', methods=['GET'])
def get_messages(sender, recipient):
    conn = get_db_connection()
    messages = conn.execute('''
        SELECT sender, recipient, content, timestamp 
        FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?) 
        ORDER BY timestamp ASC''', (sender, recipient, recipient, sender)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in messages])

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.json
    sender = data.get('sender')
    recipient = data.get('recipient')
    content = data.get('content')

    if not sender or not recipient or not content:
        return jsonify({"error": "Invalid data"}), 400

    conn = get_db_connection()
    conn.execute('INSERT INTO messages (sender, recipient, content) VALUES (?, ?, ?)',
                 (sender, recipient, content))
    conn.commit()
    conn.close()

    # Emit the message to the recipient via socketio
    socketio.emit('new_message', {'sender': sender, 'recipient': recipient, 'content': content}, room=recipient)

    return jsonify({"success": True})

@socketio.on('join_chat')
def join_chat(data):
    room = data['recipient']
    join_room(room)

@socketio.on('send_message')
def handle_message(data):
    emit('receive_message', data, room=data['recipient'])

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files:
        return redirect(request.url)
    file = request.files['image']
    if file.filename == '':
        return redirect(request.url)
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return render_template('formpage.html', uploaded_image=filename)
                               
@app.route('/process_image', methods=['POST'])
def process_image():
    filename = request.form['filename']
    if not os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        return "File not found", 404
    processed_image = spill_vector.predict_spread(filename)
    if processed_image is None:
        return jsonify(success=False, message="Не удалось найти подходящую область с чёрными пикселями."), 400
    unique_filename = f"{uuid.uuid4().hex[:8]}.jpeg"
    processed_image_path = os.path.join(app.config['PREDICTIONS_FOLDER'], unique_filename)
    with open(processed_image_path, 'wb') as f:
        f.write(processed_image.getvalue())
    return render_template('visual.html', image_path=processed_image_path)

@app.route('/static/predictions/', methods=['GET'])
def get_predictions():
    try:
        files = os.listdir(app.config['PREDICTIONS_FOLDER'])
        image_files = [
            url_for('static', filename=f'predictions/{file}')
            for file in files if allowed_file(file)
        ]
        return jsonify(image_files)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/auth', methods=['GET', 'POST'])
def auth():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        with sqlite3.connect(db_file) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
            user = c.fetchone()
            if user:
                session['user_id'] = user[0]
                return jsonify(success=True)
            else:
                return jsonify(success=False, message="Invalid credentials"), 401
    return render_template('auth.html')

@app.route('/register', methods=['POST'])
def register():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        try:
            # Make sure 'name' is correctly inserted as 'username'
            c.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)", (name, email, password))
            conn.commit()
            return jsonify(success=True)
        except sqlite3.IntegrityError:
            return jsonify(success=False, message="User already exists"), 400


@app.route('/index')
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth'))
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT username, email, avatar FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
    return render_template('index.html', user=user)

@app.route('/index.html')
def index_html():
    return redirect(url_for('index'))

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth'))

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    new_password = request.form.get('new_password')
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))
        conn.commit()
    return jsonify(success=True)

@app.route('/get_form_history')
def get_form_history():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("SELECT id, image_path, title, description FROM form_history WHERE user_id = ?", (user_id,))
        history = c.fetchall()
    return jsonify([{'id': row[0], 'image_path': row[1], 'title': row[2], 'description': row[3]} for row in history])

@app.route('/add_form_history', methods=['POST'])
def add_form_history():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    data = request.json
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO form_history (user_id, image_path, title, description) VALUES (?, ?, ?, ?)",
                  (user_id, data['image_path'], data['title'], data['description']))
        conn.commit()
    return jsonify(success=True)

@app.route('/update_form_history', methods=['POST'])
def update_form_history():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    data = request.json
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("UPDATE form_history SET title = ?, description = ? WHERE id = ? AND user_id = ?",
                  (data['title'], data['description'], data['id'], user_id))
        conn.commit()
    return jsonify(success=True)

@app.route('/delete_form_history', methods=['POST'])
def delete_form_history():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in"), 401
    user_id = session['user_id']
    data = request.json
    with sqlite3.connect(db_file) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM form_history WHERE id = ? AND user_id = ?", (data['id'], user_id))
        conn.commit()
    return jsonify(success=True)


if __name__ == '__main__':
    init_db()
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    socketio.run(app, debug=True)
