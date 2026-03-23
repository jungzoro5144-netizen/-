import { StatusBar } from "expo-status-bar";
import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

type Market = "KR" | "US";

type Move = {
  ticker: string;
  name: string;
  close: number;
  prev_close: number;
  change_pct: number;
  volume: number;
};

type Reason = {
  ticker: string;
  summary_ko: string;
  headlines_ko: string[];
};

type Forecast = {
  short_term: string;
  mid_term: string;
  long_term: string;
};

type DailyReport = {
  market: Market;
  market_name_ko: string;
  generated_at: string;
  top_market_cap: Move[];
  top_market_cap_reasons: Reason[];
  top_gainers: Move[];
  top_losers: Move[];
  movers_reasons: Reason[];
  forecasts: Record<string, Forecast>;
};

/** 배포 URL: mobile/.env 에 EXPO_PUBLIC_API_BASE=https://your-app.onrender.com */
const API_BASE = (
  process.env.EXPO_PUBLIC_API_BASE ?? "http://localhost:8000"
).replace(/\/$/, "");

export default function App() {
  const [market, setMarket] = useState<Market>("KR");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const fetchReport = async (nextMarket: Market) => {
    setLoading(true);
    setError(null);
    try {
      const path = nextMarket === "KR" ? "/kr/daily-report" : "/us/daily-report";
      const res = await fetch(`${API_BASE}${path}`);
      if (!res.ok) {
        throw new Error(`API 오류: ${res.status}`);
      }
      const data = (await res.json()) as DailyReport;
      setReport(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "알 수 없는 오류");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setSelectedTicker(null);
    void fetchReport(market);
  }, [market]);

  const reasonMap = useMemo(() => {
    const map = new Map<string, Reason>();
    if (!report) return map;
    [...report.top_market_cap_reasons, ...report.movers_reasons].forEach((r) => {
      if (!map.has(r.ticker)) map.set(r.ticker, r);
    });
    return map;
  }, [report]);

  const selectedReason = selectedTicker ? reasonMap.get(selectedTicker) : null;
  const selectedForecast = selectedTicker && report ? report.forecasts[selectedTicker] : null;
  const selectedMove = selectedTicker
    ? report?.top_market_cap.find((m) => m.ticker === selectedTicker) ||
      report?.top_gainers.find((m) => m.ticker === selectedTicker) ||
      report?.top_losers.find((m) => m.ticker === selectedTicker)
    : null;

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.title}>주식 요약 리포트</Text>
        <Text style={styles.subtitle}>한국/미국 마감 데이터 요약</Text>
      </View>

      <View style={styles.marketSwitch}>
        {(["KR", "US"] as Market[]).map((m) => (
          <Pressable
            key={m}
            style={[styles.marketButton, market === m && styles.marketButtonActive]}
            onPress={() => setMarket(m)}
          >
            <Text style={[styles.marketButtonText, market === m && styles.marketButtonTextActive]}>
              {m === "KR" ? "대한민국" : "미국"}
            </Text>
          </Pressable>
        ))}
      </View>

      {loading && (
        <View style={styles.centerBox}>
          <ActivityIndicator size="large" color="#93c5fd" />
          <Text style={styles.helperText}>리포트 불러오는 중...</Text>
        </View>
      )}

      {error && (
        <View style={styles.centerBox}>
          <Text style={styles.errorText}>오류: {error}</Text>
          <Text style={styles.helperText}>백엔드 주소/API 실행 상태를 확인하세요.</Text>
        </View>
      )}

      {!loading && !error && report && (
        <ScrollView contentContainerStyle={styles.scrollContent}>
          <Section
            title="시가총액 상위 10"
            items={report.top_market_cap}
            onPress={(ticker) => setSelectedTicker(ticker)}
          />
          <Section
            title="상승률 상위 10"
            items={report.top_gainers}
            onPress={(ticker) => setSelectedTicker(ticker)}
          />
          <Section
            title="하락률 상위 10"
            items={report.top_losers}
            onPress={(ticker) => setSelectedTicker(ticker)}
          />
        </ScrollView>
      )}

      <Modal visible={!!selectedTicker} animationType="slide" transparent>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalBody}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {selectedMove?.name ?? ""} ({selectedTicker})
              </Text>
              <Pressable onPress={() => setSelectedTicker(null)}>
                <Text style={styles.closeText}>닫기</Text>
              </Pressable>
            </View>

            <ScrollView>
              <Text style={styles.label}>당일 지표</Text>
              <Text style={styles.modalText}>
                종가 {selectedMove?.close ?? "-"} / 전일대비 {selectedMove?.change_pct ?? "-"}% / 거래량{" "}
                {selectedMove?.volume ?? "-"}
              </Text>

              <Text style={styles.label}>주요 사유 요약</Text>
              <Text style={styles.modalText}>{selectedReason?.summary_ko ?? "요약 데이터 없음"}</Text>

              <Text style={styles.label}>관련 뉴스(번역)</Text>
              {(selectedReason?.headlines_ko ?? []).map((h) => (
                <Text key={h} style={styles.bullet}>
                  - {h}
                </Text>
              ))}

              <Text style={styles.label}>전망</Text>
              <Text style={styles.modalText}>단기: {selectedForecast?.short_term ?? "-"}</Text>
              <Text style={styles.modalText}>중기: {selectedForecast?.mid_term ?? "-"}</Text>
              <Text style={styles.modalText}>장기: {selectedForecast?.long_term ?? "-"}</Text>
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function Section({
  title,
  items,
  onPress,
}: {
  title: string;
  items: Move[];
  onPress: (ticker: string) => void;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {items.map((item) => (
        <Pressable key={`${title}-${item.ticker}`} onPress={() => onPress(item.ticker)} style={styles.card}>
          <View>
            <Text style={styles.cardTitle}>
              {item.name} ({item.ticker})
            </Text>
            <Text style={styles.cardMeta}>종가 {item.close}</Text>
          </View>
          <Text style={[styles.changeText, item.change_pct >= 0 ? styles.up : styles.down]}>
            {item.change_pct >= 0 ? "+" : ""}
            {item.change_pct.toFixed(2)}%
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0b1020" },
  header: { paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 },
  title: { color: "white", fontSize: 24, fontWeight: "700" },
  subtitle: { color: "#93a4c3", marginTop: 4 },
  marketSwitch: { flexDirection: "row", paddingHorizontal: 16, gap: 8, marginBottom: 8 },
  marketButton: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 8,
    backgroundColor: "#1d2740",
  },
  marketButtonActive: { backgroundColor: "#2563eb" },
  marketButtonText: { color: "#cbd5e1", fontWeight: "600" },
  marketButtonTextActive: { color: "white" },
  centerBox: { alignItems: "center", justifyContent: "center", paddingVertical: 32 },
  helperText: { color: "#9ca3af", marginTop: 10 },
  errorText: { color: "#fca5a5", fontWeight: "600" },
  scrollContent: { padding: 16, gap: 14, paddingBottom: 40 },
  section: { backgroundColor: "#101933", borderRadius: 12, padding: 12 },
  sectionTitle: { color: "white", fontSize: 16, fontWeight: "700", marginBottom: 10 },
  card: {
    backgroundColor: "#16213f",
    borderRadius: 10,
    padding: 10,
    marginBottom: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  cardTitle: { color: "white", fontWeight: "600" },
  cardMeta: { color: "#93a4c3", fontSize: 12, marginTop: 3 },
  changeText: { fontWeight: "700", fontSize: 15 },
  up: { color: "#60a5fa" },
  down: { color: "#f87171" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.45)", justifyContent: "flex-end" },
  modalBody: {
    height: "80%",
    backgroundColor: "#0f172a",
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 24,
  },
  modalHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 12 },
  modalTitle: { color: "white", fontSize: 18, fontWeight: "700" },
  closeText: { color: "#93c5fd", fontSize: 16, fontWeight: "600" },
  label: { color: "#cbd5e1", marginTop: 12, marginBottom: 6, fontWeight: "700" },
  modalText: { color: "#e5e7eb", lineHeight: 20 },
  bullet: { color: "#cbd5e1", marginBottom: 4 },
});
