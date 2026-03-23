from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import time
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Literal, Tuple

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import httpx

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

try:
    from googletrans import Translator
except Exception:  # pragma: no cover
    Translator = None

Market = Literal["KR", "US"]

KR_UNIVERSE: Dict[str, str] = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "035420.KS": "NAVER",
    "051910.KS": "LG화학",
    "005380.KS": "현대차",
    "207940.KS": "삼성바이오로직스",
    "006400.KS": "삼성SDI",
    "035720.KS": "카카오",
    "068270.KS": "셀트리온",
    "012330.KS": "현대모비스",
}

US_UNIVERSE: Dict[str, str] = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "BRK-B": "Berkshire Hathaway",
    "TSLA": "Tesla",
    "LLY": "Eli Lilly",
    "AVGO": "Broadcom",
}

MAJOR_SOURCES = {
    "reuters",
    "bloomberg",
    "wsj",
    "wall street journal",
    "cnbc",
    "financial times",
    "ft",
    "yahoo finance",
    "marketwatch",
    "연합뉴스",
    "한국경제",
    "매일경제",
    "서울경제",
    "머니투데이",
    "이데일리",
    "조선비즈",
    "아시아경제",
}

_translator = Translator() if Translator is not None else None
NEWS_CACHE_TTL_SECONDS = 600
REPORT_CACHE_TTL_SECONDS = 600
MOVES_CACHE_TTL_SECONDS = 60
_news_cache: Dict[Tuple[Market, str], Tuple[float, List[str]]] = {}
_moves_cache: Dict[Market, Tuple[float, List[Move]]] = {}
_report_cache: Dict[Market, Tuple[float, Dict[str, Any]]] = {}


@dataclass
class Move:
    ticker: str
    name: str
    close: float
    prev_close: float
    change_pct: float
    volume: int


def _forecast_ko(change_pct: float) -> Dict[str, str]:
    tone = "강세" if change_pct > 1.5 else "약세" if change_pct < -1.5 else "중립"
    return {
        "short_term": f"단기(1주)는 {tone} 흐름 가능성이 있습니다. 변동성 확대에 유의하세요.",
        "mid_term": "중기(1~3개월)는 실적/금리/거시지표 확인이 중요합니다.",
        "long_term": "장기(1년+)는 산업 경쟁력과 현금흐름 중심으로 점검하세요.",
    }


def _translate_to_ko(text: str) -> str:
    if not text:
        return text
    if _translator is None:
        return text
    try:
        return _translator.translate(text, dest="ko").text
    except Exception:
        return text


def _fetch_news_for_stock(m: Move, market: Market, limit: int = 3) -> List[str]:
    cache_key = (market, m.ticker)
    now = time.time()
    cached = _news_cache.get(cache_key)
    if cached and (now - cached[0]) < NEWS_CACHE_TTL_SECONDS:
        return cached[1]

    lang = "ko" if market == "KR" else "en"
    region = "KR" if market == "KR" else "US"
    query = f"{m.name} {m.ticker} stock"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={lang}&gl={region}&ceid={region}:{lang}"
    )

    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
            res = client.get(url)
            res.raise_for_status()
    except Exception:
        return []

    try:
        root = ET.fromstring(res.text)
    except Exception:
        return []

    headlines: List[str] = []
    fallback: List[str] = []
    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        parts = raw_title.rsplit(" - ", 1)
        title = unescape(parts[0].strip())
        source = unescape(parts[1].strip()) if len(parts) > 1 else "Unknown"
        line = f"[출처: {source}] {title}"

        source_l = source.lower()
        if any(key in source_l for key in MAJOR_SOURCES):
            headlines.append(line)
        else:
            fallback.append(line)

        if len(headlines) >= limit:
            break

    picked = headlines if headlines else fallback[:limit]
    translated = [_translate_to_ko(h) for h in picked]
    _news_cache[cache_key] = (now, translated)
    return translated


def _reason_ko(m: Move, market: Market) -> Dict[str, object]:
    market_name = "국내" if market == "KR" else "미국"
    headlines_ko = _fetch_news_for_stock(m, market)
    if headlines_ko:
        topic = "; ".join(headlines_ko[:2])
        summary_ko = (
            f"{m.name}({m.ticker})은 전일 대비 {m.change_pct:+.2f}% 변동했습니다. "
            f"최근 주요 뉴스에서는 {topic} 이슈가 확인됩니다."
        )
    else:
        summary_ko = (
            f"{m.name}({m.ticker})은 전일 대비 {m.change_pct:+.2f}% 변동했습니다. "
            f"최근 {market_name} 시장 전반의 수급/실적 기대/금리 이슈가 복합적으로 반영된 흐름으로 해석됩니다."
        )

    return {
        "ticker": m.ticker,
        "summary_ko": summary_ko,
        "headlines_ko": headlines_ko,
    }


def _mock_moves(universe: Dict[str, str]) -> List[Move]:
    seed = [
        2.13,
        -1.52,
        0.78,
        3.24,
        -2.01,
        1.07,
        -0.62,
        2.89,
        -3.08,
        0.51,
    ]
    out: List[Move] = []
    for i, (ticker, name) in enumerate(universe.items()):
        close = float(100 + i * 8)
        pct = seed[i % len(seed)]
        prev = close / (1 + pct / 100)
        out.append(
            Move(
                ticker=ticker,
                name=name,
                close=round(close, 2),
                prev_close=round(prev, 2),
                change_pct=round(pct, 2),
                volume=1_000_000 + i * 123_456,
            )
        )
    return out


def _fetch_moves(universe: Dict[str, str], market: Market) -> List[Move]:
    now = time.time()
    cached = _moves_cache.get(market)
    if cached and (now - cached[0]) < MOVES_CACHE_TTL_SECONDS:
        return cached[1]

    if yf is None:
        mocked = _mock_moves(universe)
        _moves_cache[market] = (now, mocked)
        return mocked

    tickers = list(universe.keys())
    data = yf.download(
        tickers=tickers,
        period="5d",
        interval="1d",
        auto_adjust=False,
        group_by="ticker",
        progress=False,
        threads=True,
    )
    if data is None or len(data) == 0:
        mocked = _mock_moves(universe)
        _moves_cache[market] = (now, mocked)
        return mocked

    moves: List[Move] = []
    for ticker, name in universe.items():
        try:
            frame = data[ticker].dropna()
            if len(frame) < 2:
                continue
            close = float(frame["Close"].iloc[-1])
            prev_close = float(frame["Close"].iloc[-2])
            change_pct = (close - prev_close) / prev_close * 100
            volume = int(frame["Volume"].iloc[-1]) if "Volume" in frame else 0
            moves.append(
                Move(
                    ticker=ticker,
                    name=name,
                    close=round(close, 2),
                    prev_close=round(prev_close, 2),
                    change_pct=round(change_pct, 2),
                    volume=volume,
                )
            )
        except Exception:
            continue

    result = moves or _mock_moves(universe)
    _moves_cache[market] = (now, result)
    return result


def _build_report(market: Market) -> Dict[str, object]:
    now = time.time()
    cached = _report_cache.get(market)
    if cached and (now - cached[0]) < REPORT_CACHE_TTL_SECONDS:
        return cached[1]

    universe = KR_UNIVERSE if market == "KR" else US_UNIVERSE
    market_name = "대한민국" if market == "KR" else "미국"
    moves = _fetch_moves(universe, market)

    top_market_cap = moves[:10]
    top_gainers = sorted(moves, key=lambda x: x.change_pct, reverse=True)[:10]
    top_losers = sorted(moves, key=lambda x: x.change_pct)[:10]
    dedup_moves = {m.ticker: m for m in (top_market_cap + top_gainers + top_losers)}
    reason_map = {ticker: _reason_ko(mv, market) for ticker, mv in dedup_moves.items()}

    report = {
        "market": market,
        "market_name_ko": market_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_market_cap": [m.__dict__ for m in top_market_cap],
        "top_market_cap_reasons": [reason_map[m.ticker] for m in top_market_cap],
        "top_gainers": [m.__dict__ for m in top_gainers],
        "top_losers": [m.__dict__ for m in top_losers],
        "movers_reasons": [reason_map[m.ticker] for m in (top_gainers + top_losers)],
        "forecasts": {
            m.ticker: _forecast_ko(m.change_pct) for m in (top_market_cap + top_gainers + top_losers)
        },
    }
    _report_cache[market] = (now, report)
    return report


app = FastAPI(title="Stock Summary API", version="1.0.0")
WEB_INDEX = Path(__file__).parent / "web" / "index.html"

# 모바일/다른 도메인 웹에서 API 호출 시 필요 (배포 URL은 환경변수 CORS_ORIGINS로 제한 가능)
_cors = os.environ.get("CORS_ORIGINS", "*").strip()
_allow_origins = ["*"] if _cors == "*" else [o.strip() for o in _cors.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _web_dashboard() -> FileResponse:
    return FileResponse(WEB_INDEX)


@app.get("/")
def root_dashboard() -> FileResponse:
    """배포 후 브라우저에 `https://도메인/` 만 입력해도 대시보드 표시."""
    return _web_dashboard()


@app.get("/web")
def web_dashboard() -> FileResponse:
    return _web_dashboard()


@app.get("/api/health")
def api_health() -> Dict[str, str]:
    """로드밸런서·호스팅 헬스체크용 (JSON)."""
    return {"status": "ok", "service": "stock-summary-api"}


@app.get("/kr/daily-report")
def kr_daily_report() -> Dict[str, object]:
    return _build_report("KR")


@app.get("/us/daily-report")
def us_daily_report() -> Dict[str, object]:
    return _build_report("US")
