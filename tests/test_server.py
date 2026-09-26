from unittest.mock import Mock, patch

import pytest

from app.server import ensure_port_available


def test_available_port_does_not_raise():
    with patch(
        'app.server.socket.create_connection',
        side_effect=ConnectionRefusedError,
    ):
        ensure_port_available('127.0.0.1', 5055)


def test_occupied_port_explains_how_to_recover():
    connection = Mock()
    with patch(
        'app.server.socket.create_connection',
        return_value=connection,
    ):
        with pytest.raises(RuntimeError, match='puerto 5055 ya está ocupado'):
            ensure_port_available('127.0.0.1', 5055)

    connection.close.assert_called_once_with()


def test_public_bind_address_is_probed_through_loopback():
    with patch(
        'app.server.socket.create_connection',
        side_effect=ConnectionRefusedError,
    ) as create_connection:
        ensure_port_available('0.0.0.0', 5055)

    create_connection.assert_called_once_with(('127.0.0.1', 5055), timeout=0.2)
