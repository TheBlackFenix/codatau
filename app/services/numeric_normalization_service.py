"""Versioned numeric grammars shared by diagnosis, proposals and execution."""
from dataclasses import dataclass
from decimal import Decimal
import numbers
import re

from text_to_num import text2num
from app.services.missing_value_service import MissingValueService as Missing


@dataclass(frozen=True)
class NumericResult:
    status: str
    value: object = None
    rule: str = ''


class NumericNormalizationService:
    VERSION = 'standard-es-v1'
    SOURCES = {'auto', 'point', 'comma'}
    RULES = {
        'numeric_text': 'Texto numérico y espacios exteriores',
        'localized_number': 'Separadores decimales y de miles',
        'scientific': 'Notación científica',
        'spanish_integer': 'Números enteros escritos en español',
    }

    @staticmethod
    def _localized(text, source):
        decimal, group = ('.', ',') if source == 'point' else (',', '.')
        pattern = r'[+-]?(?:\d+|\d{1,3}(?:' + re.escape(group) + r'\d{3})+)(?:' + re.escape(decimal) + r'\d+)?'
        if not re.fullmatch(pattern, text):
            return None
        return Decimal(text.replace(group, '').replace(decimal, '.'))

    @classmethod
    def inspect(cls, value, preference):
        if isinstance(value, bool):
            return NumericResult('invalid')
        if isinstance(value, (numbers.Real, Decimal)):
            parsed = Decimal(str(value))
            return NumericResult('valid', parsed, 'numeric_text') if parsed.is_finite() else NumericResult('invalid')
        if not isinstance(value, str) or len(value) > 200:
            return NumericResult('invalid')
        text = value.strip()
        if not text:
            return NumericResult('missing')
        enabled = preference.get('numeric_rules') == cls.VERSION
        if not enabled:
            separator = preference.get('decimal_separator', '.')
            if not re.fullmatch(r'[+-]?\d+(?:' + re.escape(separator) + r'\d+)?', text):
                return NumericResult('invalid')
            return NumericResult('valid', Decimal(text.replace(separator, '.')), 'numeric_text')
        # No extraction of a numeric substring from an arbitrary sentence.
        words = text.casefold()
        sign = 1
        if words.startswith('menos '):
            sign, words = -1, words[6:]
        if re.fullmatch(r'[a-záéíóúüñ]+(?: [a-záéíóúüñ]+)*', words):
            try:
                parsed = Decimal(text2num(words, 'es'))
                if sign < 0:
                    parsed = parsed.copy_negate()
            except ValueError:
                return NumericResult('invalid')
            return NumericResult('valid', parsed, 'spanish_integer')
        if re.fullmatch(r'[+-]?\d+(?:\.\d+)?[eE][+-]?\d{1,3}', text):
            parsed = Decimal(text)
            if not parsed.is_finite() or abs(parsed.adjusted()) > 308:
                return NumericResult('invalid')
            return NumericResult('valid', parsed, 'scientific')
        source = preference.get('number_source', 'auto')
        if source not in cls.SOURCES:
            return NumericResult('invalid')
        if source == 'auto':
            possibilities = {v for s in ('point', 'comma') if (v := cls._localized(text, s)) is not None}
            if len(possibilities) > 1:
                return NumericResult('ambiguous')
            if not possibilities:
                return NumericResult('invalid')
            parsed = possibilities.pop()
        else:
            parsed = cls._localized(text, source)
            if parsed is None:
                return NumericResult('invalid')
        separator = preference.get('decimal_separator', '.')
        canonical = bool(re.fullmatch(r'[+-]?\d+(?:' + re.escape(separator) + r'\d+)?', text))
        if canonical:
            canonical = Decimal(text.replace(separator, '.')) == parsed
        return NumericResult('valid', parsed, 'numeric_text' if canonical else 'localized_number')

    @staticmethod
    def allowed(value, preference):
        return ((preference.get('type') != 'integer' or value == value.to_integral_value()) and
                (preference.get('negative_policy') != 'deny' or value >= 0) and
                ('minimum' not in preference or value >= Decimal(preference['minimum'])) and
                ('maximum' not in preference or value <= Decimal(preference['maximum'])))

    @classmethod
    def convert(cls, value, preference):
        result = cls.inspect(value, preference)
        if result.status == 'missing':
            return None
        if result.status != 'valid':
            raise ValueError('El número no tiene una interpretación inequívoca con el formato elegido.')
        if preference['type'] == 'integer':
            if result.value != result.value.to_integral_value():
                raise ValueError('No convertiremos una fracción a entero redondeando o truncando.')
            return int(result.value)
        floating = float(result.value)
        # Preserve representations which cannot round-trip through the current
        # analytical double type; the executor marks the column as mixed/text.
        if not Decimal(str(floating)).is_finite() or Decimal(str(floating)) != result.value:
            return result.value
        return floating

    @classmethod
    def summaries(cls, dataframe, preferences):
        summaries = []
        for column, pref in preferences.items():
            if pref.get('type') not in {'integer', 'decimal'} or pref.get('numeric_rules') != cls.VERSION:
                continue
            groups = {}
            for value in dataframe[column]:
                if Missing.matches(value, pref):
                    continue
                result = cls.inspect(value, pref)
                if result.status != 'valid' or not cls.allowed(result.value, pref):
                    continue
                group = groups.setdefault(result.rule, {'column': column, 'rule': cls.RULES[result.rule], 'count': 0, 'examples': []})
                group['count'] += 1
                example = {'before': str(value), 'after': format(result.value, 'f')}
                if len(group['examples']) < 3 and example not in group['examples']:
                    group['examples'].append(example)
            summaries.extend(groups.values())
        return summaries
