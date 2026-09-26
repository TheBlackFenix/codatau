import os

from app import create_app
from app.server import ensure_port_available

app = create_app('development')

if __name__ == '__main__':
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    ensure_port_available(host, port)
    app.run(
        host=host,
        port=port,
        debug=app.debug,
        # The reloader starts a second Python process, which defeats the
        # single-instance port guard and caused stale versions to coexist.
        use_reloader=False,
    )
