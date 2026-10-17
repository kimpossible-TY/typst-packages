#import "@local/text-utils:0.1.2": highlighted

// A single text element has no children field. This must render without panic.
#highlighted[A short explanation.]

// Preserve support for mixed content and a single inline equation.
#highlighted[The variable $x$ is positive.]
#highlighted[$x > 0$]
