"""Claude plan usage: fetching, history and alert rules (no Qt).

The access token is borrowed read-only from Claude Code's credentials file.
Claude Code owns refreshing it; this module never refreshes or writes it,
and sends it only to api.anthropic.com.
"""
import datetime as dt
import json
import math
import os
import pathlib
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

APP_NAME = 'ClaudeUsageMonitor'
DATA = pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home())) / APP_NAME
# Same endpoint Claude Code's /usage uses. It is not a documented public API
# and may change without notice.
USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
WINDOWS = [('five_hour', '5時間'), ('seven_day', '週次')]
EXPIRED = 'ログイン期限切れ。Claude Codeを使うと自動で更新されます。'


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


@dataclass
class Window:
    label: str
    used: float | None
    reset: float | None
    state: str = 'fresh'

    def remaining(self, now=None):
        now = time.time() if now is None else now
        if self.state != 'fresh' or self.used is None:
            return None
        if self.reset is not None and self.reset <= now:
            return None
        return max(0.0, min(100.0, 100 - self.used))


def parse_usage(data):
    windows = []
    if not isinstance(data, dict):
        return windows
    for key, label in WINDOWS:
        row = data.get(key)
        if not isinstance(row, dict):
            continue
        used = row.get('utilization')
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            continue
        reset = None
        if row.get('resets_at'):
            try:
                reset = dt.datetime.fromisoformat(row['resets_at']).timestamp()
            except (TypeError, ValueError):
                pass
        # An unused 5-hour window has no reset time yet; it is simply 0% used.
        windows.append(Window(label, float(used), reset))
    return windows


class ClaudeUsageError(RuntimeError):
    pass


class RateLimited(ClaudeUsageError):
    """The usage endpoint allows roughly one call per two minutes per account."""


def default_config_dir():
    return pathlib.Path(os.environ.get('CLAUDE_CONFIG_DIR') or pathlib.Path.home() / '.claude')


def credentials_path(config_dir=None):
    return pathlib.Path(config_dir or default_config_dir()) / '.credentials.json'


class ClaudeUsageClient:
    def __init__(self, path=None):
        self.path = path or credentials_path()

    def token(self):
        try:
            oauth = json.loads(self.path.read_text(encoding='utf-8'))['claudeAiOauth']
            token, expires = oauth['accessToken'], oauth.get('expiresAt')
        except (OSError, ValueError, KeyError, TypeError):
            raise ClaudeUsageError('Claude Codeのログイン情報がありません。Claude Codeでログインしてください。') from None
        if isinstance(expires, (int, float)) and expires / 1000 <= time.time():
            raise ClaudeUsageError(EXPIRED)
        return token

    def fetch(self):
        request = urllib.request.Request(USAGE_URL, headers={
            'Authorization': 'Bearer ' + self.token(),
            'anthropic-beta': 'oauth-2025-04-20',
            'Content-Type': 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise ClaudeUsageError(EXPIRED) from None
            if error.code == 429:
                raise RateLimited('取得間隔を空けています。') from None
            raise ClaudeUsageError(f'取得に失敗しました（HTTP {error.code}）。次回再試行します。') from None
        except (OSError, ValueError):
            raise ClaudeUsageError('接続できません。次回再試行します。') from None
        windows = parse_usage(data)
        if not windows:
            raise ClaudeUsageError('利用枠が応答に含まれていません。')
        return windows


class QuotaAlerts:
    """Low-quota alerts per account/window, independent of reset timestamps."""
    THRESHOLD = 10
    REMINDER_SECONDS = 2 * 3600
    DROP_POINTS = 2

    def __init__(self):
        self.last = {}

    def due(self, account, window, now=None):
        now = time.time() if now is None else now
        value = window.remaining(now)
        key = (account, window.label)
        if value is None:
            return False
        if value > self.THRESHOLD:
            self.last.pop(key, None)
            return False
        previous = self.last.get(key)
        if previous is not None:
            stamp, remaining = previous
            if (now - stamp < self.REMINDER_SECONDS
                    and remaining - value < self.DROP_POINTS
                    and not (value == 0 and remaining > 0)):
                return False
        self.last[key] = (now, value)
        return True


class History:
    def __init__(self, path):
        if path != ':memory:':
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS samples (account TEXT, stamp REAL, label TEXT, remaining REAL, reset REAL, UNIQUE(account,stamp,label))')

    def add(self, account, stamp, windows):
        for w in windows:
            value = w.remaining(stamp)
            if value is not None:
                self.db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?)', (account, stamp, w.label, value, w.reset))
        self.db.commit()

    def rows(self, account, since):
        return self.db.execute('SELECT stamp,label,remaining,reset FROM samples WHERE account=? AND stamp>=? ORDER BY stamp', (account, since)).fetchall()


def load_accounts(preferences):
    """Accounts from preferences; a single default Claude Code login otherwise."""
    accounts = []
    for row in preferences.get('accounts') or []:
        if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name'].strip():
            name = row['name'].strip()
            if name not in [a['name'] for a in accounts]:
                accounts.append({'name': name, 'config_dir': row.get('config_dir') or ''})
    return accounts or [{'name': 'Claude', 'config_dir': ''}]


if __name__ == '__main__':
    for w in ClaudeUsageClient().fetch():
        reset = dt.datetime.fromtimestamp(w.reset) if w.reset else '—'
        print(w.label, f'残り {w.remaining():g}%', f'リセット {reset}')
