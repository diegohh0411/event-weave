import type { components } from '../api/schema'

export type RunConfig = components['schemas']['RunConfig']
export type DeploymentOut = components['schemas']['DeploymentOut']
export type Stage = components['schemas']['Stage']
export type Capability = components['schemas']['Capability']

export const STAGES: Stage[] = ['provide', 'extract', 'tag', 'enrich', 'embed', 'cluster']

export interface StageState {
  adapter: string
  settings: Record<string, unknown>
  /** capability -> deployment id */
  deployments: Record<string, string>
  /** Schema defaults: a setting equal to its default is left out of the config. */
  defaults?: Record<string, unknown>
}

export interface BuilderState {
  name: string
  track: RunConfig['track']
  seed: number
  stages: Record<Stage, StageState | undefined>
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** The request body for /runs, /runs/validate and /runs/estimate. `version` stays unset: the server fills it in. */
export function buildRunConfig(state: BuilderState): RunConfig {
  const stages: RunConfig['stages'] = {}
  for (const stage of STAGES) {
    const s = state.stages[stage]
    if (!s) continue
    const settings: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(s.settings)) {
      if (v === undefined) continue
      if (s.defaults && k in s.defaults && same(v, s.defaults[k])) continue
      settings[k] = v
    }
    stages[stage] = { adapter: s.adapter, settings, deployments: { ...s.deployments } }
  }
  return { name: state.name, track: state.track, seed: state.seed, stages }
}

export function readyDeploymentsFor(deployments: DeploymentOut[], capability: Capability): DeploymentOut[] {
  return deployments.filter(d => d.ready && d.capability === capability)
}
