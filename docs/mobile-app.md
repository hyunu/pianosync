# iPhone / Android에서 앱처럼 쓰기

Piano Sync 웹 에디션은 **PWA(Progressive Web App)** 로 이미 앱 형태 설치를 지원합니다.
분석·렌더링(FFmpeg)은 **서버가 돌아가는 컴퓨터(Mac/PC)** 에서만 실행되고, 휴대폰은
**브라우저(또는 홈 화면 아이콘)** 로 그 서버에 접속하는 구조입니다.

```
[ iPhone / Android ]  ── Wi‑Fi ──►  [ Mac 또는 PC : pianosync.web + FFmpeg ]
        UI만                              업로드 · 분석 · 렌더 · 파일 저장
```

휴대폰 단독으로 FFmpeg 없이 “완전 오프라인 네이티브 앱”으로 바꾸려면 엔진 이식 또는
클라우드 백엔드가 필요해 범위가 크게 달라집니다. 아래는 **지금 구조를 유지하면서**
앱처럼 쓰는 방법입니다.

## 1. 같은 Wi‑Fi에서 서버 열기 (필수)

Mac/PC에서:

```bash
cd piano-switcher
source .venv/bin/activate
python -m pianosync.web --host 0.0.0.0 --port 8765
```

터미널에 **같은 Wi‑Fi의 휴대폰용 URL**(예: `http://192.168.0.12:8765`)이 함께 출력됩니다.
방화벽에서 **8765 포트** 인바운드를 허용해야 할 수 있습니다.

## 2. iPhone (Safari)

1. Safari에서 위 `http://192.168.x.x:8765` 주소를 엽니다.
2. **공유** → **홈 화면에 추가**.
3. 홈 화면의 **Piano Sync** 아이콘으로 실행하면 주소창 없이 **standalone** UI로 열립니다.

Safari는 “앱 설치” 배너를 자동으로 띄우지 않으므로, 웹 UI 상단의 **앱 설치 안내**를 참고하세요.

## 3. Android (Chrome)

1. Chrome에서 같은 LAN URL을 엽니다.
2. 메뉴(**⋮**) → **앱 설치** / **홈 화면에 추가** (기기·버전에 따라 문구가 다름).
3. 또는 화면 하단 **앱으로 설치** 배너(Chrome `beforeinstallprompt`)를 사용합니다.

## 4. 이미 포함된 PWA 요소

- `manifest.webmanifest` — `display: standalone`, 아이콘 192/512
- `sw.js` — UI 셸 캐시(네트워크 우선). `/api/*` 는 항상 서버 직통
- iOS 메타 태그 · `apple-touch-icon` · `viewport-fit=cover`(노치 safe area)

**오프라인**에서는 UI 껍데기만 캐시될 수 있고, 분석·렌더는 온라인(서버 접속)이 필요합니다.

## 5. App Store / Play Store에 올리고 싶다면

| 방식 | 난이도 | 설명 |
|------|--------|------|
| **PWA만 (스토어 없음)** | 낮음 | 위 1~3단계. 개인·스튜dio용에 적합 |
| **TWA (Android)** | 중간 | HTTPS 공개 URL + Digital Asset Links. 백엔드를 인터넷에 두거나 터널 필요 |
| **Capacitor / WebView 셸** | 중~높음 | 네이티브 프로젝트가 WebView로 **서버 URL** 로드. 스토어 심사·서명 필요. “서버 주소 설정” 화면을 앱에 넣는 패턴이 일반적 |
| **완전 네이티브** | 매우 높음 | FFmpeg·DTW를 iOS/Android에 이식하거나 API 서버 분리 |

스토어 배포를 진행할 때는 **고정 HTTPS 엔드포인트** 또는 **앱 내 서버 URL 설정** 중 하나를
먼저 정해야 합니다. LAN 전용 URL만 쓰는 PWA는 스토어용 WebView 앱과 잘 맞습니다.

## 6. 보안 참고

`--host 0.0.0.0` 은 **같은 네트워크의 다른 기기**도 접속할 수 있습니다. 공용 Wi‑Fi에서는
포트 노출을 피하거나 VPN/터널 뒤에 두세요. Gemini API 키는 서버 측 `~/.pianosync/web/settings.json` 에
저장됩니다.
