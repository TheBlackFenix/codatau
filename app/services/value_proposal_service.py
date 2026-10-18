import hashlib
import re
from copy import deepcopy
import pandas as pd

from app.services.ai_prompts import SPANISH_OUTPUT, UNTRUSTED_DATA
from app.services.ai_providers import AIProviderFactory
from app.services.structured_ai_service import StructuredAIService
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.extensions import db
from app.services.data_service import classify_numeric_column
from app.services.numeric_normalization_service import NumericNormalizationService as Numbers
from app.services.text_normalization_service import TextNormalizationService as Texts


class ValueProposalService:
    PURPOSE = 'cleaning_value_proposals'
    VERSION = '1-es'
    BATCH_SIZE = 8
    SCHEMA = {'type':'object','additionalProperties':False,'required':['proposals'],'properties':{
        'proposals':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,
        'required':['id','status','value','reason'],'properties':{'id':{'type':'string'},
        'status':{'type':'string','enum':['replace','review']},'value':{'type':'string'},'reason':{'type':'string'}}}}}}
    INSTRUCTIONS = SPANISH_OUTPUT + UNTRUSTED_DATA + (
        'Propón conversiones de valores al tipo elegido por el usuario. Devuelve exactamente '
        'una propuesta por id. Solo corrige representaciones inequívocas: dos a 2, o 3,5 a 3.5 '
        'si se pide decimal con punto. No inventes magnitudes, unidades, signos o fechas; no '
        'redondees fracciones para convertirlas a entero. Si hay ambigüedad usa status=review '
        'y value vacío, explicando qué decisión humana falta. Nunca reconstruyas identidades '
        'ni contactos ni completes valores ausentes. Para replace devuelve el valor como texto '
        'con el formato solicitado; el usuario deberá aprobarlo antes de cualquier cambio.'
    )

    @classmethod
    def candidates(cls, dataframe, configuration, context):
        issues = Treatments.issues(dataframe, configuration.columns, context, include_rows=True)
        decisions = configuration.treatments.get('decisions', {})
        if set(decisions) != {issue['id'] for issue in issues}:
            raise ValueError('El análisis cambió. Vuelve al paso anterior y guarda los tratamientos otra vez.')
        candidates = []
        for issue in issues:
            if decisions[issue['id']]['action'] != 'ai': continue
            pref = configuration.columns[issue['column']]
            if pref['type'] == 'date':
                raise ValueError('Las fechas ahora se resuelven por grupo, sin IA. Vuelve a tratamientos y guarda una decisión para este campo.')
            values = dataframe.loc[issue['rows'], issue['column']].astype(str).value_counts(sort=False)
            for value, count in values.items():
                key = hashlib.sha256((issue['id']+'\0'+value).encode()).hexdigest()
                safe = pref['type'] in {'integer','decimal','date','boolean'} and issue['scenario'] == 'invalid' and len(value)<=200
                role = next((item.get('role') for item in (context or {}).get('column_roles', []) if item['column'] == issue['column']), None)
                if role in {'identifier','contact'} or classify_numeric_column(issue['column']) == 'identifier' or re.search(r'[^\s@]+@[^\s@]+|\d{7,}', value):
                    safe = False
                candidates.append({'id':key,'column':issue['column'],'original':value[:200],
                                   'count':int(count),'target':pref,'safe':safe})
        return candidates

    @classmethod
    def fingerprint(cls, record, configuration, context, app_config):
        provider = AIProviderFactory.configuration_from_app(app_config)
        payload = {'columns':configuration.columns,'duplicates':configuration.duplicates,
                   'treatments':configuration.treatments,'context':context or {}, 'numeric_rule_version':Numbers.VERSION,
                   'text_rule_version':Texts.VERSION}
        return StructuredAIService.fingerprint(record, provider, cls.PURPOSE, cls.VERSION, payload)

    @staticmethod
    def validate(data, batch):
        if not isinstance(data,dict) or set(data)!={'proposals'} or not isinstance(data['proposals'],list):
            raise ValueError('Invalid response')
        expected={item['id']:item for item in batch};seen=set();result=[]
        for proposal in data['proposals']:
            if not isinstance(proposal,dict) or set(proposal)!={'id','status','value','reason'}:
                raise ValueError('Invalid proposal')
            key=proposal['id']
            if key not in expected or key in seen or proposal['status'] not in {'replace','review'}:
                raise ValueError('Unknown or repeated value')
            seen.add(key)
            if not isinstance(proposal['reason'],str) or not proposal['reason'].strip(): raise ValueError('Missing reason')
            value=proposal['value'];pref=expected[key]['target']
            if not isinstance(value,str) or len(value)>200: raise ValueError('Invalid value')
            status=proposal['status']
            if status=='replace':
                # Provider output uses the requested target separator, not the
                # source file's thousands/decimal convention.
                target_pref={**pref, 'numeric_rules': ''} if pref['type'] in {'integer','decimal'} else pref
                check=Treatments.issues(pd.DataFrame({'value':[value]}),{'value':target_pref})
                if check or not value.strip(): raise ValueError('Replacement does not match target')
                original=expected[key]['original']
                # Never silently round or change an already intelligible magnitude.
                if pref['type'] in {'integer','decimal'}:
                    from decimal import Decimal, InvalidOperation
                    source=Numbers.inspect(original,pref)
                    if source.status=='ambiguous':raise ValueError('Ambiguous source needs a format decision')
                    if source.status=='valid':parsed=source.value
                    else:
                        try: parsed=Decimal(original.replace(',','.'))
                        except InvalidOperation: parsed=None
                    if parsed is not None and parsed.is_finite() and parsed != Decimal(value.replace(',','.')):
                        raise ValueError('Changed magnitude')
            elif value:
                raise ValueError('Review must not contain a guessed value')
            result.append({'id':key,'status':status,'value':value,'reason':proposal['reason'].strip()[:500]})
        if seen != set(expected): raise ValueError('Missing proposals')
        return {'proposals':result}

    @classmethod
    def analyze(cls, record, configuration, candidates, context, app_config):
        fingerprint=cls.fingerprint(record,configuration,context,app_config)
        review=deepcopy(configuration.ai_review or {})
        if review.get('fingerprint') != fingerprint:
            review={'fingerprint':fingerprint,'proposals':{},'decisions':{}}
        remaining=[item for item in candidates if item['id'] not in review['proposals']]
        safe=[item for item in remaining if item['safe']][:cls.BATCH_SIZE]
        if safe:
            payload={'context':{'domain':(context or {}).get('domain','')[:250]},
                     'settings_fingerprint':fingerprint,'candidates':safe}
            run=StructuredAIService.request(record,app_config,cls.PURPOSE,cls.VERSION,cls.INSTRUCTIONS,
                                            payload,cls.SCHEMA,lambda data:cls.validate(data,safe))
            db.session.flush()
            for item in run.result['proposals']:
                review['proposals'][item['id']]={**item,'run_id':run.id,'model':run.model}
        for item in remaining:
            if not item['safe']:
                review['proposals'][item['id']]={'id':item['id'],'status':'review','value':'',
                    'reason':'No hay una corrección segura: conserva el valor o vuelve al paso anterior para elegir reemplazo, vacío o separar la fila.',
                    'run_id':None,'model':None}
        return review
