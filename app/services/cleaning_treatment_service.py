import hashlib
import math
import numbers
import re
import pandas as pd


class CleaningTreatmentService:
    ACTIONS = {'keep': 'Conservar como está', 'quarantine': 'Separar la fila para revisión',
               'replace': 'Reemplazar solamente la celda', 'null': 'Dejar la celda vacía',
               'ai': 'Pedir una propuesta a la IA',
               'absolute': 'Convertir todos estos negativos a positivo'}

    @staticmethod
    def number(value, preference):
        if isinstance(value, bool): return None
        if isinstance(value, numbers.Real):
            return float(value) if math.isfinite(float(value)) else None
        separator = preference.get('decimal_separator', '.')
        text = str(value).strip()
        if not re.fullmatch(r'[+-]?\d+(?:' + re.escape(separator) + r'\d+)?', text): return None
        result = float(text.replace(separator, '.'))
        return result if math.isfinite(result) else None

    @classmethod
    def issues(cls, dataframe, preferences, context=None, include_rows=False):
        issues = []
        roles = {item['column']: item for item in (context or {}).get('column_roles', [])}
        for name in dataframe.columns:
            pref = preferences[name]
            series = dataframe[name]
            empty = series.isna() | series.map(lambda value: isinstance(value, str) and not value.strip())
            kind = pref['type']
            invalid = pd.Series(False, index=series.index)
            numeric = None
            if kind in {'integer','decimal'}:
                numeric = series.map(lambda value: cls.number(value, pref))
                invalid = numeric.isna()
                if kind == 'integer': invalid |= numeric.map(lambda value: pd.notna(value) and not float(value).is_integer())
            elif kind == 'email':
                invalid = ~series.map(lambda value: bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', str(value).strip())))
            elif kind == 'phone':
                invalid = ~series.map(lambda value: bool(re.fullmatch(r'\+?[\d\s().-]+', str(value).strip())) and 7 <= len(re.sub(r'\D','',str(value))) <= 15)
            elif kind == 'boolean':
                invalid = ~series.map(lambda value: str(value).strip().casefold() in {'true','false','sí','si','no','1','0','yes'})
            elif kind == 'date':
                fmt = {'iso':'ISO8601','dmy':'%d/%m/%Y','mdy':'%m/%d/%Y'}[pref['date_format']]
                invalid = pd.to_datetime(series, format=fmt, errors='coerce', utc=True).isna()
            scenarios = [('missing', 'Valores vacíos', empty), ('invalid', 'Valores que no coinciden con el tipo elegido', invalid & ~empty)]
            constraints = roles.get(name, {}).get('suggested_constraints', [])
            negative_policy = pref.get('negative_policy', 'unknown')
            if numeric is not None and negative_policy == 'deny':
                scenarios.append(('negative', 'Valores negativos no permitidos por tu regla', numeric.lt(0) & ~empty & ~invalid))
            elif numeric is not None and negative_policy != 'allow' and any(c in constraints for c in ('positive','non_negative')):
                mask = numeric.le(0) if 'positive' in constraints else numeric.lt(0)
                scenarios.append(('range', 'Valores fuera del rango sugerido por el contexto', mask & ~empty & ~invalid))
            for scenario, label, mask in scenarios:
                if not mask.any(): continue
                key = hashlib.sha256((name+'\0'+scenario).encode()).hexdigest()[:24]
                examples = list(dict.fromkeys(str(value)[:100] for value in series.loc[mask].head(20) if pd.notna(value)))[:3]
                issues.append({'id':key, 'column':name, 'scenario':scenario, 'label':label,
                               'count':int(mask.sum()), 'examples':examples})
                if include_rows:
                    issues[-1]['rows'] = list(series.index[mask])
        return issues

    @classmethod
    def parse(cls, form, issues):
        result = {}
        for issue in issues:
            action = form.get('action_'+issue['id'])
            if action not in cls.ACTIONS:
                raise ValueError('Elige un tratamiento válido para cada escenario.')
            if action == 'ai' and issue['scenario'] == 'missing':
                raise ValueError('No pediremos a la IA inventar valores vacíos. Elige otro tratamiento.')
            if action == 'absolute' and issue['scenario'] != 'negative':
                raise ValueError('Solo puedes convertir el grupo a positivo si confirmaste que no permite negativos.')
            item = {'action':action}
            if action == 'replace':
                replacement = form.get('replacement_'+issue['id'], '').strip()
                if not replacement or len(replacement)>200:
                    raise ValueError('Escribe un reemplazo de entre 1 y 200 caracteres.')
                item['replacement'] = replacement
            result[issue['id']] = item
        return {'decisions':result}
