from html.parser import HTMLParser
from io import BytesIO


class Elements(HTMLParser):
    def __init__(self): super().__init__(); self.tags = []
    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


def test_shared_shell_and_core_screens_are_accessible(client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file':(BytesIO(b'id,status\nA,Pendiente\n'), 'ux.csv')})
    for path in ['/dashboard', '/files/upload', '/reports/', '/files/insights', '/files/results/1', '/files/cleaning/1', '/files/cleaning/1/columns']:
        response = client.get(path)
        assert response.status_code == 200, path
        parser = Elements(); parser.feed(response.text)
        assert any(tag=='main' and attrs.get('id')=='page-content' for tag,attrs in parser.tags)
        assert any(tag=='button' and attrs.get('aria-controls')=='main-navigation' for tag,attrs in parser.tags)
        assert 'css/ux.css' in response.text and 'Saltar al contenido' in response.text


def test_wizard_has_one_primary_submit_and_locked_future_steps(client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file':(BytesIO(b'id,status\nA,Pendiente\n'), 'ux.csv')})
    response = client.get('/files/cleaning/1/columns')
    parser = Elements(); parser.feed(response.text)
    primary = [a for tag,a in parser.tags if tag=='button' and 'btn-purple' in a.get('class','')]
    assert len(primary) == 1 and primary[0].get('name')=='next'
    assert 'aria-disabled="true"' in response.text
    assert 'class="column-grid"' in response.text


def test_ui_assets_are_available(client):
    response = client.get('/static/css/ux.css')
    assert response.status_code == 200
    assert b'prefers-reduced-motion' in response.data
    assert b'focus-visible' in response.data
