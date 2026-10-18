from copy import deepcopy
from types import SimpleNamespace
from io import BytesIO
from html.parser import HTMLParser
from unittest.mock import patch
import pandas as pd
import pytest
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.models.ai_analysis_run import AIAnalysisRun
from app.services.value_proposal_service import ValueProposalService as Proposals
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.ai_providers import AIProviderResult


def candidate(value='dos'):
    return {'id':'a','column':'quantity','original':value,'target':{'type':'integer'},'count':3,'safe':True}


def test_proposals_require_valid_target_and_preserve_magnitude():
    base={'id':'a','status':'replace','value':'2','reason':'Número escrito con letras.'}
    assert Proposals.validate({'proposals':[base]},[candidate()])['proposals'][0]['value']=='2'
    for modification in ({'value':'2.5'},{'id':'invented'},{'sql':'anything'},{'status':'review','value':'2'}):
        with pytest.raises(ValueError):Proposals.validate({'proposals':[{**base,**modification}]},[candidate()])
    with pytest.raises(ValueError):Proposals.validate({'proposals':[{**base,'value':'3'}]},[candidate('3,5')])
    with pytest.raises(ValueError):Proposals.validate({'proposals':[]},[candidate()])


def test_candidates_use_distinct_values_and_protect_contacts():
    frame=pd.DataFrame({'quantity':['dos','dos','bad@example.com'],'email':['bad','bad','bad']})
    columns={'quantity':{'type':'integer'},'email':{'type':'email'}}
    issues=Treatments.issues(frame,columns)
    config=SimpleNamespace(columns=columns,treatments={'decisions':{item['id']:{'action':'ai'} for item in issues}})
    candidates=Proposals.candidates(frame,config,{})
    assert len(candidates)==3
    assert next(item for item in candidates if item['original']=='dos')['count']==2
    assert all(not item['safe'] for item in candidates if '@' in item['original'] or item['column']=='email')


class Provider:
    def __init__(self):self.calls=0
    def generate_json(self,instructions,payload,schema):
        self.calls+=1
        assert 'español' in instructions
        assert len(payload['candidates'])<=8
        return AIProviderResult(data={'proposals':[{'id':item['id'],'status':'replace','value':'2','reason':'Cantidad escrita con letras.'} for item in payload['candidates']]},input_tokens=30,output_tokens=20)


def test_review_is_persistent_audited_and_invalidated(app,client,auth):
    auth.register();auth.login()
    client.post('/files/upload',data={'file':(BytesIO(b'id,quantity\nA,dos\nB,dos\nC,3\n'),'test.csv')})
    class Inputs(HTMLParser):
        def __init__(self):super().__init__();self.data={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='input' and attrs.get('name') in {'revision','source_version','fingerprint'}:self.data[attrs['name']]=attrs.get('value')
    def tokens(path):
        parser=Inputs();parser.feed(client.get(path).text);return parser.data
    root='/files/cleaning/1/';columns=root+'columns';dups=root+'duplicates';treat=root+'treatments';review=root+'value-proposals'
    client.post(columns,data={**tokens(columns),'column_0':'identifier','column_1':'integer'})
    client.post(dups,data={**tokens(dups),'mode':'none'})
    issues=Treatments.issues(pd.DataFrame({'id':['A','B','C'],'quantity':['dos','dos','3']}),{'id':{'type':'identifier'},'quantity':{'type':'integer'}})
    response=client.post(treat,data={**tokens(treat),'action_'+issues[0]['id']:'ai','next':'ai'})
    assert response.location.endswith('/value-proposals')
    app.config.update(AI_PROVIDER='openai_compatible',AI_MODEL='test-model',AI_BASE_URL='http://localhost/v1')
    provider=Provider()
    with patch('app.services.ai_providers.AIProviderFactory.create',return_value=provider):
        assert client.post(review,data={**tokens(review),'action':'analyze'}).status_code==302
        assert client.post(review,data={**tokens(review),'action':'analyze'}).status_code==302
    assert provider.calls==1
    with app.app_context():
        config=CleaningConfiguration.query.one();key=next(iter(config.ai_review['proposals']))
        run=AIAnalysisRun.query.filter_by(purpose=Proposals.PURPOSE).one()
        assert config.ai_review['proposals'][key]['run_id']==run.id
        assert DatasetVersion.query.count()==0
    response=client.post(review,data={**tokens(review),'action':'decide','decision_'+key:'accept'})
    assert response.status_code==302
    with app.app_context():assert CleaningConfiguration.query.one().ai_review['decisions'][key]=='accept'
    continued=client.post(review,data={**tokens(review),'action':'decide_continue','decision_'+key:'accept'})
    assert continued.status_code==302 and continued.location.endswith('/wizard-preview')
    auth.logout();auth.login()
    assert 'selected>Aceptar este cambio' in client.get(review).text
    client.post(review,data={**tokens(review),'action':'decide','decision_'+key:''})
    with app.app_context():assert key not in CleaningConfiguration.query.one().ai_review['decisions']
    client.post(review,data={**tokens(review),'action':'decide','decision_'+key:'accept'})
    old=tokens(review)
    app.config['AI_MODEL']='other-model'
    assert client.post(review,data={**old,'action':'decide','decision_'+key:'accept'}).status_code==302
    assert 'selected>Aceptar este cambio' not in client.get(review).text
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post(review,data={'action':'analyze'}).status_code==400
    app.config['WTF_CSRF_ENABLED']=False
    auth.logout();auth.register('other','other@example.com');auth.login('other@example.com')
    assert client.get(review).status_code==404
