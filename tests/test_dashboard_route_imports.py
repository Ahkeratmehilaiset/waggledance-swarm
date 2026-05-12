from pathlib import Path


ROUTES_DIR = Path(__file__).resolve().parents[1] / "waggledance" / "adapters" / "http" / "routes"


def test_hologram_route_does_not_import_compat_dashboard_state():
    source = (ROUTES_DIR / "hologram.py").read_text(encoding="utf-8")

    assert "routes.compat_dashboard import _ws_clients" not in source


def test_dashboard_websocket_clients_are_shared_from_dedicated_module():
    from waggledance.adapters.http.routes import _dashboard_shared
    from waggledance.adapters.http.routes import compat_dashboard

    assert compat_dashboard._ws_clients is _dashboard_shared._ws_clients
