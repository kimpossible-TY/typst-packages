#import "@preview/cetz:0.4.2": *

#let legend_box(
  x: 0,
  y: 0,
  width: 8.7,
  items: (),
  item_spacing: 0.5,
  bg_fill: rgb("f0f0f0"),
  border_stroke: rgb(80, 80, 80),
  title: none,
) = {
  import draw: *

  let padding = 0.5
  let line_len = 1.0
  let line_x_start = x + padding
  let text_x_start = line_x_start + line_len + 0.3
  let start_y = y - padding

  let content_height = (items.len() - 1) * item_spacing
  let box_height = content_height + (padding * 2)
  let box_bottom = y - box_height

  group({
    rect((x, y), (x + width, box_bottom), fill: bg_fill, stroke: border_stroke)

    for (i, item) in items.enumerate() {
      let row_y = start_y - i * item_spacing

      if item.at("stroke", default: none) != none {
        line((line_x_start, row_y), (line_x_start + line_len, row_y), stroke: item.stroke, mark: item.at(
          "mark",
          default: none,
        ))
      }

      content((text_x_start, row_y), anchor: "west")[
        #text(size: 9pt)[#item.text]
      ]
    }
  })
}

#let description_box(
  x: 0,
  y: 0,
  width: auto,
  body: none,
  bg_fill: rgb("f0f0f0"),
  border_stroke: rgb(80, 80, 80),
  box_anchor: "north-west",
  text_size: 8pt,
) = {
  import draw: *

  content(
    (x, y),
    box(
      width: width,
      inset: 5pt,
      text(size: text_size, body),
    ),
    anchor: box_anchor,
    fill: bg_fill,
    stroke: border_stroke,
    frame: "rect",
  )
}
