// The test runner copies this file beside lib.typ and an isolated fixture catalog.
// Keep the consumer API identical to a real package import: no setup or overrides.
#import "lib.typ": project-ref

#let equation = project-ref("fixture", <energy>)
#assert(equation.func() == link)
#assert(equation.dest == "https://example.com/reference-source/main.pdf#page=2")
#assert(equation.body == [eq.1.2.3 of Reference Source])

#let theorem = project-ref("fixture", <theorem>)
#assert(theorem.dest == "https://example.com/reference-source/main.pdf#page=1")
#assert(theorem.body == [Theorem S.1.1 of Reference Source])

// Serialized source text must remain text, even when it looks like Typst code.
#let literal = project-ref("fixture", <literal>)
#assert(literal.body == text("#panic(\"not code\") of Reference Source"))

Equation: #equation. Theorem: #theorem. Literal: #literal.
