import pandas as pd


class DuplicateConfigurationService:
    @staticmethod
    def parse(form, columns, column_preferences):
        mode, keep = form.get('mode'), form.get('keep')
        if mode not in {'all', 'keys', 'none'}:
            raise ValueError('Elige cómo identificar los duplicados.')
        if mode == 'none':
            return {'mode': mode}
        keys = list(dict.fromkeys(form.getlist('keys'))) if mode == 'keys' else list(columns)
        if not keys or any(key not in columns for key in keys):
            raise ValueError('Selecciona al menos un campo válido para comparar.')
        if keep not in {'first', 'last', 'latest'}:
            raise ValueError('Elige qué registro conservar.')
        result = {'mode': mode, 'keys': keys, 'keep': keep}
        if keep == 'latest':
            field = form.get('date_column')
            if field not in columns or column_preferences.get(field, {}).get('type') != 'date':
                raise ValueError('Para conservar el más reciente, configura primero un campo como Fecha.')
            result['date_column'] = field
        return result

    @staticmethod
    def preview(dataframe, settings, column_preferences, include_indices=False):
        if settings.get('mode') == 'none':
            result = {'removed': 0, 'remaining': len(dataframe), 'incomplete': 0, 'invalid_dates': 0, 'examples': []}
            if include_indices: result['indices'] = []
            return result
        keys = settings['keys']
        if any(key not in dataframe for key in keys):
            raise ValueError('Los campos cambiaron. Revisa la comparación de duplicados.')
        complete = dataframe[keys].notna().all(axis=1)
        for key in keys:
            complete &= dataframe[key].map(lambda value: not isinstance(value, str) or bool(value.strip()))
        working = dataframe.loc[complete].copy()
        if working.empty:
            result={'removed':0,'remaining':len(dataframe),'incomplete':int((~complete).sum()),'invalid_dates':0,'examples':[]}
            if include_indices:result['indices']=[]
            return result
        invalid_dates = 0
        keep = settings['keep']
        if keep == 'latest':
            field = settings['date_column']
            fmt = {'iso': 'ISO8601', 'dmy': '%d/%m/%Y', 'mdy': '%m/%d/%Y'}[column_preferences[field]['date_format']]
            dates = pd.to_datetime(working[field].map(lambda value: value if isinstance(value,pd.Timestamp) else pd.to_datetime(value,format=fmt,errors='coerce',utc=True)),utc=True)
            bad = dates.isna()
            # Preserve every group containing an unknown date, not just the bad row.
            blocked = working[keys].apply(tuple, axis=1).isin(set(working.loc[bad, keys].apply(tuple, axis=1)))
            invalid_dates = int(blocked.sum())
            working = working.loc[~blocked].loc[dates.loc[~blocked].sort_values(kind='stable').index]
            keep = 'last'
        removed = working.duplicated(subset=keys, keep=keep)
        examples = working.loc[removed].head(8).fillna('').astype(str).to_dict('records')
        count = int(removed.sum())
        result = {'removed': count, 'remaining': len(dataframe)-count, 'incomplete': int((~complete).sum()),
                  'invalid_dates': invalid_dates, 'examples': examples}
        if include_indices: result['indices'] = list(working.index[removed])
        return result
