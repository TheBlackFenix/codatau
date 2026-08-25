from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlsplit

from app.extensions import db
from app.models.user import User
from app.forms.auth_forms import LoginForm, RegisterForm

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _safe_next_url(target):
    if not target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/'):
        return None
    return target


@auth_bp.route('/inicio')
def inicio():
    if current_user.is_authenticated:
        return redirect(url_for('auth.bienvenida'))
    return render_template('auth/inicio.html')


@auth_bp.route('/bienvenida')
@login_required
def bienvenida():
    return render_template('auth/bienvenida.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.bienvenida'))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(form.password.data):
            if login_user(user, remember=form.remember.data):
                next_page = _safe_next_url(request.args.get('next'))
                flash(f'Hola {user.username}, bienvenido de nuevo a CoDataU.', 'success')
                return redirect(next_page or url_for('auth.bienvenida'))
            flash('Tu cuenta está inactiva.', 'danger')
            return render_template('auth/login.html', form=form)
        flash('Correo o contraseña incorrectos.', 'danger')

    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.bienvenida'))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.strip().lower(),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('El usuario o correo ya está registrado.', 'danger')
            return render_template('auth/register.html', form=form)
        flash('Cuenta creada correctamente. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)

@auth_bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def edit_profile():
    from app.forms.auth_forms import EditProfileForm
    form = EditProfileForm(original_username=current_user.username)

    if form.validate_on_submit():
        current_user.username = form.username.data.strip()
        db.session.commit()
        flash('Perfil actualizado correctamente.', 'success')
        return redirect(url_for('auth.edit_profile'))

    form.username.data = form.username.data or current_user.username
    return render_template('auth/edit_profile.html', form=form)


@auth_bp.route('/cambiar-contrasena', methods=['GET', 'POST'])
@login_required
def change_password():
    from app.forms.auth_forms import ChangePasswordForm
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('La contraseña actual es incorrecta.', 'danger')
            return redirect(url_for('auth.change_password'))
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Contraseña cambiada correctamente.', 'success')
        return redirect(url_for('auth.change_password'))

    return render_template('auth/change_password.html', form=form)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('auth.inicio'))
