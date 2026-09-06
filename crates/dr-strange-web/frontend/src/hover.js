// The node-hover drawer, kept out of `plot.js` because that module cannot be
// loaded without WebGL and this drawing can: it is plain 2D canvas work over a
// graphology graph, and it is where the plot says what the cursor is on — the
// one thing a reader is most likely to notice going wrong.

export const HOVER_COLOR = '#f59e0b' // amber — visible on both light and dark

// A theme-aware replacement for sigma's default node-hover drawer. Two jobs:
//  - the SELECTED node (sigma routes `highlighted` nodes here) draws as a
//    hollow ring in its own colour — interior filled with the canvas bg — so it
//    inverts from the solid nodes around it and is unmistakable;
//  - a plain hover draws the label box inverted — text colour as the box, box
//    colour as the text — so the key under the cursor stands out from every
//    other label. (Sigma's own default paints the box hardcoded white, which is
//    invisible under light text in dark mode, so it could not be reused.)
// `label`/`bg`/`canvasBg` are getters read every draw, so theme flips track;
// `graph` gives access to the node's stored (untouched) attributes.
export function drawNodeHover(label, bg, canvasBg, graph) {
  return (context, data, settings) => {
    const { labelSize: size, labelFont: font, labelWeight: weight } = settings
    context.font = `${weight} ${size}px ${font}`

    // A selection fades the far graph and drops those labels, unreadable text
    // being all cost. The hover box is the one place that rule cannot hold: it
    // exists to name the node under the cursor, and a node three hops from the
    // selection is exactly the one whose name is being asked for. Read the key
    // back off the graph — untouched there, as the ring colour is below.
    const text = data.label || graph.getNodeAttribute(data.key, 'label') || ''

    if (data.highlighted) {
      const r = data.size
      // The reducer made the WebGL disc transparent; its category colour still
      // lives on the graph node, which we read for the ring stroke.
      const ringColor = graph.getNodeAttribute(data.key, 'color') || HOVER_COLOR
      context.shadowBlur = 0 // clear any shadow left by a hover box this frame
      context.beginPath()
      context.arc(data.x, data.y, r, 0, Math.PI * 2)
      context.closePath()
      context.fillStyle = canvasBg() // hollow the interior to the background
      context.fill()
      context.lineWidth = Math.max(2.5, r * 0.4)
      context.strokeStyle = ringColor
      context.stroke()
      if (text) {
        context.fillStyle = label()
        context.fillText(text, data.x + r + 3, data.y + size / 3)
      }
      return
    }

    // Hovered: the label chip is drawn **inverted** — the text colour becomes
    // the box and the box colour becomes the text — so the key under the
    // cursor reads as a solid swatch against a field of plain labels.
    //
    // Inversion rather than a new accent colour, because both values are the
    // ones already chosen for this theme: it is legible on either background
    // by construction, and there is no third colour to keep in step when the
    // palette changes.
    const boxFill = label()
    const textFill = bg()

    context.fillStyle = boxFill
    context.shadowOffsetX = 0
    context.shadowOffsetY = 0
    context.shadowBlur = 8
    context.shadowColor = '#000'
    const PADDING = 2
    // Empty text takes the disc, not a chip five pixels wide holding nothing.
    if (text) {
      const boxWidth = Math.round(context.measureText(text).width + 5)
      const boxHeight = Math.round(size + 2 * PADDING)
      const radius = Math.max(data.size, size / 2) + PADDING
      const angle = Math.asin(boxHeight / 2 / radius)
      const dx = Math.sqrt(Math.abs(radius ** 2 - (boxHeight / 2) ** 2))
      context.beginPath()
      context.moveTo(data.x + dx, data.y + boxHeight / 2)
      context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2)
      context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2)
      context.lineTo(data.x + dx, data.y - boxHeight / 2)
      context.arc(data.x, data.y, radius, angle, -angle)
      context.closePath()
      context.fill()
    } else {
      context.beginPath()
      context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2)
      context.closePath()
      context.fill()
    }
    context.shadowBlur = 0
    if (text) {
      context.fillStyle = textFill
      context.fillText(text, data.x + data.size + 3, data.y + size / 3)
    }
  }
}
