"""Sec-Fetch-Site: a browser tells the server whether a POST came from the same origin; cross-site and sibling-site posts are refused."""

import pytest


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site", "weird"])
def test_a_post_the_browser_marks_as_coming_from_another_site_is_refused(client, site):
    r = client.post("/session/ping", headers={"Sec-Fetch-Site": site}, follow_redirects=False)
    assert r.status_code == 403 and "Cross-site" in r.text


@pytest.mark.parametrize("site", ["same-origin", "none", "SAME-ORIGIN"])
def test_same_origin_and_user_initiated_posts_pass(client, site):
    assert client.post("/session/ping", headers={"Sec-Fetch-Site": site}, follow_redirects=False).status_code == 204


def test_a_client_that_sends_no_fetch_metadata_still_works(client):
    assert client.post("/session/ping", follow_redirects=False).status_code == 204


def test_a_cross_site_header_cannot_be_rescued_by_a_matching_origin(client):
    r = client.post("/session/ping", headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 403
