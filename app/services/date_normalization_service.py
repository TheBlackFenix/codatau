"""Bounded, deterministic date interpretation; never guess an ambiguous order."""
from datetime import date, datetime
import re

import pandas as pd


class DateNormalizationService:
    FORMATS = {'iso': '%Y-%m-%d', 'dmy': '%d/%m/%Y', 'mdy': '%m/%d/%Y'}

    @classmethod
    def inspect(cls, value, preference):
        if pd.isna(value) or (isinstance(value, str) and not value.strip()):
            return 'missing', None
        if isinstance(value, (date, datetime, pd.Timestamp)):
            return 'valid', pd.Timestamp(value).tz_localize('UTC') if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert('UTC')
        if not isinstance(value, str):
            return 'invalid', None  # No guessing Excel serials or Unix timestamps.
        text = value.strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ].+)?', text):
            parsed = pd.to_datetime(text, format='ISO8601', errors='coerce', utc=True)
            return ('invalid', None) if pd.isna(parsed) else ('valid', parsed)
        match = re.fullmatch(r'(\d{1,2})([/.-])(\d{1,2})\2(\d{4})', text)
        if not match:
            return 'invalid', None
        first, _, second, year = match.groups()
        possible = {}
        for order, day, month in [('dmy', first, second), ('mdy', second, first)]:
            try:
                possible[order] = pd.Timestamp(datetime(int(year), int(month), int(day)), tz='UTC')
            except ValueError:
                pass
        if not possible:
            return 'invalid', None
        if len(possible) == 1 or len(set(possible.values())) == 1:
            return 'valid', next(iter(possible.values()))
        # Legacy configurations explicitly used date_format to interpret the input.
        source = preference.get('date_source', preference.get('date_format', 'auto'))
        if source in possible:
            return 'valid', possible[source]
        return 'ambiguous', None

    @classmethod
    def convert(cls, value, preference):
        status, parsed = cls.inspect(value, preference)
        if status == 'missing':
            return None
        if status != 'valid':
            raise ValueError('La fecha no es válida o necesita confirmar el orden del día y mes.')
        return parsed.tz_localize(None)

    @classmethod
    def display(cls, value, preference):
        # Mixed columns serialize typed dates as ISO text. Only re-render ISO text;
        # retained ambiguous or invalid strings must remain untouched.
        if pd.isna(value):
            return None
        if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ].+)?', value.strip()):
            status, parsed = cls.inspect(value, preference)
            if status == 'valid':
                value = parsed.tz_localize(None)
        if not isinstance(value, (date, datetime, pd.Timestamp)):
            return value
        stamp = pd.Timestamp(value)
        result = stamp.strftime(cls.FORMATS[preference['date_format']])
        if stamp != stamp.normalize():
            result += ' ' + stamp.strftime('%H:%M:%S')
            if stamp.microsecond or stamp.nanosecond:
                result += '.' + f'{stamp.microsecond:06d}{stamp.nanosecond:03d}'.rstrip('0')
        return result

    @classmethod
    def format_dataframe(cls, dataframe, preferences):
        result = dataframe.copy()
        for column, pref in preferences.items():
            if column in result and pref.get('type') == 'date':
                result[column] = result[column].map(lambda value: cls.display(value, pref))
        return result

    @classmethod
    def summaries(cls, dataframe, preferences):
        result = []
        for column, pref in preferences.items():
            if pref.get('type') != 'date':
                continue
            count, examples = 0, []
            for value in dataframe[column]:
                status, parsed = cls.inspect(value, pref)
                if status != 'valid':
                    continue
                count += 1
                example = {'before': str(value), 'after': cls.display(parsed.tz_localize(None), pref)}
                if len(examples) < 3 and example not in examples:
                    examples.append(example)
            if count:
                result.append({'column': column, 'count': count, 'examples': examples})
        return result
