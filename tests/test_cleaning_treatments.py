from io import BytesIO
from html.parser import HTMLParser
import pandas as pd
import pytest
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion


def test_scenarios_follow_user_type_without_mutation():
    data=pd.DataFrame({'quantity':['dos','3,5','2',None,' '], 'email':['bad','a@example.com',None,'b@example.com','ok@example.org']})
    before=data.copy(deep=True)
    prefs={'quantity':{'type':'integer'},'email':{'type':'email'}}
    issues=Treatments.issues(data,prefs)
    assert {(i['column'],i['scenario']):i['count'] for i in issues} == {('quantity','missing'):2,('quantity','invalid'):2,('email','missing'):1,('email','invalid'):1}
    prefs['quantity']={'type':'decimal','decimal_separator':','}
    assert next(i['count'] for i in Treatments.issues(data,prefs) if i['column']=='quantity' and i['scenario']=='invalid')==1
    pd.testing.assert_frame_equal(data,before)


def test_date_and_semantic_range_and_accepted_negative_number():
    data=pd.DataFrame({'date':['18/10/2026','2026-02-30'], 'height':[-2,3]})
    prefs={'date':{'type':'date','date_format':'dmy'}, 'height':{'type':'integer'}}
    context={'column_roles':[{'column':'height','suggested_constraints':['positive']}]}
    assert {(i['column'],i['scenario']) for i in Treatments.issues(data,prefs,context)} == {('date','invalid'),('height','range')}
    assert all(i['column']!='height' for i in Treatments.issues(data,prefs))


def test_replacement_is_only_stored_for_replace_and_missing_cannot_use_ai():
    issues=[{'id':'a','scenario':'invalid'}]
    assert Treatments.parse({'action_a':'keep','replacement_a':'N/A'},issues)=={'decisions':{'a':{'action':'keep'}}}
    assert Treatments.parse({'action_a':'replace','replacement_a':'N/A'},issues)['decisions']['a']['replacement']=='N/A'
    for form in ({'action_a':'replace'}, {'action_a':'sql'}, {'action_a':'replace','replacement_a':'x'*201}):
        with pytest.raises(ValueError): Treatments.parse(form,issues)
    with pytest.raises(ValueError): Treatments.parse({'action_a':'ai'},[{'id':'a','scenario':'missing'}])


def test_step_three_persists_and_never_changes_data(app,client,auth):
    auth.register();auth.login()
    client.post('/files/upload',data={'file':(BytesIO(b'id,quantity\nA,dos\nB,2\nC,\n'),'test.csv')})
    columns='/files/cleaning/1/columns';dups='/files/cleaning/1/duplicates';treat='/files/cleaning/1/treatments'
    class Hidden(HTMLParser):
        def __init__(self):super().__init__();self.data={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='input' and attrs.get('name') in {'revision','source_version'}:self.data[attrs['name']]=attrs.get('value')
    def tokens(url):
        parser=Hidden();parser.feed(client.get(url).text);return parser.data
    assert client.get(treat).location.endswith('/duplicates')
    client.post(columns,data={**tokens(columns),'column_0':'identifier','column_1':'integer'})
    response=client.post(dups,data={**tokens(dups),'mode':'none','next':'treatments'})
    assert response.location.endswith('/treatments')
    # Issue IDs are stable and never use arbitrary column names as form field selectors.
    issues=Treatments.issues(pd.DataFrame({'id':['A','B','C'],'quantity':['dos','2',None]}),{'id':{'type':'identifier'},'quantity':{'type':'integer'}})
    payload={**tokens(treat)}
    for issue in issues:payload['action_'+issue['id']]='ai' if issue['scenario']=='invalid' else 'keep'
    assert client.post(treat,data=payload).status_code==302
    with app.app_context():
        assert len(CleaningConfiguration.query.one().treatments['decisions'])==2
        assert DatasetVersion.query.count()==0
    auth.logout();auth.login()
    assert 'selected>Pedir una propuesta a la IA' in client.get(treat).text
    # Invalidates downstream decisions after changing duplicate criteria.
    client.post(dups,data={**tokens(dups),'mode':'all','keep':'first'})
    with app.app_context():assert CleaningConfiguration.query.one().treatments=={}
    client.post(treat,data=payload)
    with app.app_context():assert CleaningConfiguration.query.one().treatments=={}
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post(treat,data=payload).status_code==400
    app.config['WTF_CSRF_ENABLED']=False
    auth.logout();auth.register('other','other@example.com');auth.login('other@example.com')
    assert client.get(treat).status_code==404
