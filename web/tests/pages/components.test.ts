import { describe, expect, it } from 'vitest'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import DeploymentPicker from '../../app/components/DeploymentPicker.vue'
import EstimatePanel from '../../app/components/EstimatePanel.vue'
import SchemaForm from '../../app/components/SchemaForm.vue'
import { fieldsFromSchema } from '../../app/utils/schemaForm'
import { deployments } from '../../mock/fixtures/deployments'
import { estimates } from '../../mock/fixtures/runs'

describe('DeploymentPicker', () => {
  it('lists only ready deployments of the capability', async () => {
    const w = await mountSuspended(DeploymentPicker, { props: { capability: 'llm', deployments: deployments.items, modelValue: undefined } })
    const items = w.findComponent({ name: 'USelect' }).props('items') as { value: string }[]
    expect(items.map(i => i.value)).toEqual(['claude-sonnet'])
    expect(w.text()).not.toContain('No ready deployment')
  })

  it('says so when nothing is ready', async () => {
    const none = deployments.items.filter(d => d.id !== 'claude-sonnet')
    const w = await mountSuspended(DeploymentPicker, { props: { capability: 'llm', deployments: none, modelValue: undefined } })
    expect(w.text()).toContain('No ready deployment for llm')
    expect(w.findComponent({ name: 'USelect' }).exists()).toBe(false)
  })
})

describe('EstimatePanel', () => {
  it('shows cached / total per stage and the amount', async () => {
    const w = await mountSuspended(EstimatePanel, { props: { estimate: estimates.priced } })
    expect(w.text()).toContain('cluster')
    expect(w.text()).toContain('1800 / 6230')
    expect(w.text()).toContain('4.83')
  })
  it('never shows $0 when there is no amount', async () => {
    const w = await mountSuspended(EstimatePanel, { props: { estimate: estimates.unpriced } })
    expect(w.text()).toContain('No estimate yet: deployment local-qwen has no price per token')
    expect(w.text()).not.toContain('$0')
  })
})

describe('SchemaForm', () => {
  it('emits an edited number and flags unparsable json', async () => {
    const fields = fieldsFromSchema({ type: 'object', properties: { k: { type: 'integer', default: 5 }, w: { type: 'object' } } })
    const w = await mountSuspended(SchemaForm, { props: { fields, modelValue: {} } })
    const inputs = w.findAll('input')
    await inputs[0]!.setValue('7')
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([{ k: 7 }])
    await w.find('textarea').setValue('{nope')
    expect(w.emitted('update:invalid')!.at(-1)).toEqual([true])
    expect(w.text()).toContain('Must be valid JSON')
  })
})
