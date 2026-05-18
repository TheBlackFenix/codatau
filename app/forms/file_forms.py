from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import SubmitField


class UploadForm(FlaskForm):
    file   = FileField('Archivo', validators=[
        FileRequired(message='Debes seleccionar un archivo.'),
        FileAllowed(['xlsx', 'xls', 'csv'], message='Solo se permiten archivos .xlsx, .xls o .csv')
    ])
    submit = SubmitField('Cargar y procesar')