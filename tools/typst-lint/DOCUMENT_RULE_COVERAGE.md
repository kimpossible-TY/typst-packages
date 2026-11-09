# Typst linter 규칙별 지원 범위

이 표는 규칙 명세의 목표와 현재 구현을 구분하기 위한 코드 대조표다. 33개 규칙 모두에 제한적인 단일 소스 분석기가 있지만, **문서 전체에서 33개 규칙을 완전하게 검사하거나 자동으로 고치는 단계는 아니다.** 검사 결과가 없다는 것은 검사하지 못한 문맥까지 정상이라는 뜻이 아니다. 이 표는 새 테스트 결과나 전체 PDE 문서의 검증 보고서가 아니다.

| 표기 | 의미 |
| --- | --- |
| 단일 소스: 제안 | 제한된 조건에서 메모리 내 수정안을 생성한다. 일반 `fix` 명령이 원본을 쓰거나 컴파일을 승인한다는 뜻은 아니다. 별도 보호된 쓰기 프로필의 대상과 조건은 다르다. |
| 단일 소스: 진단 | 제한된 조건에서 진단한다. 현재 구현의 수정안은 없다. 명세의 장래 수정 정책과 다를 수 있다. |
| 문서: 기본 | `inspect-document` 기본 분석기. PDE 행은 `--rule-set pde`가 필요하다. 부분 검사다. |
| 문서: L | `--rule-set pde --local-rule-hints`의 파일별 조건부 힌트. include 지점의 바인딩·실행 문맥은 증명하지 못하고 수정 권한은 없다. |
| 문서: P | `--rule-set pde --package-path`로 선택한 패키지에 의존하는 조건부 힌트. 실제 런타임 바인딩을 보장하지 않는다. |
| 문서: R | 명시적인 패키지·facade 추적을 설정하고 `--reference-arguments` 또는 `--reference-catalog`를 선택한 조건부 검사. 후자는 별도로 제공한 catalog 스냅샷만 확인한다. |
| 문서: C | `--rule-set pde --preview-conditional-scoped-calls`의 독립적인 제한된 scoped 호출 소스 후보. wrapper 및 실행은 조건부이며 컴파일이나 쓰기 권한은 없다. |
| 미리보기: 기본 | 별도 `preview-compile` 또는 `typst-style compile --preview`의 세 단계 결합 후보에 포함된다. 일반 컴파일 게이트 완성이나 전체 규칙 검사를 뜻하지 않는다. |
| 미리보기: G / P / S | 각각 `--conditional-gaps`, `--conditional-punctuation`, `--conditional-spacers`의 조건부 후보. P와 S는 G도 필요하다. S는 제한된 비정렬 수식 각주 패턴에 적용되며 런타임 바인딩이나 렌더 동일성을 증명하지 않는다. |
| — | 해당 문서 검사 또는 미리보기 경로에서 확인된 지원이 없다. |

## Core 규칙

| 규칙 | 단일 소스 | 문서 검사 | 컴파일 미리보기 | 대표적인 범위와 한계 |
| --- | --- | --- | --- | --- |
| `core.parenthesized-scripts` | 제안 | 기본 | 기본 | 파서로 범위가 확인된 스크립트 피연산자; 불명확한 구문은 보류. |
| `core.scoped-function-call` | 제안 | C | — | 제한된 문자 그대로의 scoped wrapper 콜백·호출 형태. 복잡하거나 지연된 callback은 보류한다. |
| `core.scoped-marker-tags` | 진단 | C | — | 제한된 literal scoped 콜백의 math raw marker label을 조건부 진단한다. wrapper·marker 바인딩과 실행은 미증명이며 외부 참조를 임의로 바꾸지 않는다. |
| `core.scoped-anchor-access` | 제안 | — | — | 검토된 CeTZ 증거와 유일한 마커·방향이 확인된 경우. |
| `core.scoped-reference-access` | 제안 | — | — | 동일 scope와 단일 리터럴 대상이 확인된 경우. |
| `core.local-overlay-api` | 진단 | — | — | 알려진 로컬 annotation 콜백 안의 제한된 raw API 호출. |
| `core.annotation-in-display` | 진단 | L, C | — | 제한된 literal scoped callback과 raw `annot-cetz`·`annot`의 display 밖 직접 호출을 각각 조건부 진단한다. `mannot-scope`와 `local-scope-annotations`의 제한된 literal callback에서는 지연 외부 문맥을 포함해 raw `annot`의 문법상 display 내부를 별도로 세고, 외부 호출은 조건부 진단한다. 별도 `--cetz-annotation-manifest`는 캡처한 CeTZ draw를 쓰는 제한된 scoped 호출을 조건부로 추가 진단한다. helper 바인딩·실행·전이 의존성의 효과·실제 display 소유권은 미증명이며 수식 이동은 추측하지 않는다. |
| `core.annotation-marker-bounds` | 진단 | — | — | 직접 확인한 display와 같은 scope의 순서가 확인된 대상. |
| `core.unique-scope-namespace` | 진단 | C | — | 문서 검사는 include graph의 직접 literal prefix 충돌을 조건부 비교하고 반복 include를 별도 출현으로 센다. 바인딩·실행·순서는 미증명이고 중첩 부모 비교는 미지원이다. |
| `core.project-ref-arguments` | 제안 | R | — | 단일 소스의 제한된 수정에는 유일한 catalog 대상이 필요하다. R은 추적된 호출에서 검토된 scoped 패키지·namespace 출처와 단일 리터럴 인자가 확인된 직접 `(scope.tag)("이름")`을 조건부 진단한다. 호출 바인딩과 콜백 실행은 전제이며, scope 값이 다른 방식으로 사용되거나 출처가 불명확하면 미확인으로 남긴다. 이 문서 진단은 수정안을 생성하지 않는다. |
| `core.project-ref-catalog-target` | 진단 | R | — | 명시적으로 제공한 catalog와 리터럴 대상만; 최신성·런타임 선택은 별도 문제다. |
| `core.single-book-show` | 진단 | — | — | 지정한 루트와 검토된 book 패키지의 제한적인 소유 관계. |
| `core.no-duplicate-book-reset` | 진단 | — | — | 명세는 조건부 제거를 허용하지만 본문 전체의 변환 동등성이 입증되지 않아 현재 수정하지 않는다. |
| `core.no-set-theme` | 진단 | — | — | 검토된 theme 값·helper의 출처가 확인된 경우. |
| `core.theme-in-context` | 진단 | L | — | 알려진 helper와 제한된 closure; 반환값과 모든 호출 지점이 불명확해 context 삽입은 없다. |

## PDE 규칙

| 규칙 | 단일 소스 | 문서 검사 | 컴파일 미리보기 | 대표적인 범위와 한계 |
| --- | --- | --- | --- | --- |
| `pde.import-order` | 제안 | L | — | 명시적 import 그래프와 순수한 리터럴 모듈에서만 안전한 순서 변경. |
| `pde.no-redundant-facade-import` | 제안 | — | — | facade와 원래 바인딩의 동일성이 그래프로 증명된 중복 import. |
| `pde.paragraph-marker-gap` | 제안 | L, P, 독립 미리보기, G 일부 | — | 문서 조건부 힌트와 독립 소스 후보는 같은 본문의 선행 리터럴 산문 또는 content block 열림 직후 LF/CRLF와 들여쓰기만 있는 첫 marker를 지원한다. 별도 제한된 동일 본문 안에서 단독 `#block[...]` 또는 리터럴 `#block(breakable: false)[...]` 직후 한 줄바꿈인 경우도 조건부로 지원한다. 명시적 block 이름 가림이 파일 어디에든 있거나, 주석·동적 옵션·다른 호출·강조·라벨 등이 있으면 미확인으로 보고 보류한다. G 결합 컴파일은 첫 marker 및 block 뒤 경계만 선택하며 다른 미확인 경계가 있으면 이 단계 전체를 보류한다. 별도 pagebreak·flowbox 후보에도 바인딩 조건이 적용되며 렌더가 달라질 수 있고 바인딩과 실행은 미증명이다. |
| `pde.paragraph-marker-target` | 제안 | L | — | 직접 중첩 본문의 제목·display 앞 marker를 조건부 진단하며 `paragraph-tab`도 별도로 포함한다. 바인딩·실행·본문 소유권은 미확인이다. |
| `pde.prose-paragraph-marker` | 제안 | L, 독립 미리보기 | — | 독립 미리보기는 기존의 제한된 리터럴 문단 경계에서 ASCII 대문자로 시작하는 산문 앞에만 마커 삽입을 제안한다. 마커·show 바인딩과 실행은 조건부다. |
| `pde.continuation-line` | 제안 | L, 독립 미리보기 | 선택형 | 제한된 직접 리터럴 문장·어휘와 경계만. `--preview-conditional-continuations`는 소스 줄바꿈을 공백으로 바꾸는 독립 후보를 제안하며, 컴파일 미리보기의 별도 `--conditional-continuations`는 이 후보를 결합해 재검증한다. 실행 문맥과 렌더 동등성은 미증명이다. |
| `pde.display-continuation-gap` | 제안 | 기본 | 기본 | 확인된 display와 직접 이어지는 문장; 주석·불명확한 wrapper는 보류. |
| `pde.proof-trailing-gap` | 제안 | L | G | 알려진 proof helper와 제한된 경계; G는 조건부 후보다. |
| `pde.footnote-trailing-gap` | 제안 | L | G | G 결합 컴파일은 중첩 수식 각주와 기존 `paragraph_tab`, 직접 display 또는 리터럴 `#figure` 앞의 제한된 산문 각주 후보를 조건부 지원한다. 산문 후보는 `#parbreak()`를 삽입하며 helper·figure 바인딩, 실행과 렌더는 미증명이다. 복잡도 사전 한도 또는 다른 근거가 불충분하면 기존 계획만 유지한다. figure와 `paragraph-tab` 후보가 함께 있는 파일에서는 figure도 보류한다. `paragraph-tab`은 별도 소스 미리보기로만 제안한다. |
| `pde.prose-footnote-punctuation` | 제안 | L | P, 선택형 | 각주 앞 공백 제거와 각주 뒤 문장부호 이동은 독립 조건부 미리보기다. 이동은 앞에 수평 공백이 있는 경우와 바로 붙은 경우가 별도이며 후자의 삽입·삭제는 분리할 수 없다. P 결합 컴파일은 공백 제거를 포함하며, 인접 구두점 이동은 별도 선택 시 쌍을 재검증한다. |
| `pde.aligned-footnote-spacer` | 제안 | L, 독립 미리보기 | — | 독립 미리보기는 제한된 정렬 수식 각주에 spacer 삽입을 제안한다. 실제 바인딩과 실행은 조건부이며 렌더가 달라질 수 있다. |
| `pde.unaligned-footnote-no-spacer` | 제안 | L | S | 기존 명세대로 제한된 비정렬 수식 각주 앞 spacer를 제거하는 조건부 후보. 별도 `--conditional-deferred-footnotes`는 검토된 closure의 호출 경로와 캡처를 추가로 확인한다. 런타임 바인딩과 실행은 미증명이며 실제 렌더는 달라질 수 있다. |
| `pde.prose-footnote-no-spacer` | 제안 | L, 독립 미리보기 | — | 독립 미리보기는 중첩 산문에서 기존 구두점에 밀착된 리터럴 spacer와 수평 공백만 제거한다. 실제 바인딩과 실행은 조건부이며 렌더가 달라질 수 있다. |
| `pde.standalone-display` | 제안 | 기본 | 기본 | 파서가 확인한 display와 제한된 직접 문장·라벨. |
| `pde.text-only-highlight` | 제안 | P, 현대 helper 별도 진단 및 독립 미리보기 | 현대 helper 독립·결합 옵션 | P는 기존 검토 helper와 단순 리터럴 본문에 한정된다. `--modern-highlight-evidence`는 정확한 현대 helper artifact의 제한된 본문을 조건부 진단한다. 별도 소스 미리보기는 단순 텍스트·수식과 보충문 없는 직접 `@label`을 지원하며, `--preview-conditional-modern-math-highlights`는 검토된 builtin 수학 호출의 바인딩을 추가로 확인한다. 결합 소스 미리보기는 검토된 수학 호출과 지원되는 범위 지정 참조 후보를 같은 원본에서 함께 재검증한다. 리터럴 각주가 포함된 직접 본문은 별도 옵션으로 원본 기준 독립 후보만 제공하며, 서로 또는 결합 후보와 병합한 결과는 검증하지 않는다. 검토된 scope callback 안의 수학 호출은 지정한 단일 위치에 한해 별도 독립 후보를 제공한다. 제한된 `parent: s` 전달 경로는 별도로 조건부 검사할 수 있고, 검토된 바깥 참조의 강조 분리는 지정한 단일 위치의 독립 후보로 제공한다. 별도 결합 옵션은 같은 callback의 독립 후보 2~4개를 한 원본에서 다시 검증한다. 다른 계획과의 합성, 실행 및 렌더는 검증되지 않았다. 별도 결합 옵션은 같은 원본의 기존 scoped 참조 계획과 수식 후보를 합쳐 다시 검증한다. 각 원본별 조건부 계획이며 서로 다른 원본의 일괄 적용·실행·렌더는 검증되지 않았다. 이 소스 미리보기 계획은 컴파일 경로로 전달되지 않는다. 컴파일 경로에는 검토된 수학 호출을 별도로 재검증하는 옵션이 있다. 컴파일의 `--conditional-modern-highlights`는 독립 후보만, `--compose-modern-highlights`는 검증된 중간 소스 뒤의 후보를 컴파일한다. 다른 source의 route·facade 변경은 거절하며 조건부 facade 각주와 겹치는 강조 단계는 해당 소스에서 보류한다. 비중첩 각주는 최종 위치와 facade를 다시 검증한다. 바인딩·실행·참조 show 규칙·렌더는 미증명이고 원본 수정 권한은 없으며 미지원 형태와 파일별 보류가 있다. |
| `pde.theme-cancelmark` | 진단 | L | — | 확인된 수학 취소 호출; 명세의 cancelmark 교체는 현재 제공하지 않는다. |
| `pde.flowbox-colored-border` | 제안 | — | — | 검토된 전체 math-blocks 모듈의 정확한 형태와 이미 존재하는 theme binding이 필요하다. |
| `pde.paragraph-capitalization` | 제안 | L, 독립 미리보기 | — | 중첩 `paragraph_tab`/`paragraph-tab` 뒤 제한된 소문자 시작어를 별도 조건부 진단·수정 제안한다. 바인딩·언어·실행은 미증명이고 렌더가 달라질 수 있다. `--inspect-capitalization-review`는 명세의 허용 목록 밖 직접 소문자 시작을 위반이 아닌 별도 검토 항목으로 표시한다. 바인딩·언어·의도는 미증명이며 수정은 없다. |

## 읽을 때 주의할 점

- parent scope 독립 소스 검사에서는 검토된 `theme-from-text-fill()` 바인딩과 지정된 palette 필드만 읽는 제한된 `context` 지역 선언을 확인한다. child `m`을 캡처하는 제한된 리터럴 태그 함수와 같은 child의 직접 수식 호출도 확인한다. 해당 `markub`의 바인딩·효과와 다른 지역 선언·closure, 실행과 렌더는 증명하지 않는다.
- `--attest-parent-mannot`은 parent 단일 후보에 선택 root의 검토된 `mark.typ`·`util.typ`와 직접 wildcard `markub`의 조건부 소스 경로를 별도 확인한다. 실행 시 package 선택·초기화, 전이 의존성·효과와 렌더는 여전히 미증명이다.
- 단일 소스 수정안의 상세 전제와 사용자 문서에 쓰기를 허용하는 별도 경로는 [README](README.md)와 [수정 정책](../../openspec/changes/typst-style-linter/fix-policy.md)을 확인한다. 수정 정책은 목표이므로 구현의 `진단`을 `제안`으로 해석하지 않는다.
- 각주 이름이 명시적으로 다른 값이나 import 별칭에 바인딩되면 관련 수정안은 보류한다. 바인딩을 확인하지 못한 wildcard 문맥의 조건부 진단은 실제 builtin 바인딩을 증명하지 않는다.
- 문서 분석의 기본 세 규칙도 일부 문법과 문맥만 검사한다. L/P/R 힌트 및 독립 소스 미리보기는 원본에 적용할 권한이 없다. `inspect-document --preview-conditional-spacers`는 렌더 검증이 없는 별도 소스 후보이고, S 결합 컴파일은 별도로 선택한다. 격리된 0pt·8pt 사례와 단위 시험은 통과했으나 런타임 바인딩이나 렌더 동일성을 보장하지 않는다.
- 컴파일 미리보기는 캡처한 소스와 리소스로 기준 PDF와 후보 PDF를 만들 수 있지만, 전체 규칙 검사나 실행 가능한 모든 의존성의 완전성을 증명하지 않는다. 표시가 없는 규칙에 대한 렌더 검증을 의미하지 않는다.
- `--footnote-cache-path`의 wildcard 뒤 조건부 builtin 각주 바인딩은 별도 근거 보고다. 명시적 `--conditional-facade-footnotes` preview는 같은 종류의 근거를 캡처된 컴파일 리소스와 다시 대조해 S에 연결하지만, 지연 closure 등 미증명 문맥은 보류한다. 어느 경로도 include 실행, runtime 선택 또는 원본 수정 권한을 증명하지 않는다.
- [기존 audit 표](coverage-matrix.md)는 파일에 적힌 과거 커밋에서 생성된 기록이다. 이 문서는 그 기록을 새 검사 결과로 갱신하지 않는다. 최신 구현과 최종 검증 범위는 각각의 코드와 테스트 결과를 다시 확인해야 한다.

구현 근거: [단일 소스 연결](source_analysis.py), [문서 검사](document_cli.py), [조건부 로컬 힌트](document_local_rules.py), [조건부 패키지 힌트](conditional_highlight.py), [조건부 참조 인자](conditional_reference_arguments.py), [컴파일 미리보기](document_preview_cli.py).
