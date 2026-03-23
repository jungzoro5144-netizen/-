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
_news_cache: Dict[Tuple[Market, str], Tuple[float, List[Dict[str, str]]]] = {}
_moves_cache: Dict[Market, Tuple[float, List[Move]]] = {}
_report_cache: Dict[Market, Tuple[float, Dict[str, Any]]] = {}

# 간단한 경쟁/비교 기업 목록(템플릿용). 실제 기업 분석 데이터는 별도 수집이 필요하지만,
# UI에서 '왜 긍정/부정인지' 문장이 반복되지 않도록 최소한의 비교 기준을 제공합니다.
COMPETITORS_KR: Dict[str, List[str]] = {
    "005930.KS": ["SK하이닉스(메모리 경쟁)", "마이크론(글로벌 경쟁)"],
    "000660.KS": ["삼성전자(메모리 경쟁)", "마이크론(글로벌 경쟁)"],
    "035420.KS": ["카카오(플랫폼/광고 경쟁)", "네이버(트래픽/콘텐츠 경쟁)"],
    "051910.KS": ["LG에너지솔루션(배터리 공급망 경쟁)", "SK온(수요/라인업 경쟁)"],
    "005380.KS": ["현대글로비스(물류 경쟁)", "기아(완성차 경쟁)"],
    "207940.KS": ["로슈/화이자(바이오 경쟁)", "삼성바이오로직스(내부/생산 경쟁)"],
    "006400.KS": ["삼성SDI(배터리 경쟁)", "LG에너지솔루션(수요 경쟁)"],
    "035720.KS": ["네이버(플랫폼 경쟁)", "카카오게임즈(콘텐츠 경쟁)"],
    "068270.KS": ["셀트리온제약(내부/바이오 경쟁)", "바이오시밀러 경쟁사"],
    "012330.KS": ["현대모비스(부품/공급 경쟁)", "부품 협력사"],
}

COMPETITORS_US: Dict[str, List[str]] = {
    "AAPL": ["MSFT(생태계/소프트웨어 경쟁)", "GOOGL(플랫폼/광고 경쟁)"],
    "MSFT": ["AMZN(클라우드 경쟁)", "GOOGL(검색/AI 경쟁)"],
    "NVDA": ["AMD(반도체 경쟁)", "인텔(대체 수요 경쟁)"],
    "AMZN": ["MSFT(클라우드 경쟁)", "GOOGL(검색/클라우드 경쟁)"],
    "GOOGL": ["MSFT(AI/클라우드 경쟁)", "META(광고/메타버스 경쟁)"],
    "META": ["GOOGL(광고 경쟁)", "SNAP/틱톡 계열(콘텐츠 경쟁)"],
    "BRK-B": ["MS(대형 투자사)", "T. Rowe(유사 자산운용)"],
    "TSLA": ["GM/Ford(완성차 경쟁)", "BYD(글로벌 EV 경쟁)"],
    "LLY": ["PFE(면역/제약 경쟁)", "NVO(경쟁)"],
    "AVGO": ["AMD/인텔(반도체 경쟁)", "다른 인프라 벤더"],
}


@dataclass
class Move:
    ticker: str
    name: str
    close: float
    prev_close: float
    change_pct: float
    volume: int


def _forecast_ko(m: Move, market: Market) -> Dict[str, str]:
    comps = (COMPETITORS_KR if market == "KR" else COMPETITORS_US).get(m.ticker, [])
    comps_str = ", ".join(comps[:2]) if comps else "동종 기업"
    up = m.change_pct >= 0
    magnitude = abs(m.change_pct)
    momentum = "강한 모멘텀" if magnitude >= 2.0 else "완만한 모멘텀"

    if up:
        short_term = (
            f"긍정: {momentum}이 유지되면 단기 추세가 이어질 가능성이 있습니다.\n"
            f"왜 긍정적으로 보나? 시장 기대가 빠르게 반영되고, {comps_str} 대비 '해석의 속도'가 더 빠를 수 있습니다.\n"
            f"부정: 다만 단기 급등 구간에서는 차익실현과 변동성 확대가 동반될 수 있습니다.\n"
            f"왜 부정적으로 보나? 같은 호재라도 경쟁사 대비 상대 모멘텀이 약해지면 자금이 분산될 수 있기 때문입니다."
        )
    else:
        short_term = (
            f"긍정: 하락 이후에도 핵심 재료가 유지되면 기술적 반등/수급 안정이 가능할 수 있습니다.\n"
            f"왜 긍정적으로 보나? 과도한 우려가 먼저 가격에 반영됐다면, {comps_str} 대비 하방이 제한될 수 있습니다.\n"
            f"부정: 하락이 '확정 뉴스'로 촉발됐다면 단기 반등이 지연될 수 있습니다.\n"
            f"왜 부정적으로 보나? 상대 매력이 경쟁사보다 낮아 보이면 회복 자금이 늦게 유입될 수 있습니다."
        )

    mid_term = (
        "긍정: 중기(1~3개월)에서는 실적/가이던스/업황이 실제 데이터로 확인되며, 시장의 눈높이가 재정렬될 가능성이 있습니다.\n"
        f"왜 긍정적으로 보나? {comps_str} 대비 마진·수요·라인업(또는 공급망) 측면에서 우위가 관측될 수 있기 때문입니다.\n"
        "부정: 다만 금리/환율/원가 같은 외생 변수가 실적 가시성을 흔들 수 있습니다.\n"
        f"왜 부정적으로 보나? 외생 변수 악화 시 경쟁사({comps_str})와의 상대 비용 구조 차이가 중요해지는데, 격차가 제한적이면 탄력이 줄 수 있습니다."
    )

    long_term = (
        "긍정: 장기(1년+)에서는 경쟁우위가 누적되며(기술/브랜드/공급망), 현금흐름이 안정될 여지가 있습니다.\n"
        f"왜 긍정적으로 보나? {comps_str} 대비 차별화 포인트가 '반복 가능한 이익'으로 이어질 때 밸류에이션이 안정될 수 있습니다.\n"
        "부정: 장기에서는 산업 사이클 전환 시 성장 프리미엄이 축소될 위험도 있습니다.\n"
        f"왜 부정적으로 보나? 경쟁사가 유사 전략으로 따라오면 격차가 줄어들 수 있고, CAPEX/운전자본 부담이 커질 수 있기 때문입니다."
    )

    return {"short_term": short_term, "mid_term": mid_term, "long_term": long_term}


def _translate_to_ko(text: str) -> str:
    if not text:
        return text
    if _translator is None:
        return text
    try:
        return _translator.translate(text, dest="ko").text
    except Exception:
        return text


def _fetch_news_for_stock(m: Move, market: Market, limit: int = 3) -> List[Dict[str, str]]:
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

    headlines: List[Dict[str, str]] = []
    fallback: List[Dict[str, str]] = []
    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        raw_link = (item.findtext("link") or "").strip()
        parts = raw_title.rsplit(" - ", 1)
        title = unescape(parts[0].strip())
        source = unescape(parts[1].strip()) if len(parts) > 1 else "Unknown"

        source_l = source.lower()
        if any(key in source_l for key in MAJOR_SOURCES):
            headlines.append({"source": source, "title_raw": title, "url": raw_link})
        else:
            fallback.append({"source": source, "title_raw": title, "url": raw_link})

        if len(headlines) >= limit:
            break

    picked = headlines if headlines else fallback[:limit]
    translated: List[Dict[str, str]] = []
    for p in picked:
        title_ko = _translate_to_ko(p.get("title_raw") or "")
        translated.append({"source": p["source"], "title_ko": title_ko, "url": p.get("url") or ""})
    _news_cache[cache_key] = (now, translated)  # type: ignore[arg-type]
    return translated


def _reason_ko(m: Move, market: Market) -> Dict[str, object]:
    market_name = "국내" if market == "KR" else "미국"
    headlines_detailed = _fetch_news_for_stock(m, market)
    headlines_ko = [f"[출처: {h['source']}] {h['title_ko']}" for h in headlines_detailed]
    if headlines_detailed:
        topic = "; ".join([h["title_ko"] for h in headlines_detailed[:2]])
        summary_ko = (
            f"{m.name}({m.ticker})은 전일 대비 {m.change_pct:+.2f}% 변동했습니다. "
            f"최근 주요 뉴스에서는 {topic} 이슈가 확인됩니다."
        )
        details_ko = (
            f"이번 변동은 (1) {headlines_detailed[0]['title_ko']} 같은 핵심 헤드라인의 해석과 "
            f"(2) 그 이후 수급(매수/매도)의 반응이 동시에 반영된 흐름으로 볼 수 있습니다.\n"
            f"참고로 아래 기사들은 실제로 클릭해서 내용을 확인할 수 있습니다."
        )
    else:
        summary_ko = (
            f"{m.name}({m.ticker})은 전일 대비 {m.change_pct:+.2f}% 변동했습니다. "
            f"최근 {market_name} 시장 전반의 수급/실적 기대/금리 이슈가 복합적으로 반영된 흐름으로 해석됩니다."
        )
        details_ko = (
            "기사 기반 근거를 충분히 가져오지 못한 경우, 보통은 지수/섹터 내 수급과 "
            "거시 변수(금리, 환율), 업종 기대/실망이 함께 반영됩니다.\n"
            "이 경우에는 다음 날/주간의 공시·실적 이벤트 캘린더를 함께 확인하는 것이 도움이 됩니다."
        )

    return {
        "ticker": m.ticker,
        "summary_ko": summary_ko,
        "headlines_ko": headlines_ko,
        "details_ko": details_ko,
        "headlines_detailed": headlines_detailed,
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
            m.ticker: _forecast_ko(m, market) for m in (top_market_cap + top_gainers + top_losers)
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
