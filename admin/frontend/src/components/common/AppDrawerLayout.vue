<template>
  <div class="om-drawer-layout">
    <div v-if="$slots.toolbar" class="om-drawer-layout__toolbar">
      <slot name="toolbar" />
    </div>

    <slot />

    <div v-if="$slots.footer" class="om-drawer-layout__footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.om-drawer-layout {
  display: grid;
  gap: 14px;
}

.om-drawer-layout__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.om-drawer-layout__footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 760px) {
  .om-drawer-layout__footer {
    flex-direction: column;
  }
}
</style>
