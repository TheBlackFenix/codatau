"""User-confirmed exact category aliases, never fuzzy or provider-generated."""
import hashlib
from collections import Counter

from app.services.missing_value_service import MissingValueService as Missing
from app.services.text_normalization_service import TextNormalizationService as Texts


class CategoryDictionaryService:
    VERSION = 'categories-v1'
    MAX_MAPPINGS = 500

    @staticmethod
    def key(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    @classmethod
    def values(cls, series, preference):
        counts = Counter()
        excluded = 0
        for value in series:
            if not isinstance(value, str) or not value.strip() or Missing.matches(value, preference):
                excluded += 1
                continue
            prepared = Texts.normalize(value, preference)
            if not prepared.strip() or len(prepared) > 200 or Missing.matches(prepared, preference):
                excluded += 1
                continue
            counts[prepared] += 1
        return [{'id': cls.key(value), 'value': value, 'count': count}
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))], excluded

    @classmethod
    def validate(cls, mapping, preference):
        if not isinstance(mapping, dict) or len(mapping) > cls.MAX_MAPPINGS:
            raise ValueError('El diccionario admite hasta 500 equivalencias por columna.')
        for source, target in mapping.items():
            if not isinstance(source, str) or not isinstance(target, str) or not source.strip() or not target.strip() or max(len(source), len(target)) > 200:
                raise ValueError('Cada equivalencia debe usar textos de entre 1 y 200 caracteres.')
            if Texts.normalize(source, preference) != source or Texts.normalize(target, preference) != target:
                raise ValueError('El valor final debe respetar las reglas de texto elegidas. Ajusta el nombre final o revisa esas reglas.')
            if Missing.matches(source, preference) or Missing.matches(target, preference):
                raise ValueError('Un marcador de dato ausente se resuelve en tratamientos, no como categoría.')
            if target in mapping and mapping[target] != target:
                raise ValueError('Esta equivalencia crearía una cadena o un ciclo. Quita primero el grupo relacionado y define un único valor final.')
        return {'version': cls.VERSION, 'mapping': mapping}

    @classmethod
    def add(cls, dictionary, selected, target, available, preference):
        selected = list(dict.fromkeys(selected))
        target = target.strip()
        by_id = {item['id']: item['value'] for item in available}
        if not selected or len(selected) > 50 or any(key not in by_id for key in selected):
            raise ValueError('Elige entre 1 y 50 valores encontrados en esta columna.')
        if not any(by_id[key] != target for key in selected):
            raise ValueError('Selecciona al menos un valor diferente del nombre final.')
        mapping = dict(dictionary.get('mapping', {}))
        for key in selected:
            source = by_id[key]
            if source == target:
                mapping.pop(source, None)
            else:
                mapping[source] = target
        return cls.validate(mapping, preference)

    @classmethod
    def replacement(cls, value, preference):
        dictionary = preference.get('category_dictionary', {})
        if preference.get('type') != 'text' or dictionary.get('version') != cls.VERSION or not isinstance(value, str) or Missing.matches(value, preference):
            return None
        prepared = Texts.normalize(value, preference)
        if not prepared.strip() or Missing.matches(prepared, preference):
            return None
        target = dictionary.get('mapping', {}).get(prepared)
        return target if target is not None and target != value else None

    @classmethod
    def summaries(cls, dataframe, preferences):
        summaries = []
        for column, preference in preferences.items():
            if not preference.get('category_dictionary'):
                continue
            count, examples = 0, []
            for value in dataframe[column]:
                after = cls.replacement(value, preference)
                if after is None:
                    continue
                count += 1
                example = {'before': value[:200], 'after': after}
                if len(examples) < 5 and example not in examples:
                    examples.append(example)
            if count:
                summaries.append({'column': column, 'count': count, 'examples': examples})
        return summaries
