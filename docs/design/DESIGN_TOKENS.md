# Piano Sync — 디자인 토큰

구현 위치: `pianosync/web/static/styles.css`의 `:root` 및 `:root[data-theme="light"]`.
문서와 코드가 어긋나면 **코드가 기준**이며, 변경 시 이 문서를 함께 갱신한다.

## 1. Color

의미 기반 이름을 사용한다. 라이트 테마는 `:root[data-theme="light"]`에서 재정의한다.

| Token | 역할 | Dark | Light |
|---|---|---|---|
| `--bg` | 앱 배경 | `#0a0b0d` | `#f7f8fa` |
| `--bg-rail` | 레일/상태바 | `#0d0f12` | `#f1f3f6` |
| `--surface` | 카드·시트 | `#121417` | `#ffffff` |
| `--surface-2` | 보조 표면 | `#16191d` | `#f5f7f9` |
| `--hover` | hover/선택 | `#1b1f24` | `#eceff3` |
| `--sunken` | 입력·타임라인 | `#08090b` | `#eaeef3` |
| `--border` | 기본 구분선 | `#23272e` | `#e3e7ec` |
| `--border-2` | 강조 테두리 | `#2f353d` | `#ccd3db` |
| `--fg` | 기본 텍스트 | `#e7e9ec` | `#14171c` |
| `--fg-2` | 보조 텍스트 | `#a2a8b0` | `#4c545e` |
| `--fg-3` | 캡션/메타 | `#828a93` | `#666e78` |
| `--ok` | 성공 | `#3ecf8e` | `#157a4c` |
| `--warn` | 경고 | `#e3b341` | `#8a6508` |
| `--bad` | 오류 | `#f2727b` | `#c63b46` |
| `--info` | 진행/정보 | `#62b6f0` | `#2f7fc1` |
| `--ring` | 포커스 링 | `rgba(120,190,255,.40)` | `rgba(40,120,220,.35)` |
| `--backdrop` | 시트 배경 | `rgba(0,0,0,.55)` | `rgba(18,24,33,.38)` |
| `--control-border-hover` | 입력 hover | `#3a4149` | `#b7bfc9` |
| `--btn-primary-hover` | primary hover | `#ffffff` | `#2a3037` |
| `--notice-ai-fg` | AI 안내 텍스트 | `#bcdcf7` | `#1c5c8c` |

**데이터 색**(연주 레인/배지): `--lane-1..--lane-5`로 정의하고 인덱스로 순환한다(=`SOURCE_COLORS`). 배지/레인 위 텍스트는 `--on-lane`.

| Lane | Dark | Light | Light 대비(타임라인 배경) |
|---|---|---|---|
| 1 | `#34d399` | `#047857` | 4.7:1 |
| 2 | `#fbbf24` | `#b45309` | 4.3:1 |
| 3 | `#f472b6` | `#be123c` | 5.4:1 |
| 4 | `#38bdf8` | `#0369a1` | 5.1:1 |
| 5 | `#a3e635` | `#4d7c0f` | 4.3:1 |

> 다크 팔레트(네온 계열)는 라이트 배경에서 1.6–2.3:1로 비텍스트 최소 대비(3:1)에 미달해, 라이트 테마는 같은 색상 계열의 어두운 변형을 쓴다. `--on-lane`은 다크 `#0a0b0d`, 라이트 `#ffffff`.

## 2. Typography

| Token | 값 | 용도 |
|---|---|---|
| `--fs-caption` | 12px | 캡션/메타/범례 (최소 크기) |
| `--fs-sm` | 13px | 보조 텍스트·버튼 |
| `--fs-body` | 14px | 본문 |
| `--fs-num` | 15px | 통계 수치(모노) |
| `--fs-title` | 18px | 화면 제목·패널 제목(h1) |
| `--fs-label` | 13px | 폼 라벨·레일 제목 |
| `--fs-display` | 22px | (예약) 대형 수치 |
| `--sans` | Pretendard Variable + 시스템 폴백 | 본문 |
| `--mono` | `ui-monospace`/SF Mono/Menlo | 시간·수치 |

숫자는 `font-variant-numeric: tabular-nums`. **모든 텍스트는 12px 이상**을 유지한다(이전 10.5–11.5px 캡션은 모바일에서 판독성이 떨어져 상향). 스타일시트는 하드코딩 대신 위 토큰을 사용한다.

> **서체**: Pretendard Variable(jsdelivr, 한글+라틴 단일 패밀리)을 먼저 쓰고, 로드 실패 시 시스템 스택(SF Pro/Segoe UI/Roboto + Apple SD Gothic Neo/Malgun Gothic)으로 폴백한다. 이전 구성은 라틴만 Inter 웹폰트, 한글은 시스템 폰트로 렌더되어 한 화면에서 두 서체가 섞였다. 아이콘 크기는 `.ic` 16px(기본) / 18px(아이콘 버튼) / 14px(상태바 등 밀집) / `.ic-lg` 28px(빈 상태)로 통일하고 stroke 두께는 1.8 하나만 쓴다.

숫자는 `font-variant-numeric: tabular-nums`. **모든 텍스트는 12px 이상**을 유지한다(이전 10.5–11.5px 캡션은 모바일에서 판독성이 떨어져 상향). 스타일시트는 하드코딩 대신 위 토큰을 사용한다.

### 반경(radius)

| Token | 값 | 용도 |
|---|---|---|
| `--r-sm` | 8px | 컨트롤·작은 칩·배지 |
| `--r` | 10px | 버튼·입력·구간 행 |
| `--r-lg` | 14px | 카드·소스 행·시트 |

`999px`(알약·스위치·진행 바), `50%`(원형), `2px`(범례 스와치)는 의도된 예외다.

## 3. Spacing (4px 기반)

`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 20 · `--sp-6` 24 · `--sp-8` 32 · `--sp-10` 40

기존 컴포넌트 값은 이 스케일에 대응한다(예: 카드 패딩 16 = `--sp-4`, 필드 간격 14 ≈ `--sp-3`+2).
새 컴포넌트는 가급적 토큰을 사용한다.

## 4. Border / Shadow

| Token | 값 | 용도 |
|---|---|---|
| `--shadow-pop` | dark `0 12px 32px -12px rgba(0,0,0,.7)` / light `…rgba(20,30,50,.22)` | 팝오버·시트만 |

테두리는 기본 1px. 그림자는 콘텐츠 카드에 쓰지 않는다.
반경은 위 **반경(radius)** 표(8 / 10 / 14px)를 따른다.

### 상태 틴트 / 브랜드

| Token | 값 | 용도 |
|---|---|---|
| `--ok-line` / `--ok-tint` | dark `rgba(62,207,142,.35/.08)` · light `rgba(21,122,76,.42/.07)` | 기준 태그·성공 토스트 |
| `--ok-ring` | dark `rgba(62,207,142,.50)` · light `rgba(21,122,76,.78)` | 기준 소스 행 링 (배경 대비 ≥4.5:1) |
| `--info-line` / `--info-tint` | dark `rgba(98,182,240,.28/.07)` · light `rgba(47,127,193,.38/.07)` | AI 안내 |
| `--warn-line` / `--warn-tint` | dark `rgba(227,179,65,.24/.07)` · light `rgba(138,101,8,.38/.07)` | 경고 목록 |
| `--bad-line` | dark `rgba(242,114,123,.45)` · light `rgba(198,59,70,.45)` | 오류 토스트 |
| `--brand-bg` / `--brand-fg` | `#0a0b0d` / `#ffffff` (테마 공통) | 브랜드 마크 — `icon.svg`와 동일한 모양 |

> 상태 틴트는 이전에 다크 팔레트 rgba()를 고정으로 썼고, 라이트 배경에서 "기준" 링이 거의 보이지 않았다(≈1.7:1). 이제 테마별 토큰을 쓴다.


## 5. Control / Touch

| Token | 값 | 비고 |
|---|---|---|
| `--control-md` | 34px | 데스크톱 컨트롤 높이 |
| `--tap` | **44px** | coarse pointer 최소 터치 타깃 (HIG 44pt) |

Material 3는 48dp를 권장하므로, 여유가 있는 주요 버튼은 48px까지 키울 수 있다. 현재 구현은 44px 이상을 보장한다.

## 6. Motion

| Token | 값 |
|---|---|
| `--dur-fast` | .12s |
| `--dur` | .18s |
| `--dur-slow` | .24s |
| `--ease` | cubic-bezier(.2,.7,.2,1) |

`prefers-reduced-motion: reduce`에서 전환/패널·토스트 애니메이션을 제거한다(스피너 등 필수 진행 표시는 유지).

## 7. Layout / Safe areas

| Token | 값 |
|---|---|
| `--rail-w` | 236px (데스크톱 레일) |
| `--content-max` | 1160px |
| `--safe-top/bottom/left/right` | `env(safe-area-inset-*, 0px)` |

`viewport-fit=cover`와 함께 사용. `100dvh`로 모바일 뷰포트 높이 대응.

## 8. Stacking

| Token | 값 | 용도 |
|---|---|---|
| `--z-toast` | 50 | 토스트 |
| `--z-dialog` | 60 | (네이티브 `<dialog>`는 top layer 사용) |

## 9. 테마

- `html[data-theme="dark"|"light"]`
- 설정: 자동 / 다크 / 라이트 (`localStorage: pianosync-theme`)
- 자동은 `prefers-color-scheme`을 따르고, 변경/포커스 복귀 시 재해석
- `<head>` 인라인 스크립트로 첫 페인트 전 적용(FOUC 방지)
- `meta[name=theme-color]` 동기화
