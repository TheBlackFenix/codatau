import re
from decimal import Decimal


class ColumnTypeService:
    TYPES = {'text': 'Texto', 'identifier': 'Identificador o código', 'integer': 'Número entero',
             'decimal': 'Número decimal', 'date': 'Fecha', 'email': 'Correo', 'phone': 'Teléfono', 'boolean': 'Sí/No'}
    DATES = {'iso': 'Año-mes-día (2026-10-18)', 'dmy': 'Día/mes/año (18/10/2026)', 'mdy': 'Mes/día/año (10/18/2026)'}

    @classmethod
    def cards(cls, profile, context, saved=None):
        roles = {item['column']: item for item in (context or {}).get('column_roles', [])}
        cards = []
        for index, column in enumerate(profile['columns']):
            name, physical = column['name'], column['type'].upper()
            semantic = (column.get('semantic') or {}).get('type')
            role = roles.get(name, {}).get('role')
            inferred = 'text'
            if semantic in {'email', 'phone', 'identifier', 'date', 'boolean'}:
                inferred = semantic
            elif role in {'identifier', 'date', 'boolean'}:
                inferred = role
            elif re.search(r'(^|_)(id|codigo|documento|guia|telefono|cedula)($|_)', name.lower()):
                inferred = 'phone' if 'telefono' in name.lower() else 'identifier'
            elif any(word in physical for word in ('INT',)):
                inferred = 'integer'
            elif any(word in physical for word in ('DOUBLE', 'FLOAT', 'DECIMAL', 'REAL')):
                inferred = 'decimal'
            elif 'DATE' in physical or 'TIMESTAMP' in physical:
                inferred = 'date'
            elif 'BOOL' in physical:
                inferred = 'boolean'
            elif semantic == 'number':
                inferred = 'decimal'
            examples = []
            for row in profile.get('sample', []):
                value = row.get(name)
                if value is not None and str(value) not in examples:
                    examples.append(str(value)[:100])
                if len(examples) == 3:
                    break
            choice = (saved or {}).get(name, {})
            cards.append({'index': index, 'name': name, 'suggested': inferred,
                          'choice': choice or {'type': inferred, 'decimal_separator': '.', 'date_format': 'iso', 'date_source': 'auto'},
                          'examples': examples})
        return cards

    @classmethod
    def parse(cls, form, cards):
        choices = {}
        for card in cards:
            key = f'column_{card["index"]}'
            kind = form.get(key)
            if kind not in cls.TYPES:
                raise ValueError('Elige un tipo válido para cada columna.')
            choice = {'type': kind}
            if kind in {'integer', 'decimal'}:
                policy = form.get(key + '_negative', 'unknown')
                if policy not in {'allow', 'deny', 'unknown'}:
                    raise ValueError('Indica si este campo permite valores negativos.')
                choice['negative_policy'] = policy
                bounds = {}
                for field, label in [('minimum', 'mínimo'), ('maximum', 'máximo')]:
                    value = form.get(key + '_' + field, '').strip()
                    if not value:
                        continue
                    if len(value) > 64 or not re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', value):
                        raise ValueError(f'El {label} de {card["name"]} debe ser un número sin separadores de miles.')
                    bound = Decimal(value.replace(',', '.'))
                    if kind == 'integer' and bound != bound.to_integral_value():
                        raise ValueError(f'El {label} de un campo entero no puede tener fracciones.')
                    if policy == 'deny' and bound < 0:
                        raise ValueError('Si no permites negativos, los límites tampoco pueden ser negativos.')
                    bounds[field] = str(bound)
                if len(bounds) == 2 and Decimal(bounds['minimum']) > Decimal(bounds['maximum']):
                    raise ValueError('El mínimo no puede ser mayor que el máximo.')
                choice.update(bounds)
            if kind == 'decimal':
                separator = form.get(key + '_separator')
                if separator not in {'.', ','}:
                    raise ValueError('Indica cómo se separan los decimales.')
                choice['decimal_separator'] = separator
            elif kind == 'date':
                date_format = form.get(key + '_date')
                if date_format not in cls.DATES:
                    raise ValueError('Indica el orden del día, mes y año.')
                choice['date_format'] = date_format
                source = form.get(key + '_date_source', date_format)
                if source not in {'auto', 'dmy', 'mdy', 'iso'}:
                    raise ValueError('Indica cómo interpretar el día y mes en las fechas de origen.')
                choice['date_source'] = source
            choices[card['name']] = choice
        return choices
