#import "@preview/mannot:0.3.3": *

#let _local-scope-counter = counter("_local-scope-counter")

#let _local-scope(
  body,
  prefix: auto,
  namespace: "local-scope",
) = {
  let make-scope(prefix) = {
    let name = local-name => prefix + "-" + local-name
    let names = local-names => local-names.map(name)
    let tag = local-name => label((name)(local-name))
    let tags = local-names => local-names.map(tag)
    let anchor = (local-name, side) => (name)(local-name) + "." + side
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

#let _local-scope-annotation-counter = counter("_local-scope-annotation-counter")

#let _local-scope-annotation(
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

    let overlay(markers) = {
      let origin = markers.first()

      let preamble = markers
        .map(info => {
          cetz.draw.rect(
            (info.x - origin.x, -(info.y - origin.y)),
            (
              info.x + info.width - origin.x,
              -(info.y + info.height - origin.y),
            ),
            name: str(info.tag),
            stroke: none,
            fill: none,
          )
        })
        .sum()

      let ref-lab = label("_mannot-annot-cetz-ref-" + str(id))
      let ref-lab-content = cetz.draw.content((0, 0), [#none#ref-lab])

      place([#none#ref-lab])
      place(hide(cetz.canvas(ref-lab-content + preamble + drawable)))

      context {
        let ref-pos-array = query(selector(ref-lab).before(here()))
          .map(e => e.location().position())

        let ref-pos1 = ref-pos-array.at(ref-pos-array.len() - 2)
        let ref-pos2 = ref-pos-array.last()

        place(
          dx: origin.x + ref-pos1.x - ref-pos2.x,
          dy: origin.y + ref-pos1.y - ref-pos2.y,
          cetz.canvas(preamble + drawable),
        )
      }
    }

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

#let local-scope-annotations(
  body,
  prefix: auto,
  parent: auto,
  name: auto,
) = {
  let scope-prefix = if prefix != auto {
    prefix
  } else if parent != auto and name != auto {
    (parent.name)(name)
  } else {
    auto
  }

  let namespace = if parent != auto and name == auto {
    parent.prefix + "-local-scope-annotations"
  } else {
    "local-scope-annotations"
  }

  _local-scope(scope => {
    let annot = (names, cetz, drawable) => {
      let annotation-tags = if type(names) == array {
        (scope.tags)(names)
      } else {
        ((scope.tag)(names),)
      }

      _local-scope-annotation(
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
      node: scope.anchor,
      anchor: scope.anchor,
      ref: scope.ref,
      annot: annot,
    ))
  }, prefix: scope-prefix, namespace: namespace)
}
