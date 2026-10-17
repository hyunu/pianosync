# Piano Sync — 모바일 설치형 앱 (Capacitor)

APK/IPA로 **독립 설치**할 수 있는 셸입니다. WebView가 Piano Sync **웹 UI**에 접속합니다.

> **Mac/PC 없이 폰만으로 분석·렌더**하는 “완전 단독”은 아직 없습니다.  
> 계획은 [`docs/mobile-standalone.md`](../docs/mobile-standalone.md) 참고.

## 사전 요구

- Node.js 18+
- Android: Android Studio, SDK  
- iOS: Xcode (macOS), Apple Developer 계정(실기기/스토어)

## 설치·동기화

```bash
cd mobile
npm install
npx cap add android    # 최초 1회
npx cap add ios        # macOS, 최초 1회
npx cap sync
```

## PC + 휴대폰 (현재 방식)

1. PC: `python -m pianosync.web --host 0.0.0.0 --port 8765`
2. 앱 실행 → 서버 URL에 `http://<PC의 LAN IP>:8765` 입력 → 연결
3. 이후 저장된 주소로 자동 접속

## Android 빌드 (디버그 APK)

```bash
cd mobile
npx cap open android
# Android Studio: Run ▶ 또는 Build → Build APK(s)
```

HTTP(LAN) 허용을 위해 `AndroidManifest`에 cleartext가 필요하면 Capacitor 6 `cleartext: true` 설정을 확인하세요.

## iOS

`npx cap open ios` → Xcode에서 서명 후 Run.

**App Transport Security**: LAN `http://` 접속을 위해 `Info.plist`에 예외가 필요할 수 있습니다.  
(개발: `NSAppTransportSecurity` → `NSAllowsLocalNetworking` = true 권장)

## 앱 ID

`studio.pianosync.app` — 스토어 등록 전 본인 팀 ID로 `capacitor.config.json`에서 변경하세요.
