/** Turns a pydantic JSON schema (an adapter's settings_schema) into a flat list of form fields. */

export type JSONSchema = Record<string, any>

export type FieldKind = 'text' | 'int' | 'number' | 'bool' | 'select' | 'multiselect' | 'datetime' | 'json'

export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  options?: string[]
  /** select/multiselect over numbers: the form converts the chosen string back to a number. */
  numeric?: boolean
  nullable: boolean
  default?: unknown
}

function resolve(node: JSONSchema | undefined, root: JSONSchema): JSONSchema {
  let cur = node ?? {}
  for (let i = 0; i < 10 && typeof cur.$ref === 'string'; i++) {
    const target = cur.$ref.replace(/^#\//, '').split('/').reduce<any>((o, k) => o?.[k], root)
    // Sibling keys of the $ref (title, default) win over the target's.
    const { $ref: _ref, ...rest } = cur
    cur = { ...(target ?? {}), ...rest }
  }
  return cur
}

function enumOf(node: JSONSchema): unknown[] | undefined {
  if (Array.isArray(node.enum)) return node.enum
  if ('const' in node) return [node.const]
  return undefined
}

function field(key: string, raw: JSONSchema, root: JSONSchema): FieldSpec {
  let node = resolve(raw, root)
  let nullable = false
  const base = (): Omit<FieldSpec, 'kind'> => {
    const spec: Omit<FieldSpec, 'kind'> = { key, label: raw.title ?? node.title ?? key, nullable }
    if ('default' in raw) spec.default = raw.default
    else if ('default' in node) spec.default = node.default
    return spec
  }
  const json = (): FieldSpec => {
    const spec: FieldSpec = { ...base(), kind: 'json' }
    // A nested model whose every property has a default has a default of its own.
    const props = Object.entries<JSONSchema>(node.properties ?? {})
    if (!('default' in spec) && node.type === 'object' && props.length && props.every(([, p]) => 'default' in resolve(p, root))) {
      spec.default = Object.fromEntries(props.map(([k, p]) => [k, resolve(p, root).default]))
    }
    return spec
  }

  const union = node.anyOf ?? node.oneOf
  if (Array.isArray(union)) {
    const members = union.map((m: JSONSchema) => resolve(m, root))
    const rest = members.filter(m => m.type !== 'null')
    if (rest.length !== 1 || rest.length === members.length) return json()
    nullable = true
    node = { ...rest[0], title: node.title ?? rest[0]!.title, default: 'default' in node ? node.default : rest[0]!.default }
    if (!('default' in node) || node.default === undefined) delete node.default
  } else if (Array.isArray(node.type)) {
    const types = node.type.filter((t: string) => t !== 'null')
    if (types.length !== 1) return json()
    nullable = types.length !== node.type.length
    node = { ...node, type: types[0] }
  }

  const values = enumOf(node)
  if (values) {
    const numeric = values.every(v => typeof v === 'number')
    return { ...base(), kind: 'select', options: values.map(String), ...(numeric ? { numeric } : {}) }
  }
  switch (node.type) {
    case 'string': return { ...base(), kind: node.format === 'date-time' ? 'datetime' : 'text' }
    case 'integer': return { ...base(), kind: 'int' }
    case 'number': return { ...base(), kind: 'number' }
    case 'boolean': return { ...base(), kind: 'bool' }
    case 'array': {
      const items = resolve(node.items, root)
      const choices = enumOf(items)
      if (choices) {
        const numeric = choices.every(v => typeof v === 'number')
        return { ...base(), kind: 'multiselect', options: choices.map(String), ...(numeric ? { numeric } : {}) }
      }
      return json()
    }
    default: return json()
  }
}

export function fieldsFromSchema(schema: JSONSchema): FieldSpec[] {
  const root = schema ?? {}
  return Object.entries<JSONSchema>(root.properties ?? {}).map(([key, prop]) => field(key, prop, root))
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** The values that differ from the schema default: what goes into a run config's settings. */
export function changedSettings(fields: FieldSpec[], values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of fields) {
    const v = values[f.key]
    if (v === undefined) continue
    if ('default' in f && same(v, f.default)) continue
    out[f.key] = v
  }
  return out
}
