from io import BytesIO
from html.parser import HTMLParser
from unittest.mock import patch
import pytest
from types import SimpleNamespace
import pandas as pd
from app.extensions import db
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.models.file_upload import FileUpload
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.value_proposal_service import ValueProposalService as Proposals


def config_for(frame,columns,action):
    issues=Treatments.issues(frame,columns)
    return SimpleNamespace(columns=columns,duplicates={'mode':'none'},ai_review={},treatments={'decisions':{i['id']:{'action':action} for i in issues}})


def test_retained_values_are_not_silently_lost_and_duplicates_use_converted_data(app):
    data=pd.DataFrame({'id':['A','A','B'],'quantity':['2','02','bad']})
    cols={'id':{'type':'identifier'},'quantity':{'type':'integer'}}
    cfg=config_for(data,cols,'keep');cfg.duplicates={'mode':'keys','keys':['id','quantity'],'keep':'first'}
    with app.app_context():
        result=Executor.build(data,SimpleNamespace(active_stored_filename='a.csv'),cfg,{},app.config)
    assert result.metrics['duplicates_removed']==1
    assert result.cleaned['quantity'].tolist()==['2','bad']
    assert result.mixed_columns==['quantity']
    assert data['quantity'].tolist()==['2','02','bad']


def test_quarantine_precedes_changes_and_missing_can_become_null(app):
    frame=pd.DataFrame({'quantity':['bad','2',None]});cols={'quantity':{'type':'integer'}}
    cfg=config_for(frame,cols,'null')
    issues=Treatments.issues(frame,cols)
    for issue in issues:
        if issue['scenario']=='invalid':cfg.treatments['decisions'][issue['id']]={'action':'quarantine'}
    with app.app_context():result=Executor.build(frame,SimpleNamespace(active_stored_filename='a.csv'),cfg,{},app.config)
    assert result.metrics['quarantined_rows']==1 and result.metrics['after_rows']==2
    assert result.quarantine.iloc[0]['quantity']=='bad'
    assert result.cleaned['quantity'].iloc[0]==2
    assert pd.isna(result.cleaned['quantity'].iloc[1])


def test_latest_duplicate_after_date_conversion_with_retained_bad_date(app):
    data=pd.DataFrame({'id':['A','A','B','B'],'date':['03/04/2026','04/03/2026','bad','18/10/2026']})
    cols={'id':{'type':'identifier'},'date':{'type':'date','date_format':'dmy'}}
    config=config_for(data,cols,'keep');config.duplicates={'mode':'keys','keys':['id'],'keep':'latest','date_column':'date'}
    with app.app_context():result=Executor.build(data,SimpleNamespace(active_stored_filename='a.csv'),config,{},app.config)
    assert result.metrics['duplicates_removed']==1
    assert result.metrics['duplicate_invalid_date_rows']==2
    assert result.duplicate_rows==[2]
    assert len(result.cleaned)==3


def test_only_accepted_current_ai_proposals_are_applied(app):
    data=pd.DataFrame({'quantity':['dos','3']});cols={'quantity':{'type':'integer'}}
    config=config_for(data,cols,'ai');record=SimpleNamespace(active_stored_filename='a.csv')
    with app.app_context():
        candidate=Proposals.candidates(data,config,{})[0]
        config.ai_review={'fingerprint':Proposals.fingerprint(record,config,{},app.config),
            'proposals':{candidate['id']:{'id':candidate['id'],'status':'replace','value':'2','reason':'Número escrito en palabras.'}},'decisions':{}}
        with pytest.raises(ValueError):Executor.build(data,record,config,{},app.config)
        config.ai_review['decisions'][candidate['id']]='accept'
        assert Executor.build(data,record,config,{},app.config).cleaned['quantity'].tolist()==[2,3]
        config.ai_review['decisions'][candidate['id']]='reject'
        assert Executor.build(data,record,config,{},app.config).cleaned['quantity'].tolist()==['dos','3']
        config.ai_review['fingerprint']='stale'
        with pytest.raises(ValueError):Executor.build(data,record,config,{},app.config)


class Inputs(HTMLParser):
    def __init__(self):super().__init__();self.data={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='input' and attrs.get('type')=='hidden':self.data[attrs['name']]=attrs.get('value','')


def test_complete_wizard_version_export_restore_and_repeat_confirmation(app,client,auth):
    auth.register();auth.login()
    original=b'id,quantity\nA,2\nA,02\nB,bad\nC,3\n'
    client.post('/files/upload',data={'file':(BytesIO(original),'test.csv')})
    root='/files/cleaning/1/';columns=root+'columns';dups=root+'duplicates';treat=root+'treatments';preview=root+'wizard-preview'
    def tokens(url):
        parser=Inputs();response=client.get(url);assert response.status_code==200;parser.feed(response.text);return parser.data
    client.post(columns,data={**tokens(columns),'column_0':'identifier','column_1':'integer'})
    client.post(dups,data={**tokens(dups),'mode':'keys','keys':['id','quantity'],'keep':'first'})
    issues=Treatments.issues(pd.DataFrame({'id':['A','A','B','C'],'quantity':['2','02','bad','3']}),{'id':{'type':'identifier'},'quantity':{'type':'integer'}})
    client.post(treat,data={**tokens(treat),'action_'+issues[0]['id']:'quarantine'})
    with app.app_context():assert DatasetVersion.query.count()==0
    payload=tokens(preview)
    # A materialization failure must not activate a partial version or leave files.
    from pathlib import Path
    before=set(Path(app.config['ANALYTICS_FOLDER']).glob('*'))
    with patch('app.services.dataset_pipeline.DatasetPipeline.profile_existing',side_effect=RuntimeError('simulated failure')):
        client.post(preview,data=payload)
    with app.app_context():assert DatasetVersion.query.count()==0
    assert set(Path(app.config['ANALYTICS_FOLDER']).glob('*'))==before
    response=client.post(preview,data=payload)
    assert '/wizard-result/' in response.location
    assert 'Limpieza completada' in client.get(response.location).text
    with app.app_context():
        version=DatasetVersion.query.one()
        assert version.metrics['after_rows']==2
        assert version.metrics['quarantined_rows']==1
        assert version.metrics['duplicates_removed']==1
        assert version.operations[0]['changes']
        from pathlib import Path
        record=db.session.get(FileUpload,1)
        assert (Path(app.config['UPLOAD_FOLDER'])/record.filename).read_bytes()==original
    # An identical POST is not permission to create a second version.
    client.post(preview,data=payload)
    with app.app_context():assert DatasetVersion.query.count()==1
    assert client.get(root+'export/1').status_code==200
    assert client.get(root+'quarantine/1').status_code==200
    assert client.get('/dashboard').status_code==200
    client.post(root+'activate/0')
    with app.app_context():assert db.session.get(FileUpload,1).active_version is None
    # Changed preferences invalidate signed preview, even after restoring the source.
    client.post(dups,data={**tokens(dups),'mode':'none'})
    client.post(preview,data=payload)
    with app.app_context():assert DatasetVersion.query.count()==1
    auth.logout();auth.register('other','other@example.com');auth.login('other@example.com')
    assert client.post(preview,data=payload).status_code==404


def test_empty_result_can_be_versioned_and_source_is_preserved(app,client,auth):
    auth.register();auth.login()
    client.post('/files/upload',data={'file':(BytesIO(b'quantity\nbad\nwrong\n'),'test.csv')})
    root='/files/cleaning/1/'
    def tokens(path):
        parser=Inputs();parser.feed(client.get(root+path).text);return parser.data
    client.post(root+'columns',data={**tokens('columns'),'column_0':'integer'})
    client.post(root+'duplicates',data={**tokens('duplicates'),'mode':'none'})
    issue=Treatments.issues(pd.DataFrame({'quantity':['bad','wrong']}),{'quantity':{'type':'integer'}})[0]
    client.post(root+'treatments',data={**tokens('treatments'),'action_'+issue['id']:'quarantine'})
    response=client.post(root+'wizard-preview',data=tokens('wizard-preview'))
    assert '/wizard-result/' in response.location
    with app.app_context():
        assert DatasetVersion.query.one().metrics['after_rows']==0
        assert DatasetVersion.query.one().metrics['quarantined_rows']==2
