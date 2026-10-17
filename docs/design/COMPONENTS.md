# Piano Sync — 공통 컴포넌트

모든 화면은 아래 컴포넌트를 조합해 구성한다. 새 UI가 필요하면 먼저 이 목록에서 재사용 가능한 것을 찾는다.
구현: `index.html` + `styles.css` + `app.js`.

## AppBar
- 목적: 브랜드, 설정 진입, 새 프로젝트.
- 구성: 브랜드(마크+이름+태그라인), 우측 `아이콘 버튼(설정)` + `새 프로젝트`.
- 브랜드 마크는 `icon.svg`와 같은 모양(어두운 타일 + 흰 파형, 30px, `--r-sm`)으로 두 테마에서 동일하다.
- 반응형: ≤760px에서 태그라인 숨김, 브랜드 `min-width:0`으로 밀림 방지.
- 접근성: 설정 버튼 `aria-label="설정"`, 아이콘 전용.

## Button
- 변형: `btn-primary`(주요), `btn-ghost`(보조), `btn-sm`, `btn-icon`, `btn-wide`.
- 상태: default / hover / focus-visible / disabled / (필요 시 loading은 스피너+비활성).
- 터치: coarse pointer에서 최소 44px.
- 규칙: 한 화면에 primary는 하나. 파괴적 동작은 ghost+bad 색.
- `btn-ghost`는 기본 상태에서 테두리·배경 없음(hover/focus에서만 배경). 화면이 테두리 박스로 가득 차 보이지 않게 한다.
- 높이는 `--control-md`(데스크톱 34px) / coarse 44px 토큰만 사용한다.

## Icon
- 크기 4단계: `.ic` 16px(기본·인라인), `--btn-icon` 18px(아이콘 버튼·브랜드), `.ic-sm`/문맥 14px(상태바·구간 체크·토스트), `.ic-lg` 28px(빈 상태 히어로).
- stroke 두께는 **1.8** 하나(빈 상태만 1.5). 24×24 viewBox, round cap/join.
- 모든 아이콘은 `stroke="currentColor"`, `fill: none` — 색은 텍스트 색을 따른다.


## SegmentedControl (테마)
- 목적: 배타적 선택(자동/다크/라이트).
- 구현: `<div role="group">` + `<button aria-pressed>`.
- 상태: 선택(`is-active`, `aria-pressed=true`) / 비선택 / focus / hover.
- 터치: coarse에서 44px.

## Field / Input / Select / Switch
- 라벨은 항상 텍스트로 노출(`.field-label`).
- 입력 높이 34px, coarse에서 44px.
- focus: `box-shadow: 0 0 0 3px var(--ring)`.
- Switch: 시각 트랙 34×20, 실제 라벨 전체가 히트 영역. coarse에서 44px.
- 비활성: `disabled` + `useAi`처럼 의미가 있으면 툴팁/상태로 이유 전달.
- `textarea`(`#overlayText`): `min-height: 62px`, `resize: vertical`, `line-height: 1.5`,
  입력/선택과 동일한 테두리·`--r`(10px). coarse에서 16px 폰트로 iOS 자동 확대 방지.
- `#overlayFontPathRow`: 글꼴 메뉴에서 `직접 지정`을 고를 때만 `hidden`이 풀리는 경로 입력
  (`.field-wide` 그대로, 내용이 바뀌어도 레이아웃이 튀지 않게 같은 폭 유지).
- `.field-note`: 필드 아래 보조 설명, `--fs-caption` + `--fg-3`.

## Card
- 목적: 의미 단위 그룹(설정, 구간, 결과).
- 1px 테두리, `--r-lg`(14px) 라운드, 16px 패딩. 그림자 없음.
- `card-flush`: 내부에 타임라인처럼 풀블리드 콘텐츠가 있을 때.

## CaptionCard (영상에 글자 넣기)
- 목적: 렌더 결과에 구워 넣을 글자를 입력(영상 오버레이).
- 구성: `#overlayText`(textarea, `maxlength=200`, `#overlayCount` 카운터) +
  `글꼴`(`#overlayFont`)·`위치`(`top`/`center`/`bottom`)·`크기`(`small`/`medium`/`large`)·
  `색상`(`white`/`black`) 셀렉트.
- **빈 문자열 = 오버레이 끔.** 별도 on/off 토글을 두지 않아 상태가 하나로 유지된다.
- `글꼴` 목록은 서버가 `/api/config`의 `overlay_fonts`로 내려준다. 설치된 폰트를 스캔해
  한글 지원 글꼴을 먼저, 영문 전용 글꼴을 `· 영문` 라벨로 뒤에 둔다. 마지막 항목
  `직접 지정 (__custom__)`은 경로 입력(`#overlayFontPathRow`)을 펼치는 트리거일 뿐이고,
  렌더 요청에는 실제 경로가 전송된다.
- 렌더는 서버에서 Pillow로 투명 PNG를 (출력 크기에 맞춰) 래스터라이즈한 뒤 FFmpeg
  `overlay`로 합성한다(`drawtext` 필터가 없는 FFmpeg 빌드에서도 동작).
  가독성을 위해 글자색 반대색 외곽선을 항상 함께 그린다.

## SourceRow
- 구성: 색 배지(A/B/C…), 파일명·메타, 태그(기준), 제거 버튼, 업로드 진행 바.
- 상태: 기본 / 기준(`is-ref`) / 업로드 중 / 호버 / 제거 비활성.
- 접근성: 제거 버튼 `aria-label="소스 제거"`, 터치 44px.

## AddSource (dropzone)
- 목적: 클릭/드래그로 영상 추가.
- 상태: default / hover / drag / disabled(최대 개수).
- 접근성: `<button>`.

## RunBar (분석 진행)
- 목적: 분석 중 진행 메시지 + 취소.
- 상태: hidden / 진행 / 오류. `[hidden]`로 제어.
- 접근성: 취소는 버튼, 진행은 상태바·텍스트로 전달.

## ProgressBar
- 두 종류: 상태바(2px), 렌더(6px). 장식 요소로 `aria-hidden`, 수치는 텍스트(`renderPct`, `statusMsg`)로 전달.
- indeterminate 상태 지원.

## Waveform (canvas)
- 목적: 정렬 결과·구간·신뢰도·재생 위치 시각화.
- 상태: empty(차트 숨김 + 안내) / ready.
- 상호작용: 드래그/탭 탐색, 키보드 ←/→.
- 테마: 다크/라이트 팔레트 분리(`wavePalette()`).
- 접근성: `role="img"` + 설명, 키보드 탐색은 문서 레벨에서 제공.

## SegmentRow (구간)
- 구성: 소스 인덱스, 시간 범위·in-point, 사유, 신뢰도 바(+ % 라벨).
- 상태: 기본 / hover / 현재(`is-current`).
- 접근성: `role="button"`, `tabindex=0`, Enter/Space로 이동.
- 신뢰도는 툴팁이 아니라 항상 보이는 % 텍스트로 표시한다(터치 환경에서 hover 불가).
- 소스 행은 배경 `--surface-2`로 구분하고 기본 테두리는 없다(hover `--border-2`, 기준 소스는 `--ok-ring`).

## Legend
- 파형 레인 색, 저신뢰, 매핑 곡선 설명. 색+텍스트 병행.

## Toast
- 목적: 성공/오류 피드백.
- 컨테이너 `role="status" aria-live="polite"`.
- 4.2s 후 사라짐. 오류는 색+아이콘+텍스트.

## Dialog / BottomSheet
- 구현: 네이티브 `<dialog>` + `showModal()`.
- 데스크톱: 중앙 모달(최대 440px).
- 모바일(≤760px): **하단 시트**(전체 폭, 상단 `--r-lg`(14px) 라운드, safe-area 하단 패딩).
- 접근성: `aria-labelledby`, ESC 닫기, 폼 submit, 포커스 이동.

## StatusBar
- 목적: 상태(대기/작업/완료/오류), 메시지, 핵심 수치(길이/구간/신뢰도).
- safe-area 하단 패딩. `aria-live`는 토스트가 담당, 상태 텍스트는 시각 정보.

## EmptyState
- 타임라인/결과의 빈 상태. 아이콘+설명, 과한 일러스트 없음.
- 데이터 없을 때 차트 자체를 숨긴다.

## Tag / Badge
- 기준 표시, 상태(완료), 개수. 색+텍스트.
