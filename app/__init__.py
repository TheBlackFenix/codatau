from flask import Flask, redirect, url_for, render_template
from flask_login import current_user
from app.config import config
from app.extensions import db, login_manager, csrf


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Inicializar extensiones
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Crear carpeta de uploads si no existe
    import os
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Registrar blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.files import files_bp
    from app.routes.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(reports_bp)

    # Importar modelos para que SQLAlchemy los registre
    from app.models import User, FileUpload, AIInsight

    # Crear tablas de la base de datos
    with app.app_context():
        db.create_all()

    # Ruta raíz
    @app.route('/')
    def home():
        if current_user.is_authenticated:
            return redirect(url_for('auth.bienvenida'))
        return render_template('auth/inicio.html')

    # Manejo de archivo demasiado grande
    @app.errorhandler(413)
    def too_large(e):
        from flask import flash
        flash('El archivo es demasiado grande. El límite es 50 MB.', 'danger')
        return redirect(url_for('files.upload'))

    return app