import { describe, expect, test } from 'bun:test'
import { answers, matchRequests, propList, statedFrom, unwrap } from './wires.js'

// A node as `plane.cypher` returns one: properties carry their description.
const node = (id, key, props) => ({ id, external_key: key, labels: ['Method'], properties: props })
const described = (value) => ({ $desc: 'what the plugin wrote', $value: value })

describe('reading a described property', () => {
  test('a description is unwrapped, a bare value is left alone', () => {
    expect(unwrap(described(['a']))).toEqual(['a'])
    expect(unwrap('plain')).toBe('plain')
    expect(unwrap(17)).toBe(17)
    expect(unwrap(null)).toBeNull()
    expect(unwrap(undefined)).toBeUndefined()
  })

  test('a list property holds only its strings', () => {
    expect(propList(described(['GET /a', 'POST /b']))).toEqual(['GET /a', 'POST /b'])
    expect(propList(['GET /a', 7, null])).toEqual(['GET /a'])
    expect(propList(described('GET /a'))).toEqual([])
    expect(propList(undefined)).toEqual([])
  })
})

describe('the routes a node states', () => {
  test('both sides are read, with the path as written beside each', () => {
    const stated = statedFrom([
      node(5, 'OrderController.one', {
        serves: described(['GET /api/orders/{}']),
        serves_as_written: described(['/api/orders/{id}']),
        file: described('src/OrderController.kt'),
        line: described(17),
      }),
      node(9, 'OrderSync.pullAll', { requests: described(['GET /api/orders']) }),
    ])
    expect(stated).toHaveLength(2)
    expect(stated[0]).toMatchObject({
      side: 'serves',
      verb: 'GET',
      path: '/api/orders/{}',
      written: '/api/orders/{id}',
      id: 5,
      key: 'OrderController.one',
      label: 'Method',
      file: 'src/OrderController.kt',
      line: 17,
    })
    expect(stated[1]).toMatchObject({ side: 'requests', verb: 'GET', path: '/api/orders', written: null })
  })

  test('one node may state several routes, and both sides at once', () => {
    const stated = statedFrom([
      node(1, 'Gateway.proxy', {
        serves: described(['GET /in', 'POST /in']),
        requests: described(['GET /out']),
      }),
    ])
    expect(stated.map((s) => `${s.side} ${s.verb} ${s.path}`)).toEqual([
      'serves GET /in',
      'serves POST /in',
      'requests GET /out',
    ])
  })

  test('a value that is not `VERB path` was not written by a plugin, and is skipped', () => {
    const stated = statedFrom([
      node(2, 'Odd', { serves: described(['/no-verb', '', ' ', 'GET /kept']) }),
    ])
    expect(stated).toHaveLength(1)
    expect(stated[0].path).toBe('/kept')
  })

  test('a node with no routes, and no nodes at all, state nothing', () => {
    expect(statedFrom([node(3, 'Plain', { file: described('a.kt') })])).toEqual([])
    expect(statedFrom([])).toEqual([])
    expect(statedFrom(undefined)).toEqual([])
  })

  test('a node without an external key is named by its id', () => {
    expect(statedFrom([{ id: 42, properties: { serves: described(['GET /x']) } }])[0]).toMatchObject({
      key: '#42',
      label: '',
    })
  })
})

// Core's rule lives in `Stated::answers` (`compact.rs`); these pin the
// browser's copy of it, so the two cannot drift in silence.
describe('whether one route answers another', () => {
  const r = (verb, path) => ({ verb, path })

  test('the same verb on the same path answers it', () => {
    expect(answers(r('GET', '/a'), r('GET', '/a'))).toBe(true)
  })

  test('a different verb on the same path does not', () => {
    expect(answers(r('GET', '/a'), r('POST', '/a'))).toBe(false)
  })

  test('the same verb on a different path does not', () => {
    expect(answers(r('GET', '/a'), r('GET', '/b'))).toBe(false)
  })

  test('`*` on either side matches every verb on that path', () => {
    expect(answers(r('*', '/a'), r('POST', '/a'))).toBe(true)
    expect(answers(r('DELETE', '/a'), r('*', '/a'))).toBe(true)
    expect(answers(r('*', '/a'), r('*', '/b'))).toBe(false)
  })

  test('paths compare exactly — the plugins normalised them at write time', () => {
    expect(answers(r('GET', '/api/orders/{}'), r('GET', '/api/orders/{id}'))).toBe(false)
    expect(answers(r('GET', '/a'), r('GET', '/a/'))).toBe(false)
  })
})

describe('pairing requests with what serves them', () => {
  const stated = statedFrom([
    node(5, 'Controller.all', { serves: described(['GET /api/orders']) }),
    node(6, 'Mirror.all', { serves: described(['GET /api/orders']) }),
    node(7, 'Controller.one', { serves: described(['GET /api/orders/{}']) }),
    node(9, 'Sync.pullAll', { requests: described(['GET /api/orders']) }),
    node(10, 'Sync.warehouse', { requests: described(['GET /warehouse/v1/stock']) }),
  ])

  test('a request is paired with every declaration that answers it', () => {
    const matched = matchRequests(stated, stated)
    expect(matched).toHaveLength(2)
    expect(matched[0].req.key).toBe('Sync.pullAll')
    expect(matched[0].serves.map((s) => s.key)).toEqual(['Controller.all', 'Mirror.all'])
  })

  test('a request nothing answers is kept, with nothing beside it', () => {
    const [, warehouse] = matchRequests(stated, stated)
    expect(warehouse.req.path).toBe('/warehouse/v1/stock')
    expect(warehouse.serves).toEqual([])
  })

  test('filtering the rows shown does not hide the declarations they match', () => {
    const shown = stated.filter((w) => w.key === 'Sync.pullAll')
    const matched = matchRequests(shown, stated)
    expect(matched).toHaveLength(1)
    expect(matched[0].serves.map((s) => s.key)).toEqual(['Controller.all', 'Mirror.all'])
  })

  test('declarations alone pair with nothing', () => {
    expect(matchRequests(stated.filter((w) => w.side === 'serves'), stated)).toEqual([])
  })
})
