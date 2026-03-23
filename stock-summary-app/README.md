# Stock Summary App

이전 대화에서 진행했던 주식 요약 앱 구조를 이 워크스페이스에 재구성한 프로젝트입니다.

## 중요: 맥이 꺼져 있어도 / 다른 Wi‑Fi에서 보려면

`http://localhost:8001` 같은 주소는 **그 컴퓨터 안에서만** 동작합니다.  
**맥 전원이 꺼져 있거나**, **같은 Wi‑Fi가 아닐 때** 핸드폰이나 다른 PC에서 주소만 치고 보려면, 앱을 **인터넷에 있는 서버(클라우드)**에 한 번 배포해야 합니다.

이 저장소에는 그걸 위한 **Dockerfile**과 **Render Blueprint 예시**가 포함되어 있습니다.

배포가 끝나면 예를 들어 `https://내서비스.onrender.com` 처럼 **고정 URL**이 생기고, 그 주소는 어디서나 브라우저로 열 수 있습니다.

- 웹 대시보드: `https://배포주소/` 또는 `https://배포주소/web`
- API 헬스: `https://배포주소/api/health`
- JSON: `https://배포주소/kr/daily-report`, `/us/daily-report`

> 무료 호스팅(Render Free 등)은 **일정 시간 요청이 없으면 잠들었다가** 첫 접속이 느릴 수 있습니다. 항상 즉시 응답이 필요하면 유료 플랜을 검토하세요.

---

## 구성

- `backend`: FastAPI 기반 KR/US 일일 리포트 API + 웹 대시보드
- `mobile`: Expo(React Native) 기반 모바일 앱

---

## 1) 로컬에서 백엔드 실행

```bash
cd stock-summary-app/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

확인:

- `http://localhost:8000/` — 웹 대시보드
- `http://localhost:8000/api/health` — JSON 헬스
- `http://localhost:8000/kr/daily-report`, `/us/daily-report`

환경 변수 (선택):

- `PORT` — 기본 `8000` (Docker/호스팅에서 자동 설정되는 경우 많음)
- `CORS_ORIGINS` — 기본 `*` (특정 도메인만 허용 시 `https://a.com,https://b.com`)

---

## 2) Docker로 실행 (로컬에서 클라우드와 동일 환경 테스트)

```bash
cd stock-summary-app/backend
docker build -t stock-summary-api .
docker run --rm -p 8000:8000 -e PORT=8000 stock-summary-api
```

브라우저: `http://localhost:8000/`

---

## 3) Render.com에 배포 (추천: 무료로 시작)

1. [Render](https://render.com) 가입 후 GitHub에 이 저장소 연결
2. **Blueprint** 로 `stock-summary-app/render.yaml` 추가 (또는 **Web Service** 수동 생성)
3. 수동 생성 시:
   - **Environment**: Docker
   - **Dockerfile 경로**: `stock-summary-app/backend/Dockerfile`
   - **Docker context**: `stock-summary-app/backend`
   - **Health Check Path**: `/api/health`
4. 배포 완료 후 나온 `https://xxxx.onrender.com` 을 브라우저·휴대폰 어디서나 사용

`render.yaml`은 이 모노레포 루트 기준 경로로 작성되어 있습니다. **backend만 따로 저장소**로 쓰는 경우 `dockerfilePath` / `dockerContext` 를 `./` 로 수정하세요.

---

## 4) Fly.io 배포 (선택)

```bash
cd stock-summary-app/backend
fly launch   # fly.toml 의 app 이름을 본인 것으로 변경
fly deploy
```

---

## 5) 모바일 앱 (Expo)

```bash
cd stock-summary-app/mobile
cp .env.example .env
# .env 안의 EXPO_PUBLIC_API_BASE 를 배포한 https://주소 로 수정
npm install
npm run start
```

로컬만 쓸 때는 `.env` 없이 기본값 `http://localhost:8000` 입니다.

---

## 제한 사항

- 뉴스/번역(Google News, `googletrans`)은 **클라우드 IP에서 차단**되거나 불안정할 수 있습니다. 그 경우 시세는 나오고 뉴스 헤드라인만 비어 있을 수 있습니다.
- 투자 조언이 아닌 정보 요약용으로만 사용하세요.
