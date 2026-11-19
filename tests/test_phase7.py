"""Phase 7 checks: the customer frontend is served by Flask."""

FRONTEND_FILES = [
    "/css/styles.css",
    "/js/api.js",
    "/js/auth.js",
    "/js/booking.js",
    "/js/appointments.js",
    "/js/slots.js",
    "/js/ui.js",
    "/js/app.js",
]


def test_home_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "text/html"

    html = response.get_data(as_text=True)
    assert 'id="book-view"' in html
    assert 'id="appointments-view"' in html
    assert 'id="reschedule-dialog"' in html
    assert '<script type="module" src="/js/app.js"></script>' in html


def test_frontend_assets_are_served(client):
    for path in FRONTEND_FILES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.get_data(), path
