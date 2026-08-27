from org_harvest.hosts import ApiHost


def test_default_is_public_github():
    host = ApiHost()
    assert host.rest_base_url == "https://api.github.com"
    assert host.graphql_url == "https://api.github.com/graphql"


def test_explicit_github_com():
    host = ApiHost("github.com")
    assert host.rest_base_url == "https://api.github.com"
    assert host.graphql_url == "https://api.github.com/graphql"


def test_ghec_data_residency_host():
    host = ApiHost("api.octocorp.ghe.com")
    assert host.rest_base_url == "https://api.octocorp.ghe.com"
    assert host.graphql_url == "https://api.octocorp.ghe.com/graphql"


def test_enterprise_server_host():
    host = ApiHost("github.example.com")
    assert host.rest_base_url == "https://github.example.com/api/v3"
    assert host.graphql_url == "https://github.example.com/api/graphql"
