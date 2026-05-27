"""Unit tests for server.auth — credential loading, hashing, secret key."""

import os
import stat

import pytest

from server import auth


@pytest.fixture(autouse=True)
def _reset_auth():
    """Each test starts with a clean credential cache."""
    auth.reset_auth_cache()
    yield
    auth.reset_auth_cache()


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_missing_file(tmp_path):
    """No .env file → auth disabled, no crash."""
    user, hashed = auth.load_credentials(str(tmp_path))
    assert user is None
    assert hashed is None
    assert auth.auth_enabled() is False


def test_load_credentials_valid(tmp_path):
    """A complete .env file produces a usable credential tuple."""
    hashed = auth.generate_password_hash("hunter2")
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {
            auth.USERNAME_KEY: "alice",
            auth.PASSWORD_HASH_KEY: hashed,
        },
    )
    user, got_hash = auth.load_credentials(str(tmp_path))
    assert user == "alice"
    assert got_hash == hashed
    assert auth.auth_enabled() is True
    assert auth.get_username() == "alice"
    assert auth.get_users() == {"alice": hashed}


def test_load_credentials_malformed_missing_hash(tmp_path):
    """Username present but password hash missing → auth disabled."""
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {auth.USERNAME_KEY: "alice"},
    )
    user, hashed = auth.load_credentials(str(tmp_path))
    assert user is None
    assert hashed is None
    assert auth.auth_enabled() is False


def test_load_credentials_malformed_blank_values(tmp_path):
    """Blank username or hash → auth disabled, no crash."""
    p = auth.env_path(str(tmp_path))
    # Raw write with blank values (write_env_file would write them fine too).
    auth.write_env_file(
        p,
        {
            auth.USERNAME_KEY: "",
            auth.PASSWORD_HASH_KEY: "",
        },
    )
    user, hashed = auth.load_credentials(str(tmp_path))
    assert user is None
    assert hashed is None


def test_load_credentials_ignores_garbage_lines(tmp_path):
    """Comment lines, blank lines, and missing '=' lines are skipped."""
    p = auth.env_path(str(tmp_path))
    hashed = auth.generate_password_hash("pw")
    with open(p, "w") as f:
        f.write("# a comment\n")
        f.write("\n")
        f.write("not a kv line\n")
        f.write(f"{auth.USERNAME_KEY}=bob\n")
        f.write(f'{auth.PASSWORD_HASH_KEY}="{hashed}"\n')
    user, got_hash = auth.load_credentials(str(tmp_path))
    assert user == "bob"
    assert got_hash == hashed


def test_load_credentials_supports_users_json(tmp_path):
    alice_hash = auth.generate_password_hash("alicepw")
    bob_hash = auth.generate_password_hash("bobpw")
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {
            auth.USERS_JSON_KEY: '{"alice":"%s","bob":"%s"}' % (alice_hash, bob_hash),
        },
    )
    user, got_hash = auth.load_credentials(str(tmp_path))
    assert user == "alice"
    assert got_hash == alice_hash
    assert auth.get_users() == {"alice": alice_hash, "bob": bob_hash}
    assert auth.get_password_hash("bob") == bob_hash


def test_load_credentials_merges_legacy_with_users_json(tmp_path):
    bob_hash = auth.generate_password_hash("bobpw")
    legacy_hash = auth.generate_password_hash("legacy")
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {
            auth.USERS_JSON_KEY: '{"bob":"%s"}' % bob_hash,
            auth.USERNAME_KEY: "legacy",
            auth.PASSWORD_HASH_KEY: legacy_hash,
        },
    )
    auth.load_credentials(str(tmp_path))
    assert auth.get_users() == {"bob": bob_hash, "legacy": legacy_hash}


def test_apply_credentials_mapping_round_trip(tmp_path):
    existing = {auth.SECRET_KEY_KEY: "secret"}
    users = {
        "alice": auth.generate_password_hash("a"),
        "bob": auth.generate_password_hash("b"),
    }
    updated = auth.apply_credentials_mapping(existing, users)
    assert updated[auth.SECRET_KEY_KEY] == "secret"
    assert auth.USERS_JSON_KEY in updated
    assert updated[auth.USERNAME_KEY] == "alice"
    assert updated[auth.PASSWORD_HASH_KEY] == users["alice"]
    parsed_users = auth.parse_credentials_mapping(updated)
    assert parsed_users == users


def test_apply_credentials_mapping_empty_removes_auth_keys():
    existing = {
        auth.SECRET_KEY_KEY: "secret",
        auth.USERNAME_KEY: "alice",
        auth.PASSWORD_HASH_KEY: "hash",
        auth.USERS_JSON_KEY: '{"alice":"hash"}',
    }
    updated = auth.apply_credentials_mapping(existing, {})
    assert updated == {auth.SECRET_KEY_KEY: "secret"}


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert auth.parse_env_file(str(tmp_path / "nope.env")) == {}


# ---------------------------------------------------------------------------
# check_password
# ---------------------------------------------------------------------------


def test_check_password_correct():
    h = auth.generate_password_hash("correct horse battery staple")
    assert auth.check_password("correct horse battery staple", h) is True


def test_check_password_wrong():
    h = auth.generate_password_hash("right")
    assert auth.check_password("wrong", h) is False


def test_check_password_none_hash():
    assert auth.check_password("anything", None) is False


def test_check_password_invalid_hash_format():
    # A malformed hash must never raise, just return False.
    assert auth.check_password("x", "not-a-real-hash-format") is False


# ---------------------------------------------------------------------------
# SECRET_KEY
# ---------------------------------------------------------------------------


def test_secret_key_generated_and_persisted(tmp_path):
    path = auth.env_path(str(tmp_path))
    assert not os.path.exists(path)
    key = auth.load_or_create_secret_key(str(tmp_path))
    assert key and len(key) >= 32
    # The key must now be present in the file on disk.
    data = auth.parse_env_file(path)
    assert data[auth.SECRET_KEY_KEY] == key


def test_secret_key_stable(tmp_path):
    k1 = auth.load_or_create_secret_key(str(tmp_path))
    k2 = auth.load_or_create_secret_key(str(tmp_path))
    assert k1 == k2


def test_secret_key_preserves_existing_credentials(tmp_path):
    """Generating a secret key must not clobber the username or hash."""
    hashed = auth.generate_password_hash("pw")
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {
            auth.USERNAME_KEY: "alice",
            auth.PASSWORD_HASH_KEY: hashed,
        },
    )
    auth.load_or_create_secret_key(str(tmp_path))
    data = auth.parse_env_file(auth.env_path(str(tmp_path)))
    assert data[auth.USERNAME_KEY] == "alice"
    assert data[auth.PASSWORD_HASH_KEY] == hashed
    assert auth.SECRET_KEY_KEY in data


# ---------------------------------------------------------------------------
# write_env_file
# ---------------------------------------------------------------------------


def test_write_env_file_sets_mode_600(tmp_path):
    """On POSIX the env file must be chmod 600 so the hash is not
    world-readable. On Windows this is a no-op but shouldn't crash."""
    if os.name != "posix":
        pytest.skip("file mode test only meaningful on POSIX")
    p = auth.env_path(str(tmp_path))
    auth.write_env_file(p, {auth.USERNAME_KEY: "alice"})
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# is_xhr_request
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_is_xhr_request_detects_x_requested_with():
    req = _FakeRequest({"X-Requested-With": "XMLHttpRequest"})
    assert auth.is_xhr_request(req) is True


def test_is_xhr_request_detects_json_accept():
    req = _FakeRequest({"Accept": "application/json"})
    assert auth.is_xhr_request(req) is True


def test_is_xhr_request_browser_html_is_not_xhr():
    req = _FakeRequest({"Accept": "text/html,application/json"})
    assert auth.is_xhr_request(req) is False


def test_is_xhr_request_empty_headers():
    req = _FakeRequest({})
    assert auth.is_xhr_request(req) is False
