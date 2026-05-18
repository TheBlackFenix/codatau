from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from app.models.user import User


class LoginForm(FlaskForm):
    email    = StringField('Correo electrónico', validators=[DataRequired(), Email()])
    password = PasswordField('Contraseña', validators=[DataRequired()])
    remember = BooleanField('Recordarme')
    submit   = SubmitField('Iniciar sesión')


class RegisterForm(FlaskForm):
    username  = StringField('Nombre de usuario', validators=[DataRequired(), Length(3, 80)])
    email     = StringField('Correo electrónico', validators=[DataRequired(), Email()])
    password  = PasswordField('Contraseña', validators=[DataRequired(), Length(min=6)])
    password2 = PasswordField('Confirmar contraseña', validators=[DataRequired(), EqualTo('password')])
    submit    = SubmitField('Crear cuenta')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('Ese nombre de usuario ya está en uso.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Ese correo ya está registrado.')
        
class EditProfileForm(FlaskForm):
    username = StringField('Nombre de usuario', validators=[DataRequired(), Length(3, 80)])
    submit   = SubmitField('Guardar cambios')

    def __init__(self, original_username, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_username = original_username

    def validate_username(self, field):
        if field.data != self.original_username:
            if User.query.filter_by(username=field.data).first():
                raise ValidationError('Ese nombre de usuario ya está en uso.')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Contraseña actual', validators=[DataRequired()])
    new_password     = PasswordField('Nueva contraseña', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirmar nueva contraseña',
                                     validators=[DataRequired(), EqualTo('new_password')])
    submit           = SubmitField('Cambiar contraseña')