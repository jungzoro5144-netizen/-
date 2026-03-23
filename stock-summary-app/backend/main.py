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

COMPANY_TYPE_KR: Dict[str, str] = {
    "005930.KS": "반도체/전자부품(메모리·시스템반도체) + 스마트폰·가전",
    "000660.KS": "반도체(주로 D램·낸드 메모리)",
    "035420.KS": "인터넷 서비스(검색·포털·광고) 및 콘텐츠",
    "051910.KS": "석유화학 + 2차전지 소재(배터리 핵심 부품)",
    "005380.KS": "자동차 제조 + 모빌리티(완성차)",
    "207940.KS": "바이오의약품 위탁생산(CDMO)",
    "006400.KS": "2차전지(리튬이온 배터리) 및 소재",
    "035720.KS": "플랫폼(메신저·콘텐츠) + 디지털 광고",
    "068270.KS": "바이오의약품(바이오시밀러/항체치료제)",
    "012330.KS": "자동차 부품(모듈/핵심 부품) + 전장(전기부품)",
}

COMPANY_TYPE_US: Dict[str, str] = {
    "AAPL": "소비자 전자제품 + 서비스(스마트기기·생태계)",
    "MSFT": "소프트웨어 + 클라우드(Azure, 기업용 서비스)",
    "NVDA": "AI 반도체/가속기(GPU) + 데이터센터 인프라",
    "AMZN": "전자상거래 + 클라우드(AWS)",
    "GOOGL": "검색/유튜브 + 광고/클라우드(GCP)",
    "META": "소셜 플랫폼 + 광고(메타버스/인스타·페이스북)",
    "BRK-B": "보험 + 대규모 투자(지주/투자회사)",
    "TSLA": "전기차 + 에너지(배터리/전력 솔루션)",
    "LLY": "제약/바이오(의약품 개발·생산)",
    "AVGO": "반도체 인프라(네트워킹/SoC) + 기업용 칩",
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

# 뉴스 신뢰도 필터(홍보성 기사 제외 / 검증 가능한 근거 위주)
ACADEMIC_SOURCES = {
    "nature",
    "science",
    "cell",
    "lancet",
    "nejm",
    "new england journal of medicine",
    "arxiv",
    "ieee",
    "acm",
    "ssrn",
    "pubmed",
}

# 경영실적/가이던스/IR(공식 발표)처럼 "1차 자료에 가까운" 출처를 우선
OFFICIAL_IR_SOURCES = {
    "sec.gov",
    "prnewswire",
    "globenewswire",
    "businesswire",
    "business wire",
    "reuters",
    "bloomberg",
    "financial times",
    "ft",
    "cnbc",
    "yahoo finance",
    "marketwatch",
}

EARNINGS_KEYWORDS_KR = [
    "실적",
    "매출",
    "영업이익",
    "순이익",
    "잠정",
    "실적발표",
    "가이던스",
    "전망",
    "컨퍼런스콜",
    "ir",
    "투자설명회",
]

EARNINGS_KEYWORDS_US = [
    "earnings",
    "revenue",
    "guidance",
    "quarter",
    "results",
    "conference call",
    "investor",
    "press release",
    "sec",
]

TECH_PROOF_KEYWORDS = [
    "study",
    "research",
    "paper",
    "published",
    "clinical",
    "trial",
    "randomized",
    "peer-reviewed",
    "phase 1",
    "phase 2",
    "breakthrough",
    "validated",
    "benchmark",
    "evidence",
]

MARKETING_KEYWORDS = [
    "launch",
    "unveils",
    "announces",
    "partnership",
    "signs",
    "strategic cooperation",
    "memorandum",
    "excited to",
]


def _news_classify(source: str, title_raw: str, market: Market) -> Dict[str, Any]:
    source_l = (source or "").lower()
    title_l = (title_raw or "").lower()

    score = 0
    tags: List[str] = []

    # 학술/논문 성격
    if any(a in source_l for a in ACADEMIC_SOURCES) or any(a in title_l for a in ACADEMIC_SOURCES):
        score += 5
        tags.append("학술/논문 성격")

    # IR/공식 발표 성격
    if any(o in source_l for o in OFFICIAL_IR_SOURCES):
        score += 3
        tags.append("공신력 출처(보도/IR)")

    # 기술/연구 검증 키워드
    if any(k in title_l for k in TECH_PROOF_KEYWORDS):
        score += 4
        tags.append("검증/연구 키워드")

    # 실적/가이던스/컨콜 키워드
    earnings_keywords = EARNINGS_KEYWORDS_KR if market == "KR" else EARNINGS_KEYWORDS_US
    if any(k in title_l for k in earnings_keywords):
        score += 5
        tags.append("실적/가이던스/컨콜")

    # 홍보성 키워드 감점(완전한 제외가 아니라, 신뢰도 낮추는 용도)
    if any(m in title_l for m in MARKETING_KEYWORDS):
        score -= 3
        tags.append("홍보성 가능성(감점)")

    # 기준 통과: 신뢰도 높은 키워드가 충분히 묶일 때만 기본 승인
    # 단, 실적/가이던스/컨콜 키워드는 1차 성격이 강해 예외적으로 더 넓게 허용
    accepted = (score >= 7) or ("실적/가이던스/컨콜" in tags)
    return {"score": score, "tags": tags, "accepted": accepted}

_translator = Translator() if Translator is not None else None
NEWS_CACHE_TTL_SECONDS = 600
REPORT_CACHE_TTL_SECONDS = 600
MOVES_CACHE_TTL_SECONDS = 60
_news_cache: Dict[Tuple[Market, str], Tuple[float, Dict[str, Any]]] = {}
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
    company_type: str
    close: float
    prev_close: float
    change_pct: float
    volume: int


def _forecast_ko(m: Move, market: Market) -> Dict[str, Dict[str, str]]:
    comps = (COMPETITORS_KR if market == "KR" else COMPETITORS_US).get(m.ticker, [])
    comps_str = ", ".join(comps[:2]) if comps else "동종 기업"
    is_up = m.change_pct >= 0
    magnitude = abs(m.change_pct)
    momentum = "강한 모멘텀(가격이 한 방향으로 밀어주는 힘)" if magnitude >= 2.0 else "완만한 모멘텀"

    # 용어 해설을 문장 안에 괄호로 붙여 초보자도 바로 이해할 수 있게 구성
    def glossary_terms() -> str:
        return (
            "용어해설: 수급(주식을 사는/파는 흐름), 가이던스(회사가 제시하는 향후 실적 전망), "
            "마진(이익률), 밸류에이션(주가가 기업가치를 얼마나 반영하는지), "
            "설비투자(공장/설비에 쓰는 투자), 운전자본(일상 운영에 필요한 돈), 현금흐름(돈의 실제 유입/유출)."
        )

    if is_up:
        short_summary = "단기: 긍정 우세(모멘텀 지속) vs 변동성 확대 가능"
        short_details = (
            f"단기(1주) 전망:\n"
            f"- 긍정: {momentum}이 유지되면 추세가 이어질 가능성이 있습니다.\n"
            f"  왜 긍정적으로 보나? 시장 기대가 빠르게 반영되며, {comps_str} 대비 ‘좋은 해석’이 더 빨리 가격에 들어오기 때문입니다.\n"
            f"- 부정: 급등 구간에서는 차익실현(이미 오른 만큼 되팔기)과 변동성 확대가 동반될 수 있습니다.\n"
            f"  왜 부정적으로 보나? 같은 호재라도 경쟁사 대비 상대 모멘텀이 약해지면 자금이 분산될 수 있기 때문입니다.\n"
            f"{glossary_terms()}"
        )
    else:
        short_summary = "단기: 반등 여지 vs 하방 압력(실망 뉴스/경쟁 약세)"
        short_details = (
            f"단기(1주) 전망:\n"
            f"- 긍정: 핵심 재료가 흔들리지 않으면 기술적 반등과 수급 안정 가능성이 있습니다.\n"
            f"  왜 긍정적으로 보나? 과도한 우려가 먼저 가격에 반영됐다면, {comps_str} 대비 하방이 제한될 수 있습니다.\n"
            f"- 부정: 하락이 ‘확정적인 실망(가이던스 하향, 실적 미스 등)’으로 이어지면 단기 반등이 지연될 수 있습니다.\n"
            f"  왜 부정적으로 보나? 경쟁사 대비 상대 매력이 낮아 보이면 회복 자금이 늦게 유입될 수 있습니다.\n"
            f"{glossary_terms()}"
        )

    mid_summary = "중기(1~3개월): 실적/가이던스 확인으로 재평가 가능"
    mid_details = (
        "중기(1~3개월) 전망:\n"
        f"- 긍정: 실적과 가이던스(향후 실적 전망), 업황 데이터가 쌓이면 시장의 눈높이가 재정렬될 수 있습니다.\n"
        f"  왜 긍정적으로 보나? {comps_str} 대비 마진(이익률)·수요·라인업(제품 구성) 또는 공급망(원자재~생산~판매 연결)이 우호적으로 나타나기 쉽기 때문입니다.\n"
        "- 부정: 금리/환율/원가(외생 변수)가 실적 가시성을 흔들 수 있습니다.\n"
        f"  왜 부정적으로 보나? 외생 변수 악화 시 경쟁사({comps_str})와의 상대 비용 구조 차이가 중요해지는데, 격차가 크지 않으면 탄력이 줄 수 있습니다.\n"
        f"{glossary_terms()}"
    )

    long_summary = "장기(1년+): 경쟁우위 누적 vs 산업 사이클 리스크"
    long_details = (
        "장기(1년+) 전망:\n"
        "- 긍정: 기술/브랜드/공급망처럼 경쟁우위가 누적되면 현금흐름이 안정될 여지가 있습니다.\n"
        f"  왜 긍정적으로 보나? {comps_str} 대비 차별화가 ‘반복 가능한 이익’으로 연결될 때 밸류에이션이 안정되기 때문입니다.\n"
        "- 부정: 산업 사이클이 꺾이면 성장 프리미엄이 줄어들 수 있습니다.\n"
        f"  왜 부정적으로 보나? 경쟁사가 유사 전략으로 따라오면 격차가 축소되고, 설비투자·운전자본 부담이 커질 수 있기 때문입니다.\n"
        f"{glossary_terms()}"
    )

    return {
        "short_term": {"summary": short_summary, "details": short_details},
        "mid_term": {"summary": mid_summary, "details": mid_details},
        "long_term": {"summary": long_summary, "details": long_details},
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


def _fetch_news_for_stock(m: Move, market: Market, limit: int = 3) -> Dict[str, Any]:
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
        empty = {"items": [], "meta": {"total": 0, "accepted_total": 0, "rejected_total": 0, "used_fallback": False}}
        _news_cache[cache_key] = (now, empty)
        return empty

    try:
        root = ET.fromstring(res.text)
    except Exception:
        empty = {"items": [], "meta": {"total": 0, "accepted_total": 0, "rejected_total": 0, "used_fallback": False}}
        _news_cache[cache_key] = (now, empty)
        return empty

    candidates: List[Dict[str, Any]] = []
    total = 0
    accepted_total = 0
    rejected_total = 0

    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        raw_link = (item.findtext("link") or "").strip()
        parts = raw_title.rsplit(" - ", 1)
        title_raw = unescape(parts[0].strip())
        source = unescape(parts[1].strip()) if len(parts) > 1 else "Unknown"

        cls = _news_classify(source, title_raw, market)
        accepted = bool(cls.get("accepted"))
        score = int(cls.get("score", 0))
        tags = cls.get("tags", [])

        candidates.append(
            {
                "source": source,
                "title_raw": title_raw,
                "url": raw_link,
                "accepted": accepted,
                "score": score,
                "tags": tags,
            }
        )
        total += 1
        if accepted:
            accepted_total += 1
        else:
            rejected_total += 1

    accepted_items = [c for c in candidates if c["accepted"]]
    used_fallback = False

    if accepted_items:
        picked = sorted(accepted_items, key=lambda x: x["score"], reverse=True)[:limit]
    else:
        # 홍보성 가능성이 있는 항목은 최후까지 제외(요청사항 반영)
        non_marketing = [
            c
            for c in candidates
            if "홍보성 가능성(감점)" not in c.get("tags", [])
            and c.get("score", 0) >= 0
        ]
        if non_marketing:
            used_fallback = True
            picked = sorted(non_marketing, key=lambda x: x.get("score", 0), reverse=True)[:limit]
        else:
            picked = []

    translated: List[Dict[str, Any]] = []
    for p in picked:
        title_ko = _translate_to_ko(p.get("title_raw") or "")
        translated.append(
            {
                "source": p["source"],
                "title_raw": p.get("title_raw") or "",
                "title_ko": title_ko,
                "url": p.get("url") or "",
                "score": p.get("score", 0),
                "tags": p.get("tags", []),
                "accepted": p.get("accepted", False),
            }
        )

    result = {
        "items": translated,
        "meta": {
            "total": total,
            "accepted_total": accepted_total,
            "rejected_total": rejected_total,
            "used_fallback": used_fallback,
            "picked_count": len(translated),
        },
    }
    _news_cache[cache_key] = (now, result)
    return result


def _reason_ko(m: Move, market: Market) -> Dict[str, object]:
    market_name = "국내" if market == "KR" else "미국"
    news_result = _fetch_news_for_stock(m, market)
    headlines_detailed = news_result.get("items", [])
    meta = news_result.get("meta", {}) or {}
    headlines_ko = [f"[출처: {h['source']}] {h['title_ko']}" for h in headlines_detailed]
    is_up = m.change_pct > 0

    total = int(meta.get("total", 0) or 0)
    accepted_total = int(meta.get("accepted_total", 0) or 0)
    rejected_total = int(meta.get("rejected_total", 0) or 0)
    used_fallback = bool(meta.get("used_fallback", False))

    filter_note = (
        f"검증 결과: 전체 {total}개 후보 중 공신력 기준 통과 {accepted_total}개, 제외 {rejected_total}개. "
        f"{'대체 기준을 사용했습니다(통과 항목이 부족)' if used_fallback else '통과 항목 위주로 사용했습니다'}.\n"
        "검증 기준(요약): "
        "학술/논문 성격 키워드 또는 검증(동료심사) 키워드, "
        "투자자 관계(공식 발표)/실적·가이던스(공식 발표) 성격 키워드를 우선 반영하고, "
        "‘홍보성’ 가능성이 높은 표현은 감점/제외합니다.\n"
        "아래 기사들은 클릭해서 원문 내용을 직접 확인할 수 있습니다.\n"
        "용어해설: 수급(사람들이 주식을 사는/파는 흐름), 가이던스(회사가 앞으로의 실적을 제시하는 범위/전망), "
        "투자자 관계(회사와 투자자 사이의 공식 공지)."
    )

    if headlines_detailed:
        topic = "; ".join([h["title_ko"] for h in headlines_detailed[:2]])
        if is_up:
            summary_ko = (
                f"{m.name}는 전일 대비 {m.change_pct:+.2f}% 상승했습니다. "
                f"관련(통과된) 뉴스 핵심 포인트는 {topic} 입니다."
            )
            details_ko = (
                "상승한 이유(추정)는 보통 '기대(기사)' + '수급 반응(사람들의 매수/매도)'이 동시에 나타날 때입니다.\n"
                f"(1) 기사/근거: {headlines_detailed[0]['title_ko']}\n"
                "(2) 수급: 좋은 소식이 나오면 투자자들이 먼저 사고(매수), 가격이 끌어올려지는 경향이 있습니다.\n"
                f"{filter_note}"
            )
        else:
            summary_ko = (
                f"{m.name}는 전일 대비 {m.change_pct:+.2f}% 하락했습니다. "
                f"관련(통과된) 뉴스 핵심 포인트는 {topic} 입니다."
            )
            details_ko = (
                "하락한 이유(추정)는 보통 '기대가 낮아짐' 또는 '불확실성이 커짐'이 가격에 먼저 반영될 때 나타납니다.\n"
                f"(1) 기사/근거: {headlines_detailed[0]['title_ko']}\n"
                "(2) 수급: 나쁜 해석/실망이 나오면 사람들이 팔거나(매도) 새로 사는 속도가 줄어들어 하락이 커질 수 있습니다.\n"
                f"{filter_note}"
            )
    else:
        # 통과 항목이 없으면 홍보성/저신뢰를 보여주지 않기 위해 빈 근거를 유지
        if is_up:
            summary_ko = (
                f"{m.name}는 전일 대비 {m.change_pct:+.2f}% 상승했지만, "
                "요청하신 '공신력/검증' 기준을 통과한 기사 근거가 부족했습니다."
            )
            details_ko = (
                "이 경우는 두 가지 가능성이 큽니다.\n"
                "(1) 회사/업계에 실제로 큰 이슈가 있었지만, 기사 RSS에서 '검증된 형태'로 잡히지 않았거나\n"
                "(2) 지수/섹터 내 전반 수급, 금리·환율 등 거시 요인이 더 크게 작용했을 수 있습니다.\n"
                f"{filter_note}"
            )
        else:
            summary_ko = (
                f"{m.name}는 전일 대비 {m.change_pct:+.2f}% 하락했지만, "
                "요청하신 '공신력/검증' 기준을 통과한 기사 근거가 부족했습니다."
            )
            details_ko = (
                "이 경우는 보통 (1) 기대가 낮아지는 유형의 정보가 있었지만 공신력 기준으로 분류되지 않았거나, "
                "(2) 지수/섹터 내 자금 흐름이 먼저 꺾였기 때문일 수 있습니다.\n"
                f"{filter_note}"
            )

    return {
        "ticker": m.ticker,
        "name": m.name,
        "company_type": m.company_type,
        "summary_ko": summary_ko,
        "headlines_ko": headlines_ko,
        "details_ko": details_ko,
        "headlines_detailed": headlines_detailed,
    }


def _mock_moves(universe: Dict[str, str], market: Market) -> List[Move]:
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
    company_type_map = COMPANY_TYPE_KR if market == "KR" else COMPANY_TYPE_US
    for i, (ticker, name) in enumerate(universe.items()):
        close = float(100 + i * 8)
        pct = seed[i % len(seed)]
        prev = close / (1 + pct / 100)
        out.append(
            Move(
                ticker=ticker,
                name=name,
                company_type=company_type_map.get(ticker, "업종 미정"),
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
        mocked = _mock_moves(universe, market)
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
        mocked = _mock_moves(universe, market)
        _moves_cache[market] = (now, mocked)
        return mocked

    moves: List[Move] = []
    company_type_map = COMPANY_TYPE_KR if market == "KR" else COMPANY_TYPE_US
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
                    company_type=company_type_map.get(ticker, "업종 미정"),
                    close=round(close, 2),
                    prev_close=round(prev_close, 2),
                    change_pct=round(change_pct, 2),
                    volume=volume,
                )
            )
        except Exception:
            continue

    result = moves or _mock_moves(universe, market)
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
    top_gainers = sorted([m for m in moves if m.change_pct > 0], key=lambda x: x.change_pct, reverse=True)[:10]
    top_losers = sorted([m for m in moves if m.change_pct < 0], key=lambda x: x.change_pct)[:10]
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
