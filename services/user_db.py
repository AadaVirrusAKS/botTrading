"""
User Database - SQLite-based user management for BOTradeAI.
Handles user registration, authentication, and profile management.
"""
import sqlite3
import os
import hashlib
import secrets
from datetime import datetime
from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, 'users.db')


def get_db():
    """Get a database connection with row factory."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create the users table if it doesn't exist."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login TEXT,
            is_active INTEGER DEFAULT 1,
            reset_token TEXT,
            reset_token_expiry TEXT
        )
    ''')
    conn.commit()
    conn.close()


def _hash_password(password, salt=None):
    """Hash password with PBKDF2-SHA256."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"


def _verify_password(password, password_hash):
    """Verify a password against its hash."""
    salt = password_hash.split('$')[0]
    return _hash_password(password, salt) == password_hash


def create_user(email, password, first_name, last_name, phone=''):
    """Register a new user. Returns (user_id, None) or (None, error_message)."""
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            '''INSERT INTO users (email, password_hash, first_name, last_name, phone, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (email.lower().strip(), _hash_password(password), first_name.strip(),
             last_name.strip(), phone.strip(), now, now)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0], None
    except sqlite3.IntegrityError:
        return None, 'An account with this email already exists.'
    finally:
        conn.close()


def authenticate_user(email, password):
    """Authenticate a user. Returns user row or None."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ? AND is_active = 1",
                        (email.lower().strip(),)).fetchone()
    if user and _verify_password(password, user['password_hash']):
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                     (datetime.now().isoformat(), user['id']))
        conn.commit()
        conn.close()
        return user
    conn.close()
    return None


def get_user_by_id(user_id):
    """Fetch user by ID."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def get_user_by_email(email):
    """Fetch user by email."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?",
                        (email.lower().strip(),)).fetchone()
    conn.close()
    return user


def update_user_profile(user_id, first_name, last_name, phone):
    """Update user profile details."""
    conn = get_db()
    conn.execute(
        "UPDATE users SET first_name=?, last_name=?, phone=?, updated_at=? WHERE id=?",
        (first_name.strip(), last_name.strip(), phone.strip(),
         datetime.now().isoformat(), user_id)
    )
    conn.commit()
    conn.close()


def change_password(user_id, old_password, new_password):
    """Change password. Returns (True, None) or (False, error_message)."""
    conn = get_db()
    user = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return False, 'User not found.'
    if not _verify_password(old_password, user['password_hash']):
        conn.close()
        return False, 'Current password is incorrect.'
    conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                 (_hash_password(new_password), datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()
    return True, None


def generate_reset_token(email):
    """Generate a password reset token. Returns (token, None) or (None, error)."""
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email = ? AND is_active = 1",
                        (email.lower().strip(),)).fetchone()
    if not user:
        conn.close()
        return None, 'No account found with this email.'
    token = secrets.token_urlsafe(32)
    from datetime import timedelta
    expiry = (datetime.now() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE users SET reset_token=?, reset_token_expiry=? WHERE id=?",
                 (token, expiry, user['id']))
    conn.commit()
    conn.close()
    return token, None


def reset_password_with_token(token, new_password):
    """Reset password using a token. Returns (True, None) or (False, error)."""
    conn = get_db()
    user = conn.execute(
        "SELECT id, reset_token_expiry FROM users WHERE reset_token = ? AND is_active = 1",
        (token,)).fetchone()
    if not user:
        conn.close()
        return False, 'Invalid or expired reset link.'
    if user['reset_token_expiry'] and datetime.fromisoformat(user['reset_token_expiry']) < datetime.now():
        conn.close()
        return False, 'Reset link has expired. Please request a new one.'
    conn.execute(
        "UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expiry=NULL, updated_at=? WHERE id=?",
        (_hash_password(new_password), datetime.now().isoformat(), user['id']))
    conn.commit()
    conn.close()
    return True, None


# Initialize DB on import
init_db()
