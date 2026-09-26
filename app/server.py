import socket


def ensure_port_available(host, port, timeout=0.2):
    """Fail fast when another local CoDataU process already owns the port."""
    probe_host = host
    if host in {'0.0.0.0', '::'}:
        probe_host = '127.0.0.1'

    try:
        connection = socket.create_connection((probe_host, port), timeout=timeout)
    except OSError:
        return

    connection.close()
    raise RuntimeError(
        f'El puerto {port} ya está ocupado. Detén la instancia anterior de '
        'CoDataU antes de iniciar otra.'
    )
