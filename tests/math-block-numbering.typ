#import "@local/math-blocks:0.2.0": *

#set page(width: 18cm, height: auto, margin: 1.2cm)
#set heading(numbering: "1.1")
#show: apply-math-block-reset

= First Chapter
== First Section

#definition[A definition.] <definition-first>
#proposition[A proposition.] <proposition-first>
#lemma[A lemma.] <lemma-first>
#theorem[A theorem.] <theorem-first>
#note[A note.] <note-first>

== Second Section

#definition[A definition in the second section.] <definition-second>
#theorem[A theorem in the second section.] <theorem-second>

= Second Chapter
== First Section

#definition[A definition in the second chapter.] <definition-third>
#theorem[A theorem in the second chapter.] <theorem-third>

References: @definition-first, @definition-second, @definition-third.
