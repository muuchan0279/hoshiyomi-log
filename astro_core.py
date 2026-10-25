"""HOSHIYOMI LOG — 天体計算コア

すべてローカル計算（Skyfield + 同梱 DE421 暦、MIT/パブリックドメイン）。
- 黄経はトロピカル（その日の黄道座標）
- ハウスはホールサイン方式（出生時刻が分かる場合のみ）
- 逆行は1日あたりの黄経変化量で判定
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore", category=RuntimeWarning, module="skyfield_data")

from skyfield.api import Loader  # noqa: E402
from skyfield.framelib import ecliptic_frame  # noqa: E402
from skyfield_data import get_skyfield_data_path  # noqa: E402

_load = Loader(get_skyfield_data_path(), verbose=False)
TS = _load.timescale(builtin=True)
EPH = _load("de421.bsp")  # 1900–2050
EARTH = EPH["earth"]

# 表示名, 記号, DE421 のキー
PLANETS = [
    ("太陽", "☉", "sun"),
    ("月", "☽", "moon"),
    ("水星", "☿", "mercury"),
    ("金星", "♀", "venus"),
    ("火星", "♂", "mars"),
    ("木星", "♃", "jupiter barycenter"),
    ("土星", "♄", "saturn barycenter"),
    ("天王星", "♅", "uranus barycenter"),
    ("海王星", "♆", "neptune barycenter"),
    ("冥王星", "♇", "pluto barycenter"),
]

SIGNS = [
    ("牡羊座", "♈"), ("牡牛座", "♉"), ("双子座", "♊"), ("蟹座", "♋"),
    ("獅子座", "♌"), ("乙女座", "♍"), ("天秤座", "♎"), ("蠍座", "♏"),
    ("射手座", "♐"), ("山羊座", "♑"), ("水瓶座", "♒"), ("魚座", "♓"),
]

# 名前, 記号, 角度, オーブ(度)
ASPECTS = [
    ("合", "☌", 0, 8),
    ("セクスタイル", "⚹", 60, 4),
    ("スクエア", "□", 90, 6),
    ("トライン", "△", 120, 6),
    ("オポジション", "☍", 180, 8),
]


@dataclass
class BodyPos:
    name: str
    glyph: str
    lon: float  # 0–360 黄経
    speed: float  # 度/日
    house: int | None = None

    @property
    def retro(self) -> bool:
        return self.speed < 0 and self.name not in ("太陽", "月")

    @property
    def sign_index(self) -> int:
        return int(self.lon // 30) % 12

    @property
    def sign(self) -> str:
        return SIGNS[self.sign_index][0]

    @property
    def deg_in_sign(self) -> float:
        return self.lon % 30

    def label(self) -> str:
        d = self.deg_in_sign
        r = " R" if self.retro else ""
        return f"{self.glyph} {self.name} {self.sign} {int(d):02d}°{int((d % 1) * 60):02d}'{r}"


@dataclass
class Chart:
    when_utc: datetime
    bodies: list[BodyPos]
    asc: float | None = None
    mc: float | None = None
    meta: dict = field(default_factory=dict)

    def by_name(self, name: str) -> BodyPos | None:
        return next((b for b in self.bodies if b.name == name), None)


def _to_t(dt_utc: datetime):
    return TS.from_datetime(dt_utc.replace(tzinfo=timezone.utc) if dt_utc.tzinfo is None else dt_utc)


def _ecl_lon(t, key: str) -> float:
    app = EARTH.at(t).observe(EPH[key]).apparent()
    _lat, lon, _d = app.frame_latlon(ecliptic_frame)
    return lon.degrees % 360


def _obliquity_deg(t) -> float:
    # IAU 2006 平均黄道傾斜角（十分な精度）
    T = (t.tt - 2451545.0) / 36525.0
    return (84381.406 - 46.836769 * T - 0.0001831 * T * T) / 3600.0


def asc_mc(dt_utc: datetime, lat: float, lon: float) -> tuple[float, float]:
    """上昇点(ASC)と天頂(MC)の黄経を返す。"""
    t = _to_t(dt_utc)
    eps = math.radians(_obliquity_deg(t))
    lst_hours = (t.gast + lon / 15.0) % 24
    ramc = math.radians(lst_hours * 15.0)
    phi = math.radians(lat)
    mc = math.degrees(math.atan2(math.sin(ramc), math.cos(ramc) * math.cos(eps))) % 360
    asc = math.degrees(
        math.atan2(math.cos(ramc), -(math.sin(ramc) * math.cos(eps) + math.tan(phi) * math.sin(eps)))
    ) % 360
    return asc, mc


def compute_chart(dt_utc: datetime, lat: float | None = None, lon: float | None = None) -> Chart:
    t0 = _to_t(dt_utc)
    t1 = _to_t(dt_utc + timedelta(hours=12))
    tm = _to_t(dt_utc - timedelta(hours=12))
    bodies = []
    for name, glyph, key in PLANETS:
        l0 = _ecl_lon(t0, key)
        lp = _ecl_lon(t1, key)
        lm = _ecl_lon(tm, key)
        speed = ((lp - lm + 540) % 360) - 180  # 1日あたり
        bodies.append(BodyPos(name, glyph, l0, speed))
    chart = Chart(dt_utc, bodies)
    if lat is not None and lon is not None:
        chart.asc, chart.mc = asc_mc(dt_utc, lat, lon)
        asc_sign = int(chart.asc // 30)
        for b in bodies:
            b.house = ((b.sign_index - asc_sign) % 12) + 1  # ホールサイン
    return chart


def natal_chart(year, month, day, hour=None, minute=0, utc_offset=9.0, lat=None, lon=None) -> Chart:
    """出生図。時刻不明なら正午で計算し、ASC/ハウスは出さない。"""
    time_known = hour is not None
    h = hour if time_known else 12
    local = datetime(year, month, day, h, minute)
    dt_utc = local - timedelta(hours=utc_offset)
    ch = compute_chart(dt_utc, lat if time_known else None, lon if time_known else None)
    ch.meta["time_known"] = time_known
    return ch


def angle_diff(a: float, b: float) -> float:
    d = abs((a - b) % 360)
    return 360 - d if d > 180 else d


@dataclass
class Aspect:
    transit: BodyPos
    natal: BodyPos
    name: str
    glyph: str
    orb: float

    def label(self) -> str:
        r = "(逆行)" if self.transit.retro else ""
        return f"T{self.transit.glyph}{self.transit.name}{r} {self.glyph} N{self.natal.glyph}{self.natal.name}  orb {self.orb:.1f}°"


def transit_aspects(transit: Chart, natal: Chart, skip_moon: bool = False) -> list[Aspect]:
    out = []
    for tb in transit.bodies:
        if skip_moon and tb.name == "月":
            continue
        for nb in natal.bodies:
            d = angle_diff(tb.lon, nb.lon)
            for name, glyph, ang, orb in ASPECTS:
                o = abs(d - ang)
                # 動きの速い月は半分のオーブ
                limit = orb / 2 if tb.name == "月" else orb
                if o <= limit:
                    out.append(Aspect(tb, nb, name, glyph, o))
    out.sort(key=lambda a: a.orb)
    return out


def natal_house_of(lon: float, natal: Chart) -> int | None:
    if natal.asc is None:
        return None
    return ((int(lon // 30) - int(natal.asc // 30)) % 12) + 1


def summarize_for_llm(transit: Chart, natal: Chart, aspects: list[Aspect]) -> str:
    """LLM に渡す事実データ（解釈はLLM側）。"""
    lines = ["[出生図]"]
    for b in natal.bodies:
        h = f" 第{b.house}ハウス" if b.house else ""
        lines.append(f"- {b.label()}{h}")
    if natal.asc is not None:
        lines.append(f"- ASC {SIGNS[int(natal.asc // 30)][0]} / MC {SIGNS[int(natal.mc // 30)][0]}")
    else:
        lines.append("- 出生時刻不明のためハウスなし")
    lines.append("\n[今日の星（トランジット）]")
    for b in transit.bodies:
        h = natal_house_of(b.lon, natal)
        hs = f" 出生図の第{h}ハウス通過" if h else ""
        lines.append(f"- {b.label()}{hs}")
    lines.append("\n[今日のアスペクト（オーブ小さい順・最大8件）]")
    for a in aspects[:8]:
        lines.append(f"- {a.label()}")
    if not aspects:
        lines.append("- 目立つアスペクトなし")
    return "\n".join(lines)


# ───────────────────────── 社会（マンデン）: 逆行カレンダー・サイン移動 ─────────────────────────
MUNDANE = PLANETS[2:]  # 水星〜冥王星（太陽・月は逆行しない）
OUTER = ("木星", "土星", "天王星", "海王星", "冥王星")


@dataclass
class RetroSpan:
    name: str
    glyph: str
    sr: datetime  # 逆行開始（留）UTC
    sd: datetime  # 順行に戻る（留）UTC
    lon_sr: float
    lon_sd: float
    pre: datetime | None  # シャドウ入り（後で戻ってくる度数に初めて着いた日）
    post: datetime | None  # シャドウ明け（逆行開始の度数を再び越えた日）


@dataclass
class Ingress:
    name: str
    glyph: str
    when: datetime  # UTC
    sign_index: int  # 入ったサイン
    retro: bool  # 逆行で前のサインへ戻る移動


def _lon_arr(key: str, t):
    """Time 配列 → 黄経の numpy 配列（ベクトル計算）"""
    _lat, lon, _d = EARTH.at(t).observe(EPH[key]).apparent().frame_latlon(ecliptic_frame)
    return lon.degrees % 360


def _speed_arr(key: str, jd):
    """jd 配列の各点での速度（度/日、前後12時間の差）"""
    a = _lon_arr(key, TS.tt_jd(jd + 0.5))
    b = _lon_arr(key, TS.tt_jd(jd - 0.5))
    return ((a - b + 540) % 360) - 180


def _dt(jd: float) -> datetime:
    """分に丸めた UTC（計算精度は±30分なので秒は出さない）"""
    d = TS.tt_jd(jd).utc_datetime()
    return (d + timedelta(seconds=30)).replace(second=0, microsecond=0)


def mundane_calendar(start: datetime, end: datetime, pad_days: int = 240):
    """[start, end) にかかる逆行期間とサイン移動を返す。

    日ごとの黄経で当たりを付け、留とサイン移動は1時間刻みで詰める（精度±30分）。
    シャドウは前後 pad_days 日まで探す（外惑星のシャドウは数か月ある）。
    """
    import numpy as np

    j0 = TS.from_datetime(start.replace(tzinfo=timezone.utc)).tt - pad_days
    j1 = TS.from_datetime(end.replace(tzinfo=timezone.utc)).tt + pad_days
    jd = np.arange(math.floor(j0), math.ceil(j1) + 1, 1.0)
    hours = np.arange(0, 49) / 24.0  # 2日ぶんを1時間刻み
    lo, hi = start.replace(tzinfo=timezone.utc), end.replace(tzinfo=timezone.utc)
    spans, ingresses = [], []
    for name, glyph, key in MUNDANE:
        lon = _lon_arr(key, TS.tt_jd(jd))
        L = np.rad2deg(np.unwrap(np.deg2rad(lon)))  # 連続な黄経（360で折り返さない）
        spd = np.gradient(L)
        # 留: 速度の符号が変わる日 → 前後1日を1時間刻みで
        stations = []
        for i in np.nonzero(np.sign(spd[:-1]) != np.sign(spd[1:]))[0]:
            hj = jd[i] - 0.5 + hours
            s = _speed_arr(key, hj)
            k = np.nonzero(np.sign(s[:-1]) != np.sign(s[1:]))[0]
            if len(k) == 0:
                continue
            kind = "SR" if s[k[0]] > 0 else "SD"
            stations.append((kind, hj[k[0]], float(np.interp(hj[k[0]], jd, L))))
        # SR→SD の組で逆行期間
        for (k1, jsr, lsr), (k2, jsd, lsd) in zip(stations, stations[1:]):
            if (k1, k2) != ("SR", "SD"):
                continue
            isr, isd = np.searchsorted(jd, jsr), np.searchsorted(jd, jsd)
            pre = next((jd[j] for j in range(isr - 1, 0, -1) if L[j - 1] < lsd <= L[j]), None)
            post = next((jd[j] for j in range(isd, len(jd)) if L[j - 1] < lsr <= L[j]), None)
            sp = RetroSpan(name, glyph, _dt(jsr), _dt(jsd), lsr % 360, lsd % 360,
                           _dt(pre) if pre is not None else None, _dt(post) if post is not None else None)
            if (sp.pre or sp.sr) < hi and (sp.post or sp.sd) >= lo:
                spans.append(sp)
        # サイン移動: 日ごとのサイン番号が変わる日 → その1日を1時間刻みで
        sign = np.floor(L / 30)
        for i in np.nonzero(sign[:-1] != sign[1:])[0]:
            hj = jd[i] + hours[:25]
            hl = np.rad2deg(np.unwrap(np.deg2rad(_lon_arr(key, TS.tt_jd(hj)))))
            hl += 360 * np.round((L[i] - hl[0]) / 360)  # L と同じ巻き数にそろえる
            hs = np.floor(hl / 30)
            k = np.nonzero(hs[:-1] != hs[1:])[0]
            jj = hj[k[0] + 1] if len(k) else jd[i + 1]
            when = _dt(jj)
            if lo <= when < hi:
                ingresses.append(Ingress(name, glyph, when, int(sign[i + 1]) % 12, bool(sign[i + 1] < sign[i])))
    ingresses.sort(key=lambda x: x.when)
    return spans, ingresses


@dataclass
class SunPhase:
    name: str
    glyph: str
    when: datetime  # UTC
    kind: str  # 内合 / 外合 / 合 / 衝
    lon: float  # その時の惑星の黄経


def sun_phases(start: datetime, end: datetime) -> list[SunPhase]:
    """[start, end) の太陽との会合。水星・金星は内合/外合、火星〜冥王星は合/衝（日ごと→1時間刻み）"""
    import numpy as np

    j0 = TS.from_datetime(start.replace(tzinfo=timezone.utc)).tt
    j1 = TS.from_datetime(end.replace(tzinfo=timezone.utc)).tt
    jd = np.arange(math.floor(j0) - 1, math.ceil(j1) + 1, 1.0)
    hours = np.arange(0, 25) / 24.0
    sun = _lon_arr("sun", TS.tt_jd(jd))
    lo, hi = start.replace(tzinfo=timezone.utc), end.replace(tzinfo=timezone.utc)
    out = []
    for name, glyph, key in MUNDANE:
        inner = name in ("水星", "金星")
        lon = _lon_arr(key, TS.tt_jd(jd))
        for target in (0.0, 180.0):
            if inner and target:
                continue  # 内惑星は衝にならない
            d = (lon - sun - target + 180) % 360 - 180  # 目標角との差 (-180,180]
            for i in np.nonzero((np.sign(d[:-1]) != np.sign(d[1:])) & (np.abs(d[:-1]) < 20))[0]:
                hj = jd[i] + hours
                hl = _lon_arr(key, TS.tt_jd(hj))
                hd = (hl - _lon_arr("sun", TS.tt_jd(hj)) - target + 180) % 360 - 180
                k = int(np.argmin(np.abs(hd)))
                when = _dt(hj[k])
                if not lo <= when < hi:
                    continue
                if inner:
                    kind = "内合" if _speed_arr(key, hj[k:k + 1])[0] < 0 else "外合"
                else:
                    kind = "衝" if target else "合"
                out.append(SunPhase(name, glyph, when, kind, float(hl[k])))
    out.sort(key=lambda x: x.when)
    return out


# 期間キー → (表示名, 日数)
PERIODS = {"week": ("今週", 7), "month": ("今月", 0), "year": ("今年", 0)}
SOCIAL = PLANETS[4:]  # 火星〜冥王星（天体どうしのアスペクトを見る組）


def period_range(period: str, day) -> tuple[datetime, datetime]:
    """その日を含む期間の [初日, 翌期間の初日)（ローカル日付の naive datetime）"""
    d = datetime(day.year, day.month, day.day)
    if period == "week":
        a = d - timedelta(days=d.weekday())
        return a, a + timedelta(days=7)
    if period == "month":
        a = d.replace(day=1)
        return a, (a + timedelta(days=32)).replace(day=1)
    a = d.replace(month=1, day=1)
    return a, a.replace(year=a.year + 1)


def social_aspects(start_utc: datetime, end_utc: datetime, max_orb: float = 1.0, bodies=SOCIAL):
    """期間中に火星〜冥王星どうしが正確な角度になる（オーブ最小になる）日。日ごとに見る"""
    import numpy as np

    j0 = TS.from_datetime(start_utc.replace(tzinfo=timezone.utc)).tt
    j1 = TS.from_datetime(end_utc.replace(tzinfo=timezone.utc)).tt
    jd = np.arange(j0 - 1, j1 + 2, 1.0)  # 端の極小も拾えるよう前後1日
    lons = {name: _lon_arr(key, TS.tt_jd(jd)) for name, _g, key in bodies}
    glyph = {name: g for name, g, _k in bodies}
    out = []
    names = list(lons)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = np.abs((lons[a] - lons[b] + 180) % 360 - 180)  # 0〜180
            for asp, asym, ang, _orb in ASPECTS:
                o = np.abs(d - ang)
                for k in range(1, len(jd) - 1):
                    if o[k] <= max_orb and o[k] <= o[k - 1] and o[k] < o[k + 1] and j0 <= jd[k] < j1:
                        out.append((_dt(jd[k]), f"{glyph[a]}{a} {asym}{asp} {glyph[b]}{b}", float(o[k])))
    out.sort(key=lambda x: x[0])
    return out


def summarize_mundane(period: str, start_local: datetime, end_local: datetime) -> str:
    """社会の星読み用の事実データ。日付はローカル表記（社会タブはブラインド評価に使わないので日付を出してよい）"""
    tz = datetime.now().astimezone().tzinfo
    a = start_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    b = end_local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    loc = lambda dt: dt.astimezone(tz).strftime("%m/%d")  # noqa: E731
    label, _ = PERIODS[period]
    lines = [f"[期間] {label}: {start_local:%Y-%m-%d} 〜 {end_local - timedelta(days=1):%Y-%m-%d}"]

    ch = compute_chart(a + timedelta(hours=12))
    lines.append("\n[期間初日の天体（月を除く）]")
    for bd in ch.bodies:
        if bd.name != "月":
            lines.append(f"- {bd.label()}")

    spans, ing = mundane_calendar(a - timedelta(days=1), b)
    t0 = a.replace(tzinfo=timezone.utc)
    lines.append("\n[期間初日の時点で逆行中・シャドウ中]")
    st = []
    for s in spans:
        if s.sr <= t0 < s.sd:
            st.append(f"- {s.glyph}{s.name} 逆行中（{loc(s.sr)}から {loc(s.sd)}まで）")
        elif s.pre and s.pre <= t0 < s.sr:
            st.append(f"- {s.glyph}{s.name} 逆行前のシャドウ（{loc(s.sr)}に逆行開始）")
        elif s.post and s.sd <= t0 < s.post:
            st.append(f"- {s.glyph}{s.name} 逆行後のシャドウ（{loc(s.post)}に明ける）")
    lines += st or ["- なし"]

    lines.append("\n[期間中の出来事（留・シャドウ・サイン移動・太陽との会合）]")
    ev = []
    t1 = b.replace(tzinfo=timezone.utc)
    for s in spans:
        for when, what in ((s.pre, "シャドウ入り"), (s.sr, "逆行開始（留）"), (s.sd, "順行に戻る（留）"), (s.post, "シャドウ明け")):
            if when and t0 <= when < t1:
                ev.append((when, f"{s.glyph}{s.name} {what}"))
    for i in ing:
        # 年単位だと水星・金星・火星のサイン移動は多すぎるので外惑星だけ
        if t0 <= i.when < t1 and (period != "year" or i.name in OUTER):
            what = "逆行で戻る" if i.retro else "入る"
            ev.append((i.when, f"{i.glyph}{i.name} {SIGNS[i.sign_index][0]}に{what}"))
    for ph in sun_phases(a, b):
        ev.append((ph.when, f"{ph.glyph}{ph.name} 太陽と{ph.kind}（{SIGNS[int(ph.lon // 30)][0]}{int(ph.lon % 30)}°）"))
    ev.sort(key=lambda e: e[0])
    lines += [f"- {loc(w)} {t}" for w, t in ev] or ["- なし"]

    # 年単位だと火星がらみが多すぎるので木星〜冥王星だけ
    who = "木星〜冥王星" if period == "year" else "火星〜冥王星"
    lines.append(f"\n[期間中に正確になる天体どうしのアスペクト（{who}・オーブ1°以内）]")
    asp = social_aspects(a, b, bodies=SOCIAL[1:] if period == "year" else SOCIAL)
    lines += [f"- {loc(w)} {t}" for w, t, _o in asp] or ["- なし"]
    return "\n".join(lines)


if __name__ == "__main__":
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ch = compute_chart(now, 35.68, 139.77)
    for b in ch.bodies:
        print(b.label(), f"{b.speed:+.3f}°/日")
    print("ASC", ch.asc, "MC", ch.mc)
