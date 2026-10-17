# 휴대폰 단독 설치·단독 동작 로드맵

## 목표 두 가지 (구분)

| 목표 | 의미 | 현재 |
|------|------|------|
| **설치형 앱** | APK/IPA로 스토어·사이드로드 설치, 브라우저 북마크 불필요 | `mobile/` Capacitor 셸 |
| **단독 동작** | Mac/PC 없이 **폰 안에서** 업로드·분석·렌더 | **미구현** (아래 로드맵) |

지금 Piano Sync의 두뇌는 **Python + NumPy/SciPy + FFmpeg** 입니다. 브라우저/PWA는 UI만 담당하고
서버가 없으면 분석·렌더가 불가능합니다.

## 단독 동작이 어려운 이유

- **iOS**: 임의 Python 서버·FFmpeg 바이너리 번들에 App Store·보안 제약이 큼.
- **Android**: FFmpeg(ffmpeg-kit)·임베디드 Python(Chaquopy 등) 조합은 **가능**하지만 별도 네이티브
  프로젝트와 패키징 작업이 필요합니다.
- **코어 이식**: DTW·전환·렌더 파이프라인 전체를 Swift/Kotlin 또는 WASM으로 옮기면
  데스크톱/웹과 **3벌 유지보수**가 됩니다.

## 권장 단계

### 1단계 — 설치형 셸 (지금 `mobile/`)

- Capacitor APK/IPA.
- 앱 안 WebView가 **서버 주소**(기본 `http://127.0.0.1:8765`)로 접속.
- **같은 Wi‑Fi의 PC** 또는 **추후 폰 내부 서버**에 연결하는 **클라이언트**.

빌드: [`mobile/README.md`](../mobile/README.md)

### 2단계 — Android 단독 (제안)

1. Android Studio 모듈에 **Chaquopy**로 `pianosync` Python 패키지 번들.
2. 앱 시작 시 **루프백**에서 `uvicorn` + FastAPI 기동 (`127.0.0.1:8765`).
3. **ffmpeg-kit** 또는 번들 `ffmpeg` 바이너리로 `pianosync`가 기대하는 `ffmpeg` PATH 충족.
4. Capacitor WebView는 `http://127.0.0.1:8765` 고정 (설정 화면 불필요).

예상 작업량: 네이티브 빌드·ABI·용량(100MB+)·Play 정책 검토 포함 **수 주~수 개월**.

### 3단계 — iOS 단독 (선택지)

| 방안 | 장단점 |
|------|--------|
| **Swift + AVFoundation/VT** 로 핵심만 재구현 | 성능·스토어 친화, 개발량 최대 |
| **WKWebView + Pyodide** | Python 재사용 가능, 앱 용량·속도·메모리 부담 |
| **클라우드 API** | 폰은 클라이언트만, “단독”은 아니나 설치 앱은 단순 |

## 지금 당장 쓸 수 있는 조합

- **설치형 앱 + PC 서버**: `mobile/` APK + PC `--host 0.0.0.0` ( [`mobile-app.md`](mobile-app.md) ).
- **완전 폰 단독**: 2단계(Android) 또는 3단계 착수 전까지 **불가** — 기대치 조정 필요.

## 다음 결정 (Product)

1. **Android만** 먼저 단독을 목표로 할지, iOS도 동시에 할지.
2. 단독이 **오프라인 필수**인지, **설치 앱 + 가끔 Wi‑Fi 없음**인지.
3. 스토어 배포(Play/App Store) vs APK 직배포만.

결정되면 2단계용 `mobile/android-embedded/` Gradle 스캐폴드와 FFmpeg/Chaquopy PoC 이슈를 쪼개면 됩니다.
