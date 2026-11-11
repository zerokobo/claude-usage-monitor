import argparse
import datetime
import json
import os
import pathlib
import sys
import time
from collections import defaultdict

from usage import (DATA, ClaudeUsageClient, ClaudeUsageError, History, QuotaAlerts, RateLimited, Window,
                   atomic_json, credentials_path, default_config_dir, load_accounts)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF, QLockFile
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon, QFont, QPainterPath
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
    QInputDialog, QPushButton, QProgressBar, QSystemTrayIcon, QMenu, QComboBox, QMessageBox, QFrame)

PALETTE = ['#EAA07C', '#B79CF2', '#71D7C1', '#7CB7EA', '#E6C86E']
INTERVAL = 180          # seconds between fetches per account
MAX_BACKOFF = 900       # longest wait after HTTP 429
MIN_MANUAL_GAP = 60     # the refresh button never calls an account faster than this
STALE_AFTER = 1200
CONFIG = DATA / 'preferences.json'


def countdown(reset):
    if reset is None:
        return '時刻未取得（未使用）'
    seconds = max(0, int(reset - time.time()))
    if not seconds:
        return '更新待ち'
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f'{days}日 ' if days else '') + f'{hours:02d}:{minutes:02d}:{rest % 60:02d}'


def tray_icon(values, colors):
    """One bar per account (up to three), filled with the smaller remaining window."""
    names = list(colors)[:3]
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#121B28'))
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 10, 10)
    gap = 3
    width = (64 - 2 * 5 - gap * (len(names) - 1)) / max(1, len(names))
    for i, name in enumerate(names):
        x = 5 + i * (width + gap)
        p.setBrush(QColor('#344052'))
        p.drawRoundedRect(QRectF(x, 6, width, 52), 3, 3)
        value = values.get(name)
        if value is not None:
            h = max(0, min(100, value)) * 48 / 100
            p.setBrush(QColor('#F07178' if value <= 10 else colors[name]))
            p.drawRoundedRect(QRectF(x + 2, 56 - h, width - 4, h), 2, 2)
        else:
            p.setPen(QPen(QColor('#A2AEC0'), 3))
            p.drawLine(int(x + 4), 29, int(x + width - 4), 29)
            p.setPen(Qt.PenStyle.NoPen)
    p.end()
    return QIcon(pixmap)


class Poller(QThread):
    # account name, windows (None = keep the previous values), server time, message
    received = Signal(str, object, float, str)

    def __init__(self, accounts):
        super().__init__()
        self.accounts = list(accounts)
        self.refresh = False

    def run(self):
        due, last, backoff = {}, {}, {}
        while not self.isInterruptionRequested():
            if self.refresh:
                self.refresh = False
                for name, called in last.items():
                    due[name] = min(due.get(name, 0), called + max(MIN_MANUAL_GAP, backoff.get(name, 0)))
            for account in list(self.accounts):
                name = account['name']
                now = time.monotonic()
                if now < due.get(name, 0):
                    continue
                last[name] = now
                try:
                    windows = ClaudeUsageClient(credentials_path(account.get('config_dir'))).fetch()
                except RateLimited as error:
                    wait = min(MAX_BACKOFF, backoff.get(name, INTERVAL // 2) * 2)
                    backoff[name] = wait
                    due[name] = now + wait
                    self.received.emit(name, None, 0, f'{error}{wait // 60}分後に再試行')
                except ClaudeUsageError as error:
                    due[name] = now + INTERVAL
                    self.received.emit(name, [], 0, str(error))
                else:
                    backoff.pop(name, None)
                    due[name] = now + INTERVAL
                    self.received.emit(name, windows, time.time(), '')
                if self.isInterruptionRequested():
                    return
            self.msleep(250)


class Graph(QWidget):
    def __init__(self, history, colors, weekly=False):
        super().__init__()
        self.history = history
        self.colors = colors
        self.weekly = weekly
        self.hours = 168 if weekly else 6
        self.setFixedHeight(105)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(35, 25, self.width() - 45, self.height() - 47)
        p.setPen(QColor('#E3EAF4'))
        p.setFont(QFont('Yu Gothic UI', 9, QFont.Weight.DemiBold))
        p.drawText(5, 15, '週次枠 · 残量' if self.weekly else '5時間枠 · 残量')
        p.setFont(QFont('Yu Gothic UI', 8))
        for value in (0, 25, 50, 75, 100):
            y = area.bottom() - area.height() * value / 100
            p.setPen(QPen(QColor('#293446'), 1))
            p.drawLine(int(area.left()), int(y), int(area.right()), int(y))
            p.setPen(QColor('#8493A7'))
            if value in (0, 50, 100):
                p.drawText(3, int(y + 4), f'{value}%')
        now = time.time()
        since = now - self.hours * 3600
        points = 0
        for account, color in self.colors.items():
            grouped = defaultdict(list)
            for stamp, label, value, reset in self.history.rows(account, since):
                if label.startswith('週次') == self.weekly:
                    grouped[label].append((stamp, value))
            for rows in grouped.values():
                segments = []
                segment = []
                previous_stamp = None
                for stamp, value in rows:
                    x = area.left() + (stamp - since) / (now - since) * area.width()
                    y = area.bottom() - value / 100 * area.height()
                    if previous_stamp is not None and stamp - previous_stamp > 900:
                        segments.append(segment)
                        segment = []
                    segment.append((x, y))
                    previous_stamp = stamp
                    points += 1
                segments.append(segment)
                for segment in segments:
                    if len(segment) < 2:
                        continue
                    line = QPainterPath()
                    line.moveTo(*segment[0])
                    for x, y in segment[1:]:
                        line.lineTo(x, y)
                    fill = QPainterPath(line)
                    fill.lineTo(segment[-1][0], area.bottom())
                    fill.lineTo(segment[0][0], area.bottom())
                    fill.closeSubpath()
                    tint = QColor(color)
                    tint.setAlpha(12)
                    p.fillPath(fill, tint)
                    p.setPen(QPen(QColor(color), 1.6))
                    p.drawPath(line)
        p.setPen(QColor('#8493A7'))
        p.drawText(int(area.left()), self.height() - 7, f'{self.hours // 24}日前' if self.hours >= 24 else f'{self.hours}時間前')
        midpoint = now - self.hours * 1800
        p.drawText(int(area.center().x() - 17), self.height() - 7, datetime.datetime.fromtimestamp(midpoint).strftime('%m/%d' if self.hours > 24 else '%H:%M'))
        p.drawText(int(area.right() - 24), self.height() - 7, '現在')
        if not points:
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, '取得後に残量の履歴を表示します')
        p.end()


class Panel(QWidget):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.history = History(':memory:' if demo else DATA / 'history.sqlite3')
        try:
            self.preferences = {} if demo else json.loads(CONFIG.read_text(encoding='utf-8'))
            if not isinstance(self.preferences, dict):
                self.preferences = {}
        except (OSError, ValueError):
            self.preferences = {}
        if demo:
            self.preferences['accounts'] = [{'name': '個人', 'config_dir': ''}, {'name': '仕事', 'config_dir': ''}]
        self.accounts = load_accounts(self.preferences)
        self.colors = {}
        self.values, self.updated, self.errors, self.notes = {}, {}, {}, {}
        self.alerts = QuotaAlerts()
        self.drag = None
        self.setWindowTitle('Claude Usage Monitor')
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(350)
        self.setWindowOpacity(self.preferences.get('opacity', 1.0))
        self.setStyleSheet('''
            QWidget {background:#121B28;color:#EEF3FA;font-family:"Yu Gothic UI";font-size:12px;}
            QLabel#title {font-size:14px;font-weight:600;}
            QLabel#muted {color:#91A1B7;font-size:10px;}
            QFrame#usageCard {background:transparent;border:0;}
            QFrame#usageCard QLabel {background:transparent;border:0;}
            QFrame#usageCard QProgressBar {border:0;background:#2C3A4E;}
            QPushButton,QComboBox {background:#243247;border:0;border-radius:5px;padding:5px 9px;}
            QPushButton:hover {background:#344863;}
            QProgressBar {background:#273448;border:0;border-radius:3px;height:6px;}
            QMenu {background:#1C293C;}
            QMenu::item:selected {background:#344863;}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel('CLAUDE USAGE' + ('  ·  DEMO' if demo else ''))
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        hide = QPushButton('−')
        hide.setFixedWidth(28)
        hide.clicked.connect(self.hide)
        header.addWidget(hide)
        layout.addLayout(header)
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(8)
        layout.addLayout(self.cards_box)
        self.cards = {}
        controls = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItems(['直近6時間 / 7日', '両方24時間', '両方7日'])
        self.scope.currentIndexChanged.connect(self.change_scope)
        controls.addWidget(self.scope)
        controls.addStretch()
        self.graph_button = QPushButton('グラフを隠す')
        self.graph_button.clicked.connect(self.toggle_graph)
        controls.addWidget(self.graph_button)
        layout.addLayout(controls)
        self.graph_area = QWidget()
        graphs_layout = QVBoxLayout(self.graph_area)
        graphs_layout.setContentsMargins(0, 0, 0, 0)
        graphs_layout.setSpacing(7)
        self.graphs = [Graph(self.history, self.colors), Graph(self.history, self.colors, weekly=True)]
        for graph in self.graphs:
            graphs_layout.addWidget(graph)
        layout.addWidget(self.graph_area)
        self.legend = QLabel()
        self.legend.setObjectName('muted')
        layout.addWidget(self.legend)
        bottom = QHBoxLayout()
        refresh = QPushButton('更新')
        refresh.clicked.connect(self.request_refresh)
        bottom.addWidget(refresh)
        settings = QPushButton('設定')
        settings.clicked.connect(lambda: self.menu.popup(settings.mapToGlobal(settings.rect().bottomLeft())))
        bottom.addWidget(settings)
        bottom.addStretch()
        note = QLabel(f'{INTERVAL // 60}分ごとに取得')
        note.setObjectName('muted')
        bottom.addWidget(note)
        layout.addLayout(bottom)
        self.menu = QMenu(self)
        self.menu.addAction('パネルを表示 / 非表示', self.toggle_visible)
        self.menu.addAction('今すぐ更新', self.request_refresh)
        self.menu.addSeparator()
        self.menu.addAction('アカウントを追加…', self.add_account)
        self.remove_menu = self.menu.addMenu('アカウントを外す')
        self.remove_menu.aboutToShow.connect(self.fill_remove_menu)
        self.menu.addSeparator()
        self.menu.addAction('データ保存先を開く', lambda: os.startfile(DATA))
        top = self.menu.addAction('最前面に表示')
        top.setCheckable(True)
        top.setChecked(True)
        top.toggled.connect(self.set_topmost)
        transparency = self.menu.addMenu('透明度')
        for value in (100, 90, 80, 70):
            transparency.addAction(f'{value}%', lambda v=value: self.opacity(v))
        startup = self.menu.addAction('Windowsログイン時に起動')
        startup.setCheckable(True)
        startup.setChecked(self.startup_file().exists())
        startup.toggled.connect(self.set_startup)
        self.menu.addSeparator()
        self.menu.addAction('終了', self.quit)
        self.tray = QSystemTrayIcon(self)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.build_cards()
        self.tray.setIcon(tray_icon({}, self.colors))
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.render)
        self.timer.start(1000)
        self.worker = None
        if demo:
            self.load_demo()
        else:
            self.worker = Poller(self.accounts)
            self.worker.received.connect(self.receive)
            self.worker.start()
        self.adjustSize()
        available = QApplication.primaryScreen().availableGeometry()
        x, y = self.preferences.get('position', [available.right() - self.width() - 16, available.bottom() - self.height() - 16])
        self.move(max(available.left(), min(x, available.right() - self.width())), max(available.top(), min(y, available.bottom() - self.height())))
        self.render()

    def build_cards(self):
        while self.cards_box.count():
            widget = self.cards_box.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.cards.clear()
        self.colors.clear()
        for i, account in enumerate(self.accounts):
            name = account['name']
            color = PALETTE[i % len(PALETTE)]
            self.colors[name] = color
            for store, empty in ((self.values, []), (self.errors, ''), (self.notes, '')):
                store.setdefault(name, empty)
            card = QFrame()
            card.setObjectName('usageCard')
            block = QVBoxLayout(card)
            block.setContentsMargins(0, 3, 0, 3)
            block.setSpacing(4)
            heading = QHBoxLayout()
            label = QLabel(name)
            label.setStyleSheet(f'color:{color};font-size:13px;font-weight:600')
            heading.addWidget(label)
            heading.addStretch()
            big = QLabel('未取得')
            big.setTextFormat(Qt.TextFormat.RichText)
            big.setStyleSheet('font-size:11px;')
            heading.addWidget(big)
            block.addLayout(heading)
            bars = []
            for _ in range(2):
                text = QLabel('取得待ち')
                text.setObjectName('muted')
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                bar.setStyleSheet(f'QProgressBar::chunk {{background:{color};border-radius:3px}}')
                block.addWidget(text)
                bars.append((text, bar))
            captions = []
            for i, (_, bar) in enumerate(bars):
                row = QHBoxLayout()
                caption = QLabel('5時間' if i == 0 else '週次')
                caption.setObjectName('muted')
                caption.setFixedWidth(48)
                captions.append(caption)
                row.addWidget(caption)
                row.addWidget(bar)
                block.addLayout(row)
            status = QLabel('接続しています')
            status.setObjectName('muted')
            # One fixed line: messages must never change the panel height.
            status.setFixedHeight(status.fontMetrics().height() + 2)
            block.addWidget(status)
            self.cards_box.addWidget(card)
            self.cards[name] = (big, bars, status, captions)
        self.legend.setText('　'.join(f'<span style="color:{color}">━ {name}</span>' for name, color in self.colors.items()))
        self.legend.setVisible(len(self.colors) > 1)
        for graph in self.graphs:
            graph.update()
        self.adjustSize()

    def load_demo(self):
        now = time.time()
        for (name, short, week), account in zip([('個人', 32, 54), ('仕事', 78, 12)], self.accounts):
            for i in range(2017):
                stamp = now - (2016 - i) * 300
                self.history.add(name, stamp, [Window('週次', week * (i / 2016) ** 1.25, now + 212400)])
                if i >= 1944:
                    recent = (i - 1944) / 72
                    used = (25 + recent * 95) if recent < .25 else short * ((recent - .25) / .75) ** .8
                    self.history.add(name, stamp, [Window('5時間', used, now + 7800)])
            self.receive(name, [Window('5時間', short, now + 7800), Window('週次', week, now + 212400)], now, '')

    def receive(self, account, windows, stamp, message):
        if account not in self.cards:
            return
        if windows is None:
            # Temporary (rate limited): keep showing the last good values.
            self.notes[account] = message
        else:
            self.notes[account] = ''
            self.errors[account] = message
            self.values[account] = windows
        if stamp:
            self.updated[account] = stamp
            self.history.add(account, stamp, windows)
        self.render()
        for graph in self.graphs:
            graph.update()

    def render(self):
        icon_values = {}
        tooltip = []
        for name, (big, bars, status, captions) in self.cards.items():
            windows = self.values[name]
            error = self.errors.get(name)
            age = time.time() - self.updated.get(name, 0)
            stale = age >= STALE_AFTER
            if stale or error:
                windows = [Window(w.label, None, w.reset, 'stale' if stale else 'error') for w in windows]
            # Both windows constrain availability. The tray shows the smaller remainder.
            remaining = [w.remaining() for w in windows if w.remaining() is not None]
            value = min(remaining) if remaining else None
            icon_values[name] = value
            short = next((w for w in windows if not w.label.startswith('週次')), None)
            week = next((w for w in windows if w.label.startswith('週次')), None)

            def percentage(w):
                r = w.remaining() if w else None
                return f'{r:.0f}%' if r is not None else '—'
            big.setText(f'残り　5時間 <b style="font-size:16px">{percentage(short)}</b> / 週次 <b style="font-size:16px">{percentage(week)}</b>')
            for i, (text, bar) in enumerate(bars):
                # Rows stay visible while data is missing so the layout never jumps.
                if i < len(windows):
                    w = windows[i]
                    r = w.remaining()
                    captions[i].setText(w.label)
                    if r is not None:
                        text.setText(f'{w.label}枠：リセットまで {countdown(w.reset)}')
                    elif w.reset and w.reset <= time.time():
                        text.setText(f'{w.label}枠：リセット時刻経過・更新待ち')
                    else:
                        text.setText(f'{w.label}枠：取得失敗 / 更新待ち')
                    bar.setValue(int(r * 10) if r is not None else 0)
                else:
                    text.setText('取得待ち')
                    bar.setValue(0)
            stamp = self.updated.get(name)
            at = datetime.datetime.fromtimestamp(stamp).strftime('%m/%d %H:%M:%S') if stamp else '—'
            message = error or self.notes.get(name) or ('' if stamp else '接続しています')
            line = f'最終更新 {at}' + (' · 古いデータ' if stamp and stale else '') + (f' · {message}' if message else '')
            status.setText(status.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, max(status.width(), 300)))
            status.setToolTip(message)
            tooltip.append(f'{name}: ' + (f'残り {value:.0f}%' if value is not None else '未取得 / 更新待ち'))
            if not stale and not error and not self.demo:
                due = [w for w in windows if self.alerts.due(name, w)]
                if due:
                    details = ' / '.join(f'{w.label} 残り {w.remaining():.0f}%' for w in due)
                    self.tray.showMessage('Claudeの利用枠が残りわずか', f'{name} {details}', QSystemTrayIcon.MessageIcon.Warning)
        self.tray.setIcon(tray_icon(icon_values, self.colors))
        self.tray.setToolTip('\n'.join(tooltip) + '\n各アカウントの小さい方の残量')

    def set_accounts(self, accounts):
        self.accounts = accounts
        self.preferences['accounts'] = accounts
        self.save()
        self.build_cards()
        if self.worker:
            self.worker.accounts = list(accounts)
            self.worker.refresh = True
        self.render()

    def add_account(self):
        name, accepted = QInputDialog.getText(self, 'アカウントを追加', '表示名（例: 仕事）')
        name = name.strip()
        if not accepted or not name:
            return
        if name in [a['name'] for a in self.accounts]:
            QMessageBox.warning(self, 'アカウントを追加', 'その表示名はもう使われています。')
            return
        folder = QFileDialog.getExistingDirectory(self, 'Claude Code の設定フォルダ（.credentials.json がある場所）', str(default_config_dir().parent))
        if not folder:
            return
        if not credentials_path(folder).exists():
            QMessageBox.warning(self, 'アカウントを追加', 'このフォルダに .credentials.json がありません。\nそのフォルダを CLAUDE_CONFIG_DIR にして Claude Code でログインしてから選んでください。')
            return
        self.set_accounts(self.accounts + [{'name': name, 'config_dir': folder}])

    def fill_remove_menu(self):
        self.remove_menu.clear()
        for account in self.accounts:
            action = self.remove_menu.addAction(account['name'], lambda n=account['name']: self.remove_account(n))
            action.setEnabled(len(self.accounts) > 1)

    def remove_account(self, name):
        answer = QMessageBox.question(self, 'アカウントを外す', f'「{name}」を表示から外しますか？（履歴は残ります）')
        if answer == QMessageBox.StandardButton.Yes:
            self.set_accounts([a for a in self.accounts if a['name'] != name])

    def request_refresh(self):
        if self.worker:
            self.worker.refresh = True

    def change_scope(self, index):
        hours = [(6, 168), (24, 24), (168, 168)][index]
        for graph, span in zip(self.graphs, hours):
            graph.hours = span
            graph.update()

    def toggle_graph(self):
        visible = not self.graph_area.isVisible()
        self.graph_area.setVisible(visible)
        self.scope.setVisible(visible)
        self.graph_button.setText('グラフを隠す' if visible else 'グラフを表示')
        self.adjustSize()

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def set_topmost(self, enabled):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def opacity(self, value):
        self.setWindowOpacity(value / 100)
        self.preferences['opacity'] = value / 100
        self.save()

    @staticmethod
    def startup_file():
        return pathlib.Path(os.environ.get('APPDATA', pathlib.Path.home())) / 'Microsoft/Windows/Start Menu/Programs/Startup/ClaudeUsageMonitor.vbs'

    def set_startup(self, enabled):
        path = self.startup_file()
        try:
            if enabled:
                command = f'"{sys.executable}"' if getattr(sys, 'frozen', False) else f'"{pathlib.Path(sys.executable).with_name("pythonw.exe")}" "{pathlib.Path(__file__).resolve()}"'
                escaped = command.replace('"', '""')
                path.write_text(f'CreateObject("WScript.Shell").Run "{escaped}", 0, False\n', encoding='utf-16')
            elif path.exists():
                path.unlink()
        except OSError:
            QMessageBox.warning(self, '自動起動', '自動起動の設定を変更できませんでした。')

    def save(self):
        if not self.demo:
            self.preferences['position'] = [self.x(), self.y()]
            atomic_json(CONFIG, self.preferences)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.save()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit(self):
        self.save()
        self.tray.hide()
        if self.worker:
            self.worker.requestInterruption()
            # The request has a bounded timeout. Keep the UI responsive during shutdown.
            self.hide()
            self.worker.finished.connect(QApplication.instance().quit)
            if not self.worker.isRunning():
                QApplication.instance().quit()
        else:
            QApplication.instance().quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='接続せずに見本の値で表示する')
    parser.add_argument('--screenshot', help='起動後に画面をこのPNGへ保存して終了する')
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    DATA.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DATA / ('demo.lock' if args.demo else 'app.lock')))
    if not lock.tryLock(100):
        return
    panel = Panel(args.demo)
    panel.show()
    if args.screenshot:
        def capture():
            panel.grab().save(args.screenshot)
            panel.quit()
        QTimer.singleShot(1000 if args.demo else 15000, capture)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
