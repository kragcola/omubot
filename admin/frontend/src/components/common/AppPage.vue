<script setup lang="ts">
const props = withDefaults(defineProps<{
  back?: boolean
  showFooter?: boolean
  showHeader?: boolean
  title?: string
  description?: string
  eyebrow?: string
  /** Opt the page into a definite-height flex chain so a child terminal/panel
   *  (e.g. logs / sandbox) can fill the viewport's remaining space. Replaces the
   *  former `:has(.om-fill-page)` auto-detection, which silently failed when the
   *  `:has()` match didn't resolve, collapsing the terminal to content height. */
  fill?: boolean
}>(), {
  showHeader: true,
})

const route = useRoute()
const router = useRouter()

const resolvedTitle = computed(() => props.title ?? String(route.meta?.title ?? ''))
const resolvedDescription = computed(() => props.description ?? String(route.meta?.description ?? ''))
const resolvedEyebrow = computed(() => props.eyebrow ?? 'Omubot Console')
</script>

<template>
  <main class="om-page h-full flex-col flex-1 overflow-hidden" :class="{ 'om-page--fill': fill }">
    <div data-page-scroll-root class="cus-scroll om-page__body h-0 flex-1">
      <AppCard
        v-if="showHeader"
        bordered
        elevated
        class="om-page__hero mx-16 mt-16 min-h-60"
      >
        <slot v-if="$slots.header" name="header" />
        <template v-else>
          <div class="om-page__hero-inner">
            <div class="om-page__hero-main">
              <div class="om-page__eyebrow">
                {{ resolvedEyebrow }}
              </div>
              <div class="om-page__title-row">
                <slot name="title-prefix">
                  <button
                    v-if="back"
                    type="button"
                    class="om-page__back"
                    @click="router.back()"
                  >
                    <span>←</span>
                    <span>返回</span>
                  </button>
                </slot>
                <div class="om-page__title-stack">
                  <div class="om-page__title-line">
                    <h1 class="om-page__title">
                      {{ resolvedTitle }}
                    </h1>
                    <slot name="title-suffix" />
                  </div>
                  <p v-if="resolvedDescription" class="om-page__description">
                    {{ resolvedDescription }}
                  </p>
                </div>
              </div>
            </div>
            <div v-if="$slots.action" class="om-page__action">
              <slot name="action" />
            </div>
          </div>
        </template>
      </AppCard>
      <div class="om-page__surface-wrap mx-12 mb-12 mt-12 rounded-16">
        <AppCard bordered elevated class="om-page__surface p-24">
          <slot />
        </AppCard>
      </div>
    </div>
  </main>
</template>

<style scoped>
.om-page {
  position: relative;
  background: var(--om-page-gradient);
}

.om-page__hero {
  overflow: hidden;
  border-radius: 24px;
  background: var(--om-hero-gradient);
}

.om-page__hero::before {
  position: absolute;
  inset: auto -60px -70px auto;
  width: 180px;
  height: 180px;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(var(--primary-color), 0.16), transparent 68%);
  content: '';
}

.om-page__hero-inner {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 24px 28px;
}

.om-page__hero-main {
  min-width: 0;
  flex: 1;
}

.om-page__eyebrow {
  margin-bottom: 10px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.om-page__title-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.om-page__title-stack {
  min-width: 0;
}

.om-page__title-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.om-page__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.om-page__description {
  margin: 8px 0 0;
  max-width: 760px;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.65;
}

.om-page__action {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 12px;
  padding-top: 6px;
}

.om-page__back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 14px;
  border: 1px solid var(--om-border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.42);
  color: var(--om-text-2);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    transform 0.18s ease,
    color 0.18s ease,
    background-color 0.18s ease;
}

.om-page__back:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  background: rgba(255, 255, 255, 0.68);
  color: var(--om-text-1);
}

.om-page__body {
  padding: 0;
}

.om-page__surface-wrap {
  display: flex;
}

.om-page__surface {
  flex: 1;
  border-radius: 22px;
  background: var(--om-surface);
}

/* Fill-intent pages (pass `fill` prop, e.g. logs / sandbox) opt into a definite-
 * height flex chain so a child terminal/panel can fill the viewport's remaining
 * space. Driven by an explicit prop+class rather than `:has(.om-fill-page)`,
 * which silently failed when the `:has()` match didn't resolve and collapsed
 * the terminal to content height. All other pages keep their natural
 * content-height surface card unchanged. */
.om-page--fill .om-page__body {
  display: flex;
  flex-direction: column;
}

.om-page--fill .om-page__surface-wrap {
  min-height: 0;
  flex: 1;
}

.om-page--fill .om-page__surface {
  display: flex;
  min-height: 0;
  flex-direction: column;
}

@media (max-width: 900px) {
  .om-page__hero-inner {
    flex-direction: column;
    padding: 20px;
  }

  .om-page__action {
    width: 100%;
    justify-content: flex-start;
    padding-top: 0;
  }

  .om-page__title {
    font-size: 22px;
  }
}

@media (max-width: 640px) {
  .om-page__hero {
    margin: 12px 12px 0;
    border-radius: 20px;
  }

  .om-page__title-row {
    flex-direction: column;
    gap: 12px;
  }

  .om-page__surface {
    padding: 18px !important;
  }

}
</style>
