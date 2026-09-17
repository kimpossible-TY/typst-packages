#import "@preview/mannot:0.4.0": *

// Counter to ensure unique IDs for auto-generated local scopes.
#let _local-scope-counter = counter("_local-scope-counter")

// Helper function to create a new localized scope with helper utilities.
// This allows generating unique labels and references within a document block
// to avoid label collision across different document sections.
//
// Parameters:
//   - body: A callback function that takes a dictionary containing scope utilities.
//   - prefix: A custom string prefix. If auto, a unique one is generated using `_local-scope-counter`.
//   - namespace: The prefix namespace, defaulting to "local-scope".
#let local-tag-scope(
  body,
  prefix: auto,
  namespace: "local-scope",
) = {
  // Construct scope helpers for a given prefix
  let make-scope(prefix) = {
    // Generate a unique name for a local name
    let name = local-name => prefix + "-" + local-name
    // Generate unique names for a list of local names
    let names = local-names => local-names.map(name)
    // Create a label for a local name
    let tag = local-name => label((name)(local-name))
    // Create labels for a list of local names
    let tags = local-names => local-names.map(tag)
    // Create an anchor name for CeTZ drawing
    let anchor = (local-name, side) => (name)(local-name) + "." + side
    // Create a reference (ref element) to a local label
    let reference = local-name => ref(label((name)(local-name)))

    body((
      prefix: prefix,
      name: name,
      names: names,
      tag: tag,
      tags: tags,
      anchor: anchor,
      ref: reference,
    ))
  }

  if prefix == auto {
    _local-scope-counter.step()

    context {
      let n = _local-scope-counter.get().first()
      make-scope(namespace + "-" + str(n))
    }
  } else {
    make-scope(prefix)
  }
}

// Counter to ensure unique IDs for auto-generated annotations.
#let _local-scope-annotation-counter = counter("_local-scope-annotation-counter")

// Internal helper for rendering a localized annotation overlay.
// It maps targeted labels to physical locations in the document and draws
// CeTZ objects relative to those physical locations.
//
// Parameters:
//   - tag: A label or array of labels that are being annotated.
//   - cetz: The cetz module reference.
//   - drawable: CeTZ canvas child drawing elements to draw.
//   - id: An optional unique identifier. If auto, one is automatically generated.
#let annot-cetz-local(
  tag,
  cetz,
  drawable,
  id: auto,
) = {
  let build(id) = {
    let tags = if type(tag) == label {
      (tag,)
    } else {
      tag
    }

    // Define the overlay callback expected by the mannot package.
    // `markers` contains details about the target element positions.
    let overlay(markers) = {
      for data in markers {
        if not data.keys().contains("anchor-bounds") {
          panic("s.annot targets must be marked with s.mark(...) or mannot mark(..., tag: ...); plain Typst labels from s.tag(...) do not include annotation bounds")
        }
      }

      let origin = markers.first().anchor-bounds

      // Define standard dummy bounding box rectangles for the target elements.
      let preamble = markers
        .map(data => {
          let bounds = data.anchor-bounds

          cetz.draw.rect(
            (bounds.x - origin.x, -(bounds.y - origin.y)),
            (
              bounds.x + bounds.width - origin.x,
              -(bounds.y + bounds.height - origin.y),
            ),
            name: str(data.tag),
            stroke: none,
            fill: none,
          )
        })
        .sum()

      // A reference label to track the offset of the container.
      let ref-lab = label("_mannot-annot-cetz-ref-" + str(id))
      let ref-lab-content = cetz.draw.content((0, 0), [#none#ref-lab])

      place([#none#ref-lab])
      place(hide(cetz.canvas(ref-lab-content + preamble + drawable)))

      context {
        // Query the position of the reference label relative to this point in the page.
        let ref-pos-array = query(selector(ref-lab).before(here()))
          .map(e => e.location().position())

        let ref-pos1 = ref-pos-array.at(ref-pos-array.len() - 2)
        let ref-pos2 = ref-pos-array.last()

        // Place the canvas overlay shifted by the computed positional offset.
        place(
          dx: origin.x + ref-pos1.x - ref-pos2.x,
          dy: origin.y + ref-pos1.y - ref-pos2.y,
          cetz.canvas(preamble + drawable),
        )
      }
    }

    // Call mannot's core-annot to register the annotations.
    core-annot(tags, overlay)
  }

  if id == auto {
    _local-scope-annotation-counter.step()

    context {
      let n = _local-scope-annotation-counter.get().first()
      build("auto-" + str(n))
    }
  } else {
    build(id)
  }
}

// User-facing function to create a block with local annotations capability.
// It wraps a body block and provides unique tag, names, and annotation methods.
//
// Parameters:
//   - body: A callback function taking a dictionary of scope/annotation utilities.
//   - prefix: A custom prefix for this scope (optional).
//   - parent: The parent scope dictionary, if nesting scopes (optional).
//   - name: The name of this scope relative to the parent scope (optional).
#let local-scope-annotations(
  body,
  prefix: auto,
  parent: auto,
  name: auto,
  namespace: "local-scope-annotations",
) = {
  let scope-prefix = if prefix != auto {
    prefix
  } else if parent != auto and name != auto {
    (parent.name)(name)
  } else {
    auto
  }

  let namespace = if parent != auto and name == auto {
    parent.prefix + "-" + namespace
  } else {
    namespace
  }

  local-tag-scope(scope => {
    // Create a mannot marker with a scoped tag.
    let scoped-mark(name, ..args) = mark.with(tag: (scope.tag)(name), ..args)

    // Helper function inside the scope to annotate elements
    let annot = (names, cetz, drawable) => {
      let annotation-tags = if type(names) == array {
        (scope.tags)(names)
      } else {
        ((scope.tag)(names),)
      }

      annot-cetz-local(
        annotation-tags,
        cetz,
        drawable,
      )
    }

    body((
      prefix: scope.prefix,
      tag: scope.tag,
      tags: scope.tags,
      name: scope.name,
      names: scope.names,
      mark: scoped-mark,
      node: scope.anchor,
      anchor: scope.anchor,
      ref: scope.ref,
      annot: annot,
    ))
  }, prefix: scope-prefix, namespace: namespace)
}

// Compatibility name used by existing mathematical notes.
#let mannot-scope(body, prefix: auto, parent: auto, name: auto) = {
  local-scope-annotations(
    body, prefix: prefix, parent: parent, name: name, namespace: "mannot-scope",
  )
}
