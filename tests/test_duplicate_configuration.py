from io import BytesIO
from werkzeug.datastructures import MultiDict
import pandas as pd
import pytest
from app.services.duplicate_configuration_service import DuplicateConfigurationService as Duplicates
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion


def test_compare_selected_keys_preserves_incomplete_and_input():
    frame = pd.DataFrame({'id': ['A','A','A','B',None,None,' '], 'city': ['X','X','Y','X','X','X','X'], 'value': [10,20,30,40,50,60,70]})
    original = frame.copy(deep=True)
    settings = {'mode':'keys','keys':['id','city'],'keep':'first'}
    result = Duplicates.preview(frame, settings, {})
    assert result['removed'] == 1 and result['remaining'] == 6
    assert result['incomplete'] == 3
    assert result['examples'][0]['value'] == '20'
    pd.testing.assert_frame_equal(frame, original)
    assert Duplicates.preview(frame, {**settings,'keep':'last'}, {})['examples'][0]['value'] == '10'


def test_latest_uses_chosen_format_and_protects_whole_invalid_group():
    frame = pd.DataFrame({'id':['A','A','B','B','C','C'], 'date':['03/04/2026','04/03/2026','bad','18/10/2026','18/10/2026','18/10/2026'], 'value':[1,2,3,4,5,6]})
    prefs = {'date': {'type':'date','date_format':'dmy'}}
    result = Duplicates.preview(frame, {'mode':'keys','keys':['id'],'keep':'latest','date_column':'date'}, prefs)
    assert result['removed'] == 2 and result['invalid_dates'] == 2
    assert {row['value'] for row in result['examples']} == {'2','5'}


@pytest.mark.parametrize('fields', [
    [('mode','keys'),('keep','first')],
    [('mode','keys'),('keys','invented'),('keep','first')],
    [('mode','all'),('keep','sql')],
    [('mode','all'),('keep','latest'),('date_column','id')],
])
def test_invalid_criteria_rejected(fields):
    with pytest.raises(ValueError): Duplicates.parse(MultiDict(fields), ['id'], {})


def test_whole_row_and_no_deduplication():
    frame = pd.DataFrame({'a':[1,1,1], 'b':['x','y','x']})
    settings = Duplicates.parse(MultiDict({'mode':'all','keep':'first'}), list(frame.columns), {})
    assert Duplicates.preview(frame, settings, {})['removed'] == 1
    assert Duplicates.preview(frame, {'mode':'none'}, {})['removed'] == 0


def test_step_navigation_persistence_and_invalidation(app, client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'id,city\nA,X\nA,X\nA,Y\n'), 'duplicates.csv')})
    columns = '/files/cleaning/1/columns'; duplicates = '/files/cleaning/1/duplicates'
    assert client.get(duplicates).location.endswith('/columns')
    from html.parser import HTMLParser
    class Hidden(HTMLParser):
        def __init__(self): super().__init__(); self.data={}
        def handle_starttag(self, tag, attributes):
            attrs=dict(attributes)
            if tag=='input' and attrs.get('name') in {'revision','source_version'}: self.data[attrs['name']]=attrs.get('value')
    def tokens(url):
        parser=Hidden(); parser.feed(client.get(url).text); return parser.data
    response=client.post(columns, data={**tokens(columns),'column_0':'identifier','column_1':'text','next':'duplicates'})
    assert response.location.endswith('/duplicates')
    payload={**tokens(duplicates),'mode':'keys','keys':['id','city'],'keep':'first'}
    assert client.post(duplicates,data=payload).status_code==302
    assert 'Se separarían 1 registros' in client.get(duplicates).text
    with app.app_context():
        assert CleaningConfiguration.query.one().duplicates['keys']==['id','city']
        assert DatasetVersion.query.count()==0
    auth.logout(); auth.login()
    assert 'Se separarían 1 registros' in client.get(duplicates).text
    client.post(columns,data={**tokens(columns),'column_0':'text','column_1':'text'})
    with app.app_context(): assert CleaningConfiguration.query.one().duplicates == {}
    client.post(duplicates,data=payload)
    with app.app_context(): assert CleaningConfiguration.query.one().duplicates == {}
    auth.logout();auth.register('other','other@example.com');auth.login('other@example.com')
    assert client.get(duplicates).status_code==404
    assert client.post(duplicates,data=payload).status_code==404
