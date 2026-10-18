from dataclasses import dataclass
from decimal import Decimal
import math
import numbers
import pandas as pd

from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.duplicate_configuration_service import DuplicateConfigurationService as Duplicates
from app.services.value_proposal_service import ValueProposalService as Proposals
from app.services.date_normalization_service import DateNormalizationService as Dates
from app.services.numeric_normalization_service import NumericNormalizationService as Numbers
from app.services.text_normalization_service import TextNormalizationService as Texts


def cell(value):
    if pd.isna(value): return None
    if hasattr(value, 'isoformat'): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    if hasattr(value, 'item'): return value.item()
    return value


@dataclass
class WizardResult:
    cleaned: object
    quarantine: object
    changes: list
    duplicate_rows: list
    metrics: dict
    mixed_columns: list


class WizardExecutionService:
    RULE_VERSION = '5-text-catalog'

    @staticmethod
    def convert(value, preference):
        if pd.isna(value): return None
        kind=preference['type']
        if kind in {'integer', 'decimal'}:
            return Numbers.convert(value, preference)
        if kind=='date':
            return Dates.convert(value, preference)
        if kind=='boolean':
            return str(value).strip().casefold() in {'true','sí','si','1','yes'}
        if isinstance(value,numbers.Real) and not isinstance(value,bool) and math.isfinite(float(value)) and float(value).is_integer():
            return str(int(value))
        return str(value).strip() if kind in {'email','phone'} else str(value)

    @classmethod
    def build(cls, dataframe, record, config, context, app_config):
        if set(config.columns)!=set(dataframe.columns) or not config.duplicates or not config.treatments:
            raise ValueError('Completa los tipos, duplicados y tratamientos antes de revisar la limpieza.')
        original=dataframe.reset_index(drop=True).copy()
        issues=Treatments.issues(original,config.columns,context,include_rows=True)
        policies=config.treatments['decisions']
        if set(policies)!={issue['id'] for issue in issues}:
            raise ValueError('Los escenarios cambiaron. Revisa los tratamientos otra vez.')
        # Revalidate persisted policies, not just browser submissions.
        Treatments.parse({key: value for issue_id, policy in policies.items()
                          for key, value in [('action_'+issue_id, policy.get('action')),
                                             ('replacement_'+issue_id, policy.get('replacement', ''))]}, issues)
        candidates=Proposals.candidates(original,config,context)
        review=config.ai_review or {}
        if candidates:
            if review.get('fingerprint')!=Proposals.fingerprint(record,config,context,app_config):
                raise ValueError('Revisa las propuestas con el modelo y la configuración actuales.')
            if any(item['id'] not in review.get('decisions',{}) for item in candidates):
                raise ValueError('Acepta o conserva cada valor del paso de propuestas antes de continuar.')
        approved={}
        for item in candidates:
            choice=review['decisions'][item['id']]
            if choice=='reject':continue
            proposal=review.get('proposals',{}).get(item['id'])
            if choice!='accept' or not item['safe'] or not proposal or proposal['status']!='replace':
                raise ValueError('Una propuesta no tiene una aceptación válida.')
            Proposals.validate({'proposals':[{k:proposal[k] for k in ('id','status','value','reason')}]},[item])
            approved[(item['column'],item['original'])]=proposal['value']
        quarantine_reasons={}
        for issue in issues:
            if policies[issue['id']]['action']=='quarantine':
                for row in issue['rows']:
                    quarantine_reasons.setdefault(row,[]).append(issue['column']+': '+issue['label'])
        quarantine=original.loc[list(quarantine_reasons)].copy()
        if not quarantine.empty:
            if '__quarantine_reason' in quarantine.columns: raise ValueError('El archivo usa un nombre reservado para motivos de revisión.')
            quarantine['__quarantine_reason']=['; '.join(quarantine_reasons[row]) for row in quarantine.index]
        working=original.drop(index=list(quarantine_reasons)).astype(object)
        protected=set()
        causes={}
        for issue in issues:
            policy=policies[issue['id']];action=policy['action'];column=issue['column']
            for row in issue['rows']:
                if row not in working.index:continue
                key=(row,column)
                if action=='keep':protected.add(key)
                elif action=='replace':
                    working.at[row,column]=policy['replacement'];causes[key]='Reemplazo elegido'
                    if Treatments.issues(pd.DataFrame({column:[policy['replacement']]}),{column:config.columns[column]}):protected.add(key)
                elif action=='null':working.at[row,column]=None;causes[key]='Dejar vacío'
                elif action=='normalize_text':
                    working.at[row,column]=Texts.normalize(original.at[row,column],config.columns[column])
                    causes[key]='Reglas de texto sin IA confirmadas para el grupo'
                elif action=='normalize_number':
                    preference=config.columns[column]
                    parsed=Numbers.inspect(original.at[row,column],preference)
                    if parsed.status!='valid' or not Numbers.allowed(parsed.value,preference):
                        raise ValueError('Una conversión numérica ya no cumple tus reglas. Revisa los tratamientos.')
                    working.at[row,column]=Numbers.convert(original.at[row,column],preference)
                    causes[key]='Regla sin IA: '+Numbers.RULES[parsed.rule]
                elif action in {'date_dmy','date_mdy'}:
                    preference={**config.columns[column], 'date_source':action.removeprefix('date_')}
                    working.at[row,column]=Dates.convert(original.at[row,column],preference)
                    causes[key]='Orden de día y mes confirmado para el grupo'
                elif action=='absolute':
                    pref=config.columns[column]
                    value=Treatments.number(original.at[row,column],pref)
                    replacement=format(value.copy_abs(),'f')
                    if (('minimum' in pref and Decimal(replacement) < Decimal(pref['minimum'])) or
                            ('maximum' in pref and Decimal(replacement) > Decimal(pref['maximum']))):
                        raise ValueError('Convertir el grupo a positivo dejaría valores fuera de tu rango. Elige otro tratamiento para este grupo.')
                    working.at[row,column]=Decimal(replacement)
                    causes[key]='Conversión a positivo confirmada para el grupo'
                elif action=='ai':
                    replacement=approved.get((column,str(original.at[row,column])))
                    if replacement is None:protected.add(key)
                    else:
                        pref=config.columns[column]
                        working.at[row,column]=Numbers.convert(replacement,{**pref,'numeric_rules':''}) if pref['type'] in {'integer','decimal'} else replacement
                        causes[key]='Propuesta de IA aceptada'
                elif action!='quarantine':raise ValueError('Tratamiento no válido.')
        mixed=[]
        comparison_dates={}
        for column,pref in config.columns.items():
            for row in working.index:
                if (row,column) not in protected:
                    working.at[row,column]=cls.convert(working.at[row,column],pref)
            # Parquet cannot store heterogeneous numeric/text cells; preserve their
            # representations as text, never silently coerce invalid cells to null.
            values=working[column]
            kind=pref['type']
            if kind in {'integer','decimal'}:
                valid=all(pd.isna(v) or (isinstance(v,numbers.Real) and not isinstance(v,bool) and
                    (isinstance(v,numbers.Integral) or math.isfinite(float(v)))) for v in values)
                fits=kind!='integer' or (valid and all(pd.isna(v) or -(2**63)<=v<2**63 for v in values))
                if valid and fits:
                    working[column]=pd.array(values,dtype='Int64' if kind=='integer' else 'Float64')
                else:mixed.append(column)
            elif kind=='boolean':
                if all(pd.isna(v) or isinstance(v,bool) for v in values):working[column]=pd.array(values,dtype='boolean')
                else:mixed.append(column)
            elif kind=='date':
                comparison_dates[column]=values.copy()
                if all(pd.isna(v) or isinstance(v,pd.Timestamp) for v in values):working[column]=pd.to_datetime(values)
                else:mixed.append(column)
            if column in mixed:
                working[column]=values.map(lambda v:None if pd.isna(v) else str(v))
        changes=[]
        for row in working.index:
            for column in working.columns:
                before,after=cell(original.at[row,column]),cell(working.at[row,column])
                if type(before)!=type(after) or before!=after:
                    changes.append({'source_row':int(row)+1,'column':column,'before':before,'after':after,
                                    'display_after':Dates.display(working.at[row,column],config.columns[column]) if config.columns[column]['type']=='date' else after,
                                    'reason':causes.get((row,column),'Conversión al tipo elegido')})
        # Duplicate criteria are evaluated AFTER all accepted transformations.
        comparison=working.copy()
        for column,values in comparison_dates.items():comparison[column]=values
        duplicate_result=Duplicates.preview(comparison,config.duplicates,config.columns,include_indices=True)
        removed=duplicate_result['indices'];cleaned=working.drop(index=removed)
        changed_rows={change['source_row'] for change in changes}
        return WizardResult(cleaned,quarantine,changes,[int(row)+1 for row in removed],{
            'before_rows':len(original),'after_rows':len(cleaned),'quarantined_rows':len(quarantine),
            'duplicates_removed':len(removed),'changed_rows':len(changed_rows),'changed_cells':len(changes),
            'duplicate_incomplete_rows':duplicate_result['incomplete'],
            'duplicate_invalid_date_rows':duplicate_result['invalid_dates']},mixed)
