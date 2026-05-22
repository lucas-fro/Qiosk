"""
Qiosk - navegador em tela cheia travado para totem / stand.

O que faz:
  - Abre uma URL (site, HTML local ou app React) em tela cheia, sem bordas.
  - Bloqueia o menu de contexto (clique direito) e atalhos comuns de saida.
  - Impede o fechamento da janela (Alt+F4 etc.) enquanto travado.
  - Saida escondida: toque varias vezes seguidas em um canto da tela
    -> abre um campo de senha -> so fecha se a senha estiver correta.

Uso:
  Qiosk.exe                  - abre o Qiosk (modo padrao)
  Qiosk.exe --config         - abre o editor de configuracao
  Qiosk.exe https://meusite  - abre o Qiosk com a URL fornecida

Funciona em Windows e Linux. NAO roda em Android - para telas
Android use o Fully Kiosk Browser.
"""

import ctypes
import hashlib
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import Qt, QUrl, QTimer, QObject, QEvent
from PyQt5.QtGui import QColor, QCursor, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLineEdit, QMessageBox,
    QFormLayout, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
    QPushButton, QFileDialog, QLabel, QShortcut,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView

# Atalho de emergencia: sempre funciona enquanto o kiosk tem foco,
# mesmo que o canto secreto deixe de capturar toques por algum motivo.
EMERGENCY_SHORTCUT = "Ctrl+Alt+Shift+Q"

# Nome global do mutex para single-instance no Windows.
SINGLE_INSTANCE_NAME = "Qiosk_Singleton_v1"

# Mantemos uma referencia global ao handle do mutex - se for coletado
# pelo GC, o mutex some e perde o sentido.
_instance_mutex_handle = None


def hash_password(password):
    """SHA-256 hex digest. Usado para nao guardar senha em texto."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def acquire_single_instance():
    """Tenta adquirir o mutex global. Retorna True se somos a primeira
    instancia, False se outra ja esta rodando. Em sistemas nao-Windows
    sempre retorna True (sem enforcement)."""
    if sys.platform != "win32":
        return True
    global _instance_mutex_handle
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex_handle = handle  # mantem vivo enquanto o processo viver
    return True


# Pagina mostrada quando a URL principal nao carrega (sem internet,
# servidor fora do ar, etc). O retry e' feito pelo Qt, nao pelo JS.
OFFLINE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html, body { margin:0; height:100%; background:#14152b;
    color:#e6e6f0; font-family:'Segoe UI', sans-serif; }
  body { display:flex; align-items:center; justify-content:center; }
  .box { text-align:center; padding:40px; }
  h1 { color:#a78bfa; font-size:34px; margin:0 0 10px; font-weight:600; }
  p  { color:#a0a0b8; font-size:18px; margin:6px 0; }
  .spinner { width:54px; height:54px; margin:32px auto;
    border:4px solid #2a2b54; border-top-color:#8b5cf6;
    border-radius:50%; animation:spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style></head><body><div class="box">
  <h1>Sem conexao</h1>
  <div class="spinner"></div>
  <p>Tentando reconectar...</p>
</div></body></html>"""


def show_message(parent, icon, title, text):
    """QMessageBox com title bar escura - igual ao resto da UI.
    singleShot(0) garante que rodamos enable_dark_title_bar() depois que
    o exec_() iniciou o event loop e o hwnd ja foi criado."""
    msg = QMessageBox(icon, title, text, QMessageBox.Ok, parent)
    QTimer.singleShot(0, lambda: enable_dark_title_bar(msg))
    msg.exec_()


def enable_dark_title_bar(widget):
    """Forca a barra de titulo do Windows a usar o tema escuro.
    Funciona em Windows 10 1809+ e Windows 11. Falha silenciosa em
    outros sistemas - o Qt nao consegue estilizar a title bar nativa
    via QSS, entao essa e' a unica forma de combinar com o tema do app.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        # Atributo 20 em Win10 2004+/Win11, 19 em Win10 1809-1909
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                return
    except Exception:
        pass


CONFIG_QSS = """
QWidget#configRoot {
    background-color: #14152b;
    color: #f5f5f7;
}

QLabel {
    color: #e6e6f0;
    background: transparent;
    font-size: 13px;
}

QLabel#title {
    color: #ffffff;
    font-size: 22px;
    font-weight: 600;
    padding-top: 4px;
}

QLabel#subtitle {
    color: #a0a0b8;
    font-size: 12px;
    padding-bottom: 6px;
}

QLabel#hint {
    color: #d6c9ff;
    background-color: #1f2042;
    border: 1px solid #3a2d6e;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 12px;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #1f2042;
    color: #f5f5f7;
    border: 1px solid #2d2e54;
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 13px;
    selection-background-color: #6d28d9;
    selection-color: #ffffff;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #8b5cf6;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: #6b6b85;
    background-color: #1a1b35;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox::down-arrow {
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #a0a0b8;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #1f2042;
    color: #f5f5f7;
    border: 1px solid #3a2d6e;
    selection-background-color: #6d28d9;
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: transparent;
    border: none;
    width: 16px;
}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: none; width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid #a0a0b8;
}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid #a0a0b8;
}

QCheckBox {
    color: #e6e6f0;
    spacing: 8px;
    background: transparent;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3a2d6e;
    background-color: #1f2042;
}

QCheckBox::indicator:hover {
    border: 1px solid #8b5cf6;
}

QCheckBox::indicator:checked {
    background-color: #8b5cf6;
    border: 1px solid #8b5cf6;
}

QPushButton {
    background-color: #2a2b54;
    color: #f5f5f7;
    border: 1px solid #3a3b6e;
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #3a3b6e;
    border: 1px solid #6d28d9;
}

QPushButton:pressed {
    background-color: #1f2042;
}

QPushButton#primary {
    background-color: #8b5cf6;
    color: #ffffff;
    border: 1px solid #8b5cf6;
}

QPushButton#primary:hover {
    background-color: #a78bfa;
    border: 1px solid #a78bfa;
}

QPushButton#primary:pressed {
    background-color: #6d28d9;
}

QPushButton#compact {
    padding: 7px 12px;
}

QLabel#footer {
    color: #6b6b85;
    font-size: 11px;
    padding-top: 10px;
}

QMessageBox {
    background-color: #14152b;
}

QMessageBox QLabel {
    color: #f5f5f7;
    background: transparent;
    font-size: 13px;
}

QMessageBox QPushButton {
    background-color: #2a2b54;
    color: #f5f5f7;
    border: 1px solid #3a3b6e;
    border-radius: 6px;
    padding: 6px 18px;
    min-width: 80px;
    font-size: 13px;
}

QMessageBox QPushButton:hover {
    background-color: #3a3b6e;
    border: 1px solid #6d28d9;
}

QMessageBox QPushButton:default {
    background-color: #8b5cf6;
    border: 1px solid #8b5cf6;
    color: #ffffff;
}

QMessageBox QPushButton:default:hover {
    background-color: #a78bfa;
    border: 1px solid #a78bfa;
}

/* ---------- Password dialog (keypad touch) ---------- */

QDialog#passwordDialog {
    background-color: #14152b;
    border: 1px solid #3a2d6e;
}

QLabel#dialogTitle {
    color: #ffffff;
    font-size: 20px;
    font-weight: 600;
    padding-bottom: 4px;
}

QLineEdit#passwordField {
    background-color: #1f2042;
    color: #ffffff;
    border: 1px solid #3a2d6e;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 20px;
}

QLineEdit#passwordField:focus {
    border: 1px solid #8b5cf6;
}

QPushButton#keypadKey {
    background-color: #2a2b54;
    color: #ffffff;
    border: 1px solid #3a3b6e;
    border-radius: 8px;
    font-size: 17px;
    font-weight: 500;
}

QPushButton#keypadKey:hover {
    background-color: #3a3b6e;
    border: 1px solid #8b5cf6;
}

QPushButton#keypadKey:pressed {
    background-color: #6d28d9;
}

QPushButton#keypadAction {
    background-color: #1f2042;
    color: #a78bfa;
    border: 1px solid #3a2d6e;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 500;
}

QPushButton#keypadAction:hover {
    background-color: #2a2b54;
    border: 1px solid #8b5cf6;
}

QPushButton#keypadAction:pressed {
    background-color: #6d28d9;
    color: #ffffff;
}
"""


# Quando empacotado com PyInstaller, __file__ aponta para um diretorio
# temporario. Para que o config.json sempre fique ao lado do .exe (e do .py
# em modo dev), usamos sys.executable quando "frozen".
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "qiosk.log")

# Logger compartilhado pelo modulo inteiro. setup_logging() configura
# o file handler; ate la, calls em log.* viram no-op silenciosos.
log = logging.getLogger("qiosk")


def setup_logging():
    """Liga o file handler rotativo. Tolera falhas (disco read-only,
    permissao negada) sem derrubar o app - logging e' nice-to-have."""
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        fh = RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass  # disco sem permissao - segue sem arquivo
    # Em modo dev (rodando .py), tambem ecoa no stderr.
    if not getattr(sys, "frozen", False):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


def _resource_path(filename):
    """Resolve arquivo de recurso (icone, imagem). Em modo dev fica ao
    lado do .py; no .exe vai pra sys._MEIPASS via --add-data do PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


def _icon_path():
    return _resource_path("qiosk.ico")


def _logomarca_path():
    return _resource_path("logomarca-qiosk-png.png")

DEFAULTS = {
    "start_url":           "https://example.com",
    # SHA-256 de "123456" (senha padrao). Trocavel pela UI.
    "exit_password_hash":  "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
    "clicks_needed":       5,
    "click_window":        3.0,
    "corner":              "top-left",
    "corner_size":         120,
    "keep_on_top":         True,
    "show_trigger_hint":   True,  # marca visual no canto secreto
    "idle_timeout":        120,   # segundos sem toque -> volta para start_url (0 = desativado)
    "cursor_hide_delay":   3,     # segundos sem mover -> esconde cursor (0 = sempre mostrar)
}

CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migracao: senha em texto -> hash SHA-256. Configs antigos
        # ainda tinham "exit_password" em texto puro.
        if "exit_password" in data and "exit_password_hash" not in data:
            data["exit_password_hash"] = hash_password(data["exit_password"])
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    log.info("Configuracao salva em %s", CONFIG_PATH)


# ============================================================
# KIOSK
# ============================================================

class KioskWindow(QMainWindow):
    """Janela principal: o navegador em tela cheia e travado."""

    def __init__(self, cfg, url):
        super().__init__()
        self.cfg = cfg
        self.start_url = url
        self.allow_close = False  # so vira True depois da senha correta
        self.unlock_callback = None  # setado por run_kiosk
        self._offline_mode = False
        self._loading_offline_page = False

        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.setCentralWidget(self.view)

        # Auto-recovery: se o processo do Chromium morrer, recarrega
        # a URL inicial apos um pequeno delay.
        self.view.page().renderProcessTerminated.connect(self._on_renderer_crashed)

        # Modo offline: se a pagina falhar, mostra HTML local e
        # tenta de novo a cada 10s ate voltar.
        self.view.loadFinished.connect(self._on_load_finished)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(10_000)
        self._retry_timer.timeout.connect(self._retry_load)

        self.view.load(QUrl(url))
        log.info("Kiosk iniciado | url=%s | idle=%ds | cursor_hide=%ds",
                 url, cfg["idle_timeout"], cfg["cursor_hide_delay"])

        flags = Qt.FramelessWindowHint
        if cfg["keep_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.showFullScreen()

        # Atalho de emergencia (failsafe). Escopo de aplicacao garante
        # que dispara mesmo se o foco estiver dentro do web view.
        shortcut = QShortcut(QKeySequence(EMERGENCY_SHORTCUT), self)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(self._on_emergency)

    def _on_emergency(self):
        if self.unlock_callback:
            self.unlock_callback()

    def _on_renderer_crashed(self, status, exit_code):
        # status: 0=normal, 1=abnormal, 2=crashed, 3=killed
        # Em qualquer caso anormal, tenta recarregar apos 2s para dar
        # tempo do processo do Chromium se reorganizar.
        if status == 0:
            return
        log.warning("WebView renderer terminou | status=%d | exit_code=%d "
                    "- recarregando em 2s", status, exit_code)
        QTimer.singleShot(2000, lambda: self.view.load(QUrl(self.start_url)))

    def _on_load_finished(self, ok):
        # setHtml(OFFLINE_HTML) tambem dispara loadFinished(True) - usamos
        # o flag para diferenciar e nao bagunçar o estado de retry.
        if self._loading_offline_page:
            self._loading_offline_page = False
            return
        if ok:
            if self._offline_mode:
                log.info("Conexao restabelecida - saindo do modo offline")
            self._offline_mode = False
            self._retry_timer.stop()
        else:
            self._enter_offline_mode()

    def _enter_offline_mode(self):
        if not self._offline_mode:
            log.warning("Falha ao carregar %s - entrando em modo offline",
                        self.start_url)
        self._offline_mode = True
        self._loading_offline_page = True
        self.view.setHtml(OFFLINE_HTML)
        if not self._retry_timer.isActive():
            self._retry_timer.start()

    def _retry_load(self):
        self.view.load(QUrl(self.start_url))

    def go_home(self):
        """Recarrega a URL inicial. Usado pelo idle timeout."""
        log.info("Idle timeout - voltando para %s", self.start_url)
        self.view.load(QUrl(self.start_url))

    def keyPressEvent(self, e):
        # Bloqueia atalhos de saida no nivel do app.
        # (Win, Alt+Tab e Ctrl+Alt+Del sao do sistema e nao da pra bloquear.)
        mod, key = e.modifiers(), e.key()
        if key in (Qt.Key_F11, Qt.Key_Escape):
            return
        if (mod & Qt.AltModifier) and key == Qt.Key_F4:
            return
        if (mod & Qt.ControlModifier) and key in (Qt.Key_W, Qt.Key_Q):
            return

    def closeEvent(self, e):
        if self.allow_close:
            e.accept()
        else:
            e.ignore()


class CornerTrigger(QWidget):
    """Janela invisivel no canto que conta os toques e dispara a senha."""

    def __init__(self, cfg, on_trigger):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # WA_TranslucentBackground deixa a janela 100% invisivel,
        # mas precisamos pintar uma marca quando show_trigger_hint=True.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.cfg = cfg
        self.on_trigger = on_trigger
        self.clicks = []

        geo = QApplication.primaryScreen().geometry()
        size = cfg["corner_size"]
        x, y = geo.x(), geo.y()
        if "right" in cfg["corner"]:
            x = geo.x() + geo.width() - size
        if "bottom" in cfg["corner"]:
            y = geo.y() + geo.height() - size
        self.setGeometry(x, y, size, size)

    def paintEvent(self, _):
        # Sempre pinta algo. Com WA_TranslucentBackground, areas com alpha=0
        # viram click-through no Windows - os toques passam direto pro
        # web view atras. Alpha=1 e' invisivel pra humanos mas mantem
        # a janela capturando cliques.
        painter = QPainter(self)
        if self.cfg.get("show_trigger_hint", True):
            painter.fillRect(self.rect(), QColor(255, 0, 0, 60))
        else:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def mousePressEvent(self, e):
        now = time.time()
        window = self.cfg["click_window"]
        self.clicks = [t for t in self.clicks if now - t <= window]
        self.clicks.append(now)
        if len(self.clicks) >= self.cfg["clicks_needed"]:
            self.clicks = []
            self.on_trigger()


class PasswordDialog(QDialog):
    """Dialogo com teclado numerico touch para destravar o kiosk."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("passwordDialog")
        self.setWindowTitle("Manutencao")
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setModal(True)

        title = QLabel("Senha de manutencao")
        title.setObjectName("dialogTitle")
        title.setAlignment(Qt.AlignCenter)

        self.field = QLineEdit()
        self.field.setObjectName("passwordField")
        self.field.setEchoMode(QLineEdit.Password)
        self.field.setAlignment(Qt.AlignCenter)
        self.field.returnPressed.connect(self.accept)

        # Teclado: 3x4 (1-9, depois C / 0 / backspace).
        # Botoes com tamanho fixo - QGridLayout estica os widgets para
        # preencher a celula, entao min-width do QSS nao basta.
        keypad = QGridLayout()
        keypad.setSpacing(14)
        keypad.setContentsMargins(0, 0, 0, 0)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("C", 3, 0), ("0", 3, 1), ("←", 3, 2),
        ]
        for label, r, c in keys:
            btn = QPushButton(label)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedSize(50, 50)
            if label.isdigit():
                btn.setObjectName("keypadKey")
                btn.clicked.connect(lambda _, ch=label: self.field.insert(ch))
            elif label == "C":
                btn.setObjectName("keypadAction")
                btn.clicked.connect(self.field.clear)
            else:  # backspace
                btn.setObjectName("keypadAction")
                btn.clicked.connect(self._backspace)
            keypad.addWidget(btn, r, c)

        # Centraliza o keypad horizontalmente dentro do dialog
        keypad_wrap = QHBoxLayout()
        keypad_wrap.addStretch(1)
        keypad_wrap.addLayout(keypad)
        keypad_wrap.addStretch(1)

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setFocusPolicy(Qt.NoFocus)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("Entrar")
        ok_btn.setObjectName("primary")
        ok_btn.setFocusPolicy(Qt.NoFocus)
        ok_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(title)
        root.addWidget(self.field)
        root.addLayout(keypad_wrap)
        root.addLayout(btn_row)

    def _backspace(self):
        self.field.setText(self.field.text()[:-1])

    def password(self):
        return self.field.text()


def ask_password(win, parent, expected_hash):
    dlg = PasswordDialog(parent)
    if dlg.exec_() == QDialog.Accepted:
        if hash_password(dlg.password()) == expected_hash:
            log.info("Senha correta - desbloqueando kiosk")
            win.allow_close = True
            QApplication.quit()
        else:
            log.warning("Tentativa de unlock com senha incorreta")
            show_message(parent, QMessageBox.Warning,
                         "Manutencao", "Senha incorreta.")


# ---- Watchers globais (event filters no QApplication) ----

class IdleWatcher(QObject):
    """Dispara um callback quando passa N segundos sem interacao do usuario.
    Reinicia o contador a cada toque, movimento ou tecla."""

    INPUT_EVENTS = {
        QEvent.MouseButtonPress, QEvent.MouseMove,
        QEvent.TouchBegin, QEvent.TouchUpdate,
        QEvent.KeyPress, QEvent.Wheel,
    }

    def __init__(self, app, timeout_seconds, on_idle):
        super().__init__()
        self._on_idle = on_idle
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(int(timeout_seconds * 1000))
        self.timer.timeout.connect(self._on_idle)
        app.installEventFilter(self)
        self.timer.start()

    def eventFilter(self, obj, event):
        if event.type() in self.INPUT_EVENTS:
            self.timer.start()  # reinicia o tempo
        return False  # nao consome o evento


class CursorHider(QObject):
    """Esconde o cursor depois de N segundos sem movimento do mouse.
    Volta a mostrar assim que o cursor mexer. Sem efeito em touch puro."""

    def __init__(self, app, delay_seconds):
        super().__init__()
        self._app = app
        self._hidden = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(int(delay_seconds * 1000))
        self.timer.timeout.connect(self._hide)
        app.installEventFilter(self)
        self.timer.start()

    def _hide(self):
        if not self._hidden:
            self._app.setOverrideCursor(QCursor(Qt.BlankCursor))
            self._hidden = True

    def _show(self):
        if self._hidden:
            self._app.restoreOverrideCursor()
            self._hidden = False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            self._show()
            self.timer.start()
        return False


def run_kiosk(app, cfg, url=None):
    if url is None:
        url = cfg["start_url"]

    win = KioskWindow(cfg, url)

    # unlock e' usado tanto pelos toques no canto quanto pelo atalho de
    # emergencia. trigger ainda nao existe aqui, mas o closure resolve
    # em tempo de execucao - so e' chamado depois que trigger e' criado.
    def unlock():
        ask_password(win, trigger, cfg["exit_password_hash"])

    win.unlock_callback = unlock
    trigger = CornerTrigger(cfg, unlock)
    trigger.show()
    trigger.raise_()

    if cfg["keep_on_top"]:
        keeper = QTimer()
        keeper.timeout.connect(lambda: (win.raise_(), trigger.raise_()))
        keeper.start(2000)
        win._keeper = keeper

    # Idle timeout: volta para start_url se o totem ficar abandonado.
    if cfg["idle_timeout"] > 0:
        app._idle_watcher = IdleWatcher(
            app, cfg["idle_timeout"], lambda: win.go_home()
        )

    # Esconder cursor: deixa a tela mais limpa em touch.
    if cfg["cursor_hide_delay"] > 0:
        app._cursor_hider = CursorHider(app, cfg["cursor_hide_delay"])

    # Mantem referencias vivas para o GC nao matar as janelas.
    app._kiosk_win = win
    app._kiosk_trigger = trigger


# ============================================================
# CONFIG UI
# ============================================================

class ConfigWindow(QWidget):
    def __init__(self, on_start_kiosk):
        super().__init__()
        self.on_start_kiosk = on_start_kiosk
        self.setWindowTitle("Qiosk")
        self.setObjectName("configRoot")
        self.resize(580, 660)
        self.setMinimumWidth(520)

        cfg = load_config()

        # Logomarca (imagem) no lugar do texto "Qiosk"
        title = QLabel()
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        logo_pix = QPixmap(_logomarca_path())
        if not logo_pix.isNull():
            title.setPixmap(logo_pix.scaledToWidth(220, Qt.SmoothTransformation))
        else:
            title.setText("Qiosk")  # fallback se a imagem nao carregar

        subtitle = QLabel("Configuracao")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)

        # Guarda o config carregado para preservar o hash da senha
        # quando o usuario nao digita nada novo.
        self._loaded_cfg = cfg

        self.url_in = QLineEdit(cfg["start_url"])
        self.url_in.setPlaceholderText("https://... ou file:///C:/...")

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_row.addWidget(self.url_in)
        pick_btn = QPushButton("Arquivo...")
        pick_btn.setObjectName("compact")
        pick_btn.clicked.connect(self.pick_file)
        url_row.addWidget(pick_btn)

        # Senha agora e' guardada em hash. Nao da' para preencher o campo
        # com a senha atual - mostramos vazio com placeholder.
        self.pwd_in = QLineEdit()
        self.pwd_in.setEchoMode(QLineEdit.Password)
        self.pwd_in.setPlaceholderText("Deixe vazio para manter a senha atual")

        self.show_pwd = QCheckBox("Mostrar senha")
        self.show_pwd.toggled.connect(
            lambda on: self.pwd_in.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )

        self.clicks_in = QSpinBox()
        self.clicks_in.setRange(2, 20)
        self.clicks_in.setValue(int(cfg["clicks_needed"]))

        self.window_in = QDoubleSpinBox()
        self.window_in.setRange(0.5, 15.0)
        self.window_in.setSingleStep(0.5)
        self.window_in.setSuffix(" s")
        self.window_in.setValue(float(cfg["click_window"]))

        self.corner_in = QComboBox()
        self.corner_in.addItems(CORNERS)
        self.corner_in.setCurrentText(cfg["corner"])

        self.size_in = QSpinBox()
        self.size_in.setRange(20, 400)
        self.size_in.setSuffix(" px")
        self.size_in.setValue(int(cfg["corner_size"]))

        self.top_in = QCheckBox("Sempre na frente (recomendado)")
        self.top_in.setChecked(bool(cfg["keep_on_top"]))

        self.hint_in = QCheckBox(
            "Mostrar marca no canto secreto (desativar so em producao)"
        )
        self.hint_in.setChecked(bool(cfg["show_trigger_hint"]))

        self.idle_in = QSpinBox()
        self.idle_in.setRange(0, 3600)
        self.idle_in.setSingleStep(30)
        self.idle_in.setSuffix(" s")
        self.idle_in.setSpecialValueText("desativado")  # quando = 0
        self.idle_in.setValue(int(cfg["idle_timeout"]))

        self.cursor_in = QSpinBox()
        self.cursor_in.setRange(0, 60)
        self.cursor_in.setSuffix(" s")
        self.cursor_in.setSpecialValueText("sempre visivel")  # quando = 0
        self.cursor_in.setValue(int(cfg["cursor_hide_delay"]))

        form = QFormLayout()
        form.setSpacing(12)
        form.setContentsMargins(0, 8, 0, 8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("URL inicial",            url_row)
        form.addRow("Senha para destravar",   self.pwd_in)
        form.addRow("",                       self.show_pwd)
        form.addRow("Toques no canto",        self.clicks_in)
        form.addRow("Janela de tempo",        self.window_in)
        form.addRow("Canto secreto",          self.corner_in)
        form.addRow("Tamanho da area",        self.size_in)
        form.addRow("Voltar para home apos",  self.idle_in)
        form.addRow("Esconder cursor apos",   self.cursor_in)
        form.addRow("",                       self.top_in)
        form.addRow("",                       self.hint_in)

        hint = QLabel(
            "Para sair do kiosk: toque varias vezes (rapido) no canto secreto "
            f"OU pressione <b>{EMERGENCY_SHORTCUT}</b> para abrir a senha."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        save_btn = QPushButton("Salvar")
        save_btn.setMinimumHeight(42)
        save_btn.clicked.connect(self.on_save)

        run_btn = QPushButton("Salvar e iniciar Qiosk")
        run_btn.setObjectName("primary")
        run_btn.setMinimumHeight(42)
        run_btn.clicked.connect(self.on_save_and_run)

        # Stretch=1 nos dois botoes -> divide 50/50 a largura disponivel.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addWidget(save_btn, 1)
        btn_row.addWidget(run_btn, 1)

        footer = QLabel(
            "Desenvolvido por Lucas F  ·  Todos os direitos reservados  ·  v1"
        )
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignCenter)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(6)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(form)
        root.addWidget(hint)
        root.addStretch(1)
        root.addLayout(btn_row)
        root.addWidget(footer)

    def showEvent(self, e):
        super().showEvent(e)
        enable_dark_title_bar(self)

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Escolha o HTML", "", "HTML (*.html *.htm);;Todos (*)"
        )
        if path:
            self.url_in.setText("file:///" + path.replace("\\", "/"))

    def collect(self):
        # Senha: se o campo esta vazio, mantem o hash que ja existia.
        # Se foi digitada uma nova, gera hash dela.
        new_pw = self.pwd_in.text()
        if new_pw:
            pwd_hash = hash_password(new_pw)
        else:
            pwd_hash = self._loaded_cfg["exit_password_hash"]

        return {
            "start_url":          self.url_in.text().strip() or DEFAULTS["start_url"],
            "exit_password_hash": pwd_hash,
            "clicks_needed":      self.clicks_in.value(),
            "click_window":       self.window_in.value(),
            "corner":             self.corner_in.currentText(),
            "corner_size":        self.size_in.value(),
            "keep_on_top":        self.top_in.isChecked(),
            "show_trigger_hint":  self.hint_in.isChecked(),
            "idle_timeout":       self.idle_in.value(),
            "cursor_hide_delay":  self.cursor_in.value(),
        }

    def on_save(self):
        try:
            save_config(self.collect())
            show_message(self, QMessageBox.Information,
                         "Salvo", "Alteracoes salvas com sucesso.")
        except OSError as e:
            show_message(self, QMessageBox.Critical,
                         "Erro", f"Nao foi possivel salvar:\n{e}")

    def on_save_and_run(self):
        try:
            cfg = self.collect()
            save_config(cfg)
        except OSError as e:
            show_message(self, QMessageBox.Critical,
                         "Erro", f"Nao foi possivel salvar:\n{e}")
            return
        self.close()
        self.on_start_kiosk(cfg)


# ============================================================
# ENTRYPOINT
# ============================================================

def main():
    setup_logging()
    args = sys.argv[1:]
    config_mode = "--config" in args
    url_arg = next((a for a in args if not a.startswith("-")), None)
    log.info("Qiosk iniciado | modo=%s | args=%r",
             "config" if config_mode else "kiosk", args)

    # Bloqueia uma segunda instancia (clique duplo no atalho, etc).
    # Saimos em silencio - kiosk nao deve ficar piscando popups.
    if not acquire_single_instance():
        log.info("Segunda instancia bloqueada pelo mutex - encerrando")
        sys.exit(0)

    # Necessario para o QtWebEngine - antes de criar o QApplication.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    # Icone padrao para todas as janelas (title bar, taskbar do config).
    app.setWindowIcon(QIcon(_icon_path()))
    # Tema aplicado no app inteiro - vale para o config UI, o dialogo de
    # senha do kiosk e os QMessageBox (kiosk so renderiza o web view, nao
    # tem widgets Qt visiveis para o tema afetar).
    app.setStyleSheet(CONFIG_QSS)

    if config_mode:
        def start_after_config(cfg):
            run_kiosk(app, cfg)

        win = ConfigWindow(start_after_config)
        win.show()
        app._config_win = win
    else:
        cfg = load_config()
        run_kiosk(app, cfg, url=url_arg)

    exit_code = app.exec_()
    log.info("Qiosk encerrado | exit_code=%d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
