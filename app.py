"""HOSHIYOMI LOG // 星読みログ

個人ホロスコープ × 一般の星座占い × 日記 → ブラインド評価で「本当に当たるのか」を自分で検証する。
- 天体計算: astro_core (Skyfield, ローカル)
- 保存: store (SQLite, ~/.hoshiyomi)
- 星読み文: ローカル llama-server (OpenAI 互換 API)。繋がらなければ簡易ルールで代替。

起動:  python app.py
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QDate, QPointF, QRectF, Qt, QThread, QTime, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox, QFormLayout,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSplitter, QTabWidget, QTimeEdit,
    QVBoxLayout, QWidget,
)

import astro_core as A
from store import Store

# ───────────────────────── テーマ（Pixel × Neon） ─────────────────────────
BG = "#07070f"
PANEL = "#0d0d1c"
CYAN = "#00f0ff"
MAGENTA = "#ff2bd6"
YELLOW = "#f8ff4a"
GREEN = "#39ff8f"
DIM = "#3a4a66"
TEXT = "#d8f7ff"

FONT_STACK = ["DotGothic16", "PixelMplus12", "x12y16pxMaruMonica", "Noto Sans CJK JP", "Noto Sans JP", "sans-serif"]
GLYPH_FONT = "DejaVu Sans"

QSS = f"""
* {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, QWidget {{ background: {BG}; }}
QTabWidget::pane {{ border: 1px solid {DIM}; top: -1px; }}
QTabBar::tab {{ background: {PANEL}; border: 1px solid {DIM}; padding: 6px 14px; margin-right: 2px; color: {DIM}; }}
QTabBar::tab:selected {{ color: {CYAN}; border-color: {CYAN}; border-bottom: 2px solid {MAGENTA}; }}
QPlainTextEdit, QLineEdit, QListWidget, QDateEdit, QTimeEdit, QDoubleSpinBox, QComboBox {{
    background: {PANEL}; border: 1px solid {DIM}; selection-background-color: {MAGENTA}; padding: 4px; }}
QPlainTextEdit:focus, QLineEdit:focus {{ border-color: {CYAN}; }}
QPushButton {{ background: {PANEL}; border: 1px solid {CYAN}; color: {CYAN}; padding: 6px 12px; }}
QPushButton:hover {{ background: #00303a; }}
QPushButton:pressed {{ background: {CYAN}; color: {BG}; }}
QPushButton:disabled {{ border-color: {DIM}; color: {DIM}; }}
QPushButton#hot {{ border-color: {MAGENTA}; color: {MAGENTA}; }}
QPushButton#hot:hover {{ background: #3a0030; }}
QPushButton#option {{ text-align: left; padding: 10px; color: {TEXT}; border-color: {DIM}; }}
QPushButton#option:hover {{ border-color: {MAGENTA}; }}
QLabel#h {{ color: {CYAN}; font-size: 15px; letter-spacing: 2px; }}
QLabel#sub {{ color: {DIM}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL}; border: 1px solid {DIM}; }}
QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; background: {MAGENTA}; }}
QSplitter::handle {{ background: {DIM}; }}
QLabel {{ background: transparent; }}
"""


_UI_FONT = None


def ui_font() -> str:
    """インストール済みのフォントからピクセル系を優先して選ぶ"""
    global _UI_FONT
    if _UI_FONT is None:
        from PyQt6.QtGui import QFontDatabase
        available = set(QFontDatabase.families())
        _UI_FONT = next((f for f in FONT_STACK if f in available), "sans-serif")
    return _UI_FONT


def glow(widget, color=CYAN, radius=16):
    eff = QGraphicsDropShadowEffect(widget)
    eff.setColor(QColor(color))
    eff.setBlurRadius(radius)
    eff.setOffset(0, 0)
    widget.setGraphicsEffect(eff)
    return widget


def heading(text, color=CYAN):
    lb = QLabel(text)
    lb.setObjectName("h")
    lb.setStyleSheet(f"color:{color};")
    return glow(lb, color, 14)


# ───────────────────────── LLM ─────────────────────────
SYSTEM_PROMPT = """あなたは西洋占星術の読み手です。与えられた出生図とその日のトランジットの事実データだけを根拠に、その日の星読みを日本語で書いてください。
ルール:
- 200〜280字。箇条書きは使わず、2〜3段落の文章で。
- 日付・曜日・季節・天気・年中行事には一切触れない（後で日付を伏せて検証するため）。
- 最もオーブの小さいアスペクトを中心に、その日起きそうな出来事や気分を具体的に1〜2個述べる。
- 最後に、その日の行動のヒントを1つだけ添える。
- 「〜かもしれません」の多用を避け、言い切りを混ぜる。"""


MUNDANE_PROMPT = """あなたはマンデン占星術（個人ではなく社会・世の中全体を読む占星術）の読み手です。与えられた事実データだけを根拠に、{label}の社会全体の流れを日本語で占ってください。
ルール:
- {length}字程度。見出しや箇条書きは使わず、段落の文章で。
- 実在の人物・国・企業・団体の名前や、具体的なニュース・事件には触れない（あなたの知識は古く、作り話になるため）。経済・お金の流れ、通信や交通、技術、政治や制度の空気、人々の気分や価値観といったテーマで語る。
- 逆行・留・シャドウ・サイン移動・太陽との会合（内合・外合・合・衝）・天体どうしのアスペクトを根拠として挙げ、「◯月◯日前後」のように時期の目安をつけて、何が起きやすいかを具体的に述べる。
- 最後に、この期間を過ごすためのヒントを1つだけ添える。
- 「〜かもしれません」の多用を避け、言い切りを混ぜる。"""
MUNDANE_LENGTH = {"week": ("300〜400", 1500), "month": ("400〜600", 2000), "year": ("700〜1000", 3000)}


class LLMWorker(QThread):
    done = pyqtSignal(str, bool)  # text, ok

    def __init__(self, base_url: str, model: str, facts, llama: bool = False,
                 system: str = SYSTEM_PROMPT, max_tokens: int = 1200):
        super().__init__()
        self.base_url, self.model, self.facts = base_url.rstrip("/"), model, facts
        self.llama, self.system, self.max_tokens = llama, system, max_tokens

    def run(self):
        if callable(self.facts):  # 重い事実計算もこのスレッドで済ませる
            try:
                self.facts = self.facts()
            except Exception as e:  # noqa: BLE001
                self.facts = ""
                self.done.emit(f"天体計算で失敗: {type(e).__name__}: {e}", False)
                return
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.facts},
            ],
            "temperature": 0.8,
            "max_tokens": self.max_tokens,
        }
        if self.llama:
            # Qwen3 系は思考を切らないと content が空のまま reasoning_content に全部入る
            body["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            text = data["choices"][0]["message"].get("content") or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            if not text:
                raise ValueError("返事が空でした（思考モードのまま max_tokens を使い切った可能性）")
            self.done.emit(text, True)
        except Exception as e:  # noqa: BLE001
            self.done.emit(f"{type(e).__name__}: {e}", False)


# ロード中のローカルLLMを探す。動いている llama-server のコマンドラインを読む
# （/proc を見るだけ。nvidia-smi は叩かないので数秒おきに回しても軽い）
def find_llama_servers() -> list[dict]:
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            raw = (d / "cmdline").read_bytes()
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if not args or Path(args[0]).name != "llama-server":
            continue
        srv = {"pid": int(d.name), "port": 8080, "model_path": None, "ctx": None, "alias": None}
        for i, a in enumerate(args[:-1]):
            nxt = args[i + 1]
            if a in ("-m", "--model"):
                srv["model_path"] = nxt
            elif a == "--port" and nxt.isdigit():
                srv["port"] = int(nxt)
            elif a in ("-c", "--ctx-size") and nxt.isdigit():
                srv["ctx"] = int(nxt)
            elif a in ("-a", "--alias"):
                srv["alias"] = nxt
        mp = srv["model_path"]
        srv["name"] = srv["alias"] or (Path(mp).name.removesuffix(".gguf") if mp else "llama-server")
        found.append(srv)
    # 既定ポート 8000 を優先、あとはポート順
    found.sort(key=lambda s: (s["port"] != 8000, s["port"]))
    return found


def probe_server(srv: dict) -> dict:
    """/v1/models で生死と model id を確かめる。読み込み中の llama-server は 503 を返す"""
    url = f"http://127.0.0.1:{srv['port']}/v1"
    srv = dict(srv, url=url, state="down", model_id=srv["name"])
    try:
        with urllib.request.urlopen(f"{url}/models", timeout=2) as r:
            data = json.loads(r.read())
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        srv["model_id"] = ids[0] if ids else srv["name"]
        srv["state"] = "ready"
    except urllib.error.HTTPError as e:
        srv["state"] = "loading" if e.code == 503 else "down"
    except Exception:  # noqa: BLE001
        pass
    return srv


class LLMProbe(QThread):
    found = pyqtSignal(list)

    def run(self):
        self.found.emit([probe_server(s) for s in find_llama_servers()])


def ctx_label(ctx: int | None) -> str:
    return f"{ctx // 1024}K" if ctx else ""


# LLM が無いとき用の簡易文（検証の土台を止めないための保険）
KEYWORDS = {
    "太陽": "自分の意志", "月": "気分", "水星": "連絡や考えごと", "金星": "人間関係や楽しみ",
    "火星": "行動力や苛立ち", "木星": "広がりと幸運", "土星": "責任や制限", "天王星": "突然の変化",
    "海王星": "ぼんやりした感覚や理想", "冥王星": "深い変化",
}
TONE = {"合": "強く重なり", "セクスタイル": "軽く後押しし", "スクエア": "ぶつかって摩擦を生み",
        "トライン": "滑らかに噛み合い", "オポジション": "引っぱり合い"}


def rule_based_reading(aspects: list[A.Aspect]) -> str:
    if not aspects:
        return "目立つ配置は少なく、淡々と進む日。普段のペースを守るほど調子が出ます。"
    parts = []
    for a in aspects[:2]:
        t, n = KEYWORDS[a.transit.name], KEYWORDS[a.natal.name]
        retro = "（逆行中なので見直しの形で）" if a.transit.retro else ""
        parts.append(f"今日の{t}が、あなたの{n}と{TONE[a.name]}ます{retro}。")
    return "".join(parts) + "\n\n［簡易ルール文。LLM 接続後に再生成できます］"


# ───────────────────────── ホロスコープ円 ─────────────────────────
class Wheel(QWidget):
    """出生図(内側・シアン) と トランジット(外側・マゼンタ) を重ねた星図。動きのある描画。"""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.natal: A.Chart | None = None
        self.transit: A.Chart | None = None
        self.aspects: list[A.Aspect] = []
        self.phase = 0.0
        rnd = random.Random(7)
        self.stars = [(rnd.random(), rnd.random(), rnd.random() * math.tau, rnd.choice([1, 1, 2])) for _ in range(140)]
        self.timer = QTimer(self, timeout=self._tick)
        self.timer.start(50)

    def set_data(self, natal, transit, aspects):
        self.natal, self.transit, self.aspects = natal, transit, aspects
        self.update()

    def _tick(self):
        self.phase = (self.phase + 0.05) % (math.tau * 100)
        self.update()

    # 黄経 → 画面角度。ASC（なければ牡羊座0°）を左に置き反時計回り
    def _ref(self):
        if self.natal and self.natal.asc is not None:
            return self.natal.asc
        return 0.0

    def _pt(self, cx, cy, r, lon):
        th = math.radians(180 + (lon - self._ref()))
        return QPointF(cx + r * math.cos(th), cy - r * math.sin(th))

    @staticmethod
    def _spread(lons, min_gap=7.0):
        """近接した天体ラベルを少しずらす"""
        order = sorted(range(len(lons)), key=lambda i: lons[i])
        disp = {i: lons[i] for i in order}
        for _ in range(3):
            for a, b in zip(order, order[1:]):
                if (disp[b] - disp[a]) % 360 < min_gap:
                    disp[b] = disp[a] + min_gap
        return [disp[i] for i in range(len(lons))]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(BG))

        # 瞬く星（ピクセル）
        for x, y, ph, s in self.stars:
            a = int(60 + 120 * (0.5 + 0.5 * math.sin(self.phase * 0.6 + ph)))
            c = QColor(CYAN if s == 2 else TEXT)
            c.setAlpha(a)
            p.fillRect(QRectF(x * w, y * h, s, s), c)

        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 14
        r_sign_out, r_sign_in = R, R * 0.86
        r_tr = R * 0.76
        r_na = R * 0.56
        r_core = R * 0.44

        # 中心の淡い光
        g = QRadialGradient(QPointF(cx, cy), r_core)
        g.setColorAt(0, QColor(255, 43, 214, 40))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r_core, r_core)

        # レーダー掃引（動き）
        sweep = (self.phase * 12) % 360
        for k in range(18):
            c = QColor(CYAN)
            c.setAlpha(max(0, 50 - k * 3))
            p.setPen(QPen(c, 2))
            th = math.radians(sweep - k * 1.5)
            p.drawLine(QPointF(cx, cy), QPointF(cx + r_sign_in * math.cos(th), cy - r_sign_in * math.sin(th)))

        # 星座リング
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawEllipse(QPointF(cx, cy), r_sign_out, r_sign_out)
        p.setPen(QPen(QColor(DIM), 1))
        p.drawEllipse(QPointF(cx, cy), r_sign_in, r_sign_in)
        p.drawEllipse(QPointF(cx, cy), r_na + 18, r_na + 18)
        p.drawEllipse(QPointF(cx, cy), r_core, r_core)
        gf = QFont(GLYPH_FONT, max(10, int(R / 18)))
        p.setFont(gf)
        for i, (_name, gly) in enumerate(A.SIGNS):
            p.setPen(QPen(QColor(DIM), 1))
            p.drawLine(self._pt(cx, cy, r_sign_in, i * 30), self._pt(cx, cy, r_sign_out, i * 30))
            # 現在太陽がいる星座を点灯
            lit = self.transit and self.transit.bodies[0].sign_index == i
            p.setPen(QColor(YELLOW if lit else CYAN))
            pt = self._pt(cx, cy, (r_sign_in + r_sign_out) / 2, i * 30 + 15)
            p.drawText(QRectF(pt.x() - 14, pt.y() - 12, 28, 24), Qt.AlignmentFlag.AlignCenter, gly + "\uFE0E")

        # 5度刻みの目盛り
        p.setPen(QPen(QColor(DIM), 1))
        for d in range(0, 360, 5):
            p.drawLine(self._pt(cx, cy, r_sign_in, d), self._pt(cx, cy, r_sign_in - (6 if d % 30 else 0) - 3, d))

        # ASC / MC 軸
        if self.natal and self.natal.asc is not None:
            for lon, lab in ((self.natal.asc, "ASC"), (self.natal.mc, "MC")):
                c = QColor(YELLOW)
                c.setAlpha(150)
                p.setPen(QPen(c, 1, Qt.PenStyle.DashLine))
                p.drawLine(self._pt(cx, cy, r_core, lon), self._pt(cx, cy, r_sign_in, lon))
                p.setFont(QFont(ui_font(), 8))
                pt = self._pt(cx, cy, r_core - 14, lon)
                p.drawText(QRectF(pt.x() - 18, pt.y() - 8, 36, 16), Qt.AlignmentFlag.AlignCenter, lab)

        # アスペクト線（脈動）
        pulse = 0.5 + 0.5 * math.sin(self.phase * 2)
        if self.natal and self.transit:
            for a in self.aspects[:10]:
                hard = a.name in ("スクエア", "オポジション")
                c = QColor(MAGENTA if hard else GREEN if a.name != "合" else YELLOW)
                strength = max(0.15, 1 - a.orb / 8)
                c.setAlpha(int(40 + 160 * strength * (0.6 + 0.4 * pulse)))
                p.setPen(QPen(c, 1 + 2 * strength))
                p.drawLine(self._pt(cx, cy, r_core, a.transit.lon), self._pt(cx, cy, r_core, a.natal.lon))

        # 天体
        def draw_ring(chart: A.Chart, r, color, outer):
            lons = [b.lon for b in chart.bodies]
            disp = self._spread(lons)
            p.setFont(QFont(GLYPH_FONT, max(11, int(R / 16))))
            for b, dl in zip(chart.bodies, disp):
                tick_r = r_sign_in if outer else r_na + 18
                p.setPen(QPen(QColor(color), 2))
                p.drawLine(self._pt(cx, cy, tick_r, b.lon), self._pt(cx, cy, tick_r - 6, b.lon))
                pt = self._pt(cx, cy, r, dl)
                col = QColor(color)
                if b.retro:  # 逆行は黄色で点滅
                    col = QColor(YELLOW)
                    col.setAlpha(int(140 + 115 * pulse))
                p.setPen(col)
                p.drawText(QRectF(pt.x() - 13, pt.y() - 13, 26, 26), Qt.AlignmentFlag.AlignCenter, b.glyph)
                if b.retro:
                    p.setFont(QFont(ui_font(), 7))
                    p.drawText(QRectF(pt.x() + 7, pt.y() + 2, 14, 12), Qt.AlignmentFlag.AlignLeft, "R")
                    p.setFont(QFont(GLYPH_FONT, max(11, int(R / 16))))
                # 内側の点
                dot = self._pt(cx, cy, r_core, b.lon)
                c2 = QColor(color)
                c2.setAlpha(200)
                p.fillRect(QRectF(dot.x() - 2, dot.y() - 2, 4, 4), c2)

        if self.natal:
            draw_ring(self.natal, r_na, CYAN, False)
        if self.transit:
            draw_ring(self.transit, r_tr, MAGENTA, True)

        # 凡例
        p.setFont(QFont(ui_font(), 9))
        p.setPen(QColor(CYAN))
        p.drawText(8, h - 26, "内: 出生図")
        p.setPen(QColor(MAGENTA))
        p.drawText(8, h - 10, "外: 今日の星")
        p.setPen(QColor(YELLOW))
        p.drawText(w - 70, h - 10, "R: 逆行")
        p.end()


# ───────────────────────── タブ: 星図 ─────────────────────────
class ChartTab(QWidget):
    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        lay = QHBoxLayout(self)
        split = QSplitter()
        self.wheel = Wheel()
        split.addWidget(self.wheel)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(heading("TRANSITS // 今日の星"))
        self.tr_list = QListWidget()
        sl.addWidget(self.tr_list, 3)
        sl.addWidget(heading("ASPECTS // あなたの星との角度", MAGENTA))
        self.as_list = QListWidget()
        sl.addWidget(self.as_list, 3)
        split.addWidget(side)
        split.setSizes([620, 380])
        lay.addWidget(split)

    def refresh(self):
        a = self.app
        self.wheel.set_data(a.natal, a.transit, a.aspects)
        self.tr_list.clear()
        for b in a.transit.bodies:
            h = A.natal_house_of(b.lon, a.natal) if a.natal else None
            hs = f"  [{h}H]" if h else ""
            self.tr_list.addItem(b.label() + hs)
            if b.retro:
                self.tr_list.item(self.tr_list.count() - 1).setForeground(QColor(YELLOW))
        self.as_list.clear()
        if not a.natal:
            self.as_list.addItem("設定タブで出生データを入力すると表示されます")
            return
        for asp in a.aspects[:14]:
            self.as_list.addItem(asp.label())
            hard = asp.name in ("スクエア", "オポジション")
            self.as_list.item(self.as_list.count() - 1).setForeground(QColor(MAGENTA if hard else GREEN))


# ───────────────────────── タブ: 星読み ─────────────────────────
class ReadingTab(QWidget):
    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        self.worker = None
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(heading("PERSONAL // ホロスコープからの星読み"))
        top.addStretch()
        self.gen_btn = QPushButton("▶ 星読みを生成")
        self.gen_btn.setObjectName("hot")
        self.gen_btn.clicked.connect(self.generate)
        top.addWidget(self.gen_btn)
        lay.addLayout(top)
        self.status = QLabel("")
        self.status.setObjectName("sub")
        lay.addWidget(self.status)
        self.personal = QPlainTextEdit()
        self.personal.setPlaceholderText("「星読みを生成」でローカルLLMが書きます（手で編集も可）")
        lay.addWidget(self.personal, 3)

        lay.addWidget(heading("GENERAL // 一般の星座占い（貼り付け）", MAGENTA))
        hint = QLabel("雑誌やサイトの「今日の◯◯座」をそのまま貼る。比較対象としてブラインド評価に使います。")
        hint.setObjectName("sub")
        lay.addWidget(hint)
        self.general = QPlainTextEdit()
        lay.addWidget(self.general, 2)

        bot = QHBoxLayout()
        self.facts_btn = QPushButton("天体データを見る")
        self.facts_btn.clicked.connect(self.show_facts)
        bot.addWidget(self.facts_btn)
        bot.addStretch()
        save = QPushButton("■ 保存")
        save.clicked.connect(self.save)
        bot.addWidget(save)
        lay.addLayout(bot)

    def refresh(self):
        row = self.app.store.reading(self.app.date_str)
        self.personal.setPlainText(row["personal"] if row else "")
        self.general.setPlainText(row["general"] if row else "")
        self.status.setText("")

    def show_facts(self):
        QMessageBox.information(self, "天体データ（LLMに渡す内容）", self.app.facts() or "出生データ未入力")

    def generate(self):
        if not self.app.natal:
            QMessageBox.warning(self, "出生データなし", "先に「設定」タブで出生データを保存してください。")
            return
        target, why = self.app.llm_target()
        if not target:
            self.personal.setPlainText(rule_based_reading(self.app.aspects))
            self.status.setText(f"◆ {why}。簡易文で代替しました")
            return
        self.gen_btn.setEnabled(False)
        self._model_name = target["name"]
        self.status.setText(f"◇ {target['name']} に問い合わせ中…")
        self.worker = LLMWorker(target["url"], target["model_id"], self.app.facts(), target["llama"])
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, text, ok):
        self.gen_btn.setEnabled(True)
        if ok:
            self.personal.setPlainText(text)
            self.status.setText(f"◆ 生成完了 by {self._model_name}（保存を忘れずに）")
        else:
            self.personal.setPlainText(rule_based_reading(self.app.aspects))
            self.status.setText(f"◆ LLMに接続できず簡易文で代替: {text[:120]}")

    def save(self):
        self.app.store.save_reading(
            self.app.date_str, self.app.facts(), self.personal.toPlainText().strip(), self.general.toPlainText().strip()
        )
        self.status.setText("■ 保存しました")


# ───────────────────────── タブ: 日記 ─────────────────────────
class JournalTab(QWidget):
    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(heading("LOG // 今日あったこと"))
        hint = QLabel("占いを見ずに書くのがコツ。出来事・気分・人とのやりとりを事実ベースで。")
        hint.setObjectName("sub")
        left.addWidget(hint)
        self.text = QPlainTextEdit()
        left.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("手応え"))
        self.score = QSlider(Qt.Orientation.Horizontal)
        self.score.setRange(1, 5)
        self.score.setValue(3)
        self.score_lb = QLabel("3")
        self.score.valueChanged.connect(lambda v: self.score_lb.setText(str(v)))
        row.addWidget(self.score, 1)
        row.addWidget(self.score_lb)
        save = QPushButton("■ 保存")
        save.clicked.connect(self.save)
        row.addWidget(save)
        left.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("sub")
        left.addWidget(self.status)
        lay.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(heading("HISTORY", MAGENTA))
        self.hist = QListWidget()
        self.hist.itemClicked.connect(self.jump)
        right.addWidget(self.hist)
        lay.addLayout(right, 1)

    def refresh(self):
        row = self.app.store.journal(self.app.date_str)
        self.text.setPlainText(row["text"] if row else "")
        self.score.setValue(row["score"] if row and row["score"] else 3)
        self.status.setText("")
        self.hist.clear()
        for r in self.app.store.recent_journal():
            self.hist.addItem(f"{r['date']}  {'■' * (r['score'] or 0)}")

    def jump(self, item):
        d = item.text().split()[0]
        self.app.date_edit.setDate(QDate.fromString(d, "yyyy-MM-dd"))

    def save(self):
        self.app.store.save_journal(self.app.date_str, self.text.toPlainText().strip(), self.score.value())
        self.status.setText("■ 保存しました")
        self.refresh()


# ───────────────────────── タブ: 社会（逆行カレンダー・サイン移動） ─────────────────────────
def local(dt):
    """UTC(aware) → この端末のローカル時刻"""
    return dt.astimezone()


class MundaneWorker(QThread):
    done = pyqtSignal(int, object, object, object)  # year, spans, ingresses, 太陽との会合

    def __init__(self, year: int):
        super().__init__()
        self.year = year

    def run(self):
        a, b = datetime(self.year, 1, 1), datetime(self.year + 1, 1, 1)
        spans, ing = A.mundane_calendar(a, b)
        self.done.emit(self.year, spans, ing, A.sun_phases(a, b))


class Timeline(QWidget):
    """1年ぶんの帯グラフ。行=水星〜冥王星、黄=逆行、薄い帯=シャドウ、記号=サイン移動、丸=太陽との会合"""
    picked = pyqtSignal(object)  # クリックした日の date

    LEFT, TOP, BOTTOM = 78, 24, 8

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.year = datetime.now().year
        self.spans: list[A.RetroSpan] = []
        self.ingresses: list[A.Ingress] = []
        self.phases: list[A.SunPhase] = []
        self.marker = None  # 上のバーで選んでいる日

    def set_data(self, year, spans, ingresses, phases):
        self.year, self.spans, self.ingresses, self.phases = year, spans, ingresses, phases
        self.update()

    def _range(self):
        a = datetime(self.year, 1, 1).astimezone()
        b = datetime(self.year + 1, 1, 1).astimezone()
        return a, b

    def _x(self, dt) -> float:
        a, b = self._range()
        w = self.width() - self.LEFT - 10
        f = (local(dt) - a) / (b - a)
        return self.LEFT + max(0.0, min(1.0, f)) * w

    def _row_h(self):
        return (self.height() - self.TOP - self.BOTTOM) / len(A.MUNDANE)

    def _row_y(self, name):
        i = next(k for k, p in enumerate(A.MUNDANE) if p[0] == name)
        return self.TOP + i * self._row_h()

    def _dt_at(self, x):
        a, b = self._range()
        f = (x - self.LEFT) / max(1, self.width() - self.LEFT - 10)
        return a + (b - a) * max(0.0, min(0.9999, f))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(BG))
        rh = self._row_h()
        a, _b = self._range()
        # 月の目盛り
        p.setFont(QFont(ui_font(), 9))
        for m in range(1, 13):
            x = self._x(datetime(self.year, m, 1).astimezone())
            p.setPen(QPen(QColor(DIM), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, self.TOP - 4), QPointF(x, h - self.BOTTOM))
            p.setPen(QColor(DIM))
            p.drawText(QRectF(x + 3, 2, 40, 18), Qt.AlignmentFlag.AlignLeft, f"{m}月")
        # 行
        for name, glyph, _k in A.MUNDANE:
            y = self._row_y(name)
            p.setPen(QPen(QColor(PANEL), 1))
            p.drawLine(QPointF(self.LEFT, y + rh), QPointF(w - 10, y + rh))
            p.setFont(QFont(GLYPH_FONT, 11))
            p.setPen(QColor(CYAN if name in A.OUTER else TEXT))
            p.drawText(QRectF(4, y, 20, rh), Qt.AlignmentFlag.AlignVCenter, glyph)
            p.setFont(QFont(ui_font(), 10))
            p.drawText(QRectF(24, y, self.LEFT - 26, rh), Qt.AlignmentFlag.AlignVCenter, name)
        # シャドウと逆行
        for s in self.spans:
            y = self._row_y(s.name)
            shadow = QColor(CYAN)
            shadow.setAlpha(55)
            for x0, x1 in ((s.pre, s.sr), (s.sd, s.post)):
                if x0 and x1:
                    p.fillRect(QRectF(self._x(x0), y + rh * 0.3, self._x(x1) - self._x(x0), rh * 0.4), shadow)
            retro = QColor(YELLOW)
            retro.setAlpha(200)
            p.fillRect(QRectF(self._x(s.sr), y + rh * 0.2, max(2.0, self._x(s.sd) - self._x(s.sr)), rh * 0.6), retro)
        # サイン移動
        for ing in self.ingresses:
            y, x = self._row_y(ing.name), self._x(ing.when)
            c = QColor(MAGENTA if not ing.retro else DIM)
            p.setPen(QPen(c, 2))
            p.drawLine(QPointF(x, y + rh * 0.1), QPointF(x, y + rh * 0.9))
            p.setFont(QFont(GLYPH_FONT, 10))
            p.setPen(c)
            # ♈ などは絵文字フォントに化けて消えるので、文字として描く指定(U+FE0E)を付ける
            p.drawText(QRectF(x + 3, y, 22, rh * 0.55), Qt.AlignmentFlag.AlignLeft, A.SIGNS[ing.sign_index][1] + "\ufe0e")
        # 太陽との会合: 合・内合・外合=塗りの丸 / 衝=白抜きの丸
        for ph in self.phases:
            y, x = self._row_y(ph.name), self._x(ph.when)
            r = rh * 0.16
            p.setPen(QPen(QColor(BG), 1.5))
            p.setBrush(QBrush(QColor(TEXT)) if ph.kind != "衝" else Qt.BrushStyle.NoBrush)
            if ph.kind == "衝":
                p.setPen(QPen(QColor(TEXT), 2))
            p.drawEllipse(QPointF(x, y + rh / 2), r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # 今日と選択日
        now = datetime.now().astimezone()
        if now.year == self.year:
            p.setPen(QPen(QColor(GREEN), 2))
            x = self._x(now)
            p.drawLine(QPointF(x, self.TOP - 6), QPointF(x, h - self.BOTTOM))
        if self.marker and self.marker.year == self.year:
            p.setPen(QPen(QColor(MAGENTA), 1, Qt.PenStyle.DashLine))
            x = self._x(datetime(self.marker.year, self.marker.month, self.marker.day, 12).astimezone())
            p.drawLine(QPointF(x, self.TOP - 6), QPointF(x, h - self.BOTTOM))
        p.end()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if x < self.LEFT or y < self.TOP:
            self.setToolTip("")
            return
        i = int((y - self.TOP) // self._row_h())
        if not 0 <= i < len(A.MUNDANE):
            return
        name = A.MUNDANE[i][0]
        t = self._dt_at(x)
        lines = [f"{t:%Y-%m-%d}  {name}"]
        for s in self.spans:
            if s.name != name:
                continue
            if local(s.sr) <= t < local(s.sd):
                lines.append(f"逆行中  {local(s.sr):%m/%d} → {local(s.sd):%m/%d}")
            elif s.pre and local(s.pre) <= t < local(s.sr):
                lines.append(f"シャドウ（逆行前）  逆行開始 {local(s.sr):%m/%d}")
            elif s.post and local(s.sd) <= t < local(s.post):
                lines.append(f"シャドウ（逆行後）  明け {local(s.post):%m/%d}")
        for ph in self.phases:
            if ph.name == name and abs((local(ph.when) - t).total_seconds()) < 4 * 86400:
                lines.append(f"太陽と{ph.kind}  {local(ph.when):%m/%d %H:%M}")
        self.setToolTip("\n".join(lines))

    def mousePressEvent(self, e):
        if e.position().x() >= self.LEFT:
            self.picked.emit(self._dt_at(e.position().x()).date())


class MundaneTab(QWidget):
    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        self.cache: dict[int, tuple] = {}
        self.worker = None
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(heading("MUNDANE // 社会の星（逆行カレンダー・サイン移動）"))
        top.addStretch()
        self.inner = QCheckBox("水星・金星・火星のサイン移動も出す")
        self.inner.setChecked(app.store.get("mundane_inner") == "1")
        self.inner.toggled.connect(self._toggle_inner)
        top.addWidget(self.inner)
        prev_y = QPushButton("◀")
        prev_y.clicked.connect(lambda: self.load(self.year - 1))
        self.year_lb = QLabel("")
        self.year_lb.setStyleSheet(f"color:{CYAN}; font-size:15px; padding:0 8px;")
        next_y = QPushButton("▶")
        next_y.clicked.connect(lambda: self.load(self.year + 1))
        for wdg in (prev_y, self.year_lb, next_y):
            top.addWidget(wdg)
        lay.addLayout(top)

        self.now_lb = QLabel("")
        self.now_lb.setWordWrap(True)
        lay.addWidget(self.now_lb)
        legend = QLabel("黄＝逆行　薄い帯＝シャドウ（逆行で通る度数を前後に通過する期間）　記号＝サイン移動（灰は逆行で戻る移動）"
                        "　●＝太陽と合（水星・金星は内合/外合）　○＝衝　緑線＝今日　点線＝選択日　クリックでその日へ")
        legend.setObjectName("sub")
        legend.setWordWrap(True)
        lay.addWidget(legend)

        split = QSplitter(Qt.Orientation.Vertical)
        self.timeline = Timeline()
        self.timeline.picked.connect(lambda d: app.date_edit.setDate(QDate(d.year, d.month, d.day)))
        split.addWidget(self.timeline)
        bottom = QSplitter()
        self.events = QListWidget()
        bottom.addWidget(self.events)
        bottom.addWidget(self._reading_panel())
        bottom.setSizes([420, 640])
        split.addWidget(bottom)
        split.setSizes([280, 380])
        lay.addWidget(split, 1)
        self.llm = None
        self._loaded_key = None
        self._dirty = False

        self._last_date = self.app.date_edit.date()
        self.year = self._last_date.year()
        self.load(self.year)

    # ---- 社会の星読み ----
    def _reading_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 0, 0, 0)
        v.addWidget(heading("SOCIAL READING // 社会の星読み", MAGENTA))
        row = QHBoxLayout()
        self.period = QComboBox()
        for key, (label, _n) in A.PERIODS.items():
            self.period.addItem(label, key)
        self.period.setCurrentIndex(max(0, self.period.findData(self.app.store.get("mundane_period") or "month")))
        self.period.currentIndexChanged.connect(lambda _i: self._load_reading())
        row.addWidget(self.period)
        self.range_lb = QLabel("")
        self.range_lb.setObjectName("sub")
        row.addWidget(self.range_lb, 1)
        self.read_btn = QPushButton("▶ 占う")
        self.read_btn.setObjectName("hot")
        self.read_btn.clicked.connect(self.generate)
        row.addWidget(self.read_btn)
        v.addLayout(row)
        self.read_status = QLabel("")
        self.read_status.setObjectName("sub")
        self.read_status.setWordWrap(True)
        v.addWidget(self.read_status)
        self.reading = QPlainTextEdit()
        self.reading.setPlaceholderText("「占う」でロード中のローカルLLMが、この期間の社会の流れを読みます（手で編集も可）")
        v.addWidget(self.reading, 3)
        rv = QLabel("REVIEW // 期間が過ぎたら、実際どうだったかを書いて採点")
        rv.setStyleSheet(f"color:{CYAN};")
        v.addWidget(rv)
        self.review = QPlainTextEdit()
        self.review.setPlaceholderText("世の中で実際にあったこと・空気（占いを見返す前に書くとより公平）")
        v.addWidget(self.review, 1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("当たり度"))
        self.score = QComboBox()
        self.score.addItem("未採点", None)
        for k in range(1, 6):
            self.score.addItem(f"{k}", k)
        row2.addWidget(self.score)
        self.score_lb = QLabel("")
        self.score_lb.setObjectName("sub")
        row2.addWidget(self.score_lb, 1)
        facts_btn = QPushButton("天体データを見る")
        facts_btn.clicked.connect(self._show_facts)
        row2.addWidget(facts_btn)
        save = QPushButton("■ 保存")
        save.clicked.connect(self.save_reading)
        row2.addWidget(save)
        v.addLayout(row2)
        for wdg in (self.reading, self.review):
            wdg.textChanged.connect(self._mark_dirty)
        self.score.currentIndexChanged.connect(lambda _i: self._mark_dirty())
        self._facts = ""
        self._model = ""
        return w

    def _mark_dirty(self):
        if not self._loading:
            self._dirty = True

    _loading = False

    def _key(self):
        per = self.period.currentData()
        a, b = A.period_range(per, self.app.date_edit.date().toPyDate())
        return per, a, b

    def _load_reading(self):
        per, a, b = self._key()
        key = (per, a)
        if key == self._loaded_key:
            return
        if self._dirty and self._loaded_key:
            ans = QMessageBox.question(self, "未保存", "社会の星読み／振り返りに未保存の変更があります。保存しますか？")
            if ans == QMessageBox.StandardButton.Yes:
                self.save_reading()
        self._loaded_key = key
        self.app.store.set_many({"mundane_period": per})
        self.range_lb.setText(f"{a:%Y-%m-%d} 〜 {b - timedelta(days=1):%Y-%m-%d}")
        row = self.app.store.mundane(f"{a:%Y-%m-%d}", per)
        self._loading = True
        self.reading.setPlainText(row["text"] if row else "")
        self.review.setPlainText((row["review"] or "") if row else "")
        self.score.setCurrentIndex(self.score.findData(row["score"]) if row and row["score"] else 0)
        self._loading = False
        self._dirty = False
        self._facts = row["facts"] if row else ""
        self._model = (row["model"] or "") if row else ""
        self.read_status.setText(f"■ 保存済み（{self._model or '手入力'}）" if row else "")
        self._update_scores()

    def _update_scores(self):
        rows = {r["period"]: r for r in self.app.store.mundane_scores()}
        parts = []
        for key, (label, _n) in A.PERIODS.items():
            r = rows.get(key)
            parts.append(f"{label} {r['avg']:.1f}（{r['n']}件）" if r and r["n"] else f"{label} —")
        self.score_lb.setText("平均 " + " / ".join(parts))

    def _facts_fn(self):
        per, a, b = self._key()
        return lambda: A.summarize_mundane(per, a, b)

    def _show_facts(self):
        if not self._facts:
            self._facts = self._facts_fn()()
        QMessageBox.information(self, "天体データ（LLMに渡す内容）", self._facts)

    def generate(self):
        if self.reading.toPlainText().strip():
            ans = QMessageBox.question(self, "上書き", "この期間の社会の星読みを作り直しますか？（振り返りと採点は残ります）")
            if ans != QMessageBox.StandardButton.Yes:
                return
        target, why = self.app.llm_target()
        if not target:
            self.read_status.setText(f"◆ {why}。llama-server でモデルを起動してから押してください")
            return
        per, a, b = self._key()
        length, max_tokens = MUNDANE_LENGTH[per]
        system = MUNDANE_PROMPT.format(label=A.PERIODS[per][0], length=length)
        self.read_btn.setEnabled(False)
        self._gen_key, self._gen_model = (per, a), target["name"]
        self.read_status.setText(f"◇ 天体を計算して {target['name']} に問い合わせ中…")
        self.llm = LLMWorker(target["url"], target["model_id"], self._facts_fn(), target["llama"], system, max_tokens)
        self.llm.done.connect(self._read_done)
        self.llm.start()

    def _read_done(self, text, ok):
        self.read_btn.setEnabled(True)
        if not ok:
            self.read_status.setText(f"◆ 失敗: {text[:160]}")
            return
        per, a = self._gen_key
        prev = self.app.store.mundane(f"{a:%Y-%m-%d}", per)
        # 生成した文はすぐ保存（振り返りと採点は既存のものを残す）
        self.app.store.save_mundane(f"{a:%Y-%m-%d}", per, self.llm.facts, text, self._gen_model,
                                    prev["review"] if prev else "", prev["score"] if prev else None)
        if (per, a) == self._loaded_key:
            self._loading = True
            self.reading.setPlainText(text)
            self._loading = False
            self._facts, self._model = self.llm.facts, self._gen_model
        self.read_status.setText(f"◆ 占い完了 by {self._gen_model}（保存済み）")

    def save_reading(self):
        if not self._loaded_key:
            return
        per, a = self._loaded_key
        self.app.store.save_mundane(f"{a:%Y-%m-%d}", per, self._facts, self.reading.toPlainText().strip(),
                                    self._model, self.review.toPlainText().strip(), self.score.currentData())
        self._dirty = False
        self.read_status.setText("■ 保存しました")
        self._update_scores()

    def _toggle_inner(self, on):
        self.app.store.set_many({"mundane_inner": "1" if on else "0"})
        if self.year in self.cache:
            self._show(self.year, *self.cache[self.year])

    def load(self, year: int):
        if not 1901 <= year <= 2049:  # DE421 の範囲（シャドウ探索の余白込み）
            return
        self.year = year
        self.year_lb.setText(str(year))
        if year in self.cache:
            self._show(year, *self.cache[year])
            return
        if self.worker and self.worker.isRunning():
            return  # 計算中に連打されたら終わってから押し直してもらう
        self.now_lb.setText("◇ 計算中…")
        self.worker = MundaneWorker(year)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, year, spans, ing, phases):
        self.cache[year] = (spans, ing, phases)
        if year == self.year:
            self._show(year, spans, ing, phases)

    def _visible_ingresses(self, ing):
        return [i for i in ing if self.inner.isChecked() or i.name in A.OUTER]

    def _show(self, year, spans, ing, phases):
        ing = self._visible_ingresses(ing)
        self.timeline.set_data(year, spans, ing, phases)
        self._summary(spans)
        # 一覧: シャドウ入り / 逆行開始 / 順行に戻る / シャドウ明け / サイン移動
        a, b = datetime(year, 1, 1).astimezone(), datetime(year + 1, 1, 1).astimezone()
        ev = []
        for s in spans:
            for when, what, col in ((s.pre, "シャドウ入り", DIM), (s.sr, "逆行開始（留）", YELLOW),
                                    (s.sd, "順行に戻る（留）", YELLOW), (s.post, "シャドウ明け", DIM)):
                if when and a <= local(when) < b:
                    lon = s.lon_sd if what in ("シャドウ入り", "順行に戻る（留）") else s.lon_sr
                    ev.append((local(when), f"{s.glyph} {s.name}  {what}  {self._deg(lon)}", col))
        for i in ing:
            what = "逆行で戻る" if i.retro else "入る"
            sg, sy = A.SIGNS[i.sign_index]
            ev.append((local(i.when), f"{i.glyph} {i.name}  {sy} {sg}に{what}", DIM if i.retro else MAGENTA))
        for ph in phases:
            ev.append((local(ph.when), f"{ph.glyph} {ph.name}  太陽と{ph.kind}  {self._deg(ph.lon)}", TEXT))
        ev.sort(key=lambda e: e[0])
        self.events.clear()
        now = datetime.now().astimezone()
        first_future = None
        for when, text, col in ev:
            self.events.addItem(f"{when:%Y-%m-%d (%a) %H:%M}   {text}")
            item = self.events.item(self.events.count() - 1)
            c = QColor(col)
            if when < now:
                c.setAlpha(110)
            elif first_future is None:
                first_future = item
            item.setForeground(c)
        if first_future:
            self.events.scrollToItem(first_future, QListWidget.ScrollHint.PositionAtTop)
        self.refresh()

    @staticmethod
    def _deg(lon):
        d = lon % 30
        return f"{A.SIGNS[int(lon // 30) % 12][0]}{int(d):02d}°{int(round((d % 1) * 60)) % 60:02d}'"

    def _summary(self, spans):
        """選択日の時点で逆行中・シャドウ中のもの"""
        qd = self.app.date_edit.date()
        t = datetime(qd.year(), qd.month(), qd.day(), 12).astimezone()
        retro, shadow = [], []
        for s in spans:
            if local(s.sr) <= t < local(s.sd):
                retro.append(f"{s.glyph}{s.name}（〜{local(s.sd):%m/%d}）")
            elif (s.pre and local(s.pre) <= t < local(s.sr)) or (s.post and local(s.sd) <= t < local(s.post)):
                shadow.append(f"{s.glyph}{s.name}")
        if t.year != self.year:
            self.now_lb.setText("")
            return
        self.now_lb.setText(
            f"<span style='color:{YELLOW}'>{t:%m/%d} 逆行中: {'、'.join(retro) or 'なし'}</span>"
            f"　<span style='color:{CYAN}'>シャドウ中: {'、'.join(shadow) or 'なし'}</span>")

    def refresh(self):
        qd = self.app.date_edit.date()
        if qd != self._last_date:  # 上のバーで別の年へ動いたら追従（◀▶で年だけ変えたときは追わない）
            self._last_date = qd
            if qd.year() != self.year:
                self.load(qd.year())
        self.timeline.marker = qd.toPyDate()
        self.timeline.update()
        self._load_reading()
        if self.year in self.cache:
            self._summary(self.cache[self.year][0])


# ───────────────────────── タブ: ブラインド評価 ─────────────────────────
class OptionButton(QPushButton):
    """長文を折り返せる選択肢ボタン"""

    def __init__(self):
        super().__init__()
        self.setObjectName("option")
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(self.body)
        self.raw = ""

    def set_text(self, t: str):
        self.raw = t
        self.body.setText(t)
        self.setMinimumHeight(max(64, self.body.heightForWidth(max(200, self.width() - 20)) + 18))


class EvalTab(QWidget):
    N = 4

    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        self.trial = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading("BLIND TEST // 日付を伏せて当てる"))
        hint = QLabel(
            "日記1件と、日付を伏せた占い文4件。日記に一番当てはまる文を選ぶ。偶然なら正解率25%。"
            "書いてから2週間以上空けて解くと、記憶で当てるのを防げます。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("sub")
        lay.addWidget(hint)

        ctl = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem("個人ホロスコープの星読み", "personal")
        self.kind.addItem("一般の星座占い", "general")
        ctl.addWidget(self.kind)
        self.new_btn = QPushButton("▶ 新しい問題")
        self.new_btn.setObjectName("hot")
        self.new_btn.clicked.connect(self.new_trial)
        ctl.addWidget(self.new_btn)
        ctl.addStretch()
        self.pool_lb = QLabel("")
        self.pool_lb.setObjectName("sub")
        ctl.addWidget(self.pool_lb)
        lay.addLayout(ctl)

        split = QSplitter(Qt.Orientation.Horizontal)
        qbox = QWidget()
        ql = QVBoxLayout(qbox)
        ql.addWidget(QLabel("日記"))
        self.journal_view = QPlainTextEdit()
        self.journal_view.setReadOnly(True)
        ql.addWidget(self.journal_view)
        split.addWidget(qbox)

        obox = QWidget()
        self.ol = QVBoxLayout(obox)
        self.opts = []
        self.group = QButtonGroup(self)
        for i in range(self.N):
            b = OptionButton()
            b.clicked.connect(lambda _=False, k=i: self.pick(k))
            self.opts.append(b)
            self.ol.addWidget(b)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.ol.addWidget(self.result)
        split.addWidget(obox)
        split.setSizes([350, 650])
        lay.addWidget(split, 1)

        lay.addWidget(heading("SCORE // 個人 vs 一般", MAGENTA))
        self.score_view = ScoreBars()
        lay.addWidget(self.score_view)

    def refresh(self):
        sizes = self.app.store.pool_sizes()
        self.pool_lb.setText(f"出題可能: 個人 {sizes['personal']}日 / 一般 {sizes['general']}日（各{self.N}日以上で開始）")
        self.score_view.set_stats(self.app.store.stats(self.N))
        if not self.trial:
            for b in self.opts:
                b.set_text("")
                b.setEnabled(False)

    def new_trial(self):
        kind = self.kind.currentData()
        self.trial = self.app.store.make_trial(kind, self.N)
        self.result.setText("")
        if not self.trial:
            self.journal_view.setPlainText(f"まだデータが足りません。日記と占い文がそろった日が{self.N}日分必要です。")
            for b in self.opts:
                b.set_text("")
                b.setEnabled(False)
            return
        self.journal_view.setPlainText(self.trial["journal"])
        for i, (b, txt) in enumerate(zip(self.opts, self.trial["options"])):
            short = txt if len(txt) <= 320 else txt[:320] + "…"
            b.setStyleSheet("")
            b.set_text(f"{'ABCD'[i]}  {short}")
            b.setEnabled(True)

    def pick(self, k):
        if not self.trial:
            return
        ans = self.trial["answer_idx"]
        self.app.store.answer_trial(self.trial["id"], k)
        for i, b in enumerate(self.opts):
            b.setEnabled(False)
            d = self.trial["option_dates"][i]
            head, rest = b.raw.split("  ", 1)
            b.set_text(f"{head}  [{d}]  {rest}")
            for idx, col in ((ans, GREEN), (k, MAGENTA)):
                if i == idx:
                    b.setStyleSheet(f"QPushButton{{border:2px solid {col};}} QLabel{{color:{col};}}")
                    break
        hit = k == ans
        self.result.setText(("◆ HIT — 正解" if hit else f"◇ MISS — 正解は {'ABCD'[ans]}") + "（日付を表示しました）")
        self.result.setStyleSheet(f"color:{GREEN if hit else MAGENTA};")
        self.trial = None
        self.refresh()


class ScoreBars(QWidget):
    """正解率のピクセルバー。25%ライン=偶然。"""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(100)
        self.stats = {}

    def set_stats(self, s):
        self.stats = s
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), QColor(BG))
        p.setFont(QFont(ui_font(), 10))
        label_w, bar_x = 150, 160
        bar_w = w - bar_x - 230
        for row, (kind, name, col) in enumerate((("personal", "個人ホロスコープ", CYAN), ("general", "一般の星座占い", MAGENTA))):
            st = self.stats.get(kind, {"n": 0, "hits": 0, "rate": 0, "p": 1})
            y = 10 + row * 36
            p.setPen(QColor(col))
            p.drawText(QRectF(0, y, label_w, 22), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, name)
            # ピクセル単位のバー
            cells = 40
            cw = bar_w / cells
            filled = round(st["rate"] * cells)
            for i in range(cells):
                c = QColor(col) if i < filled else QColor(PANEL)
                p.fillRect(QRectF(bar_x + i * cw + 1, y + 3, cw - 2, 16), c)
            # 偶然ライン
            p.setPen(QPen(QColor(YELLOW), 2, Qt.PenStyle.DashLine))
            xc = bar_x + bar_w * 0.25
            p.drawLine(QPointF(xc, y - 2), QPointF(xc, y + 24))
            p.setPen(QColor(TEXT))
            verdict = "偶然と区別できない"
            if st["n"] >= 10 and st["p"] < 0.05:
                verdict = "偶然より当たってる"
            elif st["n"] < 10:
                verdict = "まだ試行が少ない"
            p.drawText(
                QRectF(bar_x + bar_w + 10, y, 230, 22), Qt.AlignmentFlag.AlignVCenter,
                f"{st['hits']}/{st['n']}  {st['rate'] * 100:.0f}%  p={st['p']:.2f}  {verdict}",
            )
        p.setPen(QColor(YELLOW))
        p.drawText(QRectF(bar_x, 80, 260, 18), Qt.AlignmentFlag.AlignLeft, "┆ 25% = 偶然の正解率")
        p.end()


# ───────────────────────── タブ: 設定 ─────────────────────────
class SettingsTab(QWidget):
    def __init__(self, app: "Main"):
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.addWidget(heading("BIRTH DATA // 出生データ（この端末にのみ保存）"))
        form = QFormLayout()
        self.bdate = QDateEdit(calendarPopup=True)
        self.bdate.setDisplayFormat("yyyy-MM-dd")
        self.bdate.setDate(QDate(1995, 1, 1))
        form.addRow("生年月日", self.bdate)
        trow = QHBoxLayout()
        self.time_known = QCheckBox("出生時刻が分かる")
        self.btime = QTimeEdit()
        self.btime.setDisplayFormat("HH:mm")
        self.time_known.toggled.connect(self.btime.setEnabled)
        trow.addWidget(self.time_known)
        trow.addWidget(self.btime)
        form.addRow("出生時刻", trow)
        self.offset = QDoubleSpinBox(minimum=-12, maximum=14, singleStep=0.5, value=9.0, suffix=" h")
        form.addRow("出生地のUTCオフセット", self.offset)
        self.lat = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, value=35.6812)
        self.lon = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, value=139.7671)
        form.addRow("出生地 緯度", self.lat)
        form.addRow("出生地 経度", self.lon)
        lay.addLayout(form)
        note = QLabel("時刻が不明な場合は正午で計算し、ASC・ハウスは使いません。地名検索はしません（外部送信を避けるため緯度経度を直接入力）。")
        note.setWordWrap(True)
        note.setObjectName("sub")
        lay.addWidget(note)

        lay.addWidget(heading("LOCAL LLM // OpenAI互換エンドポイント", MAGENTA))
        self.auto = QCheckBox("ロード中のローカルLLM（llama-server）に自動で合わせる")
        lay.addWidget(self.auto)
        self.detected = QLabel("")
        self.detected.setObjectName("sub")
        self.detected.setWordWrap(True)
        lay.addWidget(self.detected)
        form2 = QFormLayout()
        self.url = QLineEdit()
        self.model = QLineEdit()
        form2.addRow("Base URL（手動）", self.url)
        form2.addRow("Model（手動）", self.model)
        self.auto.toggled.connect(lambda on: (self.url.setEnabled(not on), self.model.setEnabled(not on)))
        lay.addLayout(form2)

        row = QHBoxLayout()
        row.addStretch()
        save = QPushButton("■ 保存")
        save.clicked.connect(self.save)
        row.addWidget(save)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("sub")
        lay.addWidget(self.status)
        lay.addStretch()
        self.load()

    def load(self):
        st = self.app.store
        if st.get("birth_date"):
            self.bdate.setDate(QDate.fromString(st.get("birth_date"), "yyyy-MM-dd"))
        tk = st.get("birth_time")
        self.time_known.setChecked(bool(tk))
        self.btime.setEnabled(bool(tk))
        if tk:
            self.btime.setTime(QTime.fromString(tk, "HH:mm"))
        self.offset.setValue(float(st.get("utc_offset")))
        if st.get("lat"):
            self.lat.setValue(float(st.get("lat")))
            self.lon.setValue(float(st.get("lon")))
        self.url.setText(st.get("llm_url"))
        self.model.setText(st.get("llm_model"))
        auto = st.get("llm_auto") == "1"
        self.auto.setChecked(auto)
        self.url.setEnabled(not auto)
        self.model.setEnabled(not auto)

    def show_servers(self, servers: list[dict]):
        if not servers:
            self.detected.setText("検出: llama-server は動いていません（llama-server でモデルを起動してください）")
            return
        mark = {"ready": "●", "loading": "◐ 読み込み中", "down": "× 応答なし"}
        self.detected.setText("検出:\n" + "\n".join(
            f"  {mark[s['state']]} {s['name']}  :{s['port']}  {ctx_label(s['ctx'])}" for s in servers))

    def save(self):
        self.app.store.set_many({
            "birth_date": self.bdate.date().toString("yyyy-MM-dd"),
            "birth_time": self.btime.time().toString("HH:mm") if self.time_known.isChecked() else "",
            "utc_offset": self.offset.value(),
            "lat": self.lat.value(),
            "lon": self.lon.value(),
            "llm_url": self.url.text().strip(),
            "llm_model": self.model.text().strip() or "local",
            "llm_auto": "1" if self.auto.isChecked() else "0",
        })
        self.status.setText("■ 保存しました")
        self.app.rebuild()
        self.app._on_servers(self.app.servers)  # 自動/手動の切り替えをバッジにすぐ反映


# ───────────────────────── メイン ─────────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store = Store()
        self.natal = None
        self.transit = None
        self.aspects = []
        self.setWindowTitle("HOSHIYOMI LOG // 星読みログ")
        self.resize(1120, 760)

        root = QWidget()
        rl = QVBoxLayout(root)
        bar = QHBoxLayout()
        title = QLabel("HOSHIYOMI LOG")
        title.setStyleSheet(f"color:{CYAN}; font-size:22px; letter-spacing:4px;")
        glow(title, CYAN, 22)
        bar.addWidget(title)
        self.retro_badge = QLabel("")
        self.retro_badge.setStyleSheet(f"color:{YELLOW}; font-family:'{GLYPH_FONT}'; font-size:15px; padding-left:16px;")
        glow(self.retro_badge, YELLOW, 14)
        bar.addWidget(self.retro_badge)
        bar.addStretch()
        # いま読み込んでいるローカルLLM。押すと即再検出
        self.llm_badge = QPushButton("LLM ◌ 検出中…")
        self.llm_badge.setFlat(True)
        self.llm_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.llm_badge.setToolTip("ロード中のローカルLLM（クリックで再検出・15秒ごとに自動更新）")
        self.llm_badge.clicked.connect(self.probe_llm)
        bar.addWidget(self.llm_badge)
        prev_b = QPushButton("◀")
        prev_b.clicked.connect(lambda: self.date_edit.setDate(self.date_edit.date().addDays(-1)))
        self.date_edit = QDateEdit(calendarPopup=True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.dateChanged.connect(lambda _d: self.rebuild())
        next_b = QPushButton("▶")
        next_b.clicked.connect(lambda: self.date_edit.setDate(self.date_edit.date().addDays(1)))
        today_b = QPushButton("今日")
        today_b.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        for wdg in (prev_b, self.date_edit, next_b, today_b):
            bar.addWidget(wdg)
        rl.addLayout(bar)
        line = QFrame()
        line.setFixedHeight(2)
        line.setStyleSheet(f"background:{MAGENTA};")
        glow(line, MAGENTA, 12)
        rl.addWidget(line)

        self.tabs = QTabWidget()
        self.chart_tab = ChartTab(self)
        self.reading_tab = ReadingTab(self)
        self.mundane_tab = MundaneTab(self)
        self.journal_tab = JournalTab(self)
        self.eval_tab = EvalTab(self)
        self.settings_tab = SettingsTab(self)
        self.tabs.addTab(self.chart_tab, "星図")
        self.tabs.addTab(self.reading_tab, "星読み")
        self.tabs.addTab(self.mundane_tab, "社会")
        self.tabs.addTab(self.journal_tab, "日記")
        self.tabs.addTab(self.eval_tab, "ブラインド評価")
        self.tabs.addTab(self.settings_tab, "設定")
        rl.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        self._blink = 0
        QTimer(self, timeout=self._blink_badge, interval=600).start()
        self.servers: list[dict] = []
        self._probe = None
        QTimer(self, timeout=self.probe_llm, interval=15000).start()
        self.probe_llm()
        self.rebuild()
        if not self.store.get("birth_date"):
            self.tabs.setCurrentWidget(self.settings_tab)

    @property
    def date_str(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def rebuild(self):
        st = self.store
        # 出生図
        self.natal = None
        if st.get("birth_date"):
            y, m, d = map(int, st.get("birth_date").split("-"))
            bt = st.get("birth_time")
            hh, mm = (map(int, bt.split(":")) if bt else (None, 0))
            self.natal = A.natal_chart(
                y, m, d, hh, mm, float(st.get("utc_offset")),
                float(st.get("lat")) if st.get("lat") else None,
                float(st.get("lon")) if st.get("lon") else None,
            )
        # 選択日のトランジット（いまいる場所の正午）
        qd = self.date_edit.date()
        local_off = datetime.now().astimezone().utcoffset() or timedelta(0)
        t_utc = datetime(qd.year(), qd.month(), qd.day(), 12, 0) - local_off
        self.transit = A.compute_chart(t_utc)
        self.aspects = A.transit_aspects(self.transit, self.natal) if self.natal else []
        retro = [b.glyph for b in self.transit.bodies if b.retro]
        self._retro_text = ("逆行中 " + " ".join(f"{g}R" for g in retro)) if retro else "逆行なし"
        self.retro_badge.setText(self._retro_text)
        for tab in (self.chart_tab, self.reading_tab, self.mundane_tab, self.journal_tab, self.eval_tab):
            tab.refresh()

    def facts(self) -> str:
        if not self.natal:
            return ""
        return A.summarize_for_llm(self.transit, self.natal, self.aspects)

    # ---- ローカルLLM ----
    def probe_llm(self):
        if self._probe and self._probe.isRunning():
            return
        if self.isMinimized() and self.servers:
            return  # 見えていない間は回さない
        self._probe = LLMProbe()
        self._probe.found.connect(self._on_servers)
        self._probe.start()

    def _on_servers(self, servers: list[dict]):
        self.servers = servers
        self.settings_tab.show_servers(servers)
        if self.store.get("llm_auto") != "1":
            name = self.store.get("llm_model")
            self._set_llm_badge(f"LLM ■ 手動: {name}", DIM)
            return
        ready = [s for s in servers if s["state"] == "ready"]
        if ready:
            s = ready[0]
            more = f"  +{len(servers) - 1}" if len(servers) > 1 else ""
            self._set_llm_badge(f"LLM ● {s['name']}  :{s['port']}  {ctx_label(s['ctx'])}{more}", GREEN)
        elif servers:
            self._set_llm_badge(f"LLM ◐ {servers[0]['name']} 読み込み中…", YELLOW)
        else:
            self._set_llm_badge("LLM × 未ロード", MAGENTA)

    def _set_llm_badge(self, text: str, color: str):
        self.llm_badge.setText(text)
        self.llm_badge.setStyleSheet(f"QPushButton {{ border:none; color:{color}; padding:4px 10px; }}")

    def llm_target(self) -> tuple[dict | None, str]:
        """星読みに使う先。自動なら直近の検出結果から ready のものを選ぶ"""
        st = self.store
        if st.get("llm_auto") != "1":
            name = st.get("llm_model")
            return {"url": st.get("llm_url"), "model_id": name, "name": name, "llama": False}, ""
        ready = [s for s in self.servers if s["state"] == "ready"]
        if ready:
            return dict(ready[0], llama=True), ""
        if self.servers:
            return None, f"{self.servers[0]['name']} はまだ読み込み中"
        return None, "ローカルLLMがロードされていません"

    def _blink_badge(self):
        self._blink ^= 1
        txt = getattr(self, "_retro_text", "")
        self.retro_badge.setText(txt if self._blink or "R" not in txt else txt.replace("R", " "))


def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("hoshiyomi-log")  # Wayland の app_id。.desktop と紐付けてアイコン化けを防ぐ
    app.setFont(QFont(ui_font(), 10))
    app.setStyleSheet(QSS)
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
