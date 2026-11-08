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
| `core.scoped-marker-tags` | 진단 | — | — | 확인된 scoped 콜백의 태그만; 외부 참조를 임의로 바꾸지 않는다. |
| `core.scoped-anchor-access` | 제안 | — | — | 검토된 CeTZ 증거와 유일한 마커·방향이 확인된 경우. |
| `core.scoped-reference-access` | 제안 | — | — | 동일 scope와 단일 리터럴 대상이 확인된 경우. |
| `core.local-overlay-api` | 진단 | — | — | 알려진 로컬 annotation 콜백 안의 제한된 raw API 호출. |
| `core.annotation-in-display` | 진단 | C | — | 제한된 literal scoped callback의 display 밖 직접 호출을 조건부 진단한다. 수식 이동은 추측하지 않는다. |
| `core.annotation-marker-bounds` | 진단 | — | — | 직접 확인한 display와 같은 scope의 순서가 확인된 대상. |
| `core.unique-scope-namespace` | 진단 | — | — | 확인된 scoped wrapper 문맥; 이름이나 외부 참조를 자동 변경하지 않는다. |
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
| `pde.paragraph-marker-gap` | 제안 | L, P | — | 확인된 text-utils 마커와 제한된 문단 경계; 힌트는 바인딩을 전제한다. |
| `pde.paragraph-marker-target` | 제안 | L | — | 단일 소스는 확인된 독립 마커와 제목·display 형제에 한정한다. L은 직접 중첩 본문에서 제목·display 앞 마커 형태를 조건부 진단하지만 바인딩·실행·본문 소유권은 미확인이다. |
| `pde.prose-paragraph-marker` | 제안 | L | — | 제한된 리터럴 문단 경계와 검증된 마커·show 설정. |
| `pde.continuation-line` | 제안 | L | — | 제한된 직접 리터럴 문장·어휘와 경계만. |
| `pde.display-continuation-gap` | 제안 | 기본 | 기본 | 확인된 display와 직접 이어지는 문장; 주석·불명확한 wrapper는 보류. |
| `pde.proof-trailing-gap` | 제안 | L | G | 알려진 proof helper와 제한된 경계; G는 조건부 후보다. |
| `pde.footnote-trailing-gap` | 제안 | L | G | 확인된 각주와 제한된 경계; G는 조건부 중첩 수식 각주 후보다. |
| `pde.prose-footnote-punctuation` | 제안 | L | P | 제한된 직접 문장과 builtin 각주. 문서 검사는 조건부로 각주 앞 공백 제거와 각주 뒤 문장부호 이동을 각각 독립 미리보기로 제공한다. `--footnote-punctuation-evidence`는 일부 진단에 별도 출처 근거를 붙인다. P 결합 컴파일은 공백 제거만 포함한다. |
| `pde.aligned-footnote-spacer` | 제안 | L | — | 정렬 수식과 확인된 spacer 바인딩만. |
| `pde.unaligned-footnote-no-spacer` | 제안 | L | S | 기존 명세대로 제한된 비정렬 수식 각주 앞 spacer를 제거하는 조건부 후보. 실제 렌더는 달라질 수 있다. |
| `pde.prose-footnote-no-spacer` | 제안 | L | — | 확인된 spacer와 문장·구두점의 안전한 인접 관계가 필요하다. |
| `pde.standalone-display` | 제안 | 기본 | 기본 | 파서가 확인한 display와 제한된 직접 문장·라벨. |
| `pde.text-only-highlight` | 제안 | P | — | 알려진 highlight와 단순 리터럴 본문; 옵션·중첩 스타일 등은 보류. |
| `pde.theme-cancelmark` | 진단 | L | — | 확인된 수학 취소 호출; 명세의 cancelmark 교체는 현재 제공하지 않는다. |
| `pde.flowbox-colored-border` | 제안 | — | — | 검토된 전체 math-blocks 모듈의 정확한 형태와 이미 존재하는 theme binding이 필요하다. |
| `pde.paragraph-capitalization` | 제안 | L | — | 알려진 마커 뒤 영어의 제한된 소문자 시작어; 언어·본문이 불명확하면 보류. |

## 읽을 때 주의할 점

- 단일 소스 수정안의 상세 전제와 사용자 문서에 쓰기를 허용하는 별도 경로는 [README](README.md)와 [수정 정책](../../openspec/changes/typst-style-linter/fix-policy.md)을 확인한다. 수정 정책은 목표이므로 구현의 `진단`을 `제안`으로 해석하지 않는다.
- 문서 분석의 기본 세 규칙도 일부 문법과 문맥만 검사한다. L/P/R 힌트 및 독립 소스 미리보기는 원본에 적용할 권한이 없다. `inspect-document --preview-conditional-spacers`는 렌더 검증이 없는 별도 소스 후보이고, S 결합 컴파일은 별도로 선택한다. 격리된 0pt·8pt 사례와 단위 시험은 통과했으나 런타임 바인딩이나 렌더 동일성을 보장하지 않는다.
- 컴파일 미리보기는 캡처한 소스와 리소스로 기준 PDF와 후보 PDF를 만들 수 있지만, 전체 규칙 검사나 실행 가능한 모든 의존성의 완전성을 증명하지 않는다. 표시가 없는 규칙에 대한 렌더 검증을 의미하지 않는다.
- `--footnote-cache-path`의 wildcard 뒤 조건부 builtin 각주 바인딩은 별도 근거 보고다. 명시적 `--conditional-facade-footnotes` preview는 같은 종류의 근거를 캡처된 컴파일 리소스와 다시 대조해 S에 연결하지만, 지연 closure 등 미증명 문맥은 보류한다. 어느 경로도 include 실행, runtime 선택 또는 원본 수정 권한을 증명하지 않는다.
- [기존 audit 표](coverage-matrix.md)는 파일에 적힌 과거 커밋에서 생성된 기록이다. 이 문서는 그 기록을 새 검사 결과로 갱신하지 않는다. 최신 구현과 최종 검증 범위는 각각의 코드와 테스트 결과를 다시 확인해야 한다.

구현 근거: [단일 소스 연결](source_analysis.py), [문서 검사](document_cli.py), [조건부 로컬 힌트](document_local_rules.py), [조건부 패키지 힌트](conditional_highlight.py), [조건부 참조 인자](conditional_reference_arguments.py), [컴파일 미리보기](document_preview_cli.py).
