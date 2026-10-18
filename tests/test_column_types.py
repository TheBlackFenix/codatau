from io import BytesIO
from app.extensions import db
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.services.column_type_service import ColumnTypeService


def setup_file(client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'id,amount,date\n001,2.5,2026-10-01\n002,3.5,2026-10-02\n'), 'test.csv')})
    return '/files/cleaning/1/columns'


def fields(client, path):
    from html.parser import HTMLParser
    class Inputs(HTMLParser):
        def __init__(self): super().__init__(); self.values = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('name') in {'source_version', 'revision'}:
                self.values[attrs['name']] = attrs.get('value')
    parser = Inputs(); parser.feed(client.get(path).text)
    return {**parser.values, 'column_0': 'identifier', 'column_1': 'decimal',
            'column_1_separator': '.', 'column_2': 'date', 'column_2_date': 'iso'}


def test_choices_persist_without_creating_versions_or_decisions(app, client, auth):
    path = setup_file(client, auth)
    assert client.get(path).status_code == 200
    payload = fields(client, path)
    assert client.post(path, data=payload).status_code == 302
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert config.columns['amount'] == {'type': 'decimal', 'decimal_separator': '.'}
        assert DatasetVersion.query.count() == 0
    assert 'selected>Identificador o código' in client.get(path).text
    auth.logout(); auth.login()
    assert fields(client, path)['revision'] == '1'
    changed = {**fields(client, path), 'column_1': 'text'}
    client.post(path, data=changed)
    # An old browser tab cannot overwrite the newly saved choices.
    client.post(path, data=payload)
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert config.columns['amount'] == {'type': 'text'}
        assert config.revision == 2


def test_invalid_types_and_stale_versions_are_not_saved(app, client, auth):
    path = setup_file(client, auth)
    payload = fields(client, path)
    assert client.post(path, data={**payload, 'column_1': 'SQL'}).status_code == 200
    assert client.post(path, data={**payload, 'column_1_separator': '!'}).status_code == 200
    assert client.post(path, data={**payload, 'column_2_date': 'guess'}).status_code == 200
    assert client.post(path, data={**payload, 'source_version': 'old.csv'}).status_code == 302
    with app.app_context(): assert CleaningConfiguration.query.count() == 0


def test_configuration_is_owned_and_deleted_with_file(app, client, auth):
    path = setup_file(client, auth)
    client.post(path, data=fields(client, path))
    auth.logout(); auth.register('other', 'other@example.com'); auth.login('other@example.com')
    assert client.get(path).status_code == 404
    assert client.post(path, data={}).status_code == 404
    auth.logout(); auth.login()
    client.post('/files/delete/1')
    with app.app_context(): assert CleaningConfiguration.query.count() == 0


def test_column_suggestions_use_semantics_and_natural_labels():
    profile = {'columns': [{'name': 'guide', 'type': 'BIGINT', 'semantic': {'type': 'identifier'}},
                          {'name': 'value', 'type': 'DOUBLE', 'semantic': {'type': 'number'}},
                          {'name': '<script>', 'type': 'VARCHAR', 'semantic': {'type': 'email'}}],
               'sample': [{'guide': 123, 'value': 3.5, '<script>': 'test@example.com'}]}
    cards = ColumnTypeService.cards(profile, {})
    assert [c['suggested'] for c in cards] == ['identifier', 'decimal', 'email']


def test_column_configuration_requires_csrf(app, client, auth):
    path = setup_file(client, auth)
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post(path, data={}).status_code == 400
