import hashlib
import math
import numbers
import re
from decimal import Decimal
import pandas as pd
from app.services.date_normalization_service import DateNormalizationService as Dates


class CleaningTreatmentService:
    ACTIONS = {'keep': 'Conservar como está', 'quarantine': 'Separar la fila para revisión',
               'replace': 'Reemplazar solamente la celda', 'null': 'Dejar la celda vacía',
               'ai': 'Pedir una propuesta a la IA',
               'absolute': 'Convertir todos estos negativos a positivo',
               'date_dmy': 'Interpretar este grupo como día/mes/año',
               'date_mdy': 'Interpretar este grupo como mes/día/año'}

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
            ambiguous = pd.Series(False, index=series.index)
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
                statuses = series.map(lambda value: Dates.inspect(value, pref)[0])
                invalid = statuses.eq('invalid')
                ambiguous = statuses.eq('ambiguous')
            scenarios = [('missing', 'Valores vacíos', empty), ('invalid', 'Valores que no coinciden con el tipo elegido', invalid & ~empty)]
            if kind == 'date':
                scenarios.append(('date_ambiguous', 'Fechas que necesitan confirmar el orden del día y mes', ambiguous))
            constraints = roles.get(name, {}).get('suggested_constraints', [])
            negative_policy = pref.get('negative_policy', 'unknown')
            if numeric is not None and negative_policy == 'deny':
                scenarios.append(('negative', 'Valores negativos no permitidos por tu regla', numeric.lt(0) & ~empty & ~invalid))
            elif numeric is not None and negative_policy != 'allow' and not any(k in pref for k in ('minimum','maximum')) and any(c in constraints for c in ('positive','non_negative')):
                mask = numeric.le(0) if 'positive' in constraints else numeric.lt(0)
                scenarios.append(('range', 'Valores fuera del rango sugerido por el contexto', mask & ~empty & ~invalid))
            if numeric is not None and any(k in pref for k in ('minimum', 'maximum')):
                def outside(value):
                    if cls.number(value, pref) is None:
                        return False
                    number = Decimal(str(value).strip().replace(pref.get('decimal_separator', '.'), '.'))
                    if negative_policy == 'deny' and number < 0:
                        return False  # The sign group already owns this cell.
                    return (('minimum' in pref and number < Decimal(pref['minimum'])) or
                            ('maximum' in pref and number > Decimal(pref['maximum'])))
                mask = series.map(outside) & ~empty & ~invalid
                scenarios.append(('bounds', 'Valores fuera del rango que definiste', mask))
            for scenario, label, mask in scenarios:
                if not mask.any(): continue
                key = hashlib.sha256((name+'\0'+scenario).encode()).hexdigest()[:24]
                examples = list(dict.fromkeys(str(value)[:100] for value in series.loc[mask].head(20) if pd.notna(value)))[:3]
                issues.append({'id':key, 'column':name, 'scenario':scenario, 'label':label,
                               'count':int(mask.sum()), 'examples':examples, 'type':kind})
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
            if action in {'date_dmy', 'date_mdy'} and issue['scenario'] != 'date_ambiguous':
                raise ValueError('Confirma el orden de fechas únicamente para un grupo de fechas ambiguas.')
            if action == 'ai' and issue.get('type') == 'date':
                raise ValueError('Resuelve las fechas por grupo; no pediremos a la IA adivinar fechas individuales.')
            item = {'action':action}
            if action == 'replace':
                replacement = form.get('replacement_'+issue['id'], '').strip()
                if not replacement or len(replacement)>200:
                    raise ValueError('Escribe un reemplazo de entre 1 y 200 caracteres.')
                item['replacement'] = replacement
            result[issue['id']] = item
        return {'decisions':result}
