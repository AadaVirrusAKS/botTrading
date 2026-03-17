"""
Auth Routes - Login, Registration, Password Reset, Profile management.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
import re

from services.user_db import (
    create_user, authenticate_user, get_user_by_id, get_user_by_email,
    update_user_profile, change_password, generate_reset_token, reset_password_with_token
)
from services.bot_engine import init_user_bot_state
from config import BASE_URL

auth_bp = Blueprint("auth", __name__)

# ============================================================================
# Flask-Login User class
# ============================================================================
class User(UserMixin):
    def __init__(self, user_row):
        self.id = user_row['id']
        self.email = user_row['email']
        self.first_name = user_row['first_name']
        self.last_name = user_row['last_name']
        self.phone = user_row['phone'] or ''
        self.created_at = user_row['created_at']
        self.last_login = user_row['last_login']

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}"


# ============================================================================
# Login Manager setup (called from web_app.py)
# ============================================================================
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access BOTradeAI.'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    user_row = get_user_by_id(int(user_id))
    if user_row:
        return User(user_row)
    return None


def init_auth(app):
    """Initialize Flask-Login with the Flask app."""
    login_manager.init_app(app)


# ============================================================================
# Validation helpers
# ============================================================================
def _validate_email(email):
    return re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email)


def _validate_password(password):
    if len(password) < 8:
        return 'Password must be at least 8 characters.'
    if not re.search(r'[A-Z]', password):
        return 'Password must contain at least one uppercase letter.'
    if not re.search(r'[a-z]', password):
        return 'Password must contain at least one lowercase letter.'
    if not re.search(r'[0-9]', password):
        return 'Password must contain at least one number.'
    return None


def _validate_phone(phone):
    if phone and not re.match(r'^[\d\s\-\+\(\)]{7,15}$', phone):
        return 'Please enter a valid phone number.'
    return None


# ============================================================================
# AUTH ROUTES
# ============================================================================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('pages.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('login.html', email=email)

        # Check if the email is registered at all
        existing = get_user_by_email(email)
        if not existing:
            flash('No account found with that email. Please register first.', 'error')
            return redirect(url_for('auth.register'))

        user_row = authenticate_user(email, password)
        if user_row:
            user = User(user_row)
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('pages.index'))
        else:
            flash('Invalid password. Please try again.', 'error')
            return render_template('login.html', email=email)

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('pages.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        phone = request.form.get('phone', '').strip()

        # Validate
        errors = []
        if not first_name:
            errors.append('First name is required.')
        if not last_name:
            errors.append('Last name is required.')
        if not email or not _validate_email(email):
            errors.append('Please enter a valid email address.')
        pwd_err = _validate_password(password)
        if pwd_err:
            errors.append(pwd_err)
        if password != confirm_password:
            errors.append('Passwords do not match.')
        phone_err = _validate_phone(phone)
        if phone_err:
            errors.append(phone_err)

        if errors:
            for err in errors:
                flash(err, 'error')
            return render_template('register.html', email=email, first_name=first_name,
                                   last_name=last_name, phone=phone)

        user_id, err = create_user(email, password, first_name, last_name, phone)
        if err:
            flash(err, 'error')
            return render_template('register.html', email=email, first_name=first_name,
                                   last_name=last_name, phone=phone)

        # Initialize fresh $10,000 demo bot state for this user
        init_user_bot_state(user_id)

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if not email:
            flash('Please enter your email address.', 'error')
            return render_template('forgot_password.html')

        token, err = generate_reset_token(email)
        if err:
            flash(err, 'error')
            return render_template('forgot_password.html', email=email)

        # In production, this would send an email. For local use, show the link directly.
        reset_url = f"{BASE_URL}/reset-password/{token}"
        flash(f'Password reset link (copy this): {reset_url}', 'info')
        return render_template('forgot_password.html', reset_link=reset_url)

    return render_template('forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        pwd_err = _validate_password(password)
        if pwd_err:
            flash(pwd_err, 'error')
            return render_template('reset_password.html', token=token)
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)

        success, err = reset_password_with_token(token, password)
        if success:
            flash('Password reset successfully! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        else:
            flash(err, 'error')
            return render_template('reset_password.html', token=token)

    return render_template('reset_password.html', token=token)


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            phone = request.form.get('phone', '').strip()

            if not first_name or not last_name:
                flash('First and last name are required.', 'error')
            else:
                phone_err = _validate_phone(phone)
                if phone_err:
                    flash(phone_err, 'error')
                else:
                    update_user_profile(current_user.id, first_name, last_name, phone)
                    # Refresh session
                    user_row = get_user_by_id(current_user.id)
                    if user_row:
                        login_user(User(user_row))
                    flash('Profile updated successfully.', 'success')

        elif action == 'change_password':
            old_password = request.form.get('old_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_new_password', '')

            if not old_password or not new_password:
                flash('Please fill in all password fields.', 'error')
            elif new_password != confirm_password:
                flash('New passwords do not match.', 'error')
            else:
                pwd_err = _validate_password(new_password)
                if pwd_err:
                    flash(pwd_err, 'error')
                else:
                    success, err = change_password(current_user.id, old_password, new_password)
                    if success:
                        flash('Password changed successfully.', 'success')
                    else:
                        flash(err, 'error')

        return redirect(url_for('auth.profile'))

    return render_template('profile.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ============================================================================
# API endpoint for auth status (used by JS)
# ============================================================================
@auth_bp.route('/api/auth/status')
def auth_status():
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'user': {
                'name': current_user.display_name,
                'email': current_user.email
            }
        })
    return jsonify({'authenticated': False})
