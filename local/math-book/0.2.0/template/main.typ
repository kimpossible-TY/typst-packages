#import "@local/math-book:0.2.0": apply-math-book, book-part
#import "@local/math-blocks:0.2.0": *

#show: apply-math-book.with(title: "Mathematical Notes", author: "Author", chapter-pages: true)
#include "cover.typ"
#book-part[
  = First Chapter
  == First Section
  #definition[A reusable definition.] <definition-first>
  #theorem[A reusable theorem.]
  #proof[Supply the proof here.]

  == Second Section
  See @definition-first.
]
