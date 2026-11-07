export default defineNuxtConfig({
  modules: ['@nuxt/ui'],
  css: ['~/assets/css/main.css'],
  compatibilityDate: '2026-11-01',
  // The browser talks to the API directly (it is on 127.0.0.1:8000), so there is nothing to render on a server.
  ssr: false,
  devtools: { enabled: false },
  runtimeConfig: {
    public: {
      // Overridden by NUXT_PUBLIC_API_BASE at runtime.
      apiBase: 'http://127.0.0.1:8000'
    }
  },
  typescript: {
    strict: true,
    // mock/ and the node-side tests are plain TypeScript; page tests run inside the Nuxt environment.
    nodeTsConfig: { include: ['../mock/**/*', '../tests/contract/**/*', '../tests/unit/**/*', '../vitest.config.ts'] },
    tsConfig: { include: ['../tests/pages/**/*'] }
  }
})
