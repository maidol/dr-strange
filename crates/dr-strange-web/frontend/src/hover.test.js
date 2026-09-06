// Tests for the hover drawer: what the plot says the cursor is on.
//
// The interesting case is the one a screenshot of an idle canvas would never
// show — the node reducer blanks the labels of nodes far from a selection, and
// the hover box has to name the node anyway. See `hover.js`.

import { describe, expect, test } from 'bun:test'
import Graph from 'graphology'

import { drawNodeHover } from './hover.js'

const SETTINGS = { labelSize: 12, labelFont: 'sans-serif', labelWeight: 'normal' }

// Enough of a 2D context to record what was drawn: every call is a no-op but
// `fillText`, the chip's width, and whether a ring was stroked.
function fakeContext() {
  const calls = { text: [], measured: [], stroked: 0, arcs: 0, lines: 0 }
  return {
    calls,
    font: '',
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    shadowBlur: 0,
    shadowColor: '',
    shadowOffsetX: 0,
    shadowOffsetY: 0,
    beginPath() {},
    closePath() {},
    fill() {},
    moveTo() {},
    lineTo() {
      calls.lines += 1
    },
    arc() {
      calls.arcs += 1
    },
    stroke() {
      calls.stroked += 1
    },
    measureText(s) {
      calls.measured.push(s)
      return { width: s.length * 7 }
    },
    fillText(s) {
      calls.text.push(s)
    },
  }
}

// A one-node graph plus the display data sigma would hand the drawer for it.
// `label` is what the reducer left behind, which is not always what the node is.
function draw(nodeAttrs, displayData) {
  const graph = new Graph()
  graph.addNode('n1', nodeAttrs)
  const context = fakeContext()
  const drawer = drawNodeHover(
    () => '#e8e8ee', // label colour
    () => '#26262e', // panel background
    () => '#1f1f27', // canvas background
    graph,
  )
  drawer(context, { key: 'n1', x: 10, y: 20, size: 5, ...displayData }, SETTINGS)
  return context.calls
}

describe('hover box', () => {
  test('names the node under the cursor', () => {
    const calls = draw({ label: 'core::graph::Node' }, { label: 'core::graph::Node' })
    expect(calls.text).toEqual(['core::graph::Node'])
  })

  test('names it even when the focus fade blanked the label', () => {
    // What the reducer hands over three hops from a selection: an empty label.
    // The key still has to appear — this is the whole point of hovering.
    const calls = draw({ label: 'core::graph::Node' }, { label: '' })
    expect(calls.text).toEqual(['core::graph::Node'])
  })

  test('sizes the chip to the recovered key, not to the blank', () => {
    const calls = draw({ label: 'core::graph::Node' }, { label: '' })
    expect(calls.measured).toEqual(['core::graph::Node'])
    expect(calls.lines).toBeGreaterThan(0) // a chip, not a bare disc
  })

  test('draws a plain disc for a node that has no label at all', () => {
    const calls = draw({}, { label: undefined })
    expect(calls.text).toEqual([])
    expect(calls.lines).toBe(0)
    expect(calls.arcs).toBe(1)
  })
})

describe('selected node', () => {
  test('draws a ring and still names a blanked node', () => {
    const calls = draw({ label: 'core::graph::Node', color: '#123456' }, { label: '', highlighted: true })
    expect(calls.stroked).toBe(1)
    expect(calls.text).toEqual(['core::graph::Node'])
  })

  test('draws no text for a ringed node with no label', () => {
    const calls = draw({ color: '#123456' }, { highlighted: true })
    expect(calls.stroked).toBe(1)
    expect(calls.text).toEqual([])
  })
})
