"""Unit tests for the display borrowing in scripts/daily.py (no Anki or desktop needed).

    uv run --with pytest --with markdown-it-py --with pygments --with pyyaml pytest tests/
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
spec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)

SESSION = dict(DISPLAY=':0', WAYLAND_DISPLAY='wayland-0', XDG_SESSION_TYPE='wayland', XDG_RUNTIME_DIR='/run/user/1000',
               DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus', PATH='/manager/path', HOME='/home/x')
SOCKETS = {'/run/user/1000/wayland-0', '/tmp/.X11-unix/X0'}


def borrow(env, manager=SESSION, sockets=SOCKETS, system='Linux'):
    return daily.desktop_env(env=env, manager=manager, system=system, exists=lambda p: p in sockets)


def test_a_job_with_a_display_keeps_its_own():
    env, note = borrow(dict(DISPLAY=':1', PATH='/job'))
    assert env == dict(DISPLAY=':1', PATH='/job') and note is None


def test_other_systems_are_left_alone():
    env, note = borrow(dict(PATH='/job'), system='Darwin')
    assert env == dict(PATH='/job') and note is None


def test_borrows_the_session_display_as_a_set_and_nothing_else():
    env, note = borrow(dict(PATH='/job', XDG_RUNTIME_DIR='/sandbox', HOME='/home/x'))
    assert env['WAYLAND_DISPLAY'] == 'wayland-0' and env['DISPLAY'] == ':0'
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1000'  # the socket name is relative to the session's runtime dir
    assert env['PATH'] == '/job'  # only the display variables are borrowed
    assert 'desktop' in note


def test_a_logged_out_session_is_not_borrowed():
    env, note = borrow(dict(PATH='/job'), sockets=set())
    assert 'DISPLAY' not in env and 'WAYLAND_DISPLAY' not in env
    assert 'gone' in note


def test_no_session_display_at_all():
    env, note = borrow(dict(PATH='/job'), manager=dict(PATH='/m', XDG_RUNTIME_DIR='/run/user/1000'))
    assert 'DISPLAY' not in env and 'no desktop session' in note


def test_x11_only_session():
    env, note = borrow(dict(PATH='/job'), manager=dict(DISPLAY=':0', XAUTHORITY='/run/user/1000/xauth'),
                       sockets={'/tmp/.X11-unix/X0'})
    assert env['DISPLAY'] == ':0' and env['XAUTHORITY'] == '/run/user/1000/xauth'


def test_unreadable_manager_says_why(monkeypatch):
    monkeypatch.setattr(daily, 'user_manager_env', lambda env: (None, 'Failed to connect to bus'))
    env, note = daily.desktop_env(env=dict(PATH='/job'), system='Linux')
    assert 'DISPLAY' not in env and 'Failed to connect to bus' in note


def test_manager_lookup_finds_the_bus_without_xdg_runtime_dir(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, '{"DISPLAY": ":0"}', ''

    def run(cmd, **kw):
        seen.update(cmd=cmd, env=kw['env'])
        return Done()

    monkeypatch.setattr(daily.shutil, 'which', lambda name: '/usr/bin/systemctl')
    monkeypatch.setattr(daily.subprocess, 'run', run)
    manager, error = daily.user_manager_env(dict(PATH='/job'))
    assert manager == {'DISPLAY': ':0'} and error is None
    assert seen['env']['XDG_RUNTIME_DIR'].startswith('/run/user/')


def test_manager_lookup_skips_quoted_values_on_old_systemd(monkeypatch):
    class Old:
        returncode, stdout, stderr = 1, '', 'Unknown output mode'

    class Plain:
        returncode, stderr = 0, ''
        stdout = "DISPLAY=:0\nXAUTHORITY=$'/tmp/odd\\\\tname'\n"

    calls = iter([Old(), Plain()])
    monkeypatch.setattr(daily.shutil, 'which', lambda name: '/usr/bin/systemctl')
    monkeypatch.setattr(daily.subprocess, 'run', lambda cmd, **kw: next(calls))
    manager, error = daily.user_manager_env(dict(XDG_RUNTIME_DIR='/run/user/1000'))
    assert manager == {'DISPLAY': ':0'} and error is None
