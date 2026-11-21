def test_every_response_refuses_to_be_embedded_by_another_site(client):
    for path in ("/dashboard", "/static/css/app.css", "/nope", "/healthz"):
        assert client.get(path).headers["cross-origin-resource-policy"] == "same-origin", path
