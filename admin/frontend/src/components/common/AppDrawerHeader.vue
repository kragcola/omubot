<script setup lang="ts">
withDefaults(defineProps<{
  eyebrow?: string
  title?: string
  description?: string
}>(), {
  eyebrow: '',
  title: '',
  description: '',
})
</script>

<template>
  <div class="om-drawer-header">
    <div class="om-drawer-header__copy">
      <p v-if="eyebrow" class="om-drawer-header__eyebrow">
        {{ eyebrow }}
      </p>
      <h2 v-if="title" class="om-drawer-header__title">
        {{ title }}
      </h2>
      <p v-if="description" class="om-drawer-header__description">
        {{ description }}
      </p>
    </div>

    <div v-if="$slots.aside" class="om-drawer-header__aside">
      <slot name="aside" />
    </div>
  </div>
</template>

<style scoped>
.om-drawer-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.om-drawer-header__copy {
  min-width: 0;
  flex: 1;
}

.om-drawer-header__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.om-drawer-header__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.3;
}

.om-drawer-header__description {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.om-drawer-header__aside {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 760px) {
  .om-drawer-header {
    flex-direction: column;
    align-items: stretch;
  }

  .om-drawer-header__aside {
    justify-content: flex-start;
  }
}
</style>
