from decimal import Decimal
from io import BytesIO
from html.parser import HTMLParser
from types import SimpleNamespace

import pandas as pd
import pytest

from app.extensions import db
from app.models.cleaning_configuration import CleaningConfiguration
from app.services.column_type_service import ColumnTypeService as Columns
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.value_proposal_service import ValueProposalService as Proposals


def parse(**fields):
    return Columns.parse({'column_0': 'decimal', 'column_0_separator': '.', **fields}, [{'index': 0, 'name': 'height'}])['height']


def test_optional_inclusive_and_one_sided_bounds():
    pref = parse(column_0_minimum='0', column_0_maximum='200,5')
    assert pref['minimum'] == '0' and pref['maximum'] == '200.5'
    frame = pd.DataFrame({'height': ['-1', '0', '200.5', '201', 'bad', None]})
    issues = Treatments.issues(frame, {'height': pref}, include_rows=True)
    assert next(i['rows'] for i in issues if i['scenario'] == 'bounds') == [0, 3]
    assert next(i['count'] for i in issues if i['scenario'] == 'invalid') == 1
    assert next(i['count'] for i in issues if i['scenario'] == 'missing') == 1
    assert 'minimum' not in parse(column_0_maximum='200')
    assert 'maximum' not in parse(column_0_minimum='0')
    assert not any(k in parse() for k in ('minimum', 'maximum'))


@pytest.mark.parametrize('fields', [
    {'column_0_minimum': '201', 'column_0_maximum': '200'},
    {'column_0_minimum': 'NaN'}, {'column_0_maximum': 'inf'},
    {'column_0_minimum': '1.000,50'}, {'column_0_minimum': '1e100000'},
    {'column_0_minimum': '9'*65}, {'column_0': 'integer', 'column_0_minimum': '0.5'},
    {'column_0_negative': 'deny', 'column_0_minimum': '-1'},
    {'column_0_negative': 'deny', 'column_0_maximum': '-1'}])
def test_bad_or_contradictory_bounds_are_rejected(fields):
    with pytest.raises(ValueError):
        parse(**fields)


def test_bounds_do_not_apply_to_text_and_user_rule_overrides_context():
    assert Columns.parse({'column_0': 'text', 'column_0_minimum': 'bad'}, [{'index': 0, 'name': 'code'}]) == {'code': {'type': 'text'}}
    pref = parse(column_0_minimum='-10', column_0_maximum='200')
    context = {'column_roles': [{'column': 'height', 'suggested_constraints': ['positive']}]}
    assert Treatments.issues(pd.DataFrame({'height': [-2, 0, 100]}), {'height': pref}, context) == []


def test_bounds_compare_decimal_exactly_and_sign_group_does_not_overlap():
    pref = parse(column_0_maximum='9007199254740992')
    issue = Treatments.issues(pd.DataFrame({'height': ['9007199254740993']}), {'height': pref})
    assert issue[0]['scenario'] == 'bounds'
    pref = parse(column_0_negative='deny', column_0_minimum='0', column_0_maximum='200')
    issues = Treatments.issues(pd.DataFrame({'height': [-300, 201, 0, 200]}), {'height': pref}, include_rows=True)
    assert {i['scenario']: i['rows'] for i in issues} == {'negative': [0], 'bounds': [1]}


def test_ai_cannot_replace_with_out_of_bounds_value():
    pref = parse(column_0_minimum='0', column_0_maximum='200')
    candidate = {'id': 'a', 'original': 'muchos', 'target': pref}
    proposal = {'id': 'a', 'status': 'replace', 'value': '201', 'reason': 'No aceptable.'}
    with pytest.raises(ValueError):
        Proposals.validate({'proposals': [proposal]}, [candidate])


def test_absolute_cannot_create_out_of_bounds_result(app):
    pref = parse(column_0_negative='deny', column_0_maximum='200')
    frame = pd.DataFrame({'height': [-300]})
    issue = Treatments.issues(frame, {'height': pref})[0]
    config = SimpleNamespace(columns={'height': pref}, duplicates={'mode': 'none'}, ai_review={},
                             treatments={'decisions': {issue['id']: {'action': 'absolute'}}})
    with app.app_context(), pytest.raises(ValueError, match='fuera de tu rango'):
        Executor.build(frame, SimpleNamespace(active_stored_filename='a.csv'), config, {}, app.config)
    assert frame.iloc[0, 0] == -300


def test_bounds_persist_invalidate_and_preserve_invalid_form_values(app, client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'height\n2\n201\n'), 'height.csv')})
    path = '/files/cleaning/1/columns'
    class Hidden(HTMLParser):
        def __init__(self): super().__init__(); self.data = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('name') in {'revision', 'source_version'}:
                self.data[attrs['name']] = attrs.get('value')
    def tokens():
        parser = Hidden(); parser.feed(client.get(path).text); return parser.data
    payload = {'column_0': 'integer', 'column_0_minimum': '0', 'column_0_maximum': '200'}
    assert client.post(path, data={**tokens(), **payload}).status_code == 302
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert Decimal(config.columns['height']['maximum']) == 200
        config.duplicates = {'mode': 'none'}; config.treatments = {'decisions': {}}
        config.ai_review = {'fingerprint': 'old'}; db.session.commit()
    client.post(path, data={**tokens(), **payload, 'column_0_maximum': '300'})
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert config.columns['height']['maximum'] == '300'
        assert config.duplicates == config.treatments == config.ai_review == {}
    response = client.post(path, data={**tokens(), **payload, 'column_0_minimum': '400'})
    assert response.status_code == 200 and 'value="400"' in response.text
    with app.app_context():
        assert CleaningConfiguration.query.one().columns['height']['maximum'] == '300'
