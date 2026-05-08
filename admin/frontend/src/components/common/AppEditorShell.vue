<template>
  <div class="om-editor-shell">
    <PageToolbar v-if="$slots.left || $slots.right">
      <template #left>
        <slot name="left" />
      </template>
      <template #right>
        <slot name="right" />
      </template>
    </PageToolbar>

    <slot />
  </div>
</template>

<style scoped>
.om-editor-shell {
  display: grid;
  gap: 16px;
}
</style>
