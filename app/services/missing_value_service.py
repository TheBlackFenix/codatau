"""Literal, per-column missing-value markers. No global NA dictionary."""


class MissingValueService:
    VERSION = 'markers-v1'

    @classmethod
    def parse(cls, form, key):
        raw = form.get(key+'_missing_markers', '')
        if len(raw) > 2020:
            raise ValueError('Usa hasta 20 marcadores de vacío, de máximo 100 caracteres cada uno.')
        values = list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))
        if len(values) > 20 or any(len(value) > 100 for value in values):
            raise ValueError('Usa hasta 20 marcadores de vacío, de máximo 100 caracteres cada uno.')
        if not values:
            return {}
        ignore_case = form.get(key+'_missing_ignore_case', '')
        if ignore_case not in {'', '1'}:
            raise ValueError('Indica si deseas ignorar mayúsculas y minúsculas en los marcadores.')
        return {'version': cls.VERSION, 'values': values, 'ignore_case': ignore_case == '1'}

    @classmethod
    def matches(cls, value, preference):
        settings = preference.get('missing_values', {})
        if not isinstance(value, str) or settings.get('version') != cls.VERSION:
            return False
        text = value.strip()
        if settings.get('ignore_case'):
            return text.casefold() in {marker.casefold() for marker in settings.get('values', [])}
        return text in settings.get('values', [])
