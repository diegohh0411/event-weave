<script setup lang="ts">
// Three woad warp threads crossed by a madder and a weld weft, woven over-under.
const WARP = [7, 14, 21]
const WEFT = [{ y: 10, colour: 'var(--color-madder-500)' }, { y: 18, colour: 'var(--color-weld-500)' }]
// At each crossing the weft is on top when (warp + weft) is odd.
const over = WEFT.flatMap((w, j) => WARP.filter((_, i) => (i + j) % 2 === 1).map(x => ({ x, ...w })))
</script>

<template>
  <svg viewBox="0 0 28 28" aria-hidden="true" class="shrink-0">
    <line v-for="w in WEFT" :key="`h${w.y}`" x1="2" x2="26" :y1="w.y" :y2="w.y" :stroke="w.colour" stroke-width="3.2" stroke-linecap="round" />
    <g v-for="x in WARP" :key="`v${x}`">
      <line :x1="x" :x2="x" y1="3" y2="25" stroke="var(--ew-loom)" stroke-width="6" />
      <line :x1="x" :x2="x" y1="3" y2="25" stroke="var(--ui-primary)" stroke-width="3.2" stroke-linecap="round" />
    </g>
    <g v-for="o in over" :key="`o${o.x}-${o.y}`">
      <line :x1="o.x - 4" :x2="o.x + 4" :y1="o.y" :y2="o.y" stroke="var(--ew-loom)" stroke-width="6" />
      <line :x1="o.x - 4.5" :x2="o.x + 4.5" :y1="o.y" :y2="o.y" :stroke="o.colour" stroke-width="3.2" />
    </g>
  </svg>
</template>
