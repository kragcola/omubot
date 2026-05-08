<template>
  <div class="page-toolbar">
    <div v-if="$slots.left" class="page-toolbar__side page-toolbar__side--left">
      <slot name="left" />
    </div>
    <div v-if="$slots.right" class="page-toolbar__side page-toolbar__side--right">
      <slot name="right" />
    </div>
  </div>
</template>

<style scoped>
.page-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.page-toolbar__side {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.page-toolbar__side--right {
  justify-content: flex-end;
}

@media (max-width: 680px) {
  .page-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .page-toolbar__side--right {
    justify-content: flex-start;
  }
}
</style>
