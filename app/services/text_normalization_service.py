"""Opt-in, bounded text transformations; no inference or generated code."""
import re
import unicodedata
from app.services.missing_value_service import MissingValueService as Missing


class TextNormalizationService:
    VERSION = 'text-v1'
    FLAGS = ('trim', 'collapse_spaces', 'clean_controls', 'nfc')
    CASES = {'keep', 'upper', 'lower'}

    @classmethod
    def parse(cls, form, key, kind):
        if kind not in {'text', 'identifier'}:
            return {}
        options = {flag: True for flag in cls.FLAGS if form.get(key+'_text_'+flag) == '1'}
        case = form.get(key+'_text_case', 'keep')
        if case not in cls.CASES:
            raise ValueError('Elige si deseas conservar, usar mayúsculas o usar minúsculas.')
        if case != 'keep':
            options['case'] = case
        return {'version': cls.VERSION, **options} if options else {}

    @classmethod
    def normalize(cls, value, preference):
        options = preference.get('text_rules', {})
        if not isinstance(value, str) or options.get('version') != cls.VERSION or preference.get('type') not in {'text', 'identifier'}:
            return value
        if options.get('clean_controls'):
            value = ''.join(char for char in value if
                            (unicodedata.category(char) != 'Cc' or char in '\r\n\t') and char not in '\u200b\ufeff')
        if options.get('collapse_spaces'):
            value = re.sub(r'[^\S\r\n]+', ' ', value)
        if options.get('trim'):
            # Horizontal exterior whitespace only: preserve intentional newlines.
            value = value.strip(' \t\u00a0\u202f\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a')
        if options.get('nfc'):
            value = unicodedata.normalize('NFC', value)
        if options.get('case') == 'upper':
            value = value.upper()
        elif options.get('case') == 'lower':
            value = value.lower()
        return value

    @classmethod
    def summaries(cls, dataframe, preferences):
        from app.services.category_dictionary_service import CategoryDictionaryService as Categories
        summaries = []
        for column, pref in preferences.items():
            if pref.get('type') not in {'text', 'identifier'} or pref.get('text_rules', {}).get('version') != cls.VERSION:
                continue
            count, empty_count, examples = 0, 0, []
            for value in dataframe[column]:
                if not isinstance(value, str) or not value.strip() or Missing.matches(value, pref):
                    continue
                if Categories.replacement(value, pref) is not None:
                    continue
                after = cls.normalize(value, pref)
                if after == value:
                    continue
                count += 1
                empty_count += not after.strip()
                example = {'before': value[:120], 'after': after[:120]}
                if len(examples) < 3 and example not in examples:
                    examples.append(example)
            if count:
                summaries.append({'column': column, 'count': count, 'empty_count': empty_count, 'examples': examples})
        return summaries
