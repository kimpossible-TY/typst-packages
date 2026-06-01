#import "@preview/cetz:0.4.2": *

// Draws a customizable legend box for CeTZ canvas drawings.
//
// Parameters:
//   - x: Starting X coordinate of the box (left edge).
//   - y: Starting Y coordinate of the box (top edge).
//   - width: Width of the legend box.
//   - items: An array of legend items. Each item is a dictionary:
//       - text: The label content.
//       - stroke: (Optional) The line stroke style.
//       - mark: (Optional) The mark to draw on the line (e.g. arrow, dot).
//   - item_spacing: Vertical spacing between items.
//   - bg_fill: Background fill color of the legend box.
//   - border_stroke: Border stroke style of the legend box.
//   - title: (Optional) Title of the legend.
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
    // Draw background boundary rect for the legend
    rect((x, y), (x + width, box_bottom), fill: bg_fill, stroke: border_stroke)

    // Render each legend item row
    for (i, item) in items.enumerate() {
      let row_y = start_y - i * item_spacing

      // If a stroke is specified, draw the corresponding line segment representing the series style
      if item.at("stroke", default: none) != none {
        line((line_x_start, row_y), (line_x_start + line_len, row_y), stroke: item.stroke, mark: item.at(
          "mark",
          default: none,
        ))
      }

      // Draw item text
      content((text_x_start, row_y), anchor: "west")[
        #text(size: 9pt)[#item.text]
      ]
    }
  })
}

// Draws a text description block anchored on a CeTZ canvas.
//
// Parameters:
//   - x: X coordinate of the anchor point.
//   - y: Y coordinate of the anchor point.
//   - width: Width of the description box.
//   - body: The content/markup inside the description box.
//   - bg_fill: Background fill color.
//   - border_stroke: Border stroke style.
//   - box_anchor: Anchor direction for positioning the box (e.g. "north-west").
//   - text_size: Font size for the text inside.
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

  // Place the box content using CeTZ draw.content
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

