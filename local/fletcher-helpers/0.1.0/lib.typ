#import "@preview/fletcher:0.5.8" as fletcher
#import "@local/math-blocks:0.2.0": theme-from-text-fill

// Forward Fletcher content and options; explicit options override the defaults.
// Mathematical nodes and edges remain in the consuming document.
#let themed-diagram(theme: auto, ..args) = context {
  let theme = if theme == auto { theme-from-text-fill() } else { theme }
  let options = (
    node-stroke: 0.8pt + theme.rule,
    edge-stroke: 0.8pt + theme.rule,
    node-fill: theme.callouts.note.bg,
    node-inset: 4pt,
  ) + args.named()
  fletcher.diagram(..args.pos(), ..options)
}
