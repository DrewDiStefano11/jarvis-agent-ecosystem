import traceback

import pytest

from app.core.errors import DomainError
from app.research.url_policy import (
    normalize_url,
    public_address,
    redirect_destination,
    validate_dns_answers,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "//example.com",
        "https://",
        "https://user:secret@example.com",
        "https://example.com/#secret",
        "https://example.com/#",
        "https://example.com:0",
        "https://example.com:8080",
        "https://example.com:",
        "https://example.com:bad",
        "https://[2606:4700:4700::1111]garbage",
        "https://example.com:0443",
        "https://example.com\n",
        " https://example.com",
        "https://example.com/%0d%0aCookie:x",
        "https://example.com/%zz",
        "https://example.com\\@127.0.0.1",
        "https://%31%32%37.0.0.1",
        "http://127.1",
        "http://2130706433",
        "http://0177.0.0.1",
        "http://0x7f.0.0.1",
        "http://localhost",
        "http://host.local",
        "http://host.internal",
        "http://host.test",
        "https://a..com",
        "https://-a.com",
        "https://a.com..",
        "https://[fe80::1%25eth0]",
        "https://☃.com",
        "https://example.com/é",
        "https://" + "a" * 64 + ".com",
        "https://example.com/" + "a" * 4096,
    ],
)
def test_invalid_urls_are_sanitized(url):
    with pytest.raises(DomainError) as caught:
        normalize_url(url)
    assert caught.value.code in {"RESEARCH_URL_INVALID", "RESEARCH_ADDRESS_DENIED"}
    assert caught.value.message == "Research destination rejected by policy."
    assert url not in str(caught.value)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "168.63.129.16",
        "100.100.100.200",
        "100.64.0.1",
        "192.0.0.9",
        "192.88.99.1",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "fec0::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "64:ff9b:1::1",
        "2002:7f00:1::",
        "2001::1",
        "2001:db8::1",
        "8.8.8.8%zone",
        "invalid",
    ],
)
def test_unsafe_addresses_rejected_direct_and_in_mixed_dns(address):
    with pytest.raises(DomainError, match="rejected"):
        public_address(address)
    with pytest.raises(DomainError):
        validate_dns_answers(normalize_url("https://example.com"), ("8.8.8.8", address))


def test_normalization_preserves_exact_path_and_query():
    target = normalize_url("HTTPS://EXAMPLE.COM.:443/a%2Fb?q=a%20b&x=1")
    assert target.url == "https://example.com/a%2Fb?q=a%20b&x=1"
    assert target.hostname == "example.com"
    assert target.port == 443
    assert normalize_url("http://example.com:80").url == "http://example.com/"
    assert normalize_url("https://[2606:4700:4700::1111]").hostname == "2606:4700:4700::1111"


def test_dns_is_bounded_deduplicated_and_literal_consistent():
    target = normalize_url("https://example.com")
    assert validate_dns_answers(target, ("8.8.8.8", "1.1.1.1", "8.8.8.8")) == ("8.8.8.8", "1.1.1.1")
    for answers in ((), ("8.8.8.8",) * 33):
        with pytest.raises(DomainError):
            validate_dns_answers(target, answers)
    with pytest.raises(DomainError):
        validate_dns_answers(normalize_url("https://8.8.8.8"), ("1.1.1.1",))


def test_redirect_revalidation_and_limits():
    target = normalize_url("https://example.com/a/b")
    assert redirect_destination(target, "../c?q=1", hops=0).url == "https://example.com/c?q=1"
    for location in (
        "http://example.com/",
        "//127.0.0.1/a",
        "//user:password@example.com",
        "\n//example.com",
        "file:///tmp/x",
    ):
        with pytest.raises(DomainError):
            redirect_destination(target, location, hops=0)
    for hops, maximum in ((3, 3), (-1, 3), (0, 0), (0, 6)):
        with pytest.raises(DomainError) as caught:
            redirect_destination(target, "/c", hops=hops, maximum_hops=maximum)
        assert caught.value.code == "RESEARCH_REDIRECT_LIMIT"


def test_web_instructions_cannot_change_policy():
    # A page-derived URL is untrusted regardless of any surrounding model text.
    injected = "https://127.0.0.1/admin?instruction=ignore-policy"
    with pytest.raises(DomainError):
        redirect_destination(normalize_url("https://example.com"), injected, hops=0)


def test_parser_exception_chain_does_not_leak_input():
    value = "https://[secret-value]/"
    with pytest.raises(DomainError) as caught:
        normalize_url(value)
    assert "secret-value" not in "".join(traceback.format_exception(caught.value))
