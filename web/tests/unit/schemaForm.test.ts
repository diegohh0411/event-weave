import { describe, expect, it } from 'vitest'
import { fieldsFromSchema, changedSettings } from '../../app/utils/schemaForm'
import { adapters } from '../../mock/fixtures/adapters'

const one = (prop: Record<string, unknown>, key = 'f') =>
  fieldsFromSchema({ type: 'object', properties: { [key]: prop } })[0]!

describe('fieldsFromSchema', () => {
  it('maps strings, integers, numbers and booleans', () => {
    expect(one({ type: 'string', title: 'Name', default: 'x' })).toMatchObject({ key: 'f', label: 'Name', kind: 'text', nullable: false, default: 'x' })
    expect(one({ type: 'integer' })).toMatchObject({ kind: 'int', label: 'f' })
    expect(one({ type: 'number', default: 0.5 })).toMatchObject({ kind: 'number', default: 0.5 })
    expect(one({ type: 'boolean' })).toMatchObject({ kind: 'bool' })
  })

  it('maps enum and Literal (const) to select with options', () => {
    expect(one({ enum: ['a', 'b'] })).toMatchObject({ kind: 'select', options: ['a', 'b'] })
    expect(one({ const: 'only' })).toMatchObject({ kind: 'select', options: ['only'] })
    expect(one({ type: 'integer', enum: [8, 128] })).toMatchObject({ kind: 'select', options: ['8', '128'], numeric: true })
  })

  it('maps an array of enum to multiselect', () => {
    expect(one({ type: 'array', items: { enum: ['x', 'y'] }, default: ['x'] }))
      .toMatchObject({ kind: 'multiselect', options: ['x', 'y'], default: ['x'] })
  })

  it('resolves $ref enums', () => {
    const f = fieldsFromSchema({
      type: 'object', $defs: { Lang: { enum: ['en', 'es'], type: 'string' } },
      properties: { lang: { $ref: '#/$defs/Lang' }, langs: { type: 'array', items: { $ref: '#/$defs/Lang' } } }
    })
    expect(f[0]).toMatchObject({ kind: 'select', options: ['en', 'es'] })
    expect(f[1]).toMatchObject({ kind: 'multiselect', options: ['en', 'es'] })
  })

  it('treats anyOf [X, null] as X, nullable', () => {
    expect(one({ anyOf: [{ type: 'integer' }, { type: 'null' }], default: null }))
      .toMatchObject({ kind: 'int', nullable: true, default: null })
    expect(one({ anyOf: [{ enum: ['a'] }, { type: 'null' }] })).toMatchObject({ kind: 'select', nullable: true })
  })

  it('maps format date-time to datetime', () => {
    expect(one({ type: 'string', format: 'date-time' })).toMatchObject({ kind: 'datetime' })
  })

  it('falls back to json for objects, mixed unions and plain arrays', () => {
    expect(one({ type: 'object', properties: { a: { type: 'number' } } })).toMatchObject({ kind: 'json' })
    expect(one({ type: 'array', items: { type: 'string' } })).toMatchObject({ kind: 'json' })
    expect(one({ anyOf: [{ type: 'string' }, { type: 'integer' }] })).toMatchObject({ kind: 'json' })
    expect(one({})).toMatchObject({ kind: 'json' })
  })

  it('reads every adapter schema in the fixtures', () => {
    for (const a of adapters.items) expect(() => fieldsFromSchema(a.settings_schema)).not.toThrow()
    const cluster = adapters.items.find(a => a.name === 'threshold')!
    expect(fieldsFromSchema(cluster.settings_schema).map(f => [f.key, f.kind])).toEqual([['k', 'int'], ['threshold', 'number'], ['weights', 'json']])
  })
})

describe('changedSettings', () => {
  it('drops undefined values and values equal to the default', () => {
    const fields = fieldsFromSchema({ type: 'object', properties: {
      k: { type: 'integer', default: 5 }, langs: { type: 'array', items: { enum: ['a', 'b'] }, default: ['a'] }, t: { type: 'number' }
    } })
    expect(changedSettings(fields, { k: 5, langs: ['a'], t: undefined })).toEqual({})
    expect(changedSettings(fields, { k: 6, langs: ['a', 'b'], t: 1 })).toEqual({ k: 6, langs: ['a', 'b'], t: 1 })
  })
})
