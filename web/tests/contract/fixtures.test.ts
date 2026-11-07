import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import Ajv2020 from 'ajv/dist/2020'
import addFormats from 'ajv-formats'
import { fixtures, routeKey } from '../../mock/fixtures'

const spec = JSON.parse(readFileSync(new URL('../../openapi.json', import.meta.url), 'utf8'))

const ajv = new Ajv2020({ strict: false, allErrors: true })
addFormats(ajv)
ajv.addSchema(spec, 'openapi')
// Compile every component schema up front so a schema Ajv cannot handle fails here, not in a page test.
const validators = new Map<string, ReturnType<typeof ajv.compile>>()
for (const name of Object.keys(spec.components.schemas)) {
  validators.set(name, ajv.compile({ $ref: `openapi#/components/schemas/${name}` }))
}

type Operation = { method: string, path: string, status: string, pointer: string }
const operations: Operation[] = []
for (const [path, item] of Object.entries<Record<string, any>>(spec.paths)) {
  for (const [method, op] of Object.entries<any>(item)) {
    for (const [status, resp] of Object.entries<any>(op.responses)) {
      const schema = resp.content?.['application/json']?.schema
      if (/^2/.test(status) && schema) operations.push({
        method: method.toUpperCase(),
        path,
        status,
        pointer: `/paths/${encodeURIComponent(path.replaceAll('~', '~0').replaceAll('/', '~1'))}/${method}/responses/${status}/content/application~1json/schema`
      })
    }
  }
}

describe('fixtures vs openapi.json', () => {
  it('compiles every component schema', () => {
    expect(validators.size).toBeGreaterThan(30)
  })

  it('has a fixture for every route', () => {
    const missing = operations.filter(o => !fixtures[routeKey(o.method, o.path)]?.length).map(o => `${o.method} ${o.path}`)
    expect(missing).toEqual([])
  })

  it('has no fixture for a route the API does not have', () => {
    const known = new Set(operations.map(o => routeKey(o.method, o.path)))
    expect(Object.keys(fixtures).filter(k => !known.has(k))).toEqual([])
  })

  for (const op of operations) {
    it(`${op.method} ${op.path} fixtures match the ${op.status} schema`, () => {
      const validate = ajv.compile({ $ref: `openapi#${op.pointer}` })
      const list = (fixtures[routeKey(op.method, op.path)] ?? []).filter(f => String(f.status) === op.status)
      expect(list.length).toBeGreaterThan(0)
      for (const f of list) {
        expect(validate(f.body), JSON.stringify(validate.errors)).toBe(true)
      }
    })
  }

  it('keeps list fixtures consistent with their paging fields', () => {
    for (const list of Object.values(fixtures)) {
      for (const f of list) {
        const body = f.body as { items?: unknown[], total?: number }
        if (Array.isArray(body?.items)) expect(body.total).toBe(body.items.length)
      }
    }
  })
})

describe('fixture cases later pages rely on', () => {
  const body = <T>(method: string, path: string, pick?: (f: { params?: Record<string, string> }) => boolean) =>
    fixtures[routeKey(method, path)]!.filter(f => !pick || pick(f)).map(f => f.body as T)

  it('has a deployment that is not ready, with a reason', () => {
    const [page] = body<any>('GET', '/deployments')
    const llm = page.items.filter((d: any) => d.capability === 'llm')
    expect(llm.some((d: any) => !d.ready && d.reason)).toBe(true)
    expect(llm.some((d: any) => d.ready)).toBe(true)
  })

  it('has a run with failed items', () => {
    const runs = body<any>('GET', '/runs/{run_id}')
    expect(runs.some(r => Object.values<any>(r.progress).some(p => p.failed > 0))).toBe(true)
    const [fails] = body<any>('GET', '/runs/{run_id}/failures')
    expect(fails.total).toBeGreaterThan(0)
  })

  it('has a member with a null decision and a mention with a null cluster', () => {
    const members = body<any>('GET', '/runs/{run_id}/clusters/{cluster_id}/members').flatMap(p => p.items)
    expect(members.some((m: any) => m.decision === null)).toBe(true)
    expect(members.some((m: any) => m.decision?.candidates?.some((c: any) => c.label))).toBe(true)
    const docs = body<any>('GET', '/runs/{run_id}/documents/{document_id}')
    expect(docs.flatMap(d => d.mentions).some((m: any) => m.cluster_id === null)).toBe(true)
  })

  it('has a Hindi cross row with the SKIPPED reason', () => {
    const [cmp] = body<any>('GET', '/compare')
    const hi = cmp.paper.find((r: any) => r.language === 'hi' && r.setting === 'cross')
    expect(hi.ours).toBeNull()
    expect(hi.skipped).toMatch(/Hindi crossDoc/)
  })

  it('has paper_checked false and paper rows on a Track 1 compare', () => {
    const cmps = body<any>('GET', '/compare')
    expect(cmps.some(c => c.paper_checked === false)).toBe(true)
    expect(cmps.some(c => c.paper && c.paper.length > 0 && c.runs.some((r: any) => r.track === 'paper'))).toBe(true)
    expect(cmps.some(c => c.paper === null && c.runs.every((r: any) => r.track === 'realistic'))).toBe(true)
  })

  it('has an estimate with amount null and a reason', () => {
    const ests = body<any>('POST', '/runs/estimate')
    expect(ests.some(e => e.amount === null && e.reason)).toBe(true)
    expect(ests.some(e => e.amount !== null && e.cached)).toBe(true)
  })
})
