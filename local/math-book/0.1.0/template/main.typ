#import "@local/math-book:0.1.0": *
#import "@local/math-blocks:0.1.0": *

// Setup entry point for the mathematical notes book template.
// Apply the document-wide style templates with metadata.
#show: apply-math-book.with(
  title: "Mathematical Notes",
  author: "Author",
  description: "Mathematical Notes",
)

// Include the cover page and table of contents
#include "cover.typ"

// Document chapters and mathematical blocks example demonstration
= First Chapter

#definition[
  Replace this with a definition.
]

#theorem[
  Replace this with a theorem.
]

