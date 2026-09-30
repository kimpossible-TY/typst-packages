// math-blocks re-exports project-ref for existing mathematical note styles.
#import "@local/math-blocks:0.2.0": project-ref

#set text(font: "New Computer Modern", size: 12pt)
#set page(width: 18cm, height: auto, margin: 1.2cm)

= Cross-project references

The equation is #project-ref("pde", <zero_acceleration>).

The definition is #project-ref("pde", <definition_of_geodesics>).
