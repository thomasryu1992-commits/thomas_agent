"""Who may reach a bridge door — the grant the door states rather than inherits.

The doors' access control is the socket's group (mode 0660). Until `MVP_BRIDGE_CLIENT_GID`
existed that group was simply whatever gid the door process ran as, which made "who may reach
this door" a property of the *client's* container config. It failed the way an implicit grant
fails: the assistant's container carried `group_add: ["10001"]`, its supervisor dropped
privileges with `s6-setuidgid`, supplementary groups were recomputed from an `/etc/group` with
no such gid, and every MCP subprocess ran without it — three doors up, sockets present, and
nothing on either side saying why the assistant could not reach them.

These lock the fix: the gid is validated and never guessed, the *directory* is granted as well
as the socket (the half that actually bit), the grant never widens to world, and a configured
gid that cannot be applied refuses to listen rather than serving an unreachable door.
"""

from __future__ import annotations

import os
import stat

import pytest

from runtime.mvp_runtime import socket_door
from runtime.mvp_runtime.errors import ControlBlocked

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the bridge doors listen on AF_UNIX"
)


# --- reading the configured gid -----------------------------------------------

def test_unset_means_no_grant(monkeypatch):
    """The default must be exactly the old behaviour — a deployment that never heard of this
    setting keeps the socket group it already had."""
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    assert socket_door.resolve_client_gid() is None


def test_a_configured_gid_is_read(monkeypatch):
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, "10000")
    assert socket_door.resolve_client_gid() == 10000


def test_blank_is_unset_not_zero(monkeypatch):
    """Whitespace must not resolve to gid 0. Root is not a fallback."""
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, "   ")
    assert socket_door.resolve_client_gid() is None


@pytest.mark.parametrize("value", ["hermes", "10000abc", "1e4", "-1", ""])
def test_an_unusable_value_refuses_rather_than_being_ignored(monkeypatch, value):
    """A misconfigured grant must not silently fall back to the implicit group — that is the
    failure mode this whole setting exists to end. (An empty string is the one exception: it
    is 'unset', which is a stated default, not a malformed value.)"""
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, value)
    if value == "":
        assert socket_door.resolve_client_gid() is None
        return
    with pytest.raises(ControlBlocked) as exc:
        socket_door.resolve_client_gid()
    assert exc.value.reason_code == "BRIDGE_CLIENT_GID_INVALID"


# --- applying the grant --------------------------------------------------------

def test_no_gid_touches_nothing(tmp_path):
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir()
    sock.write_text("")
    before = (sock.stat().st_gid, sock.parent.stat().st_mode)
    socket_door.grant_client_access(sock, None)
    assert (sock.stat().st_gid, sock.parent.stat().st_mode) == before


def test_the_directory_is_granted_too(tmp_path):
    """The half that actually bit: a socket the client may open is unreachable inside a
    directory the client may not traverse."""
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir()
    sock.write_text("")
    socket_door.grant_client_access(sock, os.getgid())
    assert sock.parent.stat().st_gid == os.getgid()
    assert sock.stat().st_gid == os.getgid()


def test_the_directory_grants_traverse_but_never_world(tmp_path):
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir(mode=0o777)
    sock.write_text("")
    socket_door.grant_client_access(sock, os.getgid())

    mode = sock.parent.stat().st_mode
    assert mode & stat.S_IRGRP and mode & stat.S_IXGRP   # group may walk in
    assert not mode & stat.S_IWGRP                       # but not write the directory
    assert not mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)


def test_an_unappliable_gid_refuses_to_serve(tmp_path):
    """Fail-closed. A door that comes up unreachable is invisible from both ends; a refusal at
    the moment of listening is not."""
    sock = tmp_path / "bridge" / "d.sock"
    sock.parent.mkdir()
    sock.write_text("")
    # A gid this process cannot possibly belong to, so chown is refused by the kernel.
    unreachable = 4294967294 if os.getuid() != 0 else -1
    if os.getuid() == 0:
        pytest.skip("root can chown to any gid; the refusal cannot be provoked")
    with pytest.raises(ControlBlocked) as exc:
        socket_door.grant_client_access(sock, unreachable)
    assert exc.value.reason_code == "BRIDGE_CLIENT_GID_UNAVAILABLE"


def test_the_socket_mode_still_excludes_world():
    """The grant widens to exactly one gid. World access would make 'any process on this host'
    the authorization, which is wider than the host-console precedent the doors rest on."""
    assert not socket_door.SOCKET_MODE & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)
    assert not socket_door.DIR_MODE & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)


# --- through a real door -------------------------------------------------------

@unix_only
def test_a_door_applies_the_grant_when_it_listens(tmp_path, monkeypatch):
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, str(os.getgid()))
    sock = tmp_path / "bridge" / "d.sock"
    server = socket_door.SocketDoor(sock, lambda request: {"ok": True})
    try:
        assert sock.stat().st_gid == os.getgid()
        assert sock.parent.stat().st_gid == os.getgid()
    finally:
        server.server_close()


@unix_only
def test_a_door_refuses_to_listen_on_an_unappliable_grant(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip("root can chown to any gid; the refusal cannot be provoked")
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, "4294967294")
    with pytest.raises(ControlBlocked) as exc:
        socket_door.SocketDoor(tmp_path / "bridge" / "d.sock", lambda request: {"ok": True})
    assert exc.value.reason_code == "BRIDGE_CLIENT_GID_UNAVAILABLE"


@unix_only
def test_a_malformed_grant_is_refused_before_the_socket_exists(tmp_path, monkeypatch):
    """The check runs before bind, so a misconfigured deployment leaves no half-open door."""
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, "hermes")
    sock = tmp_path / "bridge" / "d.sock"
    with pytest.raises(ControlBlocked) as exc:
        socket_door.SocketDoor(sock, lambda request: {"ok": True})
    assert exc.value.reason_code == "BRIDGE_CLIENT_GID_INVALID"
    assert not sock.exists()
