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
  <AppCard bordered embedded class="om-panel-section">
    <div v-if="eyebrow || title || description || $slots.aside" class="om-panel-section__head">
      <div class="om-panel-section__copy">
        <p v-if="eyebrow" class="om-panel-section__eyebrow">
          {{ eyebrow }}
        </p>
        <h3 v-if="title" class="om-panel-section__title">
          {{ title }}
        </h3>
        <p v-if="description" class="om-panel-section__description">
          {{ description }}
        </p>
      </div>

      <div v-if="$slots.aside" class="om-panel-section__aside">
        <slot name="aside" />
      </div>
    </div>

    <slot />
  </AppCard>
</template>

<style scoped>
.om-panel-section {
  padding: 18px;
  border-radius: 18px;
}

.om-panel-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.om-panel-section__copy {
  min-width: 0;
  flex: 1;
}

.om-panel-section__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.om-panel-section__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 700;
}

.om-panel-section__description {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.om-panel-section__aside {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 760px) {
  .om-panel-section__head {
    flex-direction: column;
    align-items: stretch;
  }

  .om-panel-section__aside {
    justify-content: flex-start;
  }
}
</style>
